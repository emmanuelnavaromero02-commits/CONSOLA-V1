#!/usr/bin/env python3
"""Canonical GCP day-2 backup, release, and isolated restore entrypoint.

The local operator authenticates to GCP only for IAP SSH and immutable source
artifact upload.  The VM uses its attached service account for GCS and Secret
Manager.  No registry or application credential crosses SSH or evidence files.
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
import os
import re
import shlex
import shutil
import subprocess
import tempfile
import time
import urllib.error
import urllib.request
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


@dataclass(frozen=True)
class InstanceMetadataSnapshot:
    fingerprint: str
    items: dict[str, str]
    instance_id: str
    status: str
    last_start_timestamp: str


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


def _validate_gcp_target(project: str, zone: str, instance: str) -> None:
    if not re.fullmatch(r"[a-z][a-z0-9-]{4,28}[a-z0-9]", project):
        raise ValueError("GCP project id is invalid")
    if not re.fullmatch(r"[a-z0-9-]{3,40}", zone):
        raise ValueError("GCP zone is invalid")
    if not re.fullmatch(r"[a-z](?:[-a-z0-9]{0,61}[a-z0-9])?", instance):
        raise ValueError("GCP instance name is invalid")


def read_instance_metadata(
    *, project: str, zone: str, instance: str
) -> InstanceMetadataSnapshot:
    _validate_gcp_target(project, zone, instance)
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
            "--format=json(id,status,lastStartTimestamp,metadata)",
        ],
        timeout=120,
    )
    if result.returncode != 0:
        raise RuntimeError("cannot read live GCE instance metadata")
    try:
        payload = json.loads(result.stdout)
        metadata = payload["metadata"]
        fingerprint = metadata["fingerprint"]
        raw_items = metadata["items"]
    except (KeyError, TypeError, json.JSONDecodeError) as exc:
        raise RuntimeError("live GCE metadata response is malformed") from exc
    if not isinstance(fingerprint, str) or not re.fullmatch(
        r"[A-Za-z0-9_-]+={0,2}", fingerprint
    ):
        raise RuntimeError("live GCE metadata fingerprint is invalid")
    if not isinstance(raw_items, list):
        raise RuntimeError("live GCE metadata items are invalid")
    items: dict[str, str] = {}
    for item in raw_items:
        if not isinstance(item, dict) or set(item) != {"key", "value"}:
            raise RuntimeError("live GCE metadata item is malformed")
        key, value = item["key"], item["value"]
        if not isinstance(key, str) or not isinstance(value, str) or key in items:
            raise RuntimeError("live GCE metadata key/value is invalid or duplicated")
        items[key] = value
    if "startup-script" not in items:
        raise RuntimeError("GCE metadata must contain exactly one startup-script")
    return InstanceMetadataSnapshot(
        fingerprint=fingerprint,
        items=items,
        instance_id=str(payload.get("id", "")),
        status=str(payload.get("status", "")),
        last_start_timestamp=str(payload.get("lastStartTimestamp", "")),
    )


def render_terraform_startup_script(*, terraform_dir: Path, var_file: Path) -> str:
    resolved = terraform_dir.resolve()
    try:
        resolved.relative_to(REPO.resolve())
    except ValueError as exc:
        raise ValueError("Terraform directory must be inside this repository") from exc
    if not var_file.is_file():
        raise ValueError("GCP_TERRAFORM_VAR_FILE must be a reviewed existing file")
    result = run(
        [
            terraform_binary(),
            f"-chdir={resolved}",
            "console",
            f"-var-file={var_file.resolve()}",
        ],
        input_text="base64encode(local.startup_script)\n",
        timeout=300,
    )
    if result.returncode != 0:
        raise RuntimeError("cannot render the reviewed Terraform startup script")
    try:
        encoded = json.loads(result.stdout.strip())
        startup = base64.b64decode(encoded, validate=True).decode("utf-8")
    except (TypeError, ValueError, UnicodeError, json.JSONDecodeError) as exc:
        raise RuntimeError("Terraform startup render is malformed") from exc
    if (
        not startup.startswith("#!/usr/bin/env bash\n")
        or len(startup.encode()) > 256000
    ):
        raise RuntimeError("Terraform startup render is unsafe or too large")
    return startup


def _set_instance_metadata_cas(
    *,
    project: str,
    zone: str,
    instance: str,
    fingerprint: str,
    items: dict[str, str],
) -> None:
    _validate_gcp_target(project, zone, instance)
    token_result = run(["gcloud", "--quiet", "auth", "print-access-token"], timeout=120)
    token = token_result.stdout.strip()
    if token_result.returncode != 0 or not token or "\n" in token:
        raise RuntimeError("cannot obtain a GCP access token for metadata CAS")
    url = (
        "https://compute.googleapis.com/compute/v1/projects/"
        f"{project}/zones/{zone}/instances/{instance}/setMetadata"
    )
    body = json.dumps(
        {
            "fingerprint": fingerprint,
            "items": [
                {"key": key, "value": value} for key, value in sorted(items.items())
            ],
        },
        separators=(",", ":"),
    ).encode()
    request = urllib.request.Request(  # nosec B310
        url,
        data=body,
        method="POST",
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=120) as response:  # nosec B310
            if response.status not in {200, 201}:
                raise RuntimeError("Compute setMetadata returned an unexpected status")
    except urllib.error.HTTPError as exc:
        if exc.code in {409, 412}:
            raise RuntimeError(
                "GCE metadata fingerprint changed; CAS rejected"
            ) from exc
        raise RuntimeError(f"Compute setMetadata failed with HTTP {exc.code}") from exc


def replace_startup_metadata_cas(
    *,
    project: str,
    zone: str,
    instance: str,
    expected_old_sha256: str,
    new_startup: str,
    before: InstanceMetadataSnapshot | None = None,
) -> tuple[InstanceMetadataSnapshot, InstanceMetadataSnapshot]:
    if not SHA256_RE.fullmatch(expected_old_sha256):
        raise ValueError("expected old startup SHA-256 is invalid")
    before = before or read_instance_metadata(
        project=project, zone=zone, instance=instance
    )
    if before.status != "RUNNING" or not before.instance_id:
        raise RuntimeError("canonical GCE instance is not a running stable identity")
    old_sha256 = hashlib.sha256(before.items["startup-script"].encode()).hexdigest()
    if old_sha256 != expected_old_sha256:
        raise RuntimeError(
            f"live startup SHA-256 {old_sha256} differs from the reviewed old hash"
        )
    expected_items = dict(before.items)
    expected_items["startup-script"] = new_startup
    new_sha256 = hashlib.sha256(new_startup.encode()).hexdigest()
    if new_sha256 == old_sha256:
        return before, before
    _set_instance_metadata_cas(
        project=project,
        zone=zone,
        instance=instance,
        fingerprint=before.fingerprint,
        items=expected_items,
    )
    deadline = time.monotonic() + 120
    while True:
        after = read_instance_metadata(project=project, zone=zone, instance=instance)
        observed_sha256 = hashlib.sha256(
            after.items["startup-script"].encode()
        ).hexdigest()
        if observed_sha256 == new_sha256:
            break
        if time.monotonic() >= deadline:
            raise RuntimeError("startup metadata CAS did not become visible")
        time.sleep(2)
    if after.fingerprint == before.fingerprint:
        raise RuntimeError("startup metadata fingerprint did not advance")
    if {k: v for k, v in after.items.items() if k != "startup-script"} != {
        k: v for k, v in before.items.items() if k != "startup-script"
    }:
        raise RuntimeError("non-startup GCE metadata changed during CAS")
    if (
        after.instance_id != before.instance_id
        or after.status != "RUNNING"
        or after.last_start_timestamp != before.last_start_timestamp
    ):
        raise RuntimeError("startup metadata update restarted or replaced the instance")
    return before, after


def validate_startup_metadata(
    *, project: str, zone: str, instance: str, expected_sha256: str
) -> None:
    """Read back the effective GCE startup script and require Terraform's hash."""
    if not SHA256_RE.fullmatch(expected_sha256):
        raise ValueError("GCP_STARTUP_SCRIPT_SHA256 must be an exact sha256")
    snapshot = read_instance_metadata(project=project, zone=zone, instance=instance)
    actual = hashlib.sha256(snapshot.items["startup-script"].encode()).hexdigest()
    if actual != expected_sha256:
        raise RuntimeError(
            f"GCE startup-script sha256 {actual} differs from Terraform output"
        )


def validate_terraform_source_contract(
    *, terraform_dir: Path, var_file: Path, source_ref: str, artifact_bucket: str
) -> str:
    """Bind the reviewed, unapplied render to one exact immutable artifact."""
    startup = render_terraform_startup_script(
        terraform_dir=terraform_dir, var_file=var_file
    )
    expected_object = f"deploy-artifacts/{source_ref}/repo.tar.gz"
    expected_lines = {
        f'SOURCE_BUCKET="{artifact_bucket}"',
        f'SOURCE_OBJECT="{expected_object}"',
        f'SOURCE_SHA="{source_ref}"',
    }
    if not expected_lines.issubset(set(startup.splitlines())):
        raise RuntimeError(
            "Terraform startup render is not bound to the exact immutable artifact"
        )
    return hashlib.sha256(startup.encode()).hexdigest()


def validate_reviewed_terraform_inputs(
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
    """Require explicit release-critical inputs without blessing full-stack drift."""
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
    if not FULL_SHA_RE.fullmatch(source_ref):
        raise ValueError("reviewed Terraform source ref must be an exact SHA")
    if not re.fullmatch(r"[a-z0-9][a-z0-9._-]{1,61}[a-z0-9]", source_bucket):
        raise ValueError("reviewed Terraform source bucket is invalid")
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


def _startup_sha256(snapshot: InstanceMetadataSnapshot) -> str:
    return hashlib.sha256(snapshot.items["startup-script"].encode()).hexdigest()


def backup_startup_metadata(
    *,
    snapshot: InstanceMetadataSnapshot,
    bucket: str,
    instance: str,
    evidence_dir: Path,
) -> dict[str, str]:
    if not re.fullmatch(r"[a-z0-9][a-z0-9._-]{1,61}[a-z0-9]", bucket):
        raise ValueError("invalid GCS startup-backup bucket")
    stamp = utc_stamp()
    old_sha256 = _startup_sha256(snapshot)
    evidence_dir.mkdir(parents=True, exist_ok=True)
    local_backup = evidence_dir / "prior-startup.sh"
    local_backup.write_text(snapshot.items["startup-script"], encoding="utf-8")
    local_backup.chmod(0o600)
    uri = (
        f"gs://{bucket}/startup-metadata-backups/{instance}/" f"{stamp}-{old_sha256}.sh"
    )
    metadata = (
        f"omega-startup-sha256={old_sha256},"
        f"omega-metadata-fingerprint={snapshot.fingerprint},"
        f"omega-instance-id={snapshot.instance_id}"
    )
    upload = run(
        [
            "gcloud",
            "--quiet",
            "storage",
            "cp",
            str(local_backup),
            uri,
            "--if-generation-match=0",
            f"--custom-metadata={metadata}",
        ],
        timeout=300,
    )
    if upload.returncode != 0:
        raise RuntimeError("cannot create immutable startup metadata backup")
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
        raise RuntimeError("cannot verify immutable startup metadata backup")
    try:
        payload = json.loads(describe.stdout)
        generation = str(payload["generation"])
        size = int(payload["size"])
    except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
        raise RuntimeError("startup metadata backup response is malformed") from exc
    custom = _custom_metadata(payload)
    if (
        not generation.isdigit()
        or size != len(snapshot.items["startup-script"].encode())
        or custom.get("omega-startup-sha256") != old_sha256
        or custom.get("omega-metadata-fingerprint") != snapshot.fingerprint
        or custom.get("omega-instance-id") != snapshot.instance_id
    ):
        raise RuntimeError("startup metadata backup verification differs")
    return {
        "uri": uri,
        "generation": generation,
        "sha256": old_sha256,
        "fingerprint": snapshot.fingerprint,
        "created_at": stamp,
    }


def _write_startup_evidence(evidence_dir: Path, payload: dict[str, Any]) -> None:
    evidence_dir.mkdir(parents=True, exist_ok=True)
    (evidence_dir / "summary.json").write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )


def command_adopt_startup_metadata(args: argparse.Namespace) -> int:
    require_target(args)
    if not args.confirm and os.environ.get("CONFIRM_GCP_STARTUP_ADOPTION") != "1":
        raise SystemExit("startup metadata adoption requires explicit confirmation")
    if not SHA256_RE.fullmatch(args.expected_old_sha256):
        raise ValueError("reviewed old startup SHA-256 is invalid")
    validate_reviewed_terraform_inputs(
        terraform_dir=args.terraform_dir,
        var_file=args.terraform_var_file,
        source_ref=args.source_ref,
        source_bucket=args.artifact_bucket,
        project_id=args.project,
        project_number=args.project_number,
        billing_account_id=args.billing_account_id,
        public_console_domain=args.public_console_domain,
        public_workspace_domain=args.public_workspace_domain,
    )
    new_startup = render_terraform_startup_script(
        terraform_dir=args.terraform_dir, var_file=args.terraform_var_file
    )
    new_sha256 = hashlib.sha256(new_startup.encode()).hexdigest()
    before = read_instance_metadata(
        project=args.project, zone=args.zone, instance=args.instance
    )
    if _startup_sha256(before) != args.expected_old_sha256:
        raise RuntimeError("live startup hash differs before backup; refusing adoption")
    evidence_dir = args.evidence_dir or (
        EVIDENCE_ROOT / "startup-metadata-gcp" / utc_stamp()
    )
    backup = backup_startup_metadata(
        snapshot=before,
        bucket=args.artifact_bucket,
        instance=args.instance,
        evidence_dir=evidence_dir,
    )
    evidence: dict[str, Any] = {
        "schema_version": 1,
        "operation": "startup-metadata-adoption",
        "status": "BACKUP_VERIFIED",
        "instance_id": before.instance_id,
        "instance_status": before.status,
        "last_start_timestamp": before.last_start_timestamp,
        "before": backup,
        "after_sha256": new_sha256,
        "restore_contract": {
            "backup_uri": backup["uri"],
            "backup_generation": backup["generation"],
            "backup_sha256": backup["sha256"],
            "expected_current_sha256": new_sha256,
        },
        "secrets_included": False,
    }
    _write_startup_evidence(evidence_dir, evidence)
    before_used, after = replace_startup_metadata_cas(
        project=args.project,
        zone=args.zone,
        instance=args.instance,
        expected_old_sha256=args.expected_old_sha256,
        new_startup=new_startup,
        before=before,
    )
    if before_used.fingerprint != backup["fingerprint"]:
        raise RuntimeError("metadata CAS did not use the backed-up fingerprint")
    evidence.update(
        {
            "status": "PASS",
            "before_fingerprint": before_used.fingerprint,
            "after_fingerprint": after.fingerprint,
            "after_sha256": _startup_sha256(after),
            "instance_restarted": False,
            "instance_replaced": False,
        }
    )
    _write_startup_evidence(evidence_dir, evidence)
    print(json.dumps({"status": "PASS", "evidence_dir": str(evidence_dir), **evidence}))
    return 0


def command_restore_startup_metadata(args: argparse.Namespace) -> int:
    require_target(args)
    if not args.confirm and os.environ.get("CONFIRM_GCP_STARTUP_RESTORE") != "1":
        raise SystemExit("startup metadata restore requires explicit confirmation")
    if not SHA256_RE.fullmatch(args.backup_sha256):
        raise ValueError("startup backup SHA-256 is invalid")
    if not re.fullmatch(r"[1-9][0-9]*", args.backup_generation):
        raise ValueError("startup backup generation is invalid")
    prefix = (
        f"gs://{args.artifact_bucket}/startup-metadata-backups/" f"{args.instance}/"
    )
    if not args.backup_uri.startswith(prefix) or not args.backup_uri.endswith(".sh"):
        raise ValueError("startup backup URI is outside the canonical prefix")
    with tempfile.TemporaryDirectory(prefix="omega-startup-restore-") as temp:
        restored_path = Path(temp) / "startup.sh"
        download = run(
            [
                "gcloud",
                "--quiet",
                "storage",
                "cp",
                f"{args.backup_uri}#{args.backup_generation}",
                str(restored_path),
            ],
            timeout=300,
        )
        if download.returncode != 0 or not restored_path.is_file():
            raise RuntimeError("cannot download the exact startup backup generation")
        restored = restored_path.read_text(encoding="utf-8")
    if hashlib.sha256(restored.encode()).hexdigest() != args.backup_sha256:
        raise RuntimeError("downloaded startup backup checksum differs")
    before, after = replace_startup_metadata_cas(
        project=args.project,
        zone=args.zone,
        instance=args.instance,
        expected_old_sha256=args.expected_current_sha256,
        new_startup=restored,
    )
    evidence_dir = args.evidence_dir or (
        EVIDENCE_ROOT / "startup-metadata-restore-gcp" / utc_stamp()
    )
    evidence = {
        "schema_version": 1,
        "operation": "startup-metadata-restore",
        "status": "PASS",
        "backup_uri": args.backup_uri,
        "backup_generation": args.backup_generation,
        "backup_sha256": args.backup_sha256,
        "before_sha256": _startup_sha256(before),
        "after_sha256": _startup_sha256(after),
        "before_fingerprint": before.fingerprint,
        "after_fingerprint": after.fingerprint,
        "instance_restarted": False,
        "instance_replaced": False,
        "secrets_included": False,
    }
    _write_startup_evidence(evidence_dir, evidence)
    print(json.dumps({"status": "PASS", "evidence_dir": str(evidence_dir), **evidence}))
    return 0


GHCR_PLAN_ACTIONS = {
    "google_secret_manager_secret.ghcr_pull_credentials": ["create"],
    "google_secret_manager_secret_iam_member.app_ghcr_pull_credentials_access": [
        "create"
    ],
}


def validate_ghcr_saved_plan(
    *,
    terraform_dir: Path,
    plan_path: Path,
    project: str,
    environment: str,
) -> dict[str, Any]:
    if not re.fullmatch(r"[a-z][a-z0-9-]{4,28}[a-z0-9]", project):
        raise ValueError("GCP project id is invalid")
    if not re.fullmatch(r"[a-z][a-z0-9-]{0,30}", environment):
        raise ValueError("GCP environment is invalid")
    resolved = terraform_dir.resolve()
    try:
        resolved.relative_to(REPO.resolve())
    except ValueError as exc:
        raise ValueError("Terraform directory must be inside this repository") from exc
    result = run(
        [
            terraform_binary(),
            f"-chdir={resolved}",
            "show",
            "-json",
            str(plan_path.resolve()),
        ],
        timeout=300,
    )
    if result.returncode != 0:
        raise RuntimeError("cannot inspect saved GHCR access plan")
    try:
        payload = json.loads(result.stdout)
        changes = payload["resource_changes"]
    except (KeyError, TypeError, json.JSONDecodeError) as exc:
        raise RuntimeError("saved GHCR access plan JSON is malformed") from exc
    actual: dict[str, list[str]] = {}
    planned_after: dict[str, dict[str, Any]] = {}
    for resource in changes:
        change = resource.get("change", {})
        actions = change.get("actions")
        if actions == ["no-op"]:
            continue
        address = resource.get("address")
        if not isinstance(address, str) or not isinstance(actions, list):
            raise RuntimeError("saved GHCR access plan change is malformed")
        if address in actual:
            raise RuntimeError("saved GHCR access plan repeats a resource change")
        if change.get("replace_paths"):
            raise RuntimeError("saved GHCR access plan contains replacement paths")
        after = change.get("after")
        if change.get("before") is not None or not isinstance(after, dict):
            raise RuntimeError("saved GHCR access plan is not a pure create")
        actual[address] = actions
        planned_after[address] = after
    if actual != GHCR_PLAN_ACTIONS:
        raise RuntimeError(
            f"saved GHCR access plan actions differ: {json.dumps(actual, sort_keys=True)}"
        )
    output_changes = payload.get("output_changes") or {}
    if any(item.get("actions") != ["no-op"] for item in output_changes.values()):
        raise RuntimeError("saved GHCR access plan contains output drift")
    secret_id = f"omega-{environment}-ghcr_pull_credentials"
    member = f"serviceAccount:omega-{environment}-app@{project}.iam.gserviceaccount.com"
    exact_fields = {
        "google_secret_manager_secret.ghcr_pull_credentials": {
            "project": project,
            "secret_id": secret_id,
        },
        "google_secret_manager_secret_iam_member.app_ghcr_pull_credentials_access": {
            "project": project,
            "secret_id": secret_id,
            "role": "roles/secretmanager.secretAccessor",
            "member": member,
        },
    }
    for address, expected in exact_fields.items():
        after = planned_after[address]
        if any(after.get(key) != value for key, value in expected.items()):
            raise RuntimeError(f"saved GHCR access plan target differs for {address}")
    return payload


def _require_plan_outside_repo(plan_path: Path) -> Path:
    resolved = plan_path.resolve()
    try:
        resolved.relative_to(REPO.resolve())
    except ValueError:
        pass
    else:
        raise ValueError("saved Terraform plans must remain outside the repository")
    if not resolved.parent.is_dir():
        raise ValueError("saved Terraform plan parent directory does not exist")
    return resolved


def command_plan_ghcr_access(args: argparse.Namespace) -> int:
    require_target(args)
    validate_reviewed_terraform_inputs(
        terraform_dir=args.terraform_dir,
        var_file=args.terraform_var_file,
        source_ref=args.source_ref,
        source_bucket=args.artifact_bucket,
        project_id=args.project,
        project_number=args.project_number,
        billing_account_id=args.billing_account_id,
        public_console_domain=args.public_console_domain,
        public_workspace_domain=args.public_workspace_domain,
    )
    plan_path = _require_plan_outside_repo(args.plan)
    if plan_path.exists():
        raise ValueError("refusing to overwrite an existing saved Terraform plan")
    result = run(
        [
            terraform_binary(),
            f"-chdir={args.terraform_dir.resolve()}",
            "plan",
            "-refresh=true",
            "-lock=false",
            "-input=false",
            "-detailed-exitcode",
            f"-var-file={args.terraform_var_file.resolve()}",
            "-target=google_secret_manager_secret.ghcr_pull_credentials",
            "-target=google_secret_manager_secret_iam_member.app_ghcr_pull_credentials_access",
            f"-out={plan_path}",
        ],
        timeout=600,
    )
    if result.returncode != 2 or not plan_path.is_file():
        raise RuntimeError(
            "GHCR target plan did not produce exactly the required changes"
        )
    validate_ghcr_saved_plan(
        terraform_dir=args.terraform_dir,
        plan_path=plan_path,
        project=args.project,
        environment=args.environment,
    )
    print(
        json.dumps(
            {
                "status": "PASS",
                "plan": str(plan_path),
                "actions": GHCR_PLAN_ACTIONS,
                "delete": 0,
                "replace": 0,
            },
            sort_keys=True,
        )
    )
    return 0


def validate_live_ghcr_access(*, project: str, environment: str) -> None:
    if not re.fullmatch(r"[a-z][a-z0-9-]{0,30}", environment):
        raise ValueError("GCP environment is invalid")
    secret = f"omega-{environment}-ghcr_pull_credentials"
    describe = run(
        [
            "gcloud",
            "--quiet",
            "secrets",
            "describe",
            secret,
            f"--project={project}",
            "--format=json(name)",
        ],
        timeout=120,
    )
    policy = run(
        [
            "gcloud",
            "--quiet",
            "secrets",
            "get-iam-policy",
            secret,
            f"--project={project}",
            "--format=json(bindings)",
        ],
        timeout=120,
    )
    if describe.returncode != 0 or policy.returncode != 0:
        raise RuntimeError("GHCR secret container/IAM readback failed")
    try:
        name = json.loads(describe.stdout)["name"]
        bindings = json.loads(policy.stdout).get("bindings", [])
    except (KeyError, TypeError, json.JSONDecodeError) as exc:
        raise RuntimeError("GHCR secret container/IAM readback is malformed") from exc
    member = f"serviceAccount:omega-{environment}-app@{project}.iam.gserviceaccount.com"
    accessor = [
        binding
        for binding in bindings
        if binding.get("role") == "roles/secretmanager.secretAccessor"
    ]
    if not str(name).endswith(f"/secrets/{secret}") or len(accessor) != 1:
        raise RuntimeError("GHCR secret container or resource IAM is not exact")
    if member not in accessor[0].get("members", []):
        raise RuntimeError("GCP app service account lacks resource-scoped GHCR access")


def command_apply_ghcr_access(args: argparse.Namespace) -> int:
    require_target(args)
    if not args.confirm and os.environ.get("CONFIRM_GCP_GHCR_ACCESS_APPLY") != "1":
        raise SystemExit("GHCR access apply requires explicit confirmation")
    plan_path = _require_plan_outside_repo(args.plan)
    if not plan_path.is_file():
        raise ValueError("saved GHCR access plan does not exist")
    validate_ghcr_saved_plan(
        terraform_dir=args.terraform_dir,
        plan_path=plan_path,
        project=args.project,
        environment=args.environment,
    )
    applied = run(
        [
            terraform_binary(),
            f"-chdir={args.terraform_dir.resolve()}",
            "apply",
            "-input=false",
            str(plan_path),
        ],
        timeout=600,
    )
    if applied.returncode != 0:
        raise RuntimeError("saved GHCR access plan apply failed")
    validate_live_ghcr_access(project=args.project, environment=args.environment)
    print(
        json.dumps(
            {
                "status": "PASS",
                "actions": GHCR_PLAN_ACTIONS,
                "readback": "secret-container+resource-scoped-iam",
            },
            sort_keys=True,
        )
    )
    return 0


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
    validate_reviewed_terraform_inputs(
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
        var_file=args.terraform_var_file,
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
    validate_reviewed_terraform_inputs(
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
        var_file=args.terraform_var_file,
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

    startup = commands.add_parser(
        "adopt-startup-metadata",
        help="replace ForceNew startup metadata in place with fingerprint CAS",
    )
    common_args(startup)
    terraform_safety_args(startup)
    startup.add_argument(
        "--source-ref",
        default=os.environ.get(
            "GCP_STARTUP_SOURCE_REF", os.environ.get("GCP_CURRENT_LIVE_REF", "")
        ),
    )
    startup.add_argument(
        "--artifact-bucket", default=os.environ.get("GCP_SOURCE_BUCKET", "")
    )
    startup.add_argument(
        "--expected-old-sha256",
        default=os.environ.get("GCP_EXPECTED_OLD_STARTUP_SHA256", ""),
    )
    startup.add_argument("--confirm", action="store_true")
    startup.set_defaults(handler=command_adopt_startup_metadata)

    startup_restore = commands.add_parser(
        "restore-startup-metadata",
        help="restore one immutable startup metadata generation with CAS",
    )
    common_args(startup_restore)
    startup_restore.add_argument(
        "--artifact-bucket", default=os.environ.get("GCP_SOURCE_BUCKET", "")
    )
    startup_restore.add_argument(
        "--backup-uri", default=os.environ.get("GCP_STARTUP_BACKUP_URI", "")
    )
    startup_restore.add_argument(
        "--backup-generation",
        default=os.environ.get("GCP_STARTUP_BACKUP_GENERATION", ""),
    )
    startup_restore.add_argument(
        "--backup-sha256",
        default=os.environ.get("GCP_STARTUP_BACKUP_SHA256", ""),
    )
    startup_restore.add_argument(
        "--expected-current-sha256",
        default=os.environ.get("GCP_EXPECTED_CURRENT_STARTUP_SHA256", ""),
    )
    startup_restore.add_argument("--confirm", action="store_true")
    startup_restore.set_defaults(handler=command_restore_startup_metadata)

    ghcr_plan = commands.add_parser(
        "plan-ghcr-access",
        help="save and validate the exact two-create GHCR Secret Manager plan",
    )
    common_args(ghcr_plan)
    terraform_safety_args(ghcr_plan)
    ghcr_plan.add_argument(
        "--source-ref", default=os.environ.get("GCP_CURRENT_LIVE_REF", "")
    )
    ghcr_plan.add_argument(
        "--artifact-bucket", default=os.environ.get("GCP_SOURCE_BUCKET", "")
    )
    ghcr_plan.add_argument(
        "--environment", default=os.environ.get("OMEGA_GCP_ENVIRONMENT", "")
    )
    ghcr_plan.add_argument(
        "--plan",
        type=Path,
        default=Path(os.environ.get("GCP_GHCR_ACCESS_PLAN", "")),
    )
    ghcr_plan.set_defaults(handler=command_plan_ghcr_access)

    ghcr_apply = commands.add_parser(
        "apply-ghcr-access",
        help="apply only the prevalidated saved GHCR access plan",
    )
    common_args(ghcr_apply)
    ghcr_apply.add_argument(
        "--terraform-dir",
        type=Path,
        default=Path(
            os.environ.get("GCP_TERRAFORM_DIR", str(REPO / "infra/terraform-gcp"))
        ),
    )
    ghcr_apply.add_argument(
        "--plan",
        type=Path,
        default=Path(os.environ.get("GCP_GHCR_ACCESS_PLAN", "")),
    )
    ghcr_apply.add_argument(
        "--environment", default=os.environ.get("OMEGA_GCP_ENVIRONMENT", "")
    )
    ghcr_apply.add_argument("--confirm", action="store_true")
    ghcr_apply.set_defaults(handler=command_apply_ghcr_access)

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
