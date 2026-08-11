#!/usr/bin/env python3
"""Canonical GCP day-2 backup, release, and isolated restore entrypoint.

The local operator authenticates to GCP only for IAP SSH and immutable source
artifact upload.  The VM uses its attached service account for GCS and Secret
Manager.  No registry or application credential crosses SSH or evidence files.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shlex
import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


REPO = Path(__file__).resolve().parents[1]
REMOTE_ROOT = REPO / "scripts" / "gcp"
EVIDENCE_ROOT = REPO / "docs" / "release-evidence"
FULL_SHA_RE = re.compile(r"^[0-9a-f]{40}$")
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
TAG_RE = re.compile(r"^v[0-9][0-9A-Za-z._-]*$")
SECRET_RE = re.compile(
    r"(?i)(ghp_[A-Za-z0-9_]+|github_pat_[A-Za-z0-9_]+|"
    r"(token|password|secret|credential)\s*[=:]\s*[^\s]+)"
)


@dataclass(frozen=True)
class RemoteResult:
    returncode: int
    stdout: str
    stderr: str


def utc_stamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def redact(value: str) -> str:
    return SECRET_RE.sub("[REDACTED]", value)


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def terraform_binary() -> str:
    configured = os.environ.get("OMEGA_TERRAFORM_BIN", "tofu")
    if not re.fullmatch(r"[A-Za-z0-9_./-]+", configured):
        raise ValueError("OMEGA_TERRAFORM_BIN contains unsafe characters")
    resolved = shutil.which(configured)
    if resolved is None:
        raise RuntimeError(
            f"configured Terraform-compatible binary is unavailable: {configured}"
        )
    return resolved


def run(
    command: list[str],
    *,
    cwd: Path = REPO,
    input_text: str | None = None,
    timeout: int = 300,
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        command,
        cwd=cwd,
        input=input_text,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
        timeout=timeout,
    )


def git(*args: str, timeout: int = 120) -> str:
    result = run(["git", *args], timeout=timeout)
    if result.returncode != 0:
        raise RuntimeError(redact(result.stderr or result.stdout))
    return result.stdout.strip()


def validate_published_release_identity(
    tag: str, deploy_ref: str, *, require_main: bool
) -> str:
    """Require a published annotated tag, exact commit, and VERSION match."""
    if not TAG_RE.fullmatch(tag) or "latest" in tag:
        raise ValueError("tag must be an immutable v-prefixed release tag")
    if not FULL_SHA_RE.fullmatch(deploy_ref):
        raise ValueError("deploy-ref must be a full lowercase commit SHA")
    fetched = run(["git", "fetch", "origin", "--tags"], timeout=300)
    if fetched.returncode != 0:
        raise RuntimeError(redact(fetched.stderr or fetched.stdout))
    if git("cat-file", "-t", f"refs/tags/{tag}") != "tag":
        raise ValueError("release tag must be annotated")
    tagged_commit = git("rev-parse", f"refs/tags/{tag}^{{commit}}")
    if tagged_commit != deploy_ref:
        raise ValueError(f"tag commit {tagged_commit} does not match {deploy_ref}")
    if require_main:
        origin_main = git("rev-parse", "origin/main")
        if origin_main != deploy_ref:
            raise ValueError(f"origin/main {origin_main} does not match {deploy_ref}")
    version = git("show", f"{deploy_ref}:VERSION").strip()
    if f"v{version}" != tag:
        raise ValueError(f"VERSION {version} does not match tag {tag}")

    remote = run(
        [
            "git",
            "ls-remote",
            "--tags",
            "origin",
            f"refs/tags/{tag}",
            f"refs/tags/{tag}^{{}}",
        ],
        timeout=120,
    )
    if remote.returncode != 0:
        raise RuntimeError(redact(remote.stderr or remote.stdout))
    commits = {line.split()[0] for line in remote.stdout.splitlines() if line.strip()}
    if deploy_ref not in commits:
        raise ValueError("annotated release tag is not published at the exact commit")
    return version


def validate_release_identity(tag: str, deploy_ref: str) -> str:
    """Require an annotated release at the exact current origin/main commit."""
    return validate_published_release_identity(tag, deploy_ref, require_main=True)


def validate_candidate_main(deploy_ref: str) -> str:
    """Bind a pre-tag operation to the exact, current origin/main candidate."""
    if not FULL_SHA_RE.fullmatch(deploy_ref):
        raise ValueError("candidate-ref must be a full lowercase commit SHA")
    fetched = run(["git", "fetch", "origin"], timeout=300)
    if fetched.returncode != 0:
        raise RuntimeError(redact(fetched.stderr or fetched.stdout))
    origin_main = git("rev-parse", "origin/main")
    if origin_main != deploy_ref:
        raise ValueError(f"origin/main {origin_main} does not match {deploy_ref}")
    version = git("show", f"{deploy_ref}:VERSION").strip()
    if not re.fullmatch(r"[0-9]+\.[0-9]+\.[0-9]+(?:-[0-9A-Za-z.-]+)?", version):
        raise ValueError("candidate VERSION is invalid")
    return version


def require_local_file_at_ref(deploy_ref: str, relative: str) -> None:
    committed = run(["git", "show", f"{deploy_ref}:{relative}"])
    if committed.returncode != 0:
        raise RuntimeError(redact(committed.stderr or committed.stdout))
    if committed.stdout != (REPO / relative).read_text(encoding="utf-8"):
        raise ValueError(f"local {relative} differs from the exact candidate commit")


def validate_candidate_identity(deploy_ref: str) -> str:
    """Bind the backup controller and helper to current origin/main."""
    version = validate_candidate_main(deploy_ref)
    committed_backup = run(["git", "show", f"{deploy_ref}:scripts/gcp/backup.sh"])
    if committed_backup.returncode != 0:
        raise RuntimeError(redact(committed_backup.stderr or committed_backup.stdout))
    if committed_backup.stdout != (REMOTE_ROOT / "backup.sh").read_text(
        encoding="utf-8"
    ):
        raise ValueError(
            "local backup controller differs from the exact candidate commit"
        )
    return version


def create_archive(deploy_ref: str, destination: Path) -> str:
    result = run(
        [
            "git",
            "archive",
            "--format=tar.gz",
            f"--output={destination}",
            deploy_ref,
        ],
        timeout=300,
    )
    if result.returncode != 0:
        raise RuntimeError(redact(result.stderr or result.stdout))
    return sha256_file(destination)


def _custom_metadata(payload: dict[str, Any]) -> dict[str, str]:
    for key in ("metadata", "customMetadata", "custom_metadata"):
        value = payload.get(key)
        if isinstance(value, dict):
            return {str(k): str(v) for k, v in value.items()}
    return {}


def upload_artifact_immutable(
    archive: Path, bucket: str, deploy_ref: str, artifact_sha256: str
) -> str:
    if not re.fullmatch(r"[a-z0-9][a-z0-9._-]{1,61}[a-z0-9]", bucket):
        raise ValueError("invalid GCS source bucket")
    uri = f"gs://{bucket}/deploy-artifacts/{deploy_ref}/repo.tar.gz"
    metadata = f"omega-artifact-sha256={artifact_sha256},omega-deploy-ref={deploy_ref}"
    upload = run(
        [
            "gcloud",
            "--quiet",
            "storage",
            "cp",
            str(archive),
            uri,
            "--if-generation-match=0",
            f"--custom-metadata={metadata}",
        ],
        timeout=600,
    )
    if upload.returncode == 0:
        return uri

    # Idempotent retry is allowed only when the existing immutable object is
    # explicitly bound to this exact commit and checksum.
    describe = run(
        [
            "gcloud",
            "--quiet",
            "storage",
            "objects",
            "describe",
            uri,
            "--format=json",
        ],
        timeout=120,
    )
    if describe.returncode != 0:
        raise RuntimeError(redact(upload.stderr or upload.stdout))
    payload = json.loads(describe.stdout)
    existing = _custom_metadata(payload)
    if existing.get("omega-artifact-sha256") != artifact_sha256:
        raise RuntimeError("existing immutable artifact checksum metadata differs")
    if existing.get("omega-deploy-ref") != deploy_ref:
        raise RuntimeError("existing immutable artifact commit metadata differs")
    return uri


def remote_script(
    *,
    project: str,
    zone: str,
    instance: str,
    script: Path,
    arguments: list[str],
    timeout: int,
) -> RemoteResult:
    if not script.is_file():
        raise FileNotFoundError(script)
    remote_command = shlex.join(
        ["sudo", "--non-interactive", "bash", "-s", "--", *arguments]
    )
    result = run(
        [
            "gcloud",
            "--quiet",
            "compute",
            "ssh",
            instance,
            f"--project={project}",
            f"--zone={zone}",
            "--tunnel-through-iap",
            f"--command={remote_command}",
        ],
        input_text=script.read_text(encoding="utf-8"),
        timeout=timeout,
    )
    return RemoteResult(result.returncode, result.stdout, result.stderr)


def parse_remote(stdout: str, prefix: str, json_prefix: str) -> tuple[list[dict], dict]:
    checks: list[dict] = []
    payload: dict = {}
    for line in stdout.splitlines():
        if line.startswith(prefix + "\t"):
            _marker, name, status, evidence = (line.split("\t", 3) + [""])[:4]
            checks.append(
                {"name": name, "status": status, "evidence": redact(evidence)}
            )
        elif line.startswith(json_prefix + "="):
            try:
                payload = json.loads(line.split("=", 1)[1])
            except json.JSONDecodeError:
                payload = {}
    return checks, payload


def write_evidence(
    evidence_dir: Path,
    *,
    operation: str,
    result: RemoteResult,
    checks: list[dict],
    payload: dict,
) -> str:
    status = "PASS"
    if (
        result.returncode != 0
        or not checks
        or any(item.get("status") != "PASS" for item in checks)
        or payload.get("status") != "PASS"
    ):
        status = "FAIL"
    evidence_dir.mkdir(parents=True, exist_ok=True)
    summary = {
        "schema_version": 1,
        "operation": operation,
        "status": status,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "remote_returncode": result.returncode,
        "checks": checks,
        "payload": payload,
        "secrets_included": False,
    }
    (evidence_dir / "summary.json").write_text(
        json.dumps(summary, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    (evidence_dir / "remote_stdout_redacted.txt").write_text(
        redact(result.stdout), encoding="utf-8"
    )
    (evidence_dir / "remote_stderr_redacted.txt").write_text(
        redact(result.stderr), encoding="utf-8"
    )
    rows = [
        f"| {item['name']} | {item['status']} | {str(item['evidence']).replace('|', chr(92) + '|')} |"
        for item in checks
    ]
    (evidence_dir / "REPORT.md").write_text(
        "\n".join(
            [
                f"# GCP {operation} Evidence",
                "",
                f"- status: `{status}`",
                "- secrets included: `false`",
                "",
                "| Check | Status | Evidence |",
                "|---|---|---|",
                *rows,
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    return status


def common_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--project", default=os.environ.get("GCP_PROJECT_ID", ""))
    parser.add_argument("--zone", default=os.environ.get("GCP_APP_ZONE", ""))
    parser.add_argument("--instance", default=os.environ.get("GCP_APP_INSTANCE", ""))
    parser.add_argument(
        "--compose-project",
        default=os.environ.get("OMEGA_GCP_COMPOSE_PROJECT", "infra"),
    )
    parser.add_argument("--evidence-dir", type=Path, default=None)
    parser.add_argument("--timeout-seconds", type=int, default=3600)


def terraform_safety_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--terraform-dir",
        type=Path,
        default=Path(
            os.environ.get("GCP_TERRAFORM_DIR", str(REPO / "infra/terraform-gcp"))
        ),
    )
    parser.add_argument(
        "--terraform-var-file",
        type=Path,
        default=Path(os.environ.get("GCP_TERRAFORM_VAR_FILE", "")),
    )
    parser.add_argument(
        "--project-number", default=os.environ.get("GCP_PROJECT_NUMBER", "")
    )
    parser.add_argument(
        "--billing-account-id", default=os.environ.get("GCP_BILLING_ACCOUNT_ID", "")
    )
    parser.add_argument(
        "--public-console-domain",
        default=os.environ.get("GCP_PUBLIC_CONSOLE_DOMAIN", ""),
    )
    parser.add_argument(
        "--public-workspace-domain",
        default=os.environ.get("GCP_PUBLIC_WORKSPACE_DOMAIN", ""),
    )


def require_target(args: argparse.Namespace) -> None:
    if not args.project or not args.zone or not args.instance:
        raise SystemExit(
            "GCP_PROJECT_ID, GCP_APP_ZONE, and GCP_APP_INSTANCE are required"
        )


def validate_startup_metadata(
    *, project: str, zone: str, instance: str, expected_sha256: str
) -> None:
    """Read back the effective GCE startup script and require Terraform's hash."""
    if not SHA256_RE.fullmatch(expected_sha256):
        raise ValueError("GCP_STARTUP_SCRIPT_SHA256 must be an exact sha256")
    result = run(
        [
            "gcloud",
            "--quiet",
            "compute",
            "instances",
            "describe",
            instance,
            f"--project={project}",
            f"--zone={zone}",
            "--format=json(metadata.items)",
        ],
        timeout=120,
    )
    if result.returncode != 0:
        raise RuntimeError(redact(result.stderr or result.stdout))
    payload = json.loads(result.stdout)
    items = payload.get("metadata", {}).get("items", [])
    values = [
        item.get("value", "") for item in items if item.get("key") == "startup-script"
    ]
    if len(values) != 1:
        raise RuntimeError("GCE metadata must contain exactly one startup-script")
    actual = hashlib.sha256(values[0].encode()).hexdigest()
    if actual != expected_sha256:
        raise RuntimeError(
            f"GCE startup-script sha256 {actual} differs from Terraform output"
        )


def validate_terraform_source_contract(
    *, terraform_dir: Path, source_ref: str, artifact_bucket: str
) -> str:
    """Bind live Terraform outputs to the exact candidate artifact and render."""
    resolved = terraform_dir.resolve()
    try:
        resolved.relative_to(REPO.resolve())
    except ValueError as exc:
        raise ValueError("Terraform directory must be inside this repository") from exc
    terraform_bin = terraform_binary()
    result = run(
        [terraform_bin, f"-chdir={resolved}", "output", "-json"],
        timeout=120,
    )
    if result.returncode != 0:
        raise RuntimeError(redact(result.stderr or result.stdout))
    payload = json.loads(result.stdout)

    def output(name: str) -> str:
        item = payload.get(name)
        value = item.get("value") if isinstance(item, dict) else None
        if not isinstance(value, str) or not value:
            raise RuntimeError(f"Terraform output is missing or not a string: {name}")
        return value

    source_sha = output("source_sha")
    source_bucket = output("source_bucket")
    source_object = output("source_object")
    startup_sha = output("startup_script_sha256")
    if source_sha != source_ref:
        raise RuntimeError(
            f"Terraform source_sha {source_sha} does not match expected ref {source_ref}"
        )
    if source_bucket != artifact_bucket:
        raise RuntimeError("Terraform source_bucket differs from the artifact bucket")
    expected_object = f"deploy-artifacts/{source_ref}/repo.tar.gz"
    if source_object != expected_object:
        raise RuntimeError(
            "Terraform source_object is not the exact immutable candidate artifact"
        )
    if not SHA256_RE.fullmatch(startup_sha):
        raise RuntimeError("Terraform startup_script_sha256 output is invalid")
    return startup_sha


def validate_reviewed_terraform_plan(
    *,
    terraform_dir: Path,
    var_file: Path,
    source_ref: str,
    source_bucket: str,
    project_id: str,
    project_number: str,
    billing_account_id: str,
    public_console_domain: str,
    public_workspace_domain: str,
) -> None:
    """Require explicit production invariants and a zero-change post-apply plan."""
    if not var_file.is_file():
        raise ValueError("GCP_TERRAFORM_VAR_FILE must be a reviewed existing file")
    text = var_file.read_text(encoding="utf-8")

    def assignment(name: str) -> str:
        matches = re.findall(
            rf"(?m)^\s*{re.escape(name)}\s*=\s*(?:\"([^\"]*)\"|([^#\s]+))\s*(?:#.*)?$",
            text,
        )
        if len(matches) != 1:
            raise ValueError(f"reviewed tfvars must assign {name} exactly once")
        quoted, bare = matches[0]
        return quoted if quoted != "" else bare

    expected = {
        "project_id": project_id,
        "project_number": project_number,
        "billing_account_id": billing_account_id,
        "source_bucket": source_bucket,
        "source_object": f"deploy-artifacts/{source_ref}/repo.tar.gz",
        "source_sha": source_ref,
        "app_machine_type": "e2-standard-4",
        "boot_disk_size_gb": "60",
        "data_disk_size_gb": "150",
        "public_console_domain": public_console_domain,
        "public_workspace_domain": public_workspace_domain,
        "enable_https": "true",
        "enable_airflow_scheduler": "true",
        "monthly_budget_currency": "EUR",
    }
    if not re.fullmatch(r"[0-9]+", project_number):
        raise ValueError("GCP_PROJECT_NUMBER must be numeric")
    if not re.fullmatch(r"[0-9A-F]{6}-[0-9A-F]{6}-[0-9A-F]{6}", billing_account_id):
        raise ValueError("GCP_BILLING_ACCOUNT_ID is invalid")
    for domain in (public_console_domain, public_workspace_domain):
        if not re.fullmatch(r"[a-z0-9](?:[a-z0-9-]{0,62}\.)+[a-z]{2,63}", domain):
            raise ValueError("both managed-certificate domains must be explicit")
    for name, value in expected.items():
        if assignment(name) != value:
            raise ValueError(f"reviewed tfvars value differs: {name}")

    resolved = terraform_dir.resolve()
    try:
        resolved.relative_to(REPO.resolve())
    except ValueError as exc:
        raise ValueError("Terraform directory must be inside this repository") from exc
    terraform_bin = terraform_binary()
    plan = run(
        [
            terraform_bin,
            f"-chdir={resolved}",
            "plan",
            "-detailed-exitcode",
            "-input=false",
            "-no-color",
            f"-var-file={var_file.resolve()}",
        ],
        timeout=600,
    )
    if plan.returncode == 2:
        raise RuntimeError(
            "Terraform has unapplied changes; review/apply the saved plan before release operations"
        )
    if plan.returncode != 0:
        raise RuntimeError(
            "Terraform zero-drift plan failed; output intentionally suppressed"
        )


def command_image_preflight(args: argparse.Namespace) -> int:
    require_target(args)
    if not args.confirm and os.environ.get("CONFIRM_GCP_IMAGE_PREFLIGHT") != "1":
        raise SystemExit(
            "image preflight requires --confirm or CONFIRM_GCP_IMAGE_PREFLIGHT=1"
        )
    if not re.fullmatch(r"[1-9][0-9]*", args.ghcr_secret_version):
        raise SystemExit("GCP_GHCR_SECRET_VERSION must be an explicit numeric version")
    validate_published_release_identity(
        args.target_tag, args.target_ref, require_main=False
    )
    validate_candidate_main(args.helper_ref)
    require_local_file_at_ref(args.helper_ref, "scripts/gcp/image-preflight.sh")
    with tempfile.TemporaryDirectory(prefix="omega-gcp-preflight-helper-") as temp:
        archive = Path(temp) / "repo.tar.gz"
        helper_sha = create_archive(args.helper_ref, archive)
        helper_uri = upload_artifact_immutable(
            archive, args.artifact_bucket, args.helper_ref, helper_sha
        )
    result = remote_script(
        project=args.project,
        zone=args.zone,
        instance=args.instance,
        script=REMOTE_ROOT / "image-preflight.sh",
        arguments=[
            args.helper_ref,
            helper_uri,
            helper_sha,
            args.target_tag,
            args.target_ref,
            args.ghcr_owner,
            args.environment,
            args.ghcr_secret_version,
            args.purpose,
        ],
        timeout=args.timeout_seconds,
    )
    checks, payload = parse_remote(
        result.stdout,
        "OMEGA_GCP_IMAGE_PREFLIGHT_CHECK",
        "OMEGA_GCP_IMAGE_PREFLIGHT_JSON",
    )
    evidence = args.evidence_dir or EVIDENCE_ROOT / "image-preflight-gcp" / utc_stamp()
    status = write_evidence(
        evidence,
        operation=f"Image Preflight ({args.purpose})",
        result=result,
        checks=checks,
        payload=payload,
    )
    print(
        json.dumps(
            {"status": status, "evidence_dir": str(evidence), **payload}, indent=2
        )
    )
    return 0 if status == "PASS" else 1


def command_prepare_artifacts(args: argparse.Namespace) -> int:
    """Publish immutable candidate and current-live archives before TF apply."""
    validate_candidate_main(args.candidate_ref)
    require_local_file_at_ref(args.candidate_ref, "scripts/gcp/backup.sh")
    if not FULL_SHA_RE.fullmatch(args.current_live_ref):
        raise SystemExit("GCP_CURRENT_LIVE_REF must be one full lowercase SHA")
    if git("cat-file", "-t", args.current_live_ref) != "commit":
        raise SystemExit("GCP_CURRENT_LIVE_REF is not a local Git commit")
    artifacts: dict[str, dict[str, str]] = {}
    with tempfile.TemporaryDirectory(prefix="omega-gcp-prepare-artifacts-") as temp:
        for name, deploy_ref in (
            ("candidate", args.candidate_ref),
            ("current_live", args.current_live_ref),
        ):
            if deploy_ref in {item["deploy_ref"] for item in artifacts.values()}:
                existing = next(
                    item
                    for item in artifacts.values()
                    if item["deploy_ref"] == deploy_ref
                )
                artifacts[name] = existing
                continue
            archive = Path(temp) / f"{name}.tar.gz"
            artifact_sha = create_archive(deploy_ref, archive)
            artifact_uri = upload_artifact_immutable(
                archive, args.artifact_bucket, deploy_ref, artifact_sha
            )
            artifacts[name] = {
                "deploy_ref": deploy_ref,
                "uri": artifact_uri,
                "sha256": artifact_sha,
            }
    print(
        json.dumps(
            {"status": "PASS", "artifacts": artifacts, "secrets_included": False},
            indent=2,
            sort_keys=True,
        )
    )
    return 0


def command_backup(args: argparse.Namespace) -> int:
    require_target(args)
    if not args.confirm and os.environ.get("CONFIRM_GCP_BACKUP") != "1":
        raise SystemExit("backup requires --confirm or CONFIRM_GCP_BACKUP=1")
    backup_id = args.backup_id or f"{utc_stamp()}-predeploy"
    validate_candidate_identity(args.candidate_ref)
    if not FULL_SHA_RE.fullmatch(args.current_live_ref):
        raise SystemExit("GCP_CURRENT_LIVE_REF must be one full lowercase SHA")
    if git("cat-file", "-t", args.current_live_ref) != "commit":
        raise SystemExit("GCP_CURRENT_LIVE_REF is not a local Git commit")
    with tempfile.TemporaryDirectory(prefix="omega-gcp-backup-candidate-") as temp:
        archive = Path(temp) / "repo.tar.gz"
        artifact_sha = create_archive(args.candidate_ref, archive)
        artifact_uri = upload_artifact_immutable(
            archive, args.artifact_bucket, args.candidate_ref, artifact_sha
        )
        if args.current_live_ref == args.candidate_ref:
            live_artifact_uri = artifact_uri
        else:
            live_archive = Path(temp) / "current-live-repo.tar.gz"
            live_artifact_sha = create_archive(args.current_live_ref, live_archive)
            live_artifact_uri = upload_artifact_immutable(
                live_archive,
                args.artifact_bucket,
                args.current_live_ref,
                live_artifact_sha,
            )
    expected_live_uri = (
        f"gs://{args.artifact_bucket}/deploy-artifacts/"
        f"{args.current_live_ref}/repo.tar.gz"
    )
    if live_artifact_uri != expected_live_uri:
        raise RuntimeError("current live source artifact URI is not exact")
    validate_reviewed_terraform_plan(
        terraform_dir=args.terraform_dir,
        var_file=args.terraform_var_file,
        source_ref=args.current_live_ref,
        source_bucket=args.artifact_bucket,
        project_id=args.project,
        project_number=args.project_number,
        billing_account_id=args.billing_account_id,
        public_console_domain=args.public_console_domain,
        public_workspace_domain=args.public_workspace_domain,
    )
    startup_sha = validate_terraform_source_contract(
        terraform_dir=args.terraform_dir,
        source_ref=args.current_live_ref,
        artifact_bucket=args.artifact_bucket,
    )
    validate_startup_metadata(
        project=args.project,
        zone=args.zone,
        instance=args.instance,
        expected_sha256=startup_sha,
    )
    result = remote_script(
        project=args.project,
        zone=args.zone,
        instance=args.instance,
        script=REMOTE_ROOT / "backup.sh",
        arguments=[
            args.bucket,
            backup_id,
            args.compose_project,
            args.candidate_ref,
            artifact_uri,
            artifact_sha,
            args.current_live_ref,
        ],
        timeout=args.timeout_seconds,
    )
    checks, payload = parse_remote(
        result.stdout, "OMEGA_GCP_BACKUP_CHECK", "OMEGA_GCP_BACKUP_JSON"
    )
    evidence = args.evidence_dir or EVIDENCE_ROOT / "backup-gcp" / utc_stamp()
    status = write_evidence(
        evidence,
        operation="Backup",
        result=result,
        checks=checks,
        payload=payload,
    )
    print(
        json.dumps(
            {"status": status, "evidence_dir": str(evidence), **payload}, indent=2
        )
    )
    return 0 if status == "PASS" else 1


def command_deploy(args: argparse.Namespace) -> int:
    require_target(args)
    if not args.confirm and os.environ.get("CONFIRM_GCP_DEPLOY") != "1":
        raise SystemExit("deploy requires --confirm or CONFIRM_GCP_DEPLOY=1")
    if not SHA256_RE.fullmatch(args.backup_manifest_sha256):
        raise SystemExit("GCP_BACKUP_MANIFEST_SHA256 must be an exact sha256")
    if not re.fullmatch(r"[1-9][0-9]*", args.ghcr_secret_version):
        raise SystemExit("GCP_GHCR_SECRET_VERSION must be an explicit numeric version")
    version = validate_release_identity(args.tag, args.deploy_ref)
    with tempfile.TemporaryDirectory(prefix="omega-gcp-artifact-") as temp:
        archive = Path(temp) / "repo.tar.gz"
        artifact_sha = create_archive(args.deploy_ref, archive)
        artifact_uri = upload_artifact_immutable(
            archive, args.artifact_bucket, args.deploy_ref, artifact_sha
        )
    validate_reviewed_terraform_plan(
        terraform_dir=args.terraform_dir,
        var_file=args.terraform_var_file,
        source_ref=args.deploy_ref,
        source_bucket=args.artifact_bucket,
        project_id=args.project,
        project_number=args.project_number,
        billing_account_id=args.billing_account_id,
        public_console_domain=args.public_console_domain,
        public_workspace_domain=args.public_workspace_domain,
    )
    startup_sha = validate_terraform_source_contract(
        terraform_dir=args.terraform_dir,
        source_ref=args.deploy_ref,
        artifact_bucket=args.artifact_bucket,
    )
    validate_startup_metadata(
        project=args.project,
        zone=args.zone,
        instance=args.instance,
        expected_sha256=startup_sha,
    )
    result = remote_script(
        project=args.project,
        zone=args.zone,
        instance=args.instance,
        script=REMOTE_ROOT / "day2-release.sh",
        arguments=[
            args.tag,
            args.deploy_ref,
            artifact_uri,
            artifact_sha,
            version,
            args.backup_manifest_uri,
            args.backup_manifest_sha256,
            args.ghcr_owner,
            args.compose_project,
            args.environment,
            args.ghcr_secret_version,
        ],
        timeout=args.timeout_seconds,
    )
    checks, payload = parse_remote(
        result.stdout, "OMEGA_GCP_RELEASE_CHECK", "OMEGA_GCP_RELEASE_JSON"
    )
    evidence = args.evidence_dir or EVIDENCE_ROOT / "deploy-gcp" / utc_stamp()
    status = write_evidence(
        evidence,
        operation="Deploy",
        result=result,
        checks=checks,
        payload=payload,
    )
    print(
        json.dumps(
            {"status": status, "evidence_dir": str(evidence), **payload}, indent=2
        )
    )
    return 0 if status == "PASS" else 1


def command_rehearsal(args: argparse.Namespace) -> int:
    require_target(args)
    if not SHA256_RE.fullmatch(args.backup_manifest_sha256):
        raise SystemExit("GCP_BACKUP_MANIFEST_SHA256 must be an exact sha256")
    result = remote_script(
        project=args.project,
        zone=args.zone,
        instance=args.instance,
        script=REMOTE_ROOT / "restore-rehearsal.sh",
        arguments=[
            args.backup_manifest_uri,
            args.backup_manifest_sha256,
            args.object_verify_mode,
        ],
        timeout=args.timeout_seconds,
    )
    checks, payload = parse_remote(
        result.stdout, "OMEGA_GCP_REHEARSAL_CHECK", "OMEGA_GCP_REHEARSAL_JSON"
    )
    evidence = (
        args.evidence_dir or EVIDENCE_ROOT / "restore-rehearsal-gcp" / utc_stamp()
    )
    status = write_evidence(
        evidence,
        operation="Restore Rehearsal",
        result=result,
        checks=checks,
        payload=payload,
    )
    print(
        json.dumps(
            {"status": status, "evidence_dir": str(evidence), **payload}, indent=2
        )
    )
    return 0 if status == "PASS" else 1


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)

    prepare = commands.add_parser(
        "prepare-artifacts",
        help="publish immutable current-live and candidate source archives",
    )
    prepare.add_argument(
        "--candidate-ref", default=os.environ.get("GCP_DEPLOY_REF", "")
    )
    prepare.add_argument(
        "--current-live-ref", default=os.environ.get("GCP_CURRENT_LIVE_REF", "")
    )
    prepare.add_argument(
        "--artifact-bucket", default=os.environ.get("GCP_SOURCE_BUCKET", "")
    )
    prepare.set_defaults(handler=command_prepare_artifacts)

    image_preflight = commands.add_parser(
        "image-preflight",
        help="prove server-owned authenticated pullability for one published tag",
    )
    common_args(image_preflight)
    image_preflight.add_argument(
        "--target-tag", default=os.environ.get("GCP_IMAGE_PREFLIGHT_TAG", "")
    )
    image_preflight.add_argument(
        "--target-ref", default=os.environ.get("GCP_IMAGE_PREFLIGHT_REF", "")
    )
    image_preflight.add_argument(
        "--helper-ref", default=os.environ.get("GCP_DEPLOY_REF", "")
    )
    image_preflight.add_argument(
        "--artifact-bucket", default=os.environ.get("GCP_SOURCE_BUCKET", "")
    )
    image_preflight.add_argument(
        "--ghcr-owner",
        default=os.environ.get("GHCR_OWNER", "emmanuelnavaromero02-commits"),
    )
    image_preflight.add_argument(
        "--environment", default=os.environ.get("OMEGA_GCP_ENVIRONMENT", "")
    )
    image_preflight.add_argument(
        "--ghcr-secret-version",
        default=os.environ.get("OMEGA_GHCR_PULL_SECRET_VERSION", ""),
    )
    image_preflight.add_argument(
        "--purpose",
        choices=("release", "rollback"),
        default=os.environ.get("GCP_IMAGE_PREFLIGHT_PURPOSE", "rollback"),
    )
    image_preflight.add_argument("--confirm", action="store_true")
    image_preflight.set_defaults(handler=command_image_preflight)

    backup = commands.add_parser("backup", help="create a writer-fenced GCP backup")
    common_args(backup)
    terraform_safety_args(backup)
    backup.add_argument("--bucket", default=os.environ.get("GCP_LAKEHOUSE_BUCKET", ""))
    backup.add_argument("--backup-id", default=os.environ.get("GCP_BACKUP_ID", ""))
    backup.add_argument("--candidate-ref", default=os.environ.get("GCP_DEPLOY_REF", ""))
    backup.add_argument(
        "--artifact-bucket", default=os.environ.get("GCP_SOURCE_BUCKET", "")
    )
    backup.add_argument(
        "--current-live-ref", default=os.environ.get("GCP_CURRENT_LIVE_REF", "")
    )
    backup.add_argument("--confirm", action="store_true")
    backup.set_defaults(handler=command_backup)

    deploy = commands.add_parser("deploy", help="deploy one exact published release")
    common_args(deploy)
    terraform_safety_args(deploy)
    deploy.add_argument("--tag", default=os.environ.get("GCP_RELEASE_TAG", ""))
    deploy.add_argument("--deploy-ref", default=os.environ.get("GCP_DEPLOY_REF", ""))
    deploy.add_argument(
        "--artifact-bucket", default=os.environ.get("GCP_SOURCE_BUCKET", "")
    )
    deploy.add_argument(
        "--backup-manifest-uri",
        default=os.environ.get("GCP_BACKUP_MANIFEST_URI", ""),
    )
    deploy.add_argument(
        "--backup-manifest-sha256",
        default=os.environ.get("GCP_BACKUP_MANIFEST_SHA256", ""),
    )
    deploy.add_argument(
        "--ghcr-owner",
        default=os.environ.get("GHCR_OWNER", "emmanuelnavaromero02-commits"),
    )
    deploy.add_argument(
        "--environment", default=os.environ.get("OMEGA_GCP_ENVIRONMENT", "")
    )
    deploy.add_argument(
        "--ghcr-secret-version",
        default=os.environ.get("OMEGA_GHCR_PULL_SECRET_VERSION", ""),
    )
    deploy.add_argument("--confirm", action="store_true")
    deploy.set_defaults(handler=command_deploy)

    rehearsal = commands.add_parser(
        "restore-rehearsal", help="restore into isolated ephemeral DB volumes"
    )
    common_args(rehearsal)
    rehearsal.add_argument(
        "--backup-manifest-uri",
        default=os.environ.get("GCP_BACKUP_MANIFEST_URI", ""),
    )
    rehearsal.add_argument(
        "--backup-manifest-sha256",
        default=os.environ.get("GCP_BACKUP_MANIFEST_SHA256", ""),
    )
    rehearsal.add_argument(
        "--object-verify-mode",
        choices=("all", "sample"),
        default=os.environ.get("GCP_OBJECT_VERIFY_MODE", "all"),
    )
    rehearsal.set_defaults(handler=command_rehearsal)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return int(args.handler(args))


if __name__ == "__main__":
    raise SystemExit(main())
