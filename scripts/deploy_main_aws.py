#!/usr/bin/env python3
"""Deploy the exact local main artifact to AWS through SSM.

This is the official no-GitHub-Actions, no-deploy-key AWS beta deploy path.
It ships a signed-by-checksum git archive to the application bucket, verifies
it on the EC2 host, backs up any dirty host checkout, updates DEPLOY_REF and
IMAGE_TAG, then recreates application services.
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
import os
import re
import subprocess
import tempfile
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from aws_ssm import (
    DEFAULT_REGION,
    REPO,
    aws_json,
    redact,
    resolve_instance_id,
    send_ssm_script,
    utc_now,
    utc_stamp,
    write_json,
)
from release_digest_env import ManifestError, load_manifest


DEFAULT_EVIDENCE_ROOT = REPO / "docs" / "release-evidence" / "deploy-main-aws"
HEX_SHA_RE = re.compile(r"^[0-9a-f]{40}$")


@dataclass
class Check:
    name: str
    status: str
    evidence: str


def _run_git(args: list[str], *, timeout: int = 120) -> str:
    result = subprocess.run(
        ["git", *args],
        cwd=REPO,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
        timeout=timeout,
    )
    if result.returncode != 0:
        raise RuntimeError(redact(result.stderr or result.stdout))
    return result.stdout.strip()


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _require_clean_main_ref(deploy_ref: str, *, allow_non_main: bool) -> None:
    if not HEX_SHA_RE.fullmatch(deploy_ref):
        raise SystemExit("DEPLOY_REF must be a full 40-character commit SHA")
    branch = _run_git(["rev-parse", "--abbrev-ref", "HEAD"])
    origin_main = _run_git(["rev-parse", "origin/main"])
    if not allow_non_main and deploy_ref != origin_main:
        raise SystemExit(
            f"DEPLOY_REF must match origin/main ({origin_main}) unless --allow-non-main is set"
        )
    if branch != "main" and not allow_non_main:
        raise SystemExit(
            f"Current branch must be main for deploy-main-aws; got {branch}"
        )


def _remote_env_probe() -> str:
    return r"""#!/usr/bin/env bash
set -euo pipefail
set +x
REPO_DIR="${REPO_DIR:-/opt/modecissions}"
DEPLOY_DIR="${DEPLOY_DIR:-${REPO_DIR}/infra/terraform/deploy}"
env_value() {
  key="$1"
  if [ -f "${DEPLOY_DIR}/.env" ]; then
    awk -F= -v key="$key" '$1 == key {print substr($0, index($0, "=") + 1)}' "${DEPLOY_DIR}/.env" | tail -n 1 | sed "s/^[ '\"]//; s/[ '\"]$//"
  fi
}
printf 'OMEGA_REMOTE_ENV\tS3_BUCKET_NAME\t%s\n' "$(env_value S3_BUCKET_NAME)"
printf 'OMEGA_REMOTE_ENV\tAWS_REGION\t%s\n' "$(env_value AWS_REGION)"
printf 'OMEGA_REMOTE_ENV\tDEPLOY_REF\t%s\n' "$(env_value DEPLOY_REF)"
printf 'OMEGA_REMOTE_ENV\tIMAGE_TAG\t%s\n' "$(env_value IMAGE_TAG)"
printf 'OMEGA_REMOTE_ENV\tHOST_VERSION\t%s\n' "$(tr -d '\r\n' < "${REPO_DIR}/VERSION" 2>/dev/null || true)"
printf 'OMEGA_REMOTE_ENV\tHOST_DIRTY_COUNT\t%s\n' "$(git -C "${REPO_DIR}" status --short 2>/dev/null | wc -l | tr -d ' ' || true)"
printf 'OMEGA_REMOTE_ENV\tHOST_REMOTE\t%s\n' "$(git -C "${REPO_DIR}" remote get-url origin 2>/dev/null || true)"
"""


def _parse_remote_env(stdout: str) -> dict[str, str]:
    values: dict[str, str] = {}
    for line in stdout.splitlines():
        if not line.startswith("OMEGA_REMOTE_ENV\t"):
            continue
        _prefix, key, value = (line.split("\t", 2) + [""])[:3]
        values[key] = value.strip()
    return values


def _upload_artifact(
    *, region: str, bucket: str, archive: Path, deploy_ref: str
) -> str:
    key = f"deploy-artifacts/{deploy_ref}/repo.tar.gz"
    result = subprocess.run(
        [
            "aws",
            "--region",
            region,
            "s3",
            "cp",
            str(archive),
            f"s3://{bucket}/{key}",
            "--only-show-errors",
        ],
        cwd=REPO,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
        timeout=300,
    )
    if result.returncode != 0:
        raise RuntimeError(redact(result.stderr or result.stdout))
    return key


def _remote_deploy_script(
    *,
    artifact_bucket: str,
    artifact_key: str,
    artifact_sha256: str,
    deploy_ref: str,
    image_tag: str,
    version: str,
    run_migrations: bool,
    images_overlay_b64: str,
    images_overlay_sha256: str,
) -> str:
    return f"""#!/usr/bin/env bash
set -euo pipefail
set +x

REPO_DIR="${{REPO_DIR:-/opt/modecissions}}"
DEPLOY_DIR="${{DEPLOY_DIR:-${{REPO_DIR}}/infra/terraform/deploy}}"
ARTIFACT_BUCKET={json.dumps(artifact_bucket)}
ARTIFACT_KEY={json.dumps(artifact_key)}
ARTIFACT_SHA256={json.dumps(artifact_sha256)}
DEPLOY_REF_NEW={json.dumps(deploy_ref)}
IMAGE_TAG_NEW={json.dumps(image_tag)}
VERSION_NEW={json.dumps(version)}
RUN_MIGRATIONS={json.dumps("1" if run_migrations else "0")}
IMAGES_OVERLAY_B64={json.dumps(images_overlay_b64)}
IMAGES_OVERLAY_SHA256={json.dumps(images_overlay_sha256)}
STAMP="$(date -u +%Y%m%dT%H%M%SZ)"

emit() {{
  local name="$1"
  local status="$2"
  local evidence="${{3:-}}"
  evidence="${{evidence//$'\\t'/ }}"
  evidence="${{evidence//$'\\r'/ }}"
  evidence="${{evidence//$'\\n'/ }}"
  printf 'OMEGA_DEPLOY_MAIN_CHECK\\t%s\\t%s\\t%s\\n' "$name" "$status" "$evidence"
}}

env_value() {{
  local key="$1"
  if [ -f "${{DEPLOY_DIR}}/.env" ]; then
    awk -F= -v key="$key" '$1 == key {{print substr($0, index($0, "=") + 1)}}' "${{DEPLOY_DIR}}/.env" | tail -n 1 | sed "s/^[ '\\"]//; s/[ '\\"]$//"
  fi
}}

set_env_value() {{
  local key="$1"
  local value="$2"
  python3 - "$DEPLOY_DIR/.env" "$key" "$value" <<'PYENV'
from pathlib import Path
import sys

path = Path(sys.argv[1])
key = sys.argv[2]
value = sys.argv[3]
lines = path.read_text(encoding="utf-8").splitlines() if path.exists() else []
out = []
seen = False
for line in lines:
    if line.startswith(key + "="):
        out.append(f"{{key}}={{value}}")
        seen = True
    else:
        out.append(line)
if not seen:
    out.append(f"{{key}}={{value}}")
path.write_text("\\n".join(out) + "\\n", encoding="utf-8")
PYENV
}}

if [ ! -d "$REPO_DIR" ]; then
  emit "repo dir exists" "FAIL" "$REPO_DIR missing"
  exit 20
fi
if [ ! -f "$DEPLOY_DIR/.env" ]; then
  emit "deploy env exists" "FAIL" "$DEPLOY_DIR/.env missing"
  exit 21
fi

AWS_REGION_VALUE="$(env_value AWS_REGION)"
if [ -z "$AWS_REGION_VALUE" ]; then
  AWS_REGION_VALUE={json.dumps(DEFAULT_REGION)}
fi
old_ref="$(env_value DEPLOY_REF || true)"
old_tag="$(env_value IMAGE_TAG || true)"
old_version="$(tr -d '\\r\\n' < "$REPO_DIR/VERSION" 2>/dev/null || true)"
dirty_count="$(git -C "$REPO_DIR" status --short 2>/dev/null | wc -l | tr -d ' ' || true)"
remote_url="$(git -C "$REPO_DIR" remote get-url origin 2>/dev/null || true)"
emit "previous release captured" "PASS" "version=${{old_version:-<missing>}} deploy_ref=${{old_ref:-<missing>}} image_tag=${{old_tag:-<missing>}} dirty_count=${{dirty_count:-unknown}} remote=${{remote_url:-<none>}}"

workdir="$(mktemp -d /tmp/omega-deploy-main.XXXXXX)"
trap 'rm -rf "$workdir"' EXIT
artifact="$workdir/repo.tar.gz"
aws s3 cp "s3://${{ARTIFACT_BUCKET}}/${{ARTIFACT_KEY}}" "$artifact" --region "$AWS_REGION_VALUE" --only-show-errors
actual_sha="$(sha256sum "$artifact" | awk '{{print $1}}')"
if [ "$actual_sha" = "$ARTIFACT_SHA256" ]; then
  emit "artifact checksum" "PASS" "sha256=$actual_sha"
else
  emit "artifact checksum" "FAIL" "sha256=$actual_sha expected=$ARTIFACT_SHA256"
  exit 22
fi

backup_root="/opt/modecissions-deploy-backups/${{STAMP}}-${{DEPLOY_REF_NEW:0:12}}"
mkdir -p "$backup_root"
tar \
  --exclude='modecissions/.git' \
  --exclude='modecissions/infra/terraform/deploy/.env' \
  -czf "$backup_root/worktree.tar.gz" \
  -C "$(dirname "$REPO_DIR")" "$(basename "$REPO_DIR")"
if [ -f "$DEPLOY_DIR/.env" ]; then
  cp "$DEPLOY_DIR/.env" "$backup_root/deploy.env"
  chmod 600 "$backup_root/deploy.env"
fi
emit "host worktree preserved" "PASS" "backup_root=$backup_root dirty_count=${{dirty_count:-unknown}}"

release_tmp="/opt/modecissions-releases/${{DEPLOY_REF_NEW}}.tmp"
release_dir="/opt/modecissions-releases/${{DEPLOY_REF_NEW}}"
rm -rf "$release_tmp"
mkdir -p "$release_tmp"
tar -xzf "$artifact" -C "$release_tmp"
rm -rf "$release_dir"
mv "$release_tmp" "$release_dir"
emit "artifact extracted" "PASS" "release_dir=$release_dir"

python3 - "$release_dir" "$REPO_DIR" <<'PYSYNC'
from pathlib import Path
import os
import shutil
import sys

src = Path(sys.argv[1])
dst = Path(sys.argv[2])
preserve = {{
    Path(".git"),
    Path("infra/terraform/deploy/.env"),
}}

def should_preserve(rel: Path) -> bool:
    return rel in preserve or any(parent in preserve for parent in rel.parents)

for root, dirs, files in os.walk(dst, topdown=False):
    root_path = Path(root)
    for name in files:
        path = root_path / name
        rel = path.relative_to(dst)
        if should_preserve(rel):
            continue
        src_path = src / rel
        if not src_path.exists():
            path.unlink()
    for name in dirs:
        path = root_path / name
        rel = path.relative_to(dst)
        if should_preserve(rel):
            continue
        src_path = src / rel
        if not src_path.exists():
            try:
                path.rmdir()
            except OSError:
                pass

for root, dirs, files in os.walk(src):
    root_path = Path(root)
    rel_root = root_path.relative_to(src)
    if should_preserve(rel_root):
        dirs[:] = []
        continue
    target_root = dst / rel_root
    target_root.mkdir(parents=True, exist_ok=True)
    for name in files:
        rel = rel_root / name
        if should_preserve(rel):
            continue
        shutil.copy2(src / rel, dst / rel)
PYSYNC
emit "host worktree updated from artifact" "PASS" "deploy_ref=$DEPLOY_REF_NEW"

printf '%s\\n' "$VERSION_NEW" > "$REPO_DIR/VERSION"
set_env_value DEPLOY_REF "$DEPLOY_REF_NEW"
set_env_value IMAGE_TAG "$IMAGE_TAG_NEW"
cd "$DEPLOY_DIR"
COMPOSE_FILES="-f docker-compose.aws.yml"
if [ "$(env_value DEPLOY_CARTRIDGES_SAME_HOST || true)" = "true" ] && [ -f docker-compose.cartridges.yml ]; then
  COMPOSE_FILES="$COMPOSE_FILES -f docker-compose.cartridges.yml"
fi

# Pin every OMEGA image to the digest the signed release manifest attests to.
# The release publishes by digest only, so the vX.Y.Z tag the compose files ask
# for does not exist in GHCR; without this overlay the pull below resolves
# nothing and the deploy dies before it can touch anything (2026-09-23).
OVERLAY_FILE="$DEPLOY_DIR/omega-release-images.yml"
printf '%s' "$IMAGES_OVERLAY_B64" | base64 -d > "$OVERLAY_FILE" || {{ emit "image digest overlay" "FAIL" "could not decode overlay"; exit 22; }}
actual_overlay_sha="$(sha256sum "$OVERLAY_FILE" | cut -d" " -f1)"
if [ "$actual_overlay_sha" != "$IMAGES_OVERLAY_SHA256" ]; then
  emit "image digest overlay" "FAIL" "sha256=$actual_overlay_sha expected=$IMAGES_OVERLAY_SHA256"
  exit 22
fi
pinned="$(grep -c '@sha256:' "$OVERLAY_FILE" || true)"
if [ "$pinned" -lt 16 ]; then
  emit "image digest overlay" "FAIL" "only $pinned digest pins in the overlay"
  exit 22
fi
COMPOSE_FILES="$COMPOSE_FILES -f omega-release-images.yml"
emit "image digest overlay" "PASS" "sha256=$IMAGES_OVERLAY_SHA256 pins=$pinned"

if docker compose $COMPOSE_FILES config --quiet >/dev/null 2>&1; then
  emit "compose config" "PASS" "ok"
else
  emit "compose config" "FAIL" "docker compose config failed"
  exit 23
fi

available_services="$(docker compose $COMPOSE_FILES config --services)"
services=""
for service in console workspace refinement vault mcp-infra airflow sap-successfactors replicon hubspot salesforce banxico inegi sec-edgar sap-hcm sap-s4hana sap-b1; do
  if printf '%s\\n' "$available_services" | grep -qx "$service"; then
    services="$services $service"
  fi
done
emit "runtime services selected" "PASS" "services=$(printf '%s' "$services" | xargs)"
AUTH_RUNNER="$DEPLOY_DIR/ghcr-auth-run.sh"
if [ ! -f "$AUTH_RUNNER" ]; then
  emit "server-owned GHCR auth" "FAIL" "$AUTH_RUNNER missing"
  exit 25
fi
OMEGA_GHCR_AUTH_ACTIVE=1 DOCKER_CONFIG=/root/.docker bash "$AUTH_RUNNER" docker compose $COMPOSE_FILES pull $services >/tmp/omega-deploy-pull.out 2>/tmp/omega-deploy-pull.err || {{ emit "pull app images" "FAIL" "$(tail -c 400 /tmp/omega-deploy-pull.err || true)"; exit 25; }}
emit "pull app images" "PASS" "image_tag=$IMAGE_TAG_NEW overlay=$IMAGES_OVERLAY_SHA256"
unpinned="$(docker compose $COMPOSE_FILES config --format json 2>/dev/null | grep -oE '"image": *"ghcr[.]io/[^"]*"' | grep -v '@sha256:' | wc -l | tr -d ' ')"
if [ "${{unpinned:-1}}" != "0" ]; then
  emit "every OMEGA image is digest-pinned" "FAIL" "$unpinned image(s) still resolve by tag"
  exit 22
fi
emit "every OMEGA image is digest-pinned" "PASS" "0 tag references remain"

# Authentication and every immutable image are proven before any schema
# mutation, so expired/missing private-package credentials fail closed.
docker compose $COMPOSE_FILES stop $services >/tmp/omega-deploy-stop.out 2>&1 || true
emit "stop app before migrations" "PASS" "services stopped"

if [ "$RUN_MIGRATIONS" = "1" ] && [ -x "$DEPLOY_DIR/apply_db_migrations.sh" ]; then
  if LC_ALL=C LANG=C bash "$DEPLOY_DIR/apply_db_migrations.sh" >/tmp/omega-deploy-migrations.out 2>/tmp/omega-deploy-migrations.err; then
    emit "db migrations" "PASS" "apply_db_migrations.sh completed"
  else
    emit "db migrations" "FAIL" "$(tail -c 400 /tmp/omega-deploy-migrations.err || true)"
    exit 24
  fi
else
  emit "db migrations" "PASS" "skipped"
fi

docker compose $COMPOSE_FILES up -d --pull never --force-recreate $services >/tmp/omega-deploy-up.out 2>/tmp/omega-deploy-up.err || {{ emit "recreate app services" "FAIL" "$(tail -c 400 /tmp/omega-deploy-up.err || true)"; exit 26; }}
emit "recreate app services" "PASS" "services=$(printf '%s' "$services" | xargs)"

health_ok=0
health_body=""
for _ in $(seq 1 90); do
  if health_body="$(curl -fsS --max-time 5 http://127.0.0.1:8000/healthz 2>/tmp/omega-health.err)"; then
    version_seen="$(printf '%s' "$health_body" | sed -n 's/.*"version":"\\([^"]*\\)".*/\\1/p' | head -n 1)"
    if [ "$version_seen" = "$VERSION_NEW" ]; then
      health_ok=1
      break
    fi
  fi
  sleep 2
done
if [ "$health_ok" = "1" ]; then
  emit "internal healthz" "PASS" "version=$VERSION_NEW"
else
  emit "internal healthz" "FAIL" "body=$(printf '%s' "$health_body" | head -c 220)"
  exit 27
fi
curl -fsS --max-time 10 http://127.0.0.1:8000/readyz >/tmp/omega-ready.out 2>/tmp/omega-ready.err && emit "internal readyz" "PASS" "status=200" || {{ emit "internal readyz" "FAIL" "$(cat /tmp/omega-ready.err /tmp/omega-ready.out 2>/dev/null | head -c 220)"; exit 28; }}
curl -fsS --max-time 10 'http://127.0.0.1:8000/readyz?require_data=1' >/tmp/omega-ready-data.out 2>/tmp/omega-ready-data.err && emit "internal readyz require_data" "PASS" "status=200" || {{ emit "internal readyz require_data" "FAIL" "$(cat /tmp/omega-ready-data.err /tmp/omega-ready-data.out 2>/dev/null | head -c 220)"; exit 29; }}

printf 'OMEGA_DEPLOY_MAIN_JSON=%s\\n' "$(python3 - <<PYJSON
import json
print(json.dumps({{
  "status": "ok",
  "deploy_ref": "$DEPLOY_REF_NEW",
  "image_tag": "$IMAGE_TAG_NEW",
  "version": "$VERSION_NEW",
  "backup_root": "$backup_root",
  "artifact_sha256": "$ARTIFACT_SHA256",
  "artifact_bucket": "$ARTIFACT_BUCKET",
  "artifact_key": "$ARTIFACT_KEY",
}}, sort_keys=True))
PYJSON
)"
"""


def _parse_checks(stdout: str) -> tuple[list[Check], dict[str, Any] | None]:
    checks: list[Check] = []
    payload = None
    for line in stdout.splitlines():
        if line.startswith("OMEGA_DEPLOY_MAIN_CHECK\t"):
            _prefix, name, status, evidence = (line.split("\t", 3) + [""])[:4]
            checks.append(Check(name=name, status=status, evidence=redact(evidence)))
        elif line.startswith("OMEGA_DEPLOY_MAIN_JSON="):
            try:
                payload = json.loads(line.split("=", 1)[1])
            except json.JSONDecodeError:
                payload = None
    return checks, payload


def _write_report(path: Path, summary: dict[str, Any]) -> None:
    lines = [
        "# AWS Main Artifact Deploy Evidence",
        "",
        f"- status: `{summary['status']}`",
        f"- generated_at_utc: `{summary['generated_at_utc']}`",
        f"- deploy_ref: `{summary['deploy_ref']}`",
        f"- image_tag: `{summary['image_tag']}`",
        f"- version: `{summary['version']}`",
        f"- ssm_command_id: `{summary.get('ssm_command_id') or '<not-run>'}`",
        f"- instance_id: `{summary['instance_id']}`",
        f"- region: `{summary['region']}`",
        f"- artifact_sha256: `{summary['artifact_sha256']}`",
        "",
        "| Check | Status | Evidence |",
        "|---|---|---|",
    ]
    for check in summary["checks"]:
        evidence = str(check["evidence"]).replace("|", "\\|")
        lines.append(f"| {check['name']} | {check['status']} | {evidence} |")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")



def release_manifest_overlay(manifest: dict) -> str:
    """A compose overlay pinning every OMEGA service to its manifest digest.

    The release builds with push-by-digest=true (.github/workflows/release.yml),
    so GHCR never receives the vX.Y.Z tag the compose files ask for, and
    `docker compose pull` resolves nothing. GCP hit this first and was fixed in
    #635 by pinning digests from the signed manifest; AWS was left on the tag
    and failed the same way on 2026-09-23, in production.

    `pull_policy` is deliberately not set. The pull that follows this overlay is
    what proves every image exists before any schema is touched, and pinning the
    digest keeps that proof while making it exact rather than name-based.
    """
    lines = [
        "# Generated by deploy_main_aws.py -- immutable release digests.",
        "# Regenerated from the signed release manifest on every deploy.",
        "services:",
    ]
    for service, reference in manifest["by_service"].items():
        compose_service = service.replace("_", "-")
        lines.append(f"  {compose_service}:")
        lines.append(f"    image: {reference}")
        if service == "airflow":
            # one image backs three compose services
            for extra in ("airflow-init", "airflow-scheduler"):
                lines.append(f"  {extra}:")
                lines.append(f"    image: {reference}")
    return "\n".join(lines) + "\n"


def resolve_release_manifest(
    manifest_path: Path | None,
    checksum_path: Path | None,
    *,
    deploy_ref: str,
    image_tag: str,
) -> dict:
    """Validate the signed manifest and bind it to the ref we are deploying.

    load_manifest is the sealed canonical validator the GCP path already uses:
    canonical bytes, exact filename, exact sibling checksum, owner-scoped image
    names, 16/16 inventory, and source_sha equality. Passing deploy_ref here is
    what makes it impossible to deploy images built from another commit.
    """
    if manifest_path is None:
        raise SystemExit(
            "OMEGA_RELEASE_MANIFEST is required: download "
            f"omega-release-manifest-{image_tag}.json and its .sha256 from the "
            "GitHub release. Images are published by digest only, so the deploy "
            "cannot resolve them from the tag alone."
        )
    checksum = checksum_path or manifest_path.with_suffix(manifest_path.suffix + ".sha256")
    try:
        build_run_id = json.loads(manifest_path.read_bytes()).get("build_run_id")
    except (OSError, ValueError) as exc:
        raise SystemExit(f"release manifest cannot be read: {exc}") from exc
    if not isinstance(build_run_id, int) or isinstance(build_run_id, bool) or build_run_id <= 0:
        raise SystemExit("release manifest build identity is invalid")
    owner = os.environ.get("GHCR_OWNER", "emmanuelnavaromero02-commits")
    try:
        return load_manifest(
            manifest_path,
            checksum_path=checksum,
            repository=f"{owner}/CONSOLA-V1",
            release_tag=image_tag,
            source_sha=deploy_ref,
            build_run_id=build_run_id,
        )
    except ManifestError as exc:
        raise SystemExit(f"release manifest validation failed: {exc}") from exc


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Deploy exact origin/main artifact to AWS via SSM."
    )
    parser.add_argument("--region", default=DEFAULT_REGION)
    parser.add_argument(
        "--instance-id", default=os.environ.get("AWS_APP_INSTANCE_ID") or ""
    )
    parser.add_argument("--deploy-ref", default=os.environ.get("DEPLOY_REF") or "")
    parser.add_argument("--image-tag", default=os.environ.get("IMAGE_TAG") or "")
    parser.add_argument(
        "--public-url",
        default=os.environ.get("PUBLIC_CONSOLE_URL")
        or os.environ.get("CONSOLE_URL")
        or "",
    )
    parser.add_argument(
        "--release-manifest",
        type=Path,
        default=(Path(os.environ["OMEGA_RELEASE_MANIFEST"]) if os.environ.get("OMEGA_RELEASE_MANIFEST") else None),
        help="signed omega-release-manifest-<tag>.json from the GitHub release",
    )
    parser.add_argument(
        "--release-manifest-checksum",
        type=Path,
        default=(Path(os.environ["OMEGA_RELEASE_MANIFEST_CHECKSUM"]) if os.environ.get("OMEGA_RELEASE_MANIFEST_CHECKSUM") else None),
        help="its sibling .sha256 asset (defaults to <manifest>.sha256)",
    )
    parser.add_argument("--allow-non-main", action="store_true")
    parser.add_argument("--no-fetch", action="store_true")
    parser.add_argument("--skip-migrations", action="store_true")
    parser.add_argument("--evidence-dir", type=Path, default=None)
    parser.add_argument(
        "--timeout-seconds",
        type=int,
        default=int(os.environ.get("OMEGA_AWS_DEPLOY_TIMEOUT_SECONDS", "2400")),
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if not args.no_fetch:
        _run_git(["fetch", "origin", "main", "--tags", "--prune"], timeout=300)
    deploy_ref = args.deploy_ref or _run_git(["rev-parse", "origin/main"])
    _require_clean_main_ref(deploy_ref, allow_non_main=args.allow_non_main)
    version = _run_git(["show", f"{deploy_ref}:VERSION"]).strip()
    image_tag = args.image_tag or f"v{version}"
    if image_tag in {"", "latest"}:
        raise SystemExit("IMAGE_TAG must be an immutable release tag")

    manifest = resolve_release_manifest(
        args.release_manifest,
        args.release_manifest_checksum,
        deploy_ref=deploy_ref,
        image_tag=image_tag,
    )
    overlay_text = release_manifest_overlay(manifest)
    overlay_sha256 = hashlib.sha256(overlay_text.encode("utf-8")).hexdigest()
    overlay_b64 = base64.b64encode(overlay_text.encode("utf-8")).decode("ascii")

    evidence_dir = args.evidence_dir or DEFAULT_EVIDENCE_ROOT / utc_stamp()
    evidence_dir.mkdir(parents=True, exist_ok=True)
    manifest_provenance = {
        "release_tag": manifest.get("release_tag"),
        "source_sha": manifest.get("source_sha"),
        "build_run_id": manifest.get("build_run_id"),
        "images_overlay_sha256": overlay_sha256,
        "digests": manifest["by_service"],
    }
    instance_id = resolve_instance_id(args.region, args.instance_id or None)

    env_probe = send_ssm_script(
        region=args.region,
        instance_id=instance_id,
        script=_remote_env_probe(),
        comment="omega-deploy-main-env-probe",
        timeout_seconds=120,
    )
    remote_env = _parse_remote_env(env_probe.stdout)
    artifact_bucket = (
        remote_env.get("S3_BUCKET_NAME")
        or os.environ.get("OMEGA_DEPLOY_ARTIFACT_BUCKET")
        or ""
    )
    artifact_region = remote_env.get("AWS_REGION") or args.region
    if not artifact_bucket:
        raise SystemExit(
            "Could not resolve S3_BUCKET_NAME from AWS host; set OMEGA_DEPLOY_ARTIFACT_BUCKET"
        )

    with tempfile.TemporaryDirectory(prefix="omega-deploy-main-") as tmp:
        archive = Path(tmp) / "repo.tar.gz"
        subprocess.run(
            ["git", "archive", "--format=tar.gz", "-o", str(archive), deploy_ref],
            cwd=REPO,
            check=True,
            timeout=300,
        )
        artifact_sha = _sha256(archive)
        artifact_key = _upload_artifact(
            region=artifact_region,
            bucket=artifact_bucket,
            archive=archive,
            deploy_ref=deploy_ref,
        )
        artifact_size = archive.stat().st_size

    remote = send_ssm_script(
        region=args.region,
        instance_id=instance_id,
        script=_remote_deploy_script(
            artifact_bucket=artifact_bucket,
            artifact_key=artifact_key,
            artifact_sha256=artifact_sha,
            deploy_ref=deploy_ref,
            image_tag=image_tag,
            version=version,
            run_migrations=not args.skip_migrations,
            images_overlay_b64=overlay_b64,
            images_overlay_sha256=overlay_sha256,
        ),
        comment="omega-deploy-main-aws-artifact",
        timeout_seconds=args.timeout_seconds,
        poll_seconds=3.0,
    )
    checks, payload = _parse_checks(remote.stdout)
    checks.append(
        Check(
            name="SSM command completed",
            status="PASS"
            if remote.status == "Success" and remote.response_code == 0
            else "FAIL",
            evidence=f"command_id={remote.command_id} status={remote.status} response_code={remote.response_code}",
        )
    )
    status = "PASS" if all(check.status == "PASS" for check in checks) else "FAIL"
    summary = {
        "status": status,
        "generated_at_utc": utc_now().isoformat(),
        "deploy_method": "artifact_s3_ssm",
        "deploy_ref": deploy_ref,
        "image_tag": image_tag,
        "version": version,
        "artifact_bucket": artifact_bucket,
        "artifact_key": artifact_key,
        "artifact_sha256": artifact_sha,
        "artifact_size_bytes": artifact_size,
        "public_url": args.public_url,
        "ssm_command_id": remote.command_id,
        "instance_id": instance_id,
        "region": args.region,
        "remote_env": {
            "DEPLOY_REF_OLD": remote_env.get("DEPLOY_REF", ""),
            "IMAGE_TAG_OLD": remote_env.get("IMAGE_TAG", ""),
            "HOST_VERSION_OLD": remote_env.get("HOST_VERSION", ""),
            "HOST_DIRTY_COUNT": remote_env.get("HOST_DIRTY_COUNT", ""),
            "HOST_REMOTE": remote_env.get("HOST_REMOTE", ""),
        },
        "remote_payload": payload or {},
        "checks": [asdict(check) for check in checks],
    }
    write_json(evidence_dir / "summary.json", summary)
    (evidence_dir / "remote_stdout_redacted.txt").write_text(
        redact(remote.stdout), encoding="utf-8"
    )
    (evidence_dir / "remote_stderr_redacted.txt").write_text(
        redact(remote.stderr), encoding="utf-8"
    )
    _write_report(evidence_dir / "REPORT.md", summary)
    print(
        json.dumps(
            {
                "status": status,
                "evidence_dir": str(evidence_dir),
                "ssm_command_id": remote.command_id,
            },
            indent=2,
        )
    )
    return 0 if status == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
