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


def validate_release_identity(tag: str, deploy_ref: str) -> str:
    """Require an annotated tag, exact commit, origin/main, and VERSION match."""
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
    metadata = (
        f"omega-artifact-sha256={artifact_sha256},omega-deploy-ref={deploy_ref}"
    )
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
    remote_command = shlex.join(["sudo", "--non-interactive", "bash", "-s", "--", *arguments])
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


def require_target(args: argparse.Namespace) -> None:
    if not args.project or not args.zone or not args.instance:
        raise SystemExit("GCP_PROJECT_ID, GCP_APP_ZONE, and GCP_APP_INSTANCE are required")


def command_backup(args: argparse.Namespace) -> int:
    require_target(args)
    if not args.confirm and os.environ.get("CONFIRM_GCP_BACKUP") != "1":
        raise SystemExit("backup requires --confirm or CONFIRM_GCP_BACKUP=1")
    backup_id = args.backup_id or f"{utc_stamp()}-predeploy"
    result = remote_script(
        project=args.project,
        zone=args.zone,
        instance=args.instance,
        script=REMOTE_ROOT / "backup.sh",
        arguments=[args.bucket, backup_id, args.compose_project],
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
    print(json.dumps({"status": status, "evidence_dir": str(evidence), **payload}, indent=2))
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
    print(json.dumps({"status": status, "evidence_dir": str(evidence), **payload}, indent=2))
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
    evidence = args.evidence_dir or EVIDENCE_ROOT / "restore-rehearsal-gcp" / utc_stamp()
    status = write_evidence(
        evidence,
        operation="Restore Rehearsal",
        result=result,
        checks=checks,
        payload=payload,
    )
    print(json.dumps({"status": status, "evidence_dir": str(evidence), **payload}, indent=2))
    return 0 if status == "PASS" else 1


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)

    backup = commands.add_parser("backup", help="create a writer-fenced GCP backup")
    common_args(backup)
    backup.add_argument("--bucket", default=os.environ.get("GCP_LAKEHOUSE_BUCKET", ""))
    backup.add_argument("--backup-id", default=os.environ.get("GCP_BACKUP_ID", ""))
    backup.add_argument("--confirm", action="store_true")
    backup.set_defaults(handler=command_backup)

    deploy = commands.add_parser("deploy", help="deploy one exact published release")
    common_args(deploy)
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
