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
import http.client
import json
import os
import re
import shlex
import shutil
import socket
import ssl
import stat
import subprocess
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

REPO = Path(__file__).resolve().parents[1]
REMOTE_ROOT = REPO / "scripts" / "gcp"
SAFE_IO = REMOTE_ROOT / "safe_io.py"
EVIDENCE_ROOT = Path(
    os.environ.get("OMEGA_GCP_EVIDENCE_ROOT", "/var/tmp/omega-gcp-release-evidence")
).resolve()
FULL_SHA_RE = re.compile(r"^[0-9a-f]{40}$")
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
OCI_DIGEST_RE = re.compile(r"^sha256:[0-9a-f]{64}$")
TAG_RE = re.compile(r"^v[0-9][0-9A-Za-z._-]*$")
GCS_BUCKET_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{1,61}[a-z0-9]$")
RECONCILIATION_SCHEMA = "omega.pipeline-run-reconciliation/v1"
ROUTING_SCHEDULER_ATTESTATION_SCHEMA = (
    "omega.gcp-canonical-routing-scheduler-attestation/v1"
)
RECONCILIATION_RUN_KEYS = frozenset(
    {
        "run_id",
        "tenant_id",
        "workspace_id",
        "expected_status",
        "expected_started_at",
        "expected_fencing_token",
        "target_status",
        "reason",
        "evidence",
    }
)
MAX_RECONCILIATION_MANIFEST_BYTES = 1024 * 1024
MAX_ROUTING_SCHEDULER_ATTESTATION_BYTES = 64 * 1024
CANONICAL_GITHUB_REPOSITORY = "emmanuelnavaromero02-commits/CONSOLA-V1"
CANONICAL_GHCR_OWNER = "emmanuelnavaromero02-commits"
CANONICAL_TRANSFER_JOB = "transferJobs/10381442634122910808"
CANONICAL_GCP_CERTIFICATE_MAP = "sevenbs-production-map"
CANONICAL_PUBLIC_TLS_NAME = "console.7businesssolutions.com"
CANONICAL_AWS_ORIGIN_ALB = "modecissions-public-255609366.us-east-1.elb.amazonaws.com"
CANONICAL_AWS_DESTINATION_ALB = (
    "modecissions-public-1973504078.us-east-1.elb.amazonaws.com"
)
CANONICAL_AWS_ORIGIN_FROZEN_SHA = "ee35b035044cffea7270160d829cb17505c4d16a"
CANONICAL_AWS_DESTINATION_SCHEDULER_HEARTBEAT = "2026-08-07T01:26:04.241563+00:00"
CANONICAL_AWS_FORENSIC_OBSERVED_AT = "2026-08-11T10:44:00Z"
CANONICAL_AWS_FORENSIC_EVENT_AT = "2026-08-11T10:47:44.485Z"
CANONICAL_AWS_FORENSIC_REFERENCE = (
    "codex-session-event:019ff064-b6ab-7ac1-aa60-078ec27f2c29"
    "@2026-08-11T10:47:44.485Z#payload.message+LF"
)
CANONICAL_AWS_FORENSIC_SHA256 = (
    "adf099625e43bbe93ea66151f132a5baa9e6d5617e0b037e5005b604429238a2"
)
CANONICAL_ORIGIN_URLS = frozenset(
    {
        f"https://github.com/{CANONICAL_GITHUB_REPOSITORY}",
        f"https://github.com/{CANONICAL_GITHUB_REPOSITORY}.git",
        f"git@github.com:{CANONICAL_GITHUB_REPOSITORY}.git",
        f"ssh://git@github.com/{CANONICAL_GITHUB_REPOSITORY}.git",
    }
)
SECRET_RE = re.compile(
    r"(?is)(ghp_[A-Za-z0-9_]+|github_pat_[A-Za-z0-9_]+|"
    r"(?:\"?(?:access_?token|token|password|secret|credential)\"?\s*[:=]\s*)"
    r"(?:\"(?:\\.|[^\"])*\"|[^\s,}\]]+))"
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


@dataclass(frozen=True)
class ArtifactRef:
    uri: str
    generation: str
    size_bytes: int
    sha256: str

    def as_dict(self) -> dict[str, str | int]:
        return {
            "uri": self.uri,
            "generation": self.generation,
            "size_bytes": self.size_bytes,
            "sha256": self.sha256,
        }


@dataclass(frozen=True)
class BoundReleaseIdentity:
    version: str
    manifest_digest: str
    tag_object_sha: str


@dataclass(frozen=True)
class CandidateWorkflowAuthority:
    run_id: str
    run_attempt: str
    manifest_digest: str
    payload_sha256: str
    artifact_id: str


@dataclass(frozen=True)
class LegacyRollbackIdentity:
    version: str
    tag_object_sha: str
    tag_commit: str
    runtime_source_ref: str


def _strict_json_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"JSON object repeats key: {key}")
        result[key] = value
    return result


def _parse_utc_timestamp(value: object, *, field: str) -> datetime:
    if (
        not isinstance(value, str)
        or re.fullmatch(
            r"[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}"
            r"(?:\.[0-9]{1,6})?\+00:00",
            value,
        )
        is None
    ):
        raise ValueError(f"{field} must be an explicit UTC ISO-8601 timestamp")
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError as exc:
        raise ValueError(f"{field} is not a valid timestamp") from exc
    if parsed.utcoffset() is None or parsed.utcoffset().total_seconds() != 0:
        raise ValueError(f"{field} must use UTC")
    return parsed


def _validate_reconciliation_evidence(value: object) -> dict[str, Any]:
    if not isinstance(value, dict) or not value:
        raise ValueError("run evidence must be a non-empty JSON object")
    nodes = 0

    def visit(item: object, *, depth: int) -> None:
        nonlocal nodes
        nodes += 1
        if nodes > 1_000 or depth > 6:
            raise ValueError("run evidence exceeds its bounded JSON shape")
        if item is None or isinstance(item, bool):
            return
        if isinstance(item, int):
            if abs(item) > 2**63 - 1:
                raise ValueError("run evidence integer is out of range")
            return
        if isinstance(item, float):
            if item != item or item in {float("inf"), float("-inf")}:
                raise ValueError("run evidence number must be finite")
            return
        if isinstance(item, str):
            if len(item.encode("utf-8")) > 4_096 or any(
                ord(char) < 0x20 and char not in "\t\n\r" for char in item
            ):
                raise ValueError("run evidence string is invalid or too large")
            if re.search(r"(?i)(?:ghp_|github_pat_|bearer\s+[a-z0-9._-]{8,})", item):
                raise ValueError("run evidence contains credential-shaped data")
            return
        if isinstance(item, list):
            if len(item) > 200:
                raise ValueError("run evidence list is too large")
            for nested in item:
                visit(nested, depth=depth + 1)
            return
        if isinstance(item, dict):
            if len(item) > 100:
                raise ValueError("run evidence object is too large")
            for key, nested in item.items():
                if (
                    not isinstance(key, str)
                    or re.fullmatch(r"[a-z][a-z0-9_]{0,63}", key) is None
                ):
                    raise ValueError("run evidence key is invalid")
                if re.search(
                    r"(?i)(?:password|secret|credential|authorization|access_token|token)",
                    key,
                ):
                    raise ValueError("run evidence contains a credential-bearing key")
                visit(nested, depth=depth + 1)
            return
        raise ValueError("run evidence contains an unsupported JSON value")

    visit(value, depth=0)
    return value


def validate_pipeline_run_reconciliation_manifest(
    raw: bytes, *, expected_count: int
) -> dict[str, Any]:
    """Strictly validate one external, CAS-bound pipeline-run transition set."""
    if not isinstance(expected_count, int) or isinstance(expected_count, bool):
        raise ValueError("expected reconciliation count must be an integer")
    if not 1 <= expected_count <= 1_000:
        raise ValueError("expected reconciliation count is out of range")
    if not 1 <= len(raw) <= MAX_RECONCILIATION_MANIFEST_BYTES:
        raise ValueError("pipeline-run reconciliation manifest size is invalid")
    try:
        text_value = raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ValueError("pipeline-run reconciliation manifest is not UTF-8") from exc
    if text_value.startswith("\ufeff"):
        raise ValueError("pipeline-run reconciliation manifest must not use a BOM")
    try:
        payload = json.loads(
            text_value,
            object_pairs_hook=_strict_json_object,
            parse_constant=lambda value: (_ for _ in ()).throw(
                ValueError(f"non-finite JSON constant: {value}")
            ),
        )
    except (json.JSONDecodeError, ValueError) as exc:
        raise ValueError("pipeline-run reconciliation manifest is malformed") from exc
    if not isinstance(payload, dict) or set(payload) != {"schema", "change_id", "runs"}:
        raise ValueError("pipeline-run reconciliation manifest shape is invalid")
    if payload["schema"] != RECONCILIATION_SCHEMA:
        raise ValueError("pipeline-run reconciliation schema is unsupported")
    change_id = payload["change_id"]
    if (
        not isinstance(change_id, str)
        or re.fullmatch(r"[a-z0-9][a-z0-9._-]{2,127}", change_id) is None
    ):
        raise ValueError("pipeline-run reconciliation change_id is invalid")
    runs = payload["runs"]
    if not isinstance(runs, list) or len(runs) != expected_count:
        raise ValueError("pipeline-run reconciliation count differs from the gate")

    now = datetime.now(timezone.utc)
    identities: set[tuple[str, str, str]] = set()
    run_ids: set[str] = set()
    for row in runs:
        if not isinstance(row, dict) or set(row) != RECONCILIATION_RUN_KEYS:
            raise ValueError("pipeline-run reconciliation row shape is invalid")
        run_id = row["run_id"]
        if (
            not isinstance(run_id, str)
            or not 1 <= len(run_id.encode("utf-8")) <= 512
            or any(ord(char) < 0x20 or ord(char) == 0x7F for char in run_id)
        ):
            raise ValueError("pipeline-run reconciliation run_id is invalid")
        canonical_scope: list[str] = []
        for field in ("tenant_id", "workspace_id"):
            value = row[field]
            if not isinstance(value, str):
                raise ValueError(f"pipeline-run reconciliation {field} is invalid")
            try:
                parsed_uuid = uuid.UUID(value)
            except ValueError as exc:
                raise ValueError(
                    f"pipeline-run reconciliation {field} is invalid"
                ) from exc
            if str(parsed_uuid) != value:
                raise ValueError(
                    f"pipeline-run reconciliation {field} must be canonical"
                )
            canonical_scope.append(value)
        identity = (run_id, canonical_scope[0], canonical_scope[1])
        if identity in identities or run_id in run_ids:
            raise ValueError("pipeline-run reconciliation repeats a run identity")
        identities.add(identity)
        run_ids.add(run_id)
        if row["expected_status"] != "running":
            raise ValueError("pipeline-run expected status must be running")
        started_at = _parse_utc_timestamp(
            row["expected_started_at"], field="expected_started_at"
        )
        fencing_token = row["expected_fencing_token"]
        if (
            not isinstance(fencing_token, int)
            or isinstance(fencing_token, bool)
            or not 0 <= fencing_token < 2**63 - 1
        ):
            raise ValueError("pipeline-run expected fencing token is invalid")
        if row["target_status"] not in {"failed", "blocked"}:
            raise ValueError("pipeline-run target status is not terminal and approved")
        reason = row["reason"]
        if (
            not isinstance(reason, str)
            or re.fullmatch(r"[a-z][a-z0-9_]{2,63}", reason) is None
        ):
            raise ValueError("pipeline-run reconciliation reason is invalid")
        evidence = _validate_reconciliation_evidence(row["evidence"])
        observed_at = _parse_utc_timestamp(
            evidence.get("observed_at"), field="evidence.observed_at"
        )
        if (
            observed_at < started_at
            or observed_at > now + timedelta(minutes=5)
            or now - observed_at > timedelta(hours=24)
        ):
            raise ValueError("pipeline-run evidence observation time is invalid")
        if started_at > now + timedelta(minutes=5):
            raise ValueError("pipeline-run expected start time is in the future")
    return payload


def read_private_reconciliation_manifest(
    path: Path, *, expected_count: int
) -> tuple[bytes, dict[str, Any]]:
    """Read an operator-owned external manifest once through a no-follow FD."""
    resolved = path.resolve()
    try:
        resolved.relative_to(REPO.resolve())
    except ValueError:
        pass
    else:
        raise ValueError(
            "pipeline-run reconciliation manifest must remain outside the repo"
        )
    flags = os.O_RDONLY
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    try:
        descriptor = os.open(path, flags)
    except OSError as exc:
        raise ValueError("pipeline-run reconciliation manifest is unavailable") from exc
    try:
        info = os.fstat(descriptor)
        if (
            not stat.S_ISREG(info.st_mode)
            or info.st_uid != os.geteuid()
            or info.st_mode & 0o777 != 0o600
            or info.st_nlink != 1
            or not 1 <= info.st_size <= MAX_RECONCILIATION_MANIFEST_BYTES
        ):
            raise ValueError(
                "pipeline-run reconciliation manifest must be an operator-owned "
                "mode-0600 single-link regular file"
            )
        with os.fdopen(descriptor, "rb", closefd=False) as stream:
            raw = stream.read(MAX_RECONCILIATION_MANIFEST_BYTES + 1)
        if len(raw) != info.st_size:
            raise ValueError("pipeline-run reconciliation manifest changed while read")
    finally:
        os.close(descriptor)
    return raw, validate_pipeline_run_reconciliation_manifest(
        raw, expected_count=expected_count
    )


def validate_external_routing_scheduler_attestation(
    raw: bytes,
    *,
    source_sha: str,
    instance_id: str,
    project: str,
    environment: str,
    console_domain: str,
    workspace_domain: str,
) -> dict[str, Any]:
    """Validate a short-lived routing/scheduler observation, never a hard fence."""
    if not 1 <= len(raw) <= MAX_ROUTING_SCHEDULER_ATTESTATION_BYTES:
        raise ValueError("external routing/scheduler attestation size is invalid")
    try:
        payload = json.loads(
            raw.decode("utf-8"),
            object_pairs_hook=_strict_json_object,
            parse_constant=lambda value: (_ for _ in ()).throw(
                ValueError(f"non-finite JSON constant: {value}")
            ),
        )
    except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
        raise ValueError("external routing/scheduler attestation is malformed") from exc
    root_keys = {
        "schema",
        "scope",
        "attested_at",
        "source_sha",
        "instance_id",
        "gcp_public_ip",
        "gcp_console_domain",
        "gcp_console_addresses",
        "gcp_workspace_domain",
        "gcp_workspace_addresses",
        "gcp_scheduler_count",
        "transfer_job",
        "aws",
        "forensic_evidence",
        "decision",
    }
    if not isinstance(payload, dict) or set(payload) != root_keys:
        raise ValueError("external routing/scheduler attestation shape is invalid")
    observed = _parse_utc_timestamp(payload["attested_at"], field="attested_at")
    now = datetime.now(timezone.utc)
    if observed > now or now - observed > timedelta(minutes=15):
        raise ValueError(
            "external routing/scheduler attestation is outside its 15-minute TTL"
        )
    if payload["schema"] != ROUTING_SCHEDULER_ATTESTATION_SCHEMA:
        raise ValueError("external routing/scheduler attestation schema is unsupported")
    if payload["scope"] != "routing-and-scheduler-observation-only":
        raise ValueError("external routing/scheduler attestation overstates its scope")
    if payload["source_sha"] != source_sha or payload["instance_id"] != instance_id:
        raise ValueError("external routing/scheduler candidate or GCE instance differs")

    address_result = run(
        [
            "gcloud",
            "--quiet",
            "compute",
            "addresses",
            "describe",
            f"omega-{environment}-public-https-ip",
            f"--project={project}",
            "--global",
            "--format=value(address)",
        ],
        timeout=120,
    )
    public_ip = address_result.stdout.strip()
    if address_result.returncode != 0 or not public_ip:
        raise RuntimeError(
            "cannot bind routing/scheduler evidence to the GCP public IP"
        )
    expected_dns = {
        "gcp_console_domain": console_domain,
        "gcp_console_addresses": [public_ip],
        "gcp_workspace_domain": workspace_domain,
        "gcp_workspace_addresses": [public_ip],
    }
    for key, expected in expected_dns.items():
        if payload[key] != expected:
            raise ValueError(
                f"external routing/scheduler GCP DNS assertion differs: {key}"
            )
    if payload["gcp_public_ip"] != public_ip or payload["gcp_scheduler_count"] != 1:
        raise ValueError("external routing/scheduler GCP IP or scheduler count differs")
    transfer = payload["transfer_job"]
    if not isinstance(transfer, dict) or set(transfer) != {
        "name",
        "status",
        "last_operation_name",
        "last_operation_status",
        "last_operation_ended_at",
    }:
        raise ValueError(
            "external routing/scheduler transfer assertion shape is invalid"
        )
    if (
        transfer["name"] != CANONICAL_TRANSFER_JOB
        or transfer["status"] != "DISABLED"
        or not isinstance(transfer["last_operation_name"], str)
        or not transfer["last_operation_name"].startswith("transferOperations/")
        or transfer["last_operation_status"] not in {"SUCCESS", "FAILED", "ABORTED"}
    ):
        raise ValueError(
            "external routing/scheduler transfer job is not disabled and closed"
        )
    ended = _parse_utc_timestamp(
        transfer["last_operation_ended_at"], field="last_operation_ended_at"
    )
    if ended > observed + timedelta(minutes=2):
        raise ValueError(
            "external routing/scheduler transfer operation end time is inconsistent"
        )

    aws = payload["aws"]
    if not isinstance(aws, dict) or set(aws) != {
        "origin",
        "destination",
        "db_api_hard_fence_proven",
        "hard_fence_phase",
    }:
        raise ValueError("external routing/scheduler AWS assertion shape is invalid")
    origin = aws["origin"]
    destination = aws["destination"]
    if (
        not isinstance(origin, dict)
        or set(origin)
        != {
            "alb_hostname",
            "https_healthz_status",
            "http_redirect_status",
            "frozen_source_sha",
        }
        or origin["alb_hostname"] != CANONICAL_AWS_ORIGIN_ALB
        or origin["https_healthz_status"] != 502
        or origin["http_redirect_status"] not in {301, 302, 307, 308}
        or origin["frozen_source_sha"] != CANONICAL_AWS_ORIGIN_FROZEN_SHA
    ):
        raise ValueError("external routing/scheduler AWS origin assertion is invalid")
    if (
        not isinstance(destination, dict)
        or set(destination)
        != {
            "alb_hostname",
            "https_healthz_status",
            "scheduler_status",
            "scheduler_last_heartbeat_at",
            "canonical_dns_target",
            "scheduled_writer_count",
        }
        or destination["alb_hostname"] != CANONICAL_AWS_DESTINATION_ALB
        or destination["https_healthz_status"] != 200
        or destination["scheduler_status"] != "unhealthy"
        or destination["canonical_dns_target"] is not False
        or destination["scheduled_writer_count"] != 0
        or aws["db_api_hard_fence_proven"] is not False
        or aws["hard_fence_phase"] != "16-17"
    ):
        raise ValueError(
            "external routing/scheduler AWS destination assertion is invalid"
        )
    heartbeat = _parse_utc_timestamp(
        destination["scheduler_last_heartbeat_at"],
        field="scheduler_last_heartbeat_at",
    )
    expected_heartbeat = _parse_utc_timestamp(
        CANONICAL_AWS_DESTINATION_SCHEDULER_HEARTBEAT,
        field="canonical_scheduler_last_heartbeat_at",
    )
    if heartbeat != expected_heartbeat or heartbeat > observed:
        raise ValueError("external routing/scheduler AWS heartbeat differs")

    forensic = payload["forensic_evidence"]
    if not isinstance(forensic, dict) or forensic != {
        "observed_at": CANONICAL_AWS_FORENSIC_OBSERVED_AT,
        "event_at": CANONICAL_AWS_FORENSIC_EVENT_AT,
        "reference": CANONICAL_AWS_FORENSIC_REFERENCE,
        "sha256": CANONICAL_AWS_FORENSIC_SHA256,
    }:
        raise ValueError("external routing/scheduler forensic binding differs")

    revalidate_external_routing_scheduler_live(
        payload,
        console_domain=console_domain,
        workspace_domain=workspace_domain,
    )

    decision = payload["decision"]
    if not isinstance(decision, dict) or decision != {
        "canonical_cloud": "GCP",
        "canonical_writer": "GCP",
        "aws_role": "standby",
        "exactly_one_scheduled_writer_gcp": True,
        "checkpoint_zero_aws_writer_gate": "BLOCKED",
        "deployment_authorized": False,
    }:
        raise ValueError(
            "external routing/scheduler operator decision is not GCP-canonical"
        )
    return payload


def read_private_external_routing_scheduler_attestation(
    path: Path, **validation: str
) -> tuple[bytes, dict[str, Any]]:
    """Read one scoped mode-0600 attestation without following links."""
    resolved = path.resolve()
    try:
        resolved.relative_to(REPO.resolve())
    except ValueError:
        pass
    else:
        raise ValueError(
            "external routing/scheduler attestation must remain outside the repo"
        )
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(path, flags)
    except OSError as exc:
        raise ValueError(
            "external routing/scheduler attestation is unavailable"
        ) from exc
    try:
        info = os.fstat(descriptor)
        if (
            not stat.S_ISREG(info.st_mode)
            or info.st_uid != os.geteuid()
            or info.st_mode & 0o777 != 0o600
            or info.st_nlink != 1
            or not 1 <= info.st_size <= MAX_ROUTING_SCHEDULER_ATTESTATION_BYTES
        ):
            raise ValueError(
                "external routing/scheduler attestation must be operator-owned mode 0600 "
                "single-link regular file"
            )
        with os.fdopen(descriptor, "rb", closefd=False) as stream:
            raw = stream.read(MAX_ROUTING_SCHEDULER_ATTESTATION_BYTES + 1)
        if len(raw) != info.st_size:
            raise ValueError(
                "external routing/scheduler attestation changed while read"
            )
    finally:
        os.close(descriptor)
    return raw, validate_external_routing_scheduler_attestation(raw, **validation)


def require_zero_aws_writer_gate(payload: dict[str, Any], *, operation: str) -> None:
    """Keep mutations out until a separately verified AWS DB/API hard fence exists."""
    aws = payload.get("aws", {})
    decision = payload.get("decision", {})
    if (
        aws.get("db_api_hard_fence_proven") is not True
        or decision.get("checkpoint_zero_aws_writer_gate") != "PASS"
        or decision.get("deployment_authorized") is not True
    ):
        raise SystemExit(
            f"{operation} BLOCKED: routing/scheduler evidence does not prove the "
            "required AWS database and API write fence"
        )


def wait_for_fresh_postdeploy_routing_scheduler_attestation(
    path: Path,
    *,
    predeploy_sha256: str,
    not_before: datetime,
    timeout_seconds: int,
    **validation: str,
) -> tuple[bytes, dict[str, Any]]:
    """Wait for distinct evidence observed after the remote deploy completed."""
    if not SHA256_RE.fullmatch(predeploy_sha256):
        raise ValueError("pre-deploy routing/scheduler checksum is invalid")
    if not_before.tzinfo is None or not_before.utcoffset() != timedelta(0):
        raise ValueError("post-deploy evidence boundary must be UTC")
    if not 30 <= timeout_seconds <= 600:
        raise ValueError("post-deploy evidence wait must be 30..600 seconds")
    deadline = time.monotonic() + timeout_seconds
    last_error = "post-deploy routing/scheduler evidence is unavailable"
    while True:
        try:
            raw, payload = read_private_external_routing_scheduler_attestation(
                path, **validation
            )
            digest = hashlib.sha256(raw).hexdigest()
            observed = _parse_utc_timestamp(
                payload.get("attested_at"), field="attested_at"
            )
            if digest == predeploy_sha256:
                raise ValueError("post-deploy evidence reused pre-deploy bytes")
            if observed <= not_before:
                raise ValueError("post-deploy evidence predates deploy completion")
            return raw, payload
        except (OSError, RuntimeError, ValueError) as exc:
            last_error = str(exc)
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise RuntimeError(last_error)
        time.sleep(min(2.0, remaining))


def artifact_from_inputs(
    *, bucket: str, deploy_ref: str, generation: str, size_bytes: str, sha256: str
) -> ArtifactRef:
    if not re.fullmatch(r"[a-z0-9][a-z0-9._-]{1,61}[a-z0-9]", bucket):
        raise ValueError("GCS artifact bucket is invalid")
    if not FULL_SHA_RE.fullmatch(deploy_ref):
        raise ValueError("artifact ref must be an exact lowercase commit SHA")
    if not re.fullmatch(r"[1-9][0-9]*", generation):
        raise ValueError("artifact generation must be a positive integer")
    if not re.fullmatch(r"[1-9][0-9]*", size_bytes):
        raise ValueError("artifact size must be a positive integer")
    if not SHA256_RE.fullmatch(sha256):
        raise ValueError("artifact SHA-256 is invalid")
    return ArtifactRef(
        uri=f"gs://{bucket}/deploy-artifacts/{deploy_ref}/repo.tar.gz",
        generation=generation,
        size_bytes=int(size_bytes),
        sha256=sha256,
    )


def _nested_first(payload: dict[str, Any], *paths: tuple[str, ...]) -> Any:
    for path in paths:
        value: Any = payload
        for key in path:
            if not isinstance(value, dict) or key not in value:
                break
            value = value[key]
        else:
            return value
    return None


def validate_backup_bucket_controls(
    *, project: str, project_number: str, environment: str, bucket: str
) -> dict[str, Any]:
    """Read back the isolated backup bucket and effective non-destructive IAM."""
    expected_bucket = f"omega-{environment}-release-backups-{project_number}"
    if bucket != expected_bucket:
        raise ValueError(f"release backup bucket must be exactly {expected_bucket}")
    described = run(
        [
            "gcloud",
            "--quiet",
            "storage",
            "buckets",
            "describe",
            f"gs://{bucket}",
            f"--project={project}",
            "--format=json",
        ],
        timeout=120,
    )
    if described.returncode != 0:
        raise RuntimeError("cannot read the release backup bucket policy")
    try:
        raw = json.loads(described.stdout)
    except json.JSONDecodeError as exc:
        raise RuntimeError("release backup bucket metadata is malformed") from exc
    if not isinstance(raw, dict):
        raise RuntimeError("release backup bucket metadata is malformed")
    controls = {
        "bucket": _nested_first(raw, ("name",)),
        "location": _nested_first(raw, ("location",)),
        "uniform_bucket_level_access": _nested_first(
            raw,
            ("uniform_bucket_level_access",),
            ("iamConfiguration", "uniformBucketLevelAccess", "enabled"),
        ),
        "public_access_prevention": _nested_first(
            raw,
            ("public_access_prevention",),
            ("iamConfiguration", "publicAccessPrevention"),
        ),
        "versioning_enabled": _nested_first(
            raw, ("versioning_enabled",), ("versioning", "enabled")
        ),
        "soft_delete_seconds": _nested_first(
            raw,
            ("soft_delete_policy", "retentionDurationSeconds"),
            ("softDeletePolicy", "retentionDurationSeconds"),
        ),
        "retention_seconds": _nested_first(
            raw,
            ("retention_policy", "retentionPeriod"),
            ("retentionPolicy", "retentionPeriod"),
        ),
        "retention_locked": _nested_first(
            raw,
            ("retention_policy", "isLocked"),
            ("retentionPolicy", "isLocked"),
        ),
    }
    try:
        controls["soft_delete_seconds"] = int(controls["soft_delete_seconds"])
        controls["retention_seconds"] = int(controls["retention_seconds"])
    except (TypeError, ValueError) as exc:
        raise RuntimeError("release backup retention metadata is malformed") from exc
    if (
        controls["bucket"] != bucket
        or not isinstance(controls["location"], str)
        or re.fullmatch(r"[A-Z0-9-]{2,40}", controls["location"]) is None
        or controls["uniform_bucket_level_access"] is not True
        or controls["public_access_prevention"] != "enforced"
        or controls["versioning_enabled"] is not True
        or controls["soft_delete_seconds"] != 2592000
        or controls["retention_seconds"] != 604800
        or controls["retention_locked"] is not False
    ):
        raise RuntimeError(
            "release backup bucket controls differ from the reviewed policy"
        )

    member = f"serviceAccount:omega-{environment}-app@{project}.iam.gserviceaccount.com"
    policy = run(
        [
            "gcloud",
            "--quiet",
            "storage",
            "buckets",
            "get-iam-policy",
            f"gs://{bucket}",
            f"--project={project}",
            "--format=json",
        ],
        timeout=120,
    )
    if policy.returncode != 0:
        raise RuntimeError("cannot read release backup bucket IAM")
    try:
        bindings = json.loads(policy.stdout).get("bindings", [])
    except (AttributeError, json.JSONDecodeError) as exc:
        raise RuntimeError("release backup bucket IAM is malformed") from exc
    roles: set[str] = set()
    for binding in bindings:
        if not isinstance(binding, dict):
            raise RuntimeError("release backup bucket IAM binding is malformed")
        if member in binding.get("members", []):
            if "condition" in binding or not isinstance(binding.get("role"), str):
                raise RuntimeError(
                    "release backup VM IAM binding is conditional or invalid"
                )
            roles.add(binding["role"])
    expected_roles = {f"projects/{project}/roles/omegaReleaseBackupWriter"}
    if roles != expected_roles:
        raise RuntimeError("release backup VM roles are not exact least privilege")

    controls["vm_role"] = next(iter(expected_roles))
    controls["vm_permissions"] = [
        "storage.buckets.get",
        "storage.objects.create",
        "storage.objects.get",
    ]
    controls["policy_sha256"] = hashlib.sha256(
        json.dumps(controls, separators=(",", ":"), sort_keys=True).encode()
    ).hexdigest()
    return controls


def validate_artifact_bucket_identity(
    *, project: str, project_number: str, bucket: str, var_file: Path | None = None
) -> None:
    """Bind source-artifact writes to the reviewed private GCP project bucket."""
    if GCS_BUCKET_RE.fullmatch(bucket) is None:
        raise ValueError("GCP source artifact bucket is invalid")
    if not re.fullmatch(r"[1-9][0-9]{5,30}", project_number):
        raise ValueError("GCP source artifact project number is invalid")
    if var_file is not None and _tfvar_value(var_file, "source_bucket") != bucket:
        raise ValueError("source artifact bucket differs from reviewed tfvars")
    describe = run(
        [
            "gcloud",
            "--quiet",
            "storage",
            "buckets",
            "describe",
            f"gs://{bucket}",
            f"--project={project}",
            "--format=json(name,projectNumber,iamConfiguration)",
        ],
        timeout=120,
    )
    try:
        payload = json.loads(describe.stdout)
    except json.JSONDecodeError as exc:
        raise RuntimeError("source artifact bucket identity is malformed") from exc
    if (
        describe.returncode != 0
        or payload.get("name") != bucket
        or str(payload.get("projectNumber")) != project_number
    ):
        raise RuntimeError("source artifact bucket differs from the GCP target")
    policy = run(
        [
            "gcloud",
            "--quiet",
            "storage",
            "buckets",
            "get-iam-policy",
            f"gs://{bucket}",
            f"--project={project}",
            "--format=json(bindings)",
        ],
        timeout=120,
    )
    try:
        bindings = json.loads(policy.stdout).get("bindings", [])
    except (AttributeError, json.JSONDecodeError) as exc:
        raise RuntimeError("source artifact bucket IAM is malformed") from exc
    public_members = {"allUsers", "allAuthenticatedUsers"}
    if policy.returncode != 0 or any(
        public_members.intersection(binding.get("members", []))
        for binding in bindings
        if isinstance(binding, dict)
    ):
        raise RuntimeError("source artifact bucket has public or unreadable IAM")


def validate_transfer_fence(
    *, project: str, project_number: str, lakehouse_bucket: str
) -> None:
    """Require the historical AWS->GCP sink-delete transfer to be powerless."""
    if GCS_BUCKET_RE.fullmatch(lakehouse_bucket) is None:
        raise ValueError("canonical lakehouse bucket is invalid")
    jobs_result = run(
        [
            "gcloud",
            "--quiet",
            "transfer",
            "jobs",
            "list",
            f"--project={project}",
            "--format=json",
        ],
        timeout=120,
    )
    if jobs_result.returncode != 0:
        raise RuntimeError("cannot read Storage Transfer jobs")
    try:
        jobs = json.loads(jobs_result.stdout)
    except json.JSONDecodeError as exc:
        raise RuntimeError("Storage Transfer job inventory is malformed") from exc
    if not isinstance(jobs, list):
        raise RuntimeError("Storage Transfer job inventory is malformed")
    sink_jobs = [
        job
        for job in jobs
        if isinstance(job, dict)
        and _nested_first(job, ("transferSpec", "gcsDataSink", "bucketName"))
        == lakehouse_bucket
    ]
    if len(sink_jobs) != 1 or sink_jobs[0].get("name") != CANONICAL_TRANSFER_JOB:
        raise RuntimeError("lakehouse Storage Transfer job inventory is not exact")
    job = sink_jobs[0]
    if job.get("status") != "DISABLED":
        raise RuntimeError("AWS-to-GCP Storage Transfer job is not disabled")
    if (
        _nested_first(
            job, ("transferSpec", "transferOptions", "deleteObjectsUniqueInSink")
        )
        is not True
    ):
        raise RuntimeError(
            "historical sink-delete transfer contract unexpectedly changed"
        )
    source = _nested_first(job, ("transferSpec", "awsS3DataSource"))
    nested_access = source.get("awsAccessKey", {}) if isinstance(source, dict) else {}
    role_arn = (source.get("roleArn") if isinstance(source, dict) else None) or (
        nested_access.get("roleArn") if isinstance(nested_access, dict) else None
    )
    if (
        not isinstance(source, dict)
        or not isinstance(role_arn, str)
        or not role_arn.startswith("arn:aws:iam::")
        or any(key in source for key in ("accessKeyId", "secretAccessKey"))
        or (
            isinstance(nested_access, dict)
            and any(key in nested_access for key in ("accessKeyId", "secretAccessKey"))
        )
    ):
        raise RuntimeError("Storage Transfer source identity is not role-only")

    operations = run(
        [
            "gcloud",
            "--quiet",
            "transfer",
            "operations",
            "list",
            f"--job-names={CANONICAL_TRANSFER_JOB}",
            f"--project={project}",
            "--format=json",
        ],
        timeout=120,
    )
    if operations.returncode != 0:
        raise RuntimeError("cannot read Storage Transfer operation state")
    try:
        operation_rows = json.loads(operations.stdout)
    except json.JSONDecodeError as exc:
        raise RuntimeError("Storage Transfer operation inventory is malformed") from exc
    active = {"IN_PROGRESS", "PAUSED", "QUEUED"}
    if not isinstance(operation_rows, list) or any(
        isinstance(item, dict)
        and str(_nested_first(item, ("metadata", "status"), ("status",))) in active
        for item in operation_rows
    ):
        raise RuntimeError("a Storage Transfer operation is still active")

    service_agent = (
        f"serviceAccount:service-{project_number}"
        "@gcp-sa-storagetransfer.iam.gserviceaccount.com"
    )
    bucket_iam = run(
        [
            "gcloud",
            "--quiet",
            "storage",
            "buckets",
            "get-iam-policy",
            f"gs://{lakehouse_bucket}",
            f"--project={project}",
            "--format=json",
        ],
        timeout=120,
    )
    if bucket_iam.returncode != 0:
        raise RuntimeError("cannot read lakehouse IAM for transfer fence")
    try:
        bindings = json.loads(bucket_iam.stdout).get("bindings", [])
    except (AttributeError, json.JSONDecodeError) as exc:
        raise RuntimeError("lakehouse IAM for transfer fence is malformed") from exc
    if any(service_agent in binding.get("members", []) for binding in bindings):
        raise RuntimeError(
            "Storage Transfer service agent still has lakehouse bucket IAM"
        )

    project_iam = run(
        [
            "gcloud",
            "--quiet",
            "projects",
            "get-iam-policy",
            project,
            "--format=json(bindings)",
        ],
        timeout=120,
    )
    try:
        project_bindings = json.loads(project_iam.stdout).get("bindings", [])
    except (AttributeError, json.JSONDecodeError) as exc:
        raise RuntimeError("project IAM for transfer fence is malformed") from exc
    project_roles = {
        binding.get("role")
        for binding in project_bindings
        if isinstance(binding, dict) and service_agent in binding.get("members", [])
    }
    if project_iam.returncode != 0 or project_roles != {
        "roles/storagetransfer.serviceAgent"
    }:
        raise RuntimeError("Storage Transfer project roles are not exactly fenced")

    principal = service_agent.removeprefix("serviceAccount:")
    resource = f"//storage.googleapis.com/projects/_/buckets/{lakehouse_bucket}"
    for permission in ("storage.objects.create", "storage.objects.delete"):
        troubleshoot = run(
            [
                "gcloud",
                "--quiet",
                "policy-intelligence",
                "troubleshoot-policy",
                "iam",
                resource,
                f"--principal-email={principal}",
                f"--permission={permission}",
                "--format=json(access)",
            ],
            timeout=120,
        )
        try:
            access = json.loads(troubleshoot.stdout).get("access")
        except (AttributeError, json.JSONDecodeError) as exc:
            raise RuntimeError(
                "effective transfer permission response is malformed"
            ) from exc
        if troubleshoot.returncode != 0 or access != "NOT_GRANTED":
            raise RuntimeError("Storage Transfer remains capable of writing the sink")


def utc_stamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def redact(value: str) -> str:
    return SECRET_RE.sub("[REDACTED]", value)


def _redact_json(value: Any) -> Any:
    """Recursively redact both sensitive keys and string-shaped secrets."""
    if isinstance(value, dict):
        output: dict[str, Any] = {}
        for raw_key, item in value.items():
            key = str(raw_key)
            if re.search(
                r"(?i)(?:token|password|secret|credential|authorization)", key
            ):
                output[key] = "[REDACTED]"
            else:
                output[key] = _redact_json(item)
        return output
    if isinstance(value, list):
        return [_redact_json(item) for item in value]
    if isinstance(value, str):
        return redact(value)
    return value


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _fsync_directory(path: Path) -> None:
    descriptor = os.open(path, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _secure_evidence_dir(path: Path) -> Path:
    resolved = path.resolve()
    try:
        resolved.relative_to(REPO.resolve())
    except ValueError:
        pass
    else:
        raise ValueError("release evidence must remain outside the repository")
    cursor = resolved
    while not cursor.exists():
        cursor = cursor.parent
    if cursor.is_symlink() or not cursor.is_dir():
        raise ValueError("release evidence parent is unsafe")
    resolved.mkdir(mode=0o700, parents=True, exist_ok=True)
    if resolved.is_symlink() or not resolved.is_dir():
        raise ValueError("release evidence directory is unsafe")
    resolved.chmod(0o700)
    return resolved


def _atomic_private_text(path: Path, text_value: str) -> None:
    parent = _secure_evidence_dir(path.parent)
    descriptor, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=parent)
    try:
        os.fchmod(descriptor, 0o600)
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            stream.write(text_value)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        path.chmod(0o600)
        _fsync_directory(parent)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


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


def require_canonical_origin() -> str:
    """Reject fetches from a same-shaped repository controlled elsewhere."""
    origin = git("remote", "get-url", "origin")
    if origin not in CANONICAL_ORIGIN_URLS:
        raise ValueError(
            "origin must identify the canonical GitHub repository "
            f"{CANONICAL_GITHUB_REPOSITORY}"
        )
    return origin


def require_canonical_ghcr_owner(owner: str) -> None:
    if owner != CANONICAL_GHCR_OWNER:
        raise ValueError(f"GHCR owner must be exactly {CANONICAL_GHCR_OWNER}")


def validate_published_release_identity(
    tag: str, deploy_ref: str, *, require_main: bool
) -> str:
    """Require a published annotated tag, exact commit, and VERSION match."""
    if not TAG_RE.fullmatch(tag) or "latest" in tag:
        raise ValueError("tag must be an immutable v-prefixed release tag")
    if not FULL_SHA_RE.fullmatch(deploy_ref):
        raise ValueError("deploy-ref must be a full lowercase commit SHA")
    require_canonical_origin()
    fetched = run(["git", "fetch", "origin", "--tags"], timeout=300)
    if fetched.returncode != 0:
        raise RuntimeError(redact(fetched.stderr or fetched.stdout))
    if git("cat-file", "-t", f"refs/tags/{tag}") != "tag":
        raise ValueError("release tag must be annotated")
    local_tag_object = git("rev-parse", f"refs/tags/{tag}")
    if not FULL_SHA_RE.fullmatch(local_tag_object):
        raise ValueError("local annotated release tag object is invalid")
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
    remote_refs: dict[str, str] = {}
    for line in remote.stdout.splitlines():
        fields = line.split()
        if len(fields) != 2 or not FULL_SHA_RE.fullmatch(fields[0]):
            raise ValueError("remote annotated release tag response is malformed")
        if fields[1] in remote_refs:
            raise ValueError("remote annotated release tag response is duplicated")
        remote_refs[fields[1]] = fields[0]
    expected_refs = {f"refs/tags/{tag}", f"refs/tags/{tag}^{{}}"}
    if set(remote_refs) != expected_refs:
        raise ValueError("remote annotated release tag refs are incomplete")
    if remote_refs[f"refs/tags/{tag}"] != local_tag_object:
        raise ValueError("remote annotated release tag object differs from local")
    if remote_refs[f"refs/tags/{tag}^{{}}"] != deploy_ref:
        raise ValueError("annotated release tag is not published at the exact commit")
    return version


def validate_release_identity(tag: str, deploy_ref: str) -> str:
    """Require an annotated release at the exact current origin/main commit."""
    return validate_published_release_identity(tag, deploy_ref, require_main=True)


def validate_bound_release_identity(
    tag: str, deploy_ref: str, *, require_main: bool = True
) -> BoundReleaseIdentity:
    """Bind the exact remote tag object to one sealed candidate payload digest."""
    version = validate_published_release_identity(
        tag, deploy_ref, require_main=require_main
    )
    tag_object_sha = git("rev-parse", f"refs/tags/{tag}")
    raw_tag = git("cat-file", "tag", f"refs/tags/{tag}")
    raw_headers, separator, message = raw_tag.partition("\n\n")
    if not separator:
        raise ValueError("annotated release tag has no manifest-binding message")
    selected: dict[str, list[str]] = {"object": [], "type": [], "tag": []}
    for line in raw_headers.splitlines():
        key, split, value = line.partition(" ")
        if split and key in selected:
            selected[key].append(value)
    if selected != {
        "object": [deploy_ref],
        "type": ["commit"],
        "tag": [tag],
    }:
        raise ValueError("annotated release tag headers differ from the exact release")
    prefix = "OMEGA-Release-Candidate-Manifest-SHA256: "
    bindings = [
        line.removeprefix(prefix)
        for line in message.splitlines()
        if line.startswith(prefix)
    ]
    if len(bindings) != 1 or OCI_DIGEST_RE.fullmatch(bindings[0]) is None:
        raise ValueError(
            "annotated release tag must contain one exact sealed manifest binding"
        )
    return BoundReleaseIdentity(version, bindings[0], tag_object_sha)


def _positive_decimal(value: object, *, field: str) -> str:
    rendered = str(value)
    if re.fullmatch(r"[1-9][0-9]{0,19}", rendered) is None:
        raise ValueError(f"{field} must be an explicit positive integer")
    return rendered


def _gh_api_json(path: str) -> dict[str, Any]:
    """Read one authenticated GitHub API object without exposing credentials."""
    if (
        not path.startswith(f"/repos/{CANONICAL_GITHUB_REPOSITORY}/")
        or "#" in path
        or re.search(r"[\x00-\x20]", path)
    ):
        raise ValueError("GitHub API path is not canonical")
    result = run(
        [
            "gh",
            "api",
            "--method",
            "GET",
            "-H",
            "Accept: application/vnd.github+json",
            "-H",
            "X-GitHub-Api-Version: 2022-11-28",
            path,
        ],
        timeout=120,
    )
    if result.returncode != 0:
        raise RuntimeError(
            "authenticated GitHub release-authority lookup failed: "
            + redact(result.stderr or result.stdout)
        )
    try:
        payload = json.loads(
            result.stdout,
            object_pairs_hook=_strict_json_object,
            parse_constant=lambda value: (_ for _ in ()).throw(
                ValueError(f"non-finite JSON constant: {value}")
            ),
        )
    except (json.JSONDecodeError, ValueError) as exc:
        raise RuntimeError("GitHub release-authority response is malformed") from exc
    if not isinstance(payload, dict):
        raise RuntimeError("GitHub release-authority response is not an object")
    return payload


def _read_candidate_manifest_artifact(
    directory: Path,
    *,
    source_sha: str,
    version: str,
    run_id: str,
    run_attempt: str,
) -> bytes:
    entries = list(directory.rglob("*"))
    expected = directory / "release-candidate.json"
    if entries != [expected]:
        raise RuntimeError(
            "release-candidate workflow artifact must contain exactly "
            "release-candidate.json"
        )
    flags = os.O_RDONLY
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    try:
        descriptor = os.open(expected, flags)
    except OSError as exc:
        raise RuntimeError("release-candidate workflow artifact is unavailable") from exc
    try:
        info = os.fstat(descriptor)
        if (
            not stat.S_ISREG(info.st_mode)
            or info.st_uid != os.geteuid()
            or info.st_nlink != 1
            or not 1 <= info.st_size <= 1024 * 1024
        ):
            raise RuntimeError("release-candidate workflow artifact file is unsafe")
        with os.fdopen(descriptor, "rb", closefd=False) as stream:
            raw = stream.read(1024 * 1024 + 1)
        if len(raw) != info.st_size:
            raise RuntimeError("release-candidate workflow artifact changed while read")
    finally:
        os.close(descriptor)
    try:
        payload = json.loads(
            raw.decode("utf-8"),
            object_pairs_hook=_strict_json_object,
            parse_constant=lambda value: (_ for _ in ()).throw(
                ValueError(f"non-finite JSON constant: {value}")
            ),
        )
    except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
        raise RuntimeError("release-candidate workflow artifact is malformed") from exc
    exact_keys = {
        "candidate_tag",
        "github_run_attempt",
        "github_run_id",
        "images",
        "registry",
        "release_tag",
        "repository",
        "schema_version",
        "source",
        "source_sha",
        "version",
    }
    if (
        not isinstance(payload, dict)
        or set(payload) != exact_keys
        or payload.get("schema_version") != 1
        or payload.get("candidate_tag") != f"candidate-{source_sha}"
        or payload.get("github_run_id") != run_id
        or payload.get("github_run_attempt") != run_attempt
        or payload.get("registry") != "ghcr.io"
        or payload.get("release_tag") != f"v{version}"
        or payload.get("repository") != CANONICAL_GITHUB_REPOSITORY
        or payload.get("source")
        != f"https://github.com/{CANONICAL_GITHUB_REPOSITORY}"
        or payload.get("source_sha") != source_sha
        or payload.get("version") != version
        or not isinstance(payload.get("images"), list)
        or len(payload["images"]) != 15
    ):
        raise RuntimeError("release-candidate workflow artifact identity differs")
    canonical = (json.dumps(payload, sort_keys=True, separators=(",", ":")) + "\n").encode()
    if raw != canonical:
        raise RuntimeError("release-candidate workflow artifact is not canonical JSON")
    return raw


def validate_candidate_workflow_authority(
    *, source_sha: str, version: str, run_id: str, run_attempt: str
) -> CandidateWorkflowAuthority:
    """Bind a pre-tag 15/15 preflight to one successful private workflow artifact."""
    if not FULL_SHA_RE.fullmatch(source_sha):
        raise ValueError("candidate workflow source must be one exact commit SHA")
    if not re.fullmatch(
        r"[0-9]+\.[0-9]+\.[0-9]+(?:-[0-9A-Za-z][0-9A-Za-z.-]*)?", version
    ):
        raise ValueError("candidate workflow VERSION is invalid")
    run_id = _positive_decimal(run_id, field="candidate workflow run ID")
    run_attempt = _positive_decimal(
        run_attempt, field="candidate workflow run attempt"
    )
    run_payload = _gh_api_json(
        f"/repos/{CANONICAL_GITHUB_REPOSITORY}/actions/runs/{run_id}"
    )
    repository = run_payload.get("repository")
    head_repository = run_payload.get("head_repository")
    workflow_path = str(run_payload.get("path", "")).removeprefix("/")
    if (
        str(run_payload.get("id")) != run_id
        or str(run_payload.get("run_attempt")) != run_attempt
        or run_payload.get("head_sha") != source_sha
        or run_payload.get("head_branch") != "main"
        or run_payload.get("event") != "workflow_dispatch"
        or run_payload.get("status") != "completed"
        or run_payload.get("conclusion") != "success"
        or workflow_path != ".github/workflows/release-candidate.yml"
        or not isinstance(repository, dict)
        or repository.get("full_name") != CANONICAL_GITHUB_REPOSITORY
        or not isinstance(head_repository, dict)
        or head_repository.get("full_name") != CANONICAL_GITHUB_REPOSITORY
    ):
        raise RuntimeError(
            "release-candidate workflow run/head/attempt is not exact and successful"
        )

    artifacts_payload = _gh_api_json(
        f"/repos/{CANONICAL_GITHUB_REPOSITORY}/actions/runs/{run_id}"
        "/artifacts?per_page=100"
    )
    artifacts = artifacts_payload.get("artifacts")
    total_count = artifacts_payload.get("total_count")
    if (
        not isinstance(artifacts, list)
        or not isinstance(total_count, int)
        or total_count != len(artifacts)
        or total_count > 100
    ):
        raise RuntimeError("release-candidate workflow artifact listing is incomplete")
    artifact_name = f"release-candidate-{source_sha}"
    matches = [
        artifact
        for artifact in artifacts
        if isinstance(artifact, dict) and artifact.get("name") == artifact_name
    ]
    if len(matches) != 1:
        raise RuntimeError("exact release-candidate workflow artifact is not unique")
    artifact = matches[0]
    artifact_id = _positive_decimal(
        artifact.get("id"), field="candidate workflow artifact ID"
    )
    workflow_run = artifact.get("workflow_run")
    if (
        artifact.get("expired") is not False
        or not isinstance(workflow_run, dict)
        or str(workflow_run.get("id")) != run_id
        or workflow_run.get("head_sha") != source_sha
        or artifact.get("archive_download_url")
        != (
            f"https://api.github.com/repos/{CANONICAL_GITHUB_REPOSITORY}"
            f"/actions/artifacts/{artifact_id}/zip"
        )
    ):
        raise RuntimeError("release-candidate workflow artifact metadata differs")

    with tempfile.TemporaryDirectory(prefix="omega-release-candidate-authority-") as temp:
        destination = Path(temp) / "artifact"
        destination.mkdir(mode=0o700)
        downloaded = run(
            [
                "gh",
                "run",
                "download",
                run_id,
                "--repo",
                CANONICAL_GITHUB_REPOSITORY,
                "--name",
                artifact_name,
                "--dir",
                str(destination),
            ],
            timeout=300,
        )
        if downloaded.returncode != 0:
            raise RuntimeError(
                "cannot download exact release-candidate workflow artifact: "
                + redact(downloaded.stderr or downloaded.stdout)
            )
        raw = _read_candidate_manifest_artifact(
            destination,
            source_sha=source_sha,
            version=version,
            run_id=run_id,
            run_attempt=run_attempt,
        )
    payload_sha256 = hashlib.sha256(raw).hexdigest()
    return CandidateWorkflowAuthority(
        run_id=run_id,
        run_attempt=run_attempt,
        manifest_digest=f"sha256:{payload_sha256}",
        payload_sha256=payload_sha256,
        artifact_id=artifact_id,
    )


def validate_image_target_identity(
    *, target_tag: str, target_ref: str, helper_ref: str, purpose: str
) -> tuple[str, str]:
    """Validate either the pre-tag candidate or one published immutable tag."""
    candidate_tag = f"candidate-{target_ref}"
    if target_tag == candidate_tag:
        if purpose != "release" or helper_ref != target_ref:
            raise ValueError(
                "candidate image preflight must be release-purpose and self-hosted"
            )
        return validate_candidate_main(target_ref), "candidate"
    return (
        validate_published_release_identity(target_tag, target_ref, require_main=False),
        "published",
    )


def validate_legacy_rollback_identity(
    *, target_tag: str, runtime_source_ref: str, helper_ref: str, purpose: str
) -> LegacyRollbackIdentity:
    """Record a legacy tag separately from checksum-bound runtime authority."""
    if purpose != "rollback" or helper_ref == runtime_source_ref:
        raise ValueError("legacy rollback target/helper relationship is invalid")
    if not TAG_RE.fullmatch(target_tag) or "latest" in target_tag:
        raise ValueError("legacy rollback tag is invalid")
    require_canonical_origin()
    fetched = run(["git", "fetch", "origin", "--tags"], timeout=300)
    if fetched.returncode != 0:
        raise RuntimeError(redact(fetched.stderr or fetched.stdout))
    if git("cat-file", "-t", f"refs/tags/{target_tag}") != "tag":
        raise ValueError("legacy rollback tag must be annotated")
    tag_object_sha = git("rev-parse", f"refs/tags/{target_tag}")
    tag_commit = git("rev-parse", f"refs/tags/{target_tag}^{{commit}}")
    if not FULL_SHA_RE.fullmatch(tag_object_sha) or not FULL_SHA_RE.fullmatch(
        tag_commit
    ):
        raise ValueError("legacy rollback tag identity is malformed")
    raw_tag = git("cat-file", "tag", f"refs/tags/{target_tag}")
    headers = raw_tag.partition("\n\n")[0]
    selected: dict[str, list[str]] = {"object": [], "type": [], "tag": []}
    for line in headers.splitlines():
        key, separator, value = line.partition(" ")
        if separator and key in selected:
            selected[key].append(value)
    if selected != {
        "object": [tag_commit],
        "type": ["commit"],
        "tag": [target_tag],
    }:
        raise ValueError("legacy rollback annotated tag headers differ")
    remote = run(
        [
            "git",
            "ls-remote",
            "--tags",
            "origin",
            f"refs/tags/{target_tag}",
            f"refs/tags/{target_tag}^{{}}",
        ],
        timeout=120,
    )
    if remote.returncode != 0:
        raise RuntimeError(redact(remote.stderr or remote.stdout))
    remote_refs: dict[str, str] = {}
    for line in remote.stdout.splitlines():
        fields = line.split()
        if len(fields) != 2 or not FULL_SHA_RE.fullmatch(fields[0]):
            raise ValueError("remote legacy rollback tag response is malformed")
        if fields[1] in remote_refs:
            raise ValueError("remote legacy rollback tag response is duplicated")
        remote_refs[fields[1]] = fields[0]
    if remote_refs != {
        f"refs/tags/{target_tag}": tag_object_sha,
        f"refs/tags/{target_tag}^{{}}": tag_commit,
    }:
        raise ValueError("remote legacy rollback tag differs from local")
    version = target_tag.removeprefix("v")
    validate_current_live_source(tag_commit, version)
    validate_current_live_source(runtime_source_ref, version)
    return LegacyRollbackIdentity(
        version=version,
        tag_object_sha=tag_object_sha,
        tag_commit=tag_commit,
        runtime_source_ref=runtime_source_ref,
    )


def validate_candidate_main(deploy_ref: str) -> str:
    """Bind a pre-tag operation to the exact, current origin/main candidate."""
    if not FULL_SHA_RE.fullmatch(deploy_ref):
        raise ValueError("candidate-ref must be a full lowercase commit SHA")
    require_canonical_origin()
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


def validate_unpublished_release_identity(tag: str, deploy_ref: str) -> str:
    """Require an exact current-main candidate whose intended tag is still absent."""
    version = validate_candidate_main(deploy_ref)
    if not TAG_RE.fullmatch(tag) or tag != f"v{version}":
        raise ValueError("unpublished release tag differs from candidate VERSION")
    fetched = run(["git", "fetch", "origin", "--tags"], timeout=300)
    if fetched.returncode != 0:
        raise RuntimeError(redact(fetched.stderr or fetched.stdout))
    local_tag = run(["git", "show-ref", "--verify", "--quiet", f"refs/tags/{tag}"])
    if local_tag.returncode == 0:
        raise ValueError("candidate release tag already exists locally")
    if local_tag.returncode not in {1}:
        raise RuntimeError(redact(local_tag.stderr or local_tag.stdout))
    remote_tag = run(
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
    if remote_tag.returncode != 0:
        raise RuntimeError(redact(remote_tag.stderr or remote_tag.stdout))
    if remote_tag.stdout.strip():
        raise ValueError("candidate release tag already exists remotely")
    return version


def validate_current_live_source(deploy_ref: str, expected_version: str) -> str:
    """Bind the observed live release to canonical history without trusting a tag."""
    if not FULL_SHA_RE.fullmatch(deploy_ref):
        raise ValueError("current live ref must be one exact commit SHA")
    if not re.fullmatch(
        r"[0-9]+\.[0-9]+\.[0-9]+(?:-[0-9A-Za-z][0-9A-Za-z.-]*)?",
        expected_version,
    ):
        raise ValueError("current live VERSION is invalid")
    if git("cat-file", "-t", deploy_ref) != "commit":
        raise ValueError("current live ref is not a canonical commit")
    ancestor = run(["git", "merge-base", "--is-ancestor", deploy_ref, "origin/main"])
    if ancestor.returncode != 0:
        if ancestor.returncode == 1:
            raise ValueError("current live ref is not an ancestor of origin/main")
        raise RuntimeError(redact(ancestor.stderr or ancestor.stdout))
    actual_version = git("show", f"{deploy_ref}:VERSION").strip()
    if actual_version != expected_version:
        raise ValueError(
            f"current live VERSION {actual_version} differs from operator contract"
        )
    return actual_version


def require_local_file_at_ref(deploy_ref: str, relative: str) -> None:
    committed = run(["git", "show", f"{deploy_ref}:{relative}"])
    if committed.returncode != 0:
        raise RuntimeError(redact(committed.stderr or committed.stdout))
    if committed.stdout != (REPO / relative).read_text(encoding="utf-8"):
        raise ValueError(f"local {relative} differs from the exact candidate commit")


def require_startup_files_at_ref(deploy_ref: str) -> None:
    for relative in (
        "infra/terraform-gcp/templates/startup.sh.tftpl",
        "infra/terraform-gcp/templates/omega-operation-gate",
        "scripts/gcp/bootstrap-runtime.sh",
        "scripts/gcp/finalize-startup-adoption.sh",
        "scripts/gcp/metadata-firewall.sh",
        "scripts/gcp/operation-watchdog.sh",
        "scripts/gcp/safe_io.py",
        "scripts/gcp/verify-secret-access.sh",
    ):
        require_local_file_at_ref(deploy_ref, relative)


def require_exact_terraform_tree_at_ref(deploy_ref: str, terraform_dir: Path) -> None:
    """Bind every locally loaded Terraform source file to one reviewed ref.

    Provider/backend cache data under ``.terraform`` is runtime state rather
    than source.  Every other filesystem entry must be tracked by the exact
    ref, and Git must report byte/mode identity for the complete module.
    """
    if not FULL_SHA_RE.fullmatch(deploy_ref):
        raise ValueError("Terraform source ref must be one exact commit SHA")
    canonical = (REPO / "infra/terraform-gcp").resolve()
    resolved = terraform_dir.resolve()
    if resolved != canonical or terraform_dir.is_symlink() or not resolved.is_dir():
        raise ValueError("Terraform directory must be the canonical repository module")

    relative_root = "infra/terraform-gcp"
    diff = run(
        ["git", "diff", "--quiet", deploy_ref, "--", relative_root],
        timeout=120,
    )
    if diff.returncode != 0:
        if diff.returncode == 1:
            raise ValueError("local Terraform tree differs from the exact release ref")
        raise RuntimeError(redact(diff.stderr or diff.stdout))
    listing = run(
        ["git", "ls-tree", "-r", "--name-only", deploy_ref, "--", relative_root],
        timeout=120,
    )
    if listing.returncode != 0:
        raise RuntimeError(redact(listing.stderr or listing.stdout))
    tracked = {line for line in listing.stdout.splitlines() if line}
    if not tracked:
        raise ValueError("exact release ref has no canonical Terraform module")

    for path in resolved.rglob("*"):
        relative = path.relative_to(REPO).as_posix()
        module_relative = path.relative_to(resolved).as_posix()
        if path.is_symlink():
            raise ValueError(f"Terraform tree contains a symlink: {module_relative}")
        if module_relative == ".terraform" or module_relative.startswith(".terraform/"):
            continue
        if path.is_file() and relative not in tracked:
            raise ValueError(
                f"Terraform tree contains an unreviewed file: {module_relative}"
            )


def require_private_reviewed_var_file(var_file: Path) -> Path:
    resolved = var_file.resolve()
    try:
        resolved.relative_to(REPO.resolve())
    except ValueError:
        pass
    else:
        raise ValueError("reviewed production tfvars must remain outside the repo")
    info = var_file.lstat()
    if (
        var_file.is_symlink()
        or not stat.S_ISREG(info.st_mode)
        or info.st_uid != os.geteuid()
        or info.st_mode & 0o077
    ):
        raise ValueError("reviewed production tfvars must be operator-owned mode 0600")
    if var_file.name in {"terraform.tfvars", "terraform.tfvars.json"} or re.search(
        r"\.auto\.tfvars(?:\.json)?$", var_file.name
    ):
        raise ValueError("reviewed tfvars must not use an implicit auto-load name")
    return resolved


def validate_candidate_identity(deploy_ref: str) -> str:
    """Bind the backup controller and helper to current origin/main."""
    version = validate_candidate_main(deploy_ref)
    require_local_file_at_ref(deploy_ref, "scripts/gcp/backup.sh")
    require_local_file_at_ref(deploy_ref, "scripts/gcp/safe_io.py")
    require_startup_files_at_ref(deploy_ref)
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
) -> ArtifactRef:
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
    # Whether this process created the object or hit generation-match=0 on an
    # idempotent retry, trust only the exact stored generation and bytes. Custom
    # metadata remains useful evidence but is never the integrity decision.
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
    try:
        payload = json.loads(describe.stdout)
        generation = str(payload["generation"])
        size_bytes = int(payload["size"])
    except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
        raise RuntimeError("immutable artifact metadata is malformed") from exc
    if not re.fullmatch(r"[1-9][0-9]*", generation):
        raise RuntimeError("immutable artifact generation is invalid")
    if size_bytes != archive.stat().st_size or size_bytes < 1:
        raise RuntimeError("immutable artifact stored size differs")
    existing = _custom_metadata(payload)
    if existing.get("omega-artifact-sha256") != artifact_sha256:
        raise RuntimeError("existing immutable artifact checksum metadata differs")
    if existing.get("omega-deploy-ref") != deploy_ref:
        raise RuntimeError("existing immutable artifact commit metadata differs")
    with tempfile.TemporaryDirectory(prefix="omega-gcs-byte-verify-") as temp:
        downloaded = Path(temp) / "repo.tar.gz"
        exact_uri = f"{uri}#{generation}"
        readback = run(
            ["gcloud", "--quiet", "storage", "cp", exact_uri, str(downloaded)],
            timeout=600,
        )
        if readback.returncode != 0 or not downloaded.is_file():
            raise RuntimeError("cannot read back exact immutable artifact generation")
        if (
            downloaded.stat().st_size != size_bytes
            or sha256_file(downloaded) != artifact_sha256
        ):
            raise RuntimeError("immutable artifact readback bytes differ")
    return ArtifactRef(uri, generation, size_bytes, artifact_sha256)


def upload_reconciliation_manifest_immutable(
    *, manifest_bytes: bytes, manifest: dict[str, Any], bucket: str, helper_ref: str
) -> ArtifactRef:
    """Publish one validated reconciliation set under its content address."""
    if GCS_BUCKET_RE.fullmatch(bucket) is None:
        raise ValueError("invalid GCS source bucket")
    if not FULL_SHA_RE.fullmatch(helper_ref):
        raise ValueError("reconciliation helper ref must be an exact commit")
    change_id = manifest.get("change_id")
    if (
        not isinstance(change_id, str)
        or re.fullmatch(r"[a-z0-9][a-z0-9._-]{2,127}", change_id) is None
    ):
        raise ValueError("reconciliation change id is invalid")
    manifest_sha256 = hashlib.sha256(manifest_bytes).hexdigest()
    uri = (
        f"gs://{bucket}/pipeline-run-reconciliations/{change_id}/"
        f"{manifest_sha256}.json"
    )
    metadata = ",".join(
        (
            f"omega-reconciliation-sha256={manifest_sha256}",
            f"omega-change-id={change_id}",
            f"omega-helper-ref={helper_ref}",
        )
    )
    with tempfile.TemporaryDirectory(prefix="omega-reconciliation-upload-") as temp:
        source = Path(temp) / "manifest.json"
        descriptor = os.open(source, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        try:
            with os.fdopen(descriptor, "wb", closefd=False) as stream:
                stream.write(manifest_bytes)
                stream.flush()
                os.fsync(stream.fileno())
        finally:
            os.close(descriptor)
        upload = run(
            [
                "gcloud",
                "--quiet",
                "storage",
                "cp",
                str(source),
                uri,
                "--if-generation-match=0",
                f"--custom-metadata={metadata}",
            ],
            timeout=600,
        )
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
        try:
            payload = json.loads(describe.stdout)
            generation = str(payload["generation"])
            size_bytes = int(payload["size"])
        except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
            raise RuntimeError(
                "immutable reconciliation metadata is malformed"
            ) from exc
        if not re.fullmatch(r"[1-9][0-9]*", generation):
            raise RuntimeError("immutable reconciliation generation is invalid")
        if size_bytes != len(manifest_bytes):
            raise RuntimeError("immutable reconciliation stored size differs")
        existing = _custom_metadata(payload)
        if existing != {
            "omega-reconciliation-sha256": manifest_sha256,
            "omega-change-id": change_id,
            "omega-helper-ref": helper_ref,
        }:
            raise RuntimeError("immutable reconciliation metadata differs")
        downloaded = Path(temp) / "readback.json"
        readback = run(
            [
                "gcloud",
                "--quiet",
                "storage",
                "cp",
                f"{uri}#{generation}",
                str(downloaded),
            ],
            timeout=600,
        )
        if (
            readback.returncode != 0
            or not downloaded.is_file()
            or downloaded.stat().st_size != size_bytes
            or sha256_file(downloaded) != manifest_sha256
        ):
            raise RuntimeError("immutable reconciliation readback bytes differ")
    return ArtifactRef(uri, generation, size_bytes, manifest_sha256)


def upload_external_routing_scheduler_attestation_immutable(
    *, raw: bytes, bucket: str, source_sha: str
) -> ArtifactRef:
    """Publish the scoped routing/scheduler observation under its content address."""
    if GCS_BUCKET_RE.fullmatch(bucket) is None or not FULL_SHA_RE.fullmatch(source_sha):
        raise ValueError("external routing/scheduler upload identity is invalid")
    digest = hashlib.sha256(raw).hexdigest()
    uri = (
        f"gs://{bucket}/external-routing-scheduler-attestations/"
        f"{source_sha}/{digest}.json"
    )
    metadata = f"omega-routing-scheduler-sha256={digest},omega-source-sha={source_sha}"
    with tempfile.TemporaryDirectory(prefix="omega-routing-scheduler-upload-") as temp:
        source = Path(temp) / "attestation.json"
        descriptor = os.open(source, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        try:
            with os.fdopen(descriptor, "wb", closefd=False) as stream:
                stream.write(raw)
                stream.flush()
                os.fsync(stream.fileno())
        finally:
            os.close(descriptor)
        upload = run(
            [
                "gcloud",
                "--quiet",
                "storage",
                "cp",
                str(source),
                uri,
                "--if-generation-match=0",
                f"--custom-metadata={metadata}",
            ],
            timeout=600,
        )
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
        try:
            payload = json.loads(describe.stdout)
            generation = str(payload["generation"])
            size_bytes = int(payload["size"])
        except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
            raise RuntimeError(
                "immutable external routing/scheduler metadata is malformed"
            ) from exc
        if (
            re.fullmatch(r"[1-9][0-9]*", generation) is None
            or size_bytes != len(raw)
            or _custom_metadata(payload)
            != {
                "omega-routing-scheduler-sha256": digest,
                "omega-source-sha": source_sha,
            }
        ):
            raise RuntimeError("immutable external routing/scheduler identity differs")
        downloaded = Path(temp) / "readback.json"
        readback = run(
            [
                "gcloud",
                "--quiet",
                "storage",
                "cp",
                f"{uri}#{generation}",
                str(downloaded),
            ],
            timeout=600,
        )
        if (
            readback.returncode != 0
            or not downloaded.is_file()
            or downloaded.stat().st_size != size_bytes
            or sha256_file(downloaded) != digest
        ):
            raise RuntimeError(
                "immutable external routing/scheduler readback bytes differ"
            )
    return ArtifactRef(uri, generation, size_bytes, digest)


def remote_script(
    *,
    project: str,
    zone: str,
    instance: str,
    script: Path,
    arguments: list[str],
    timeout: int,
    companion_scripts: dict[str, Path] | None = None,
) -> RemoteResult:
    if not script.is_file():
        raise FileNotFoundError(script)
    if not SAFE_IO.is_file():
        raise FileNotFoundError(SAFE_IO)
    remote_command = shlex.join(
        ["sudo", "--non-interactive", "bash", "-s", "--", *arguments]
    )
    script_bytes = script.read_bytes()
    helper_bytes = SAFE_IO.read_bytes()
    script_b64 = base64.b64encode(script_bytes).decode("ascii")
    helper_b64 = base64.b64encode(helper_bytes).decode("ascii")
    companion_lines: list[str] = []
    for name, path in sorted((companion_scripts or {}).items()):
        if re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,127}", name) is None:
            raise ValueError("remote companion script name is invalid")
        if not path.is_file() or path.is_symlink():
            raise FileNotFoundError(path)
        value = path.read_bytes()
        encoded = base64.b64encode(value).decode("ascii")
        digest = hashlib.sha256(value).hexdigest()
        companion_lines.extend(
            (
                f"printf '%s' '{encoded}' | base64 -d > \"$remote_root/{name}\"",
                f'chmod 0700 "$remote_root/{name}"',
                f'test "$(sha256sum "$remote_root/{name}" | awk \'{{print $1}}\')" = "{digest}"',
            )
        )
    companions = "\n".join(companion_lines)
    wrapper = f"""#!/usr/bin/env bash
set -Eeuo pipefail
umask 077
remote_root="$(mktemp -d /run/omega-gcp-remote.XXXXXX)"
cleanup_remote() {{
  status=$?
  trap - EXIT
  case "$remote_root" in /run/omega-gcp-remote.*) rm -rf -- "$remote_root" ;; *) status=70 ;; esac
  exit "$status"
}}
trap cleanup_remote EXIT
printf '%s' '{script_b64}' | base64 -d > "$remote_root/entrypoint.sh"
printf '%s' '{helper_b64}' | base64 -d > "$remote_root/safe_io.py"
chmod 0700 "$remote_root/entrypoint.sh" "$remote_root/safe_io.py"
test "$(sha256sum "$remote_root/entrypoint.sh" | awk '{{print $1}}')" = "{hashlib.sha256(script_bytes).hexdigest()}"
test "$(sha256sum "$remote_root/safe_io.py" | awk '{{print $1}}')" = "{hashlib.sha256(helper_bytes).hexdigest()}"
{companions}
export OMEGA_GCP_SAFE_IO="$remote_root/safe_io.py"
export OMEGA_GCP_REMOTE_ROOT="$remote_root"
bash "$remote_root/entrypoint.sh" "$@"
"""
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
        input_text=wrapper,
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
                candidate = json.loads(line.split("=", 1)[1])
                payload = _redact_json(candidate) if isinstance(candidate, dict) else {}
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
    evidence_dir = _secure_evidence_dir(evidence_dir)
    checks = _redact_json(checks)
    payload = _redact_json(payload)
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
    _atomic_private_text(
        evidence_dir / "summary.json",
        json.dumps(summary, indent=2, sort_keys=True) + "\n",
    )
    _atomic_private_text(
        evidence_dir / "remote_stdout_redacted.txt", redact(result.stdout)
    )
    _atomic_private_text(
        evidence_dir / "remote_stderr_redacted.txt", redact(result.stderr)
    )
    rows = [
        f"| {item['name']} | {item['status']} | {str(item['evidence']).replace('|', chr(92) + '|')} |"
        for item in checks
    ]
    _atomic_private_text(
        evidence_dir / "REPORT.md",
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
    )
    return status


def common_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--project", default=os.environ.get("GCP_PROJECT_ID", ""))
    parser.add_argument("--zone", default=os.environ.get("GCP_APP_ZONE", ""))
    parser.add_argument("--instance", default=os.environ.get("GCP_APP_INSTANCE", ""))
    parser.add_argument(
        "--instance-id", default=os.environ.get("GCP_APP_INSTANCE_ID", "")
    )
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
    parser.add_argument(
        "--source-generation", default=os.environ.get("GCP_SOURCE_GENERATION", "")
    )
    parser.add_argument(
        "--source-size-bytes", default=os.environ.get("GCP_SOURCE_SIZE_BYTES", "")
    )
    parser.add_argument(
        "--source-archive-sha256",
        default=os.environ.get("GCP_SOURCE_ARCHIVE_SHA256", ""),
    )


def require_target(args: argparse.Namespace) -> None:
    if not args.project or not args.zone or not args.instance or not args.instance_id:
        raise SystemExit(
            "GCP_PROJECT_ID, GCP_APP_ZONE, GCP_APP_INSTANCE, and "
            "GCP_APP_INSTANCE_ID are required"
        )


def _tfvar_value(var_file: Path, name: str) -> str:
    text = require_private_reviewed_var_file(var_file).read_text(encoding="utf-8")
    matches = re.findall(
        rf"(?m)^\s*{re.escape(name)}\s*=\s*(?:\"([^\"]*)\"|([^#\s]+))\s*(?:#.*)?$",
        text,
    )
    if len(matches) != 1:
        raise ValueError(f"reviewed tfvars must assign {name} exactly once")
    quoted, bare = matches[0]
    return quoted if quoted != "" else bare


def validate_reviewed_lakehouse_identity(
    *,
    var_file: Path,
    project_number: str,
    environment: str,
    lakehouse_bucket: str,
) -> str:
    """Bind operator transfer checks to the bucket rendered into live startup."""
    configured = _tfvar_value(var_file, "lakehouse_bucket_name")
    expected = configured or f"omega-{environment}-lakehouse-{project_number}"
    if GCS_BUCKET_RE.fullmatch(expected) is None or lakehouse_bucket != expected:
        raise ValueError("canonical lakehouse bucket differs from reviewed tfvars")
    return expected


def validate_reviewed_target_identity(
    *,
    var_file: Path,
    project: str,
    project_number: str,
    zone: str,
    instance: str,
    environment: str,
) -> None:
    expected = {
        "project_id": project,
        "project_number": project_number,
        "zone": zone,
        "environment": environment,
    }
    for key, value in expected.items():
        if _tfvar_value(var_file, key) != value:
            raise ValueError(f"canonical target differs from reviewed tfvars: {key}")
    if instance != f"omega-{environment}-app":
        raise ValueError("GCP app instance name is not canonical")


def validate_live_canonical_target(
    *,
    project: str,
    project_number: str,
    zone: str,
    instance: str,
    instance_id: str,
    environment: str,
) -> None:
    _validate_gcp_target(project, zone, instance)
    if not re.fullmatch(r"[1-9][0-9]{5,30}", project_number):
        raise ValueError("GCP_PROJECT_NUMBER must be the exact numeric project number")
    if not re.fullmatch(r"[1-9][0-9]{5,30}", instance_id):
        raise ValueError("GCP_APP_INSTANCE_ID must be the exact numeric instance id")
    expected_instance = f"omega-{environment}-app"
    expected_group = f"omega-{environment}-app-ig"
    expected_sa = f"omega-{environment}-app@{project}.iam.gserviceaccount.com"
    if instance != expected_instance:
        raise RuntimeError("target instance name differs from canonical environment")
    project_identity = run(
        [
            "gcloud",
            "--quiet",
            "projects",
            "describe",
            project,
            "--format=value(projectNumber)",
        ],
        timeout=120,
    )
    if (
        project_identity.returncode != 0
        or project_identity.stdout.strip() != project_number
    ):
        raise RuntimeError("live GCP project number differs from the reviewed target")
    described = run(
        [
            "gcloud",
            "--quiet",
            "compute",
            "instances",
            "describe",
            instance,
            f"--project={project}",
            f"--zone={zone}",
            "--format=json(id,name,zone,status,labels,serviceAccounts)",
        ],
        timeout=120,
    )
    if described.returncode != 0:
        raise RuntimeError("cannot read the canonical GCE instance identity")
    try:
        payload = json.loads(described.stdout)
    except json.JSONDecodeError as exc:
        raise RuntimeError("canonical GCE instance identity is malformed") from exc
    accounts = payload.get("serviceAccounts", [])
    labels = payload.get("labels", {})
    if (
        str(payload.get("id")) != instance_id
        or payload.get("name") != instance
        or str(payload.get("zone", "")).rsplit("/", 1)[-1] != zone
        or payload.get("status") != "RUNNING"
        or not isinstance(accounts, list)
        or len(accounts) != 1
        or accounts[0].get("email") != expected_sa
        or set(accounts[0].get("scopes", []))
        != {"https://www.googleapis.com/auth/cloud-platform"}
        or not isinstance(labels, dict)
        or labels.get("app") != "omega"
        or labels.get("env") != environment
        or labels.get("project") != "modecissions"
    ):
        raise RuntimeError(
            "live GCE instance identity differs from the reviewed target"
        )

    inventory = run(
        [
            "gcloud",
            "--quiet",
            "compute",
            "instances",
            "list",
            f"--project={project}",
            "--filter=status=RUNNING",
            "--format=json(id,name,zone,labels,serviceAccounts)",
        ],
        timeout=120,
    )
    if inventory.returncode != 0:
        raise RuntimeError("cannot prove canonical GCE writer uniqueness")
    try:
        running = json.loads(inventory.stdout)
    except json.JSONDecodeError as exc:
        raise RuntimeError("GCE writer inventory is malformed") from exc
    if (
        not isinstance(running, list)
        or len(running) != 1
        or str(running[0].get("id")) != instance_id
    ):
        raise RuntimeError("canonical GCP project must contain one running VM")
    same_identity = [
        item
        for item in running
        if isinstance(item, dict)
        and any(
            account.get("email") == expected_sa
            for account in item.get("serviceAccounts", [])
            if isinstance(account, dict)
        )
    ]
    if len(same_identity) != 1 or str(same_identity[0].get("id")) != instance_id:
        raise RuntimeError("GCP has more or fewer than one running canonical writer VM")
    canonical_writer_labels = [
        item
        for item in running
        if isinstance(item, dict)
        and isinstance(item.get("labels"), dict)
        and item["labels"].get("app") == "omega"
        and item["labels"].get("env") == environment
        and item["labels"].get("project") == "modecissions"
    ]
    if (
        len(canonical_writer_labels) != 1
        or str(canonical_writer_labels[0].get("id")) != instance_id
    ):
        raise RuntimeError(
            "GCP has more or fewer than one running environment writer VM"
        )

    group = run(
        [
            "gcloud",
            "--quiet",
            "compute",
            "instance-groups",
            "unmanaged",
            "list-instances",
            expected_group,
            f"--project={project}",
            f"--zone={zone}",
            "--format=json(instance,status)",
        ],
        timeout=120,
    )
    if group.returncode != 0:
        raise RuntimeError("cannot read canonical GCE instance group")
    try:
        members = json.loads(group.stdout)
    except json.JSONDecodeError as exc:
        raise RuntimeError("canonical GCE instance group is malformed") from exc
    if (
        not isinstance(members, list)
        or len(members) != 1
        or str(members[0].get("instance", "")).rsplit("/", 1)[-1] != instance
        or members[0].get("status") != "RUNNING"
    ):
        raise RuntimeError("canonical load-balancer instance group is not singular")

    group_suffix = f"/zones/{zone}/instanceGroups/{expected_group}"
    for service in ("console", "workspace", "airflow"):
        backend = run(
            [
                "gcloud",
                "--quiet",
                "compute",
                "backend-services",
                "describe",
                f"omega-{environment}-{service}-backend",
                f"--project={project}",
                "--global",
                "--format=json(backends)",
            ],
            timeout=120,
        )
        if backend.returncode != 0:
            raise RuntimeError(f"cannot read canonical {service} backend")
        try:
            backends = json.loads(backend.stdout).get("backends", [])
        except (AttributeError, json.JSONDecodeError) as exc:
            raise RuntimeError(f"canonical {service} backend is malformed") from exc
        if len(backends) != 1 or not str(backends[0].get("group", "")).endswith(
            group_suffix
        ):
            raise RuntimeError(f"canonical {service} backend target differs")


def _resolve_public_addresses(hostname: str) -> set[str]:
    try:
        values = socket.getaddrinfo(
            hostname, 443, family=socket.AF_UNSPEC, type=socket.SOCK_STREAM
        )
    except socket.gaierror as exc:
        raise RuntimeError("canonical public DNS cannot be resolved") from exc
    return {str(item[4][0]) for item in values}


def _probe_https_health(hostname: str, address: str) -> None:
    """Validate public TLS hostname/SAN and the exact HTTPS health route."""
    context = ssl.create_default_context()
    try:
        with socket.create_connection((address, 443), timeout=15) as raw:
            with context.wrap_socket(raw, server_hostname=hostname) as stream:
                stream.sendall(
                    (
                        f"GET /healthz HTTP/1.1\r\nHost: {hostname}\r\n"
                        "Connection: close\r\nAccept: application/json\r\n\r\n"
                    ).encode("ascii")
                )
                response = b""
                while b"\r\n" not in response and len(response) <= 8192:
                    chunk = stream.recv(1024)
                    if not chunk:
                        break
                    response += chunk
    except (OSError, ssl.SSLError) as exc:
        raise RuntimeError("canonical public HTTPS/certificate probe failed") from exc
    status_line = response.split(b"\r\n", 1)[0]
    if not re.fullmatch(rb"HTTP/1\.[01] 200(?: .*)?", status_line):
        raise RuntimeError("canonical public health endpoint is not HTTP 200")


def _resolve_external_a_records(hostname: str, port: int) -> set[str]:
    """Resolve every IPv4 A record for one allowlisted external endpoint."""
    try:
        values = socket.getaddrinfo(
            hostname,
            port,
            family=socket.AF_INET,
            type=socket.SOCK_STREAM,
        )
    except socket.gaierror as exc:
        raise RuntimeError("external ALB A records cannot be resolved") from exc
    addresses = {str(item[4][0]) for item in values}
    if not addresses:
        raise RuntimeError("external ALB returned no A records")
    return addresses


def _probe_external_endpoint(
    endpoint_hostname: str,
    *,
    http_host: str,
    path: str,
    tls: bool,
) -> list[tuple[str, int, dict[str, str], bytes]]:
    """Probe all A records while keeping canonical HTTP Host and verified TLS SNI."""
    if endpoint_hostname not in {
        CANONICAL_AWS_ORIGIN_ALB,
        CANONICAL_AWS_DESTINATION_ALB,
    }:
        raise ValueError("external ALB hostname is outside the exact allowlist")
    if http_host != CANONICAL_PUBLIC_TLS_NAME:
        raise ValueError("external ALB probe Host/SNI is not canonical")
    if not path.startswith("/") or "\r" in path or "\n" in path:
        raise ValueError("external ALB probe path is invalid")
    port = 443 if tls else 80
    context = ssl.create_default_context() if tls else None
    results: list[tuple[str, int, dict[str, str], bytes]] = []
    for address in sorted(_resolve_external_a_records(endpoint_hostname, port)):
        stream: Any = None
        try:
            raw = socket.create_connection((address, port), timeout=15)
            stream = (
                context.wrap_socket(raw, server_hostname=http_host)
                if context is not None
                else raw
            )
            stream.sendall(
                (
                    f"GET {path} HTTP/1.1\r\n"
                    f"Host: {http_host}\r\n"
                    "User-Agent: omega-gcp-routing-scheduler-attestation/1\r\n"
                    "Accept: application/json\r\n"
                    "Connection: close\r\n\r\n"
                ).encode("ascii")
            )
            response = http.client.HTTPResponse(stream)
            response.begin()
            body = response.read(MAX_ROUTING_SCHEDULER_ATTESTATION_BYTES + 1)
            if len(body) > MAX_ROUTING_SCHEDULER_ATTESTATION_BYTES:
                raise RuntimeError("external ALB response exceeded the bounded size")
            headers = {key.lower(): value for key, value in response.getheaders()}
            results.append((address, response.status, headers, body))
        except (OSError, ssl.SSLError, http.client.HTTPException) as exc:
            raise RuntimeError(
                f"external ALB probe failed for allowlisted A record {address}"
            ) from exc
        finally:
            if stream is not None:
                stream.close()
    return results


def _probe_external_health_status(
    hostname: str, *, tls_server_name: str, expected_status: int
) -> None:
    results = _probe_external_endpoint(
        hostname,
        http_host=tls_server_name,
        path="/healthz",
        tls=True,
    )
    if any(status != expected_status for _address, status, _headers, _body in results):
        raise RuntimeError(
            "external ALB health status changed on at least one A record"
        )


def _probe_external_json_status(
    hostname: str,
    *,
    tls_server_name: str,
    path: str,
    expected_status: int,
) -> dict[str, Any]:
    parsed: list[dict[str, Any]] = []
    for _address, status, _headers, body in _probe_external_endpoint(
        hostname,
        http_host=tls_server_name,
        path=path,
        tls=True,
    ):
        if status != expected_status:
            raise RuntimeError("external ALB JSON status changed")
        try:
            value = json.loads(body)
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise RuntimeError("external ALB JSON response is malformed") from exc
        if not isinstance(value, dict):
            raise RuntimeError("external ALB JSON response is not an object")
        parsed.append(value)
    if not parsed or any(value != parsed[0] for value in parsed[1:]):
        raise RuntimeError("external ALB A records returned inconsistent JSON")
    return parsed[0]


def _probe_external_http_redirect(
    hostname: str, *, http_host: str, expected_status: int
) -> None:
    results = _probe_external_endpoint(
        hostname,
        http_host=http_host,
        path="/healthz",
        tls=False,
    )
    for _address, status, headers, _body in results:
        location = headers.get("location", "")
        allowed_locations = {
            f"https://{http_host}/healthz",
            f"https://{http_host}:443/healthz",
        }
        if status != expected_status or location not in allowed_locations:
            raise RuntimeError("external ALB HTTP-to-HTTPS redirect changed")


def revalidate_external_routing_scheduler_live(
    payload: dict[str, Any], *, console_domain: str, workspace_domain: str
) -> dict[str, Any]:
    """Re-read DNS and every allowlisted ALB A record at the call boundary."""
    public_ip = str(payload.get("gcp_public_ip", ""))
    if _resolve_public_addresses(console_domain) != {
        public_ip
    } or _resolve_public_addresses(workspace_domain) != {public_ip}:
        raise RuntimeError("live public DNS no longer points only to canonical GCP")
    aws = payload.get("aws")
    if not isinstance(aws, dict):
        raise ValueError("external routing/scheduler AWS payload is unavailable")
    origin = aws.get("origin")
    destination = aws.get("destination")
    if not isinstance(origin, dict) or not isinstance(destination, dict):
        raise ValueError("external routing/scheduler ALB observations are unavailable")
    canonical_names = {console_domain, workspace_domain}
    if (
        origin.get("alb_hostname") in canonical_names
        or destination.get("alb_hostname") in canonical_names
    ):
        raise ValueError("an AWS ALB appears in the canonical GCP DNS names")

    _probe_external_health_status(
        str(origin.get("alb_hostname", "")),
        tls_server_name=console_domain,
        expected_status=502,
    )
    _probe_external_http_redirect(
        str(origin.get("alb_hostname", "")),
        http_host=console_domain,
        expected_status=int(origin.get("http_redirect_status", 0)),
    )
    _probe_external_health_status(
        str(destination.get("alb_hostname", "")),
        tls_server_name=console_domain,
        expected_status=200,
    )
    scheduler_health = _probe_external_json_status(
        str(destination.get("alb_hostname", "")),
        tls_server_name=console_domain,
        path="/airflow/health",
        expected_status=200,
    )
    live_scheduler = scheduler_health.get("scheduler")
    if (
        not isinstance(live_scheduler, dict)
        or live_scheduler.get("status") != destination.get("scheduler_status")
        or live_scheduler.get("latest_scheduler_heartbeat")
        != destination.get("scheduler_last_heartbeat_at")
    ):
        raise RuntimeError("live AWS scheduler status or heartbeat changed")
    return {
        "observed_at": datetime.now(timezone.utc).isoformat(),
        "gcp_dns_only": True,
        "origin_healthz_status": 502,
        "destination_healthz_status": 200,
        "destination_scheduler_status": live_scheduler["status"],
        "destination_scheduler_last_heartbeat_at": live_scheduler[
            "latest_scheduler_heartbeat"
        ],
        "db_api_hard_fence_proven": aws.get("db_api_hard_fence_proven") is True,
    }


def validate_live_public_edge(
    *,
    project: str,
    environment: str,
    public_console_domain: str,
    public_workspace_domain: str,
) -> None:
    """Bind public DNS/TLS routing to the canonical GCP writer backends."""
    domains = (public_console_domain, public_workspace_domain)
    if len(set(domains)) != 2 or any(
        re.fullmatch(r"[a-z0-9](?:[a-z0-9-]{0,62}\.)+[a-z]{2,63}", value) is None
        for value in domains
    ):
        raise ValueError("canonical public domains are invalid or not distinct")
    prefix = f"omega-{environment}"

    address_result = run(
        [
            "gcloud",
            "--quiet",
            "compute",
            "addresses",
            "describe",
            f"{prefix}-public-https-ip",
            f"--project={project}",
            "--global",
            "--format=value(address)",
        ],
        timeout=120,
    )
    address = address_result.stdout.strip()
    if (
        address_result.returncode != 0
        or re.fullmatch(r"(?:[0-9]{1,3}\.){3}[0-9]{1,3}", address) is None
    ):
        raise RuntimeError("canonical public GCP address cannot be verified")

    forwarding_result = run(
        [
            "gcloud",
            "--quiet",
            "compute",
            "forwarding-rules",
            "describe",
            f"{prefix}-https",
            f"--project={project}",
            "--global",
            "--format=json(IPAddress,portRange,target,loadBalancingScheme)",
        ],
        timeout=120,
    )
    try:
        forwarding = json.loads(forwarding_result.stdout)
    except json.JSONDecodeError as exc:
        raise RuntimeError("canonical HTTPS forwarding rule is malformed") from exc
    if (
        forwarding_result.returncode != 0
        or forwarding.get("IPAddress") != address
        or str(forwarding.get("portRange")) not in {"443", "443-443"}
        or forwarding.get("loadBalancingScheme") != "EXTERNAL_MANAGED"
        or not str(forwarding.get("target", "")).endswith(
            f"/targetHttpsProxies/{prefix}-https-proxy"
        )
    ):
        raise RuntimeError("canonical HTTPS forwarding rule differs")

    proxy_result = run(
        [
            "gcloud",
            "--quiet",
            "compute",
            "target-https-proxies",
            "describe",
            f"{prefix}-https-proxy",
            f"--project={project}",
            "--global",
            "--format=json(urlMap,certificateMap,sslCertificates)",
        ],
        timeout=120,
    )
    try:
        proxy = json.loads(proxy_result.stdout)
    except json.JSONDecodeError as exc:
        raise RuntimeError("canonical HTTPS proxy is malformed") from exc
    if (
        proxy_result.returncode != 0
        or not str(proxy.get("urlMap", "")).endswith(f"/urlMaps/{prefix}-url-map")
        or not str(proxy.get("certificateMap", "")).endswith(
            f"/certificateMaps/{CANONICAL_GCP_CERTIFICATE_MAP}"
        )
    ):
        raise RuntimeError("canonical HTTPS proxy URL/certificate map differs")

    url_map_result = run(
        [
            "gcloud",
            "--quiet",
            "compute",
            "url-maps",
            "describe",
            f"{prefix}-url-map",
            f"--project={project}",
            "--global",
            "--format=json(defaultService,hostRules,pathMatchers)",
        ],
        timeout=120,
    )
    try:
        url_map = json.loads(url_map_result.stdout)
    except json.JSONDecodeError as exc:
        raise RuntimeError("canonical URL map is malformed") from exc
    host_rules = url_map.get("hostRules", [])
    matchers = url_map.get("pathMatchers", [])
    rules = {
        (tuple(item.get("hosts", [])), item.get("pathMatcher"))
        for item in host_rules
        if isinstance(item, dict) and isinstance(item.get("hosts"), list)
    }
    defaults = {
        item.get("name"): str(item.get("defaultService", ""))
        for item in matchers
        if isinstance(item, dict)
    }
    if (
        url_map_result.returncode != 0
        or not str(url_map.get("defaultService", "")).endswith(
            f"/backendServices/{prefix}-console-backend"
        )
        or (("*",), "console") not in rules
        or ((public_workspace_domain,), "workspace") not in rules
        or not defaults.get("console", "").endswith(
            f"/backendServices/{prefix}-console-backend"
        )
        or not defaults.get("workspace", "").endswith(
            f"/backendServices/{prefix}-workspace-backend"
        )
    ):
        raise RuntimeError("canonical URL map host routing differs")

    for domain in domains:
        resolved = _resolve_public_addresses(domain)
        if resolved != {address}:
            raise RuntimeError("canonical public DNS does not point only to GCP")
        _probe_https_health(domain, address)


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
    *,
    terraform_dir: Path,
    var_file: Path,
    artifact: ArtifactRef,
    source_ref: str,
    lakehouse_bucket: str | None = None,
) -> str:
    """Bind the reviewed, unapplied render to one exact immutable artifact."""
    startup = render_terraform_startup_script(
        terraform_dir=terraform_dir, var_file=var_file
    )
    match = re.search(r"(?m)^STARTUP_CONFIG_BASE64='([A-Za-z0-9+/=]+)'$", startup)
    if match is None:
        raise RuntimeError("Terraform startup render lacks its encoded contract")
    try:
        config = json.loads(base64.b64decode(match.group(1), validate=True))
    except (ValueError, json.JSONDecodeError) as exc:
        raise RuntimeError("Terraform startup contract is malformed") from exc
    expected_source = {
        "bucket": artifact.uri.removeprefix("gs://").split("/", 1)[0],
        "object": artifact.uri.removeprefix("gs://").split("/", 1)[1],
        "ref": source_ref,
        "generation": artifact.generation,
        "size_bytes": artifact.size_bytes,
        "archive_sha256": artifact.sha256,
    }
    if not isinstance(config, dict) or config.get("source") != expected_source:
        raise RuntimeError("Terraform startup render is not byte-bound to the artifact")
    if (
        lakehouse_bucket is not None
        and config.get("lakehouse_bucket") != lakehouse_bucket
    ):
        raise RuntimeError(
            "Terraform startup render targets a different lakehouse bucket"
        )
    return hashlib.sha256(startup.encode()).hexdigest()


def validate_reviewed_terraform_inputs(
    *,
    terraform_dir: Path,
    var_file: Path,
    source_ref: str,
    artifact: ArtifactRef,
    project_id: str,
    project_number: str,
    billing_account_id: str,
    public_console_domain: str,
    public_workspace_domain: str,
) -> None:
    """Require explicit release-critical inputs without blessing full-stack drift."""
    if not var_file.is_file():
        raise ValueError("GCP_TERRAFORM_VAR_FILE must be a reviewed existing file")
    reviewed_var_file = require_private_reviewed_var_file(var_file)
    text = reviewed_var_file.read_text(encoding="utf-8")

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
        "source_bucket": artifact.uri.removeprefix("gs://").split("/", 1)[0],
        "source_object": f"deploy-artifacts/{source_ref}/repo.tar.gz",
        "source_sha": source_ref,
        "source_generation": artifact.generation,
        "source_size_bytes": str(artifact.size_bytes),
        "source_archive_sha256": artifact.sha256,
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
    source_bucket = artifact.uri.removeprefix("gs://").split("/", 1)[0]
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
    if resolved != (REPO / "infra/terraform-gcp").resolve():
        raise ValueError("Terraform directory must be the canonical repository module")


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
    evidence_dir = _secure_evidence_dir(evidence_dir)
    local_backup = evidence_dir / "prior-startup.sh"
    _atomic_private_text(local_backup, snapshot.items["startup-script"])
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
    _atomic_private_text(
        evidence_dir / "summary.json",
        json.dumps(_redact_json(payload), indent=2, sort_keys=True) + "\n",
    )


def command_adopt_startup_metadata(args: argparse.Namespace) -> int:
    require_target(args)
    if not args.confirm and os.environ.get("CONFIRM_GCP_STARTUP_ADOPTION") != "1":
        raise SystemExit("startup metadata adoption requires explicit confirmation")
    if not SHA256_RE.fullmatch(args.expected_old_sha256):
        raise ValueError("reviewed old startup SHA-256 is invalid")
    if not FULL_SHA_RE.fullmatch(args.current_live_ref):
        raise ValueError("current live runtime ref must be one exact SHA")
    if args.source_ref != args.helper_ref:
        raise ValueError(
            "startup source must be the exact candidate helper artifact; "
            "legacy live source archives are not required"
        )
    validate_candidate_main(args.helper_ref)
    require_local_file_at_ref(args.helper_ref, "scripts/gcp/backup.sh")
    require_local_file_at_ref(
        args.helper_ref, "scripts/gcp/finalize-startup-adoption.sh"
    )
    require_startup_files_at_ref(args.helper_ref)
    require_exact_terraform_tree_at_ref(args.helper_ref, args.terraform_dir)
    artifact = artifact_from_inputs(
        bucket=args.artifact_bucket,
        deploy_ref=args.source_ref,
        generation=args.source_generation,
        size_bytes=args.source_size_bytes,
        sha256=args.source_archive_sha256,
    )
    validate_reviewed_terraform_inputs(
        terraform_dir=args.terraform_dir,
        var_file=args.terraform_var_file,
        source_ref=args.source_ref,
        artifact=artifact,
        project_id=args.project,
        project_number=args.project_number,
        billing_account_id=args.billing_account_id,
        public_console_domain=args.public_console_domain,
        public_workspace_domain=args.public_workspace_domain,
    )
    validate_reviewed_target_identity(
        var_file=args.terraform_var_file,
        project=args.project,
        project_number=args.project_number,
        zone=args.zone,
        instance=args.instance,
        environment=args.environment,
    )
    validate_live_canonical_target(
        project=args.project,
        project_number=args.project_number,
        zone=args.zone,
        instance=args.instance,
        instance_id=args.instance_id,
        environment=args.environment,
    )
    validate_live_public_edge(
        project=args.project,
        environment=args.environment,
        public_console_domain=args.public_console_domain,
        public_workspace_domain=args.public_workspace_domain,
    )
    validate_artifact_bucket_identity(
        project=args.project,
        project_number=args.project_number,
        bucket=args.artifact_bucket,
        var_file=args.terraform_var_file,
    )
    validate_reviewed_lakehouse_identity(
        var_file=args.terraform_var_file,
        project_number=args.project_number,
        environment=args.environment,
        lakehouse_bucket=args.lakehouse_bucket,
    )
    backup_controls = validate_backup_bucket_controls(
        project=args.project,
        project_number=args.project_number,
        environment=args.environment,
        bucket=args.backup_bucket,
    )
    new_startup = render_terraform_startup_script(
        terraform_dir=args.terraform_dir, var_file=args.terraform_var_file
    )
    validate_terraform_source_contract(
        terraform_dir=args.terraform_dir,
        var_file=args.terraform_var_file,
        artifact=artifact,
        source_ref=args.source_ref,
        lakehouse_bucket=args.lakehouse_bucket,
    )
    new_sha256 = hashlib.sha256(new_startup.encode()).hexdigest()
    if args.helper_ref == args.source_ref:
        helper_artifact = artifact
    else:
        with tempfile.TemporaryDirectory(prefix="omega-gcp-adoption-helper-") as temp:
            helper_archive = Path(temp) / "repo.tar.gz"
            helper_sha = create_archive(args.helper_ref, helper_archive)
            helper_artifact = upload_artifact_immutable(
                helper_archive, args.artifact_bucket, args.helper_ref, helper_sha
            )
    adoption_id = f"{utc_stamp()}-startup-adoption"
    adoption_result = remote_script(
        project=args.project,
        zone=args.zone,
        instance=args.instance,
        script=REMOTE_ROOT / "backup.sh",
        arguments=[
            args.backup_bucket,
            adoption_id,
            args.compose_project,
            args.helper_ref,
            helper_artifact.uri,
            helper_artifact.generation,
            str(helper_artifact.size_bytes),
            helper_artifact.sha256,
            args.current_live_ref,
            str(backup_controls["policy_sha256"]),
            str(backup_controls["location"]),
            args.project,
            "adopt-runtime",
        ],
        timeout=args.timeout_seconds,
    )
    adoption_checks, adoption_payload = parse_remote(
        adoption_result.stdout,
        "OMEGA_GCP_BACKUP_CHECK",
        "OMEGA_GCP_BACKUP_JSON",
    )
    if (
        adoption_result.returncode != 0
        or not adoption_checks
        or any(item.get("status") != "PASS" for item in adoption_checks)
        or adoption_payload.get("status") != "PASS"
        or adoption_payload.get("operation") != "adopt-runtime"
        or adoption_payload.get("adoption_id") != adoption_id
        or adoption_payload.get("current_ref") != args.current_live_ref
        or adoption_payload.get("helper_ref") != args.helper_ref
        or adoption_payload.get("metadata_cas_pending") is not True
    ):
        raise RuntimeError(
            "atomic runtime-state adoption failed before startup metadata CAS"
        )
    validate_live_canonical_target(
        project=args.project,
        project_number=args.project_number,
        zone=args.zone,
        instance=args.instance,
        instance_id=args.instance_id,
        environment=args.environment,
    )
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
        "runtime_state_adoption": adoption_payload,
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
    validate_live_canonical_target(
        project=args.project,
        project_number=args.project_number,
        zone=args.zone,
        instance=args.instance,
        instance_id=args.instance_id,
        environment=args.environment,
    )
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
    finalization = remote_script(
        project=args.project,
        zone=args.zone,
        instance=args.instance,
        script=REMOTE_ROOT / "finalize-startup-adoption.sh",
        arguments=[adoption_id, args.current_live_ref, args.helper_ref, new_sha256],
        timeout=args.timeout_seconds,
    )
    final_checks, final_payload = parse_remote(
        finalization.stdout,
        "OMEGA_GCP_ADOPTION_FINALIZE_CHECK",
        "OMEGA_GCP_ADOPTION_FINALIZE_JSON",
    )
    if (
        finalization.returncode != 0
        or not final_checks
        or any(item.get("status") != "PASS" for item in final_checks)
        or final_payload.get("status") != "PASS"
        or final_payload.get("operation") != "startup-adoption-finalize"
    ):
        evidence["status"] = "CAS_APPLIED_FINALIZATION_FENCED"
        _write_startup_evidence(evidence_dir, evidence)
        raise RuntimeError(
            "startup metadata CAS succeeded but durable remote finalization failed; "
            "operation marker remains fenced"
        )
    evidence["remote_finalization"] = final_payload
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
    validate_reviewed_target_identity(
        var_file=args.terraform_var_file,
        project=args.project,
        project_number=args.project_number,
        zone=args.zone,
        instance=args.instance,
        environment=args.environment,
    )
    validate_live_canonical_target(
        project=args.project,
        project_number=args.project_number,
        zone=args.zone,
        instance=args.instance,
        instance_id=args.instance_id,
        environment=args.environment,
    )
    validate_live_public_edge(
        project=args.project,
        environment=args.environment,
        public_console_domain=args.public_console_domain,
        public_workspace_domain=args.public_workspace_domain,
    )
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
    validate_live_canonical_target(
        project=args.project,
        project_number=args.project_number,
        zone=args.zone,
        instance=args.instance,
        instance_id=args.instance_id,
        environment=args.environment,
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


APP_HOST_SECRET_NAMES = (
    "control_room_evidence_signing_key_id",
    "control_room_evidence_signing_key",
    "control_room_evidence_signing_previous_keys",
    "gcs_hmac_access_key_id",
    "gcs_hmac_secret_access_key",
)
GHCR_GRANTS_ACTIONS = {
    "google_secret_manager_secret.ghcr_pull_credentials": ["create"],
    "google_secret_manager_secret_iam_member.app_ghcr_pull_credentials_access": [
        "create"
    ],
    **{
        f'google_secret_manager_secret_iam_member.app_runtime_secret_access["{name}"]': [
            "create"
        ]
        for name in APP_HOST_SECRET_NAMES
    },
}
GHCR_REVOKE_ACTIONS = {
    'google_project_iam_member.app_project_roles["roles/secretmanager.secretAccessor"]': [
        "delete"
    ]
}
# Kept as a public module constant for existing operator tooling. Stage A is
# deliberately seven creates; Stage B is represented separately above.
GHCR_PLAN_ACTIONS = GHCR_GRANTS_ACTIONS
BACKUP_STORAGE_PLAN_ACTIONS = {
    "google_storage_bucket.release_backups": ["create"],
    "google_project_iam_custom_role.release_backup_writer": ["create"],
    "google_storage_bucket_iam_member.app_release_backup": ["create"],
}


def validate_backup_storage_saved_plan(
    *,
    terraform_dir: Path,
    plan_path: Path,
    project: str,
    project_number: str,
    environment: str,
    region: str,
) -> dict[str, Any]:
    result = run(
        [
            terraform_binary(),
            f"-chdir={terraform_dir.resolve()}",
            "show",
            "-json",
            str(plan_path.resolve()),
        ],
        timeout=300,
    )
    if result.returncode != 0:
        raise RuntimeError("cannot inspect saved release-backup plan")
    try:
        payload = json.loads(result.stdout)
        changes = payload["resource_changes"]
    except (KeyError, TypeError, json.JSONDecodeError) as exc:
        raise RuntimeError("saved release-backup plan JSON is malformed") from exc
    actual: dict[str, list[str]] = {}
    after_by_address: dict[str, dict[str, Any]] = {}
    for resource in changes:
        change = resource.get("change", {})
        actions = change.get("actions")
        if actions == ["no-op"]:
            continue
        address = resource.get("address")
        after = change.get("after")
        if (
            not isinstance(address, str)
            or not isinstance(actions, list)
            or change.get("before") is not None
            or not isinstance(after, dict)
            or change.get("replace_paths")
        ):
            raise RuntimeError("release-backup plan is not a pure exact create")
        actual[address] = actions
        after_by_address[address] = after
    if actual != BACKUP_STORAGE_PLAN_ACTIONS:
        raise RuntimeError(
            "release-backup plan actions differ: " + json.dumps(actual, sort_keys=True)
        )
    output_changes = payload.get("output_changes") or {}
    unexpected_outputs = {
        name: value.get("actions")
        for name, value in output_changes.items()
        if name != "release_backup_bucket" and value.get("actions") != ["no-op"]
    }
    if unexpected_outputs:
        raise RuntimeError("release-backup target plan contains unrelated output drift")

    bucket_name = f"omega-{environment}-release-backups-{project_number}"
    bucket = after_by_address["google_storage_bucket.release_backups"]
    versioning = bucket.get("versioning") or []
    soft_delete = bucket.get("soft_delete_policy") or []
    retention = bucket.get("retention_policy") or []
    if (
        bucket.get("name") != bucket_name
        or bucket.get("project") != project
        or str(bucket.get("location", "")).upper() != region.upper()
        or bucket.get("uniform_bucket_level_access") is not True
        or bucket.get("public_access_prevention") != "enforced"
        or bucket.get("force_destroy") is not False
        or bucket.get("lifecycle_rule") not in (None, [])
        or len(versioning) != 1
        or versioning[0].get("enabled") is not True
        or len(soft_delete) != 1
        or int(soft_delete[0].get("retention_duration_seconds", -1)) != 2592000
        or len(retention) != 1
        or int(retention[0].get("retention_period", -1)) != 604800
        or retention[0].get("is_locked") is not False
    ):
        raise RuntimeError("release-backup bucket target differs from policy")
    custom = after_by_address["google_project_iam_custom_role.release_backup_writer"]
    if (
        custom.get("project") != project
        or custom.get("role_id") != "omegaReleaseBackupWriter"
        or set(custom.get("permissions", []))
        != {
            "storage.buckets.get",
            "storage.objects.create",
            "storage.objects.get",
        }
        or custom.get("deleted") is True
    ):
        raise RuntimeError("release-backup custom role target differs")
    binding = after_by_address["google_storage_bucket_iam_member.app_release_backup"]
    expected_member = (
        f"serviceAccount:omega-{environment}-app@{project}.iam.gserviceaccount.com"
    )
    if (
        binding.get("bucket") != bucket_name
        or binding.get("role") != f"projects/{project}/roles/omegaReleaseBackupWriter"
        or binding.get("member") != expected_member
        or binding.get("condition") not in (None, [])
    ):
        raise RuntimeError("release-backup IAM binding target differs")
    return payload


def validate_ghcr_saved_plan(
    *,
    terraform_dir: Path,
    plan_path: Path,
    project: str,
    environment: str,
    stage: str = "grants",
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
    if stage not in {"grants", "revoke"}:
        raise ValueError("IAM plan stage must be grants or revoke")
    expected_actions = GHCR_GRANTS_ACTIONS if stage == "grants" else GHCR_REVOKE_ACTIONS
    actual: dict[str, list[str]] = {}
    planned_after: dict[str, dict[str, Any] | None] = {}
    planned_before: dict[str, dict[str, Any] | None] = {}
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
        before = change.get("before")
        after = change.get("after")
        if stage == "grants" and (before is not None or not isinstance(after, dict)):
            raise RuntimeError("saved grants plan is not a pure create")
        if stage == "revoke" and (not isinstance(before, dict) or after is not None):
            raise RuntimeError("saved revoke plan is not one exact delete")
        actual[address] = actions
        planned_after[address] = after
        planned_before[address] = before
    if actual != expected_actions:
        raise RuntimeError(
            f"saved GHCR access plan actions differ: {json.dumps(actual, sort_keys=True)}"
        )
    output_changes = payload.get("output_changes") or {}
    if any(item.get("actions") != ["no-op"] for item in output_changes.values()):
        raise RuntimeError("saved GHCR access plan contains output drift")
    secret_id = f"omega-{environment}-ghcr_pull_credentials"
    member = f"serviceAccount:omega-{environment}-app@{project}.iam.gserviceaccount.com"
    exact_fields: dict[str, dict[str, str]] = {
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
        **{
            f'google_secret_manager_secret_iam_member.app_runtime_secret_access["{name}"]': {
                "project": project,
                "secret_id": f"omega-{environment}-{name}",
                "role": "roles/secretmanager.secretAccessor",
                "member": member,
            }
            for name in APP_HOST_SECRET_NAMES
        },
    }
    if stage == "revoke":
        exact_fields = {
            next(iter(GHCR_REVOKE_ACTIONS)): {
                "project": project,
                "role": "roles/secretmanager.secretAccessor",
                "member": member,
            }
        }
    selected = planned_after if stage == "grants" else planned_before
    for address, expected in exact_fields.items():
        after = selected[address]
        if after is None:
            raise RuntimeError(f"saved IAM plan lacks target state for {address}")
        if any(after.get(key) != value for key, value in expected.items()):
            raise RuntimeError(f"saved IAM plan target differs for {address}")
    return payload


def _require_plan_outside_repo(plan_path: Path) -> Path:
    resolved = plan_path.resolve()
    try:
        resolved.relative_to(REPO.resolve())
    except ValueError:
        pass
    else:
        raise ValueError("saved Terraform plans must remain outside the repository")
    if not resolved.parent.is_dir() or resolved.parent.is_symlink():
        raise ValueError("saved Terraform plan parent directory does not exist")
    parent = resolved.parent.stat()
    if parent.st_uid != os.geteuid() or parent.st_mode & 0o022:
        raise ValueError(
            "saved Terraform plan parent must be private and operator-owned"
        )
    return resolved


def _verify_private_plan(plan_path: Path, expected_sha256: str | None = None) -> str:
    info = plan_path.lstat()
    if not plan_path.is_file() or plan_path.is_symlink():
        raise ValueError("saved Terraform plan must be a regular non-symlink file")
    if info.st_uid != os.geteuid() or info.st_mode & 0o777 != 0o600:
        raise ValueError("saved Terraform plan must be operator-owned mode 0600")
    digest = sha256_file(plan_path)
    if expected_sha256 is not None and digest != expected_sha256:
        raise ValueError("saved Terraform plan SHA-256 differs")
    return digest


def command_plan_ghcr_access(args: argparse.Namespace) -> int:
    require_target(args)
    validate_candidate_main(args.helper_ref)
    require_startup_files_at_ref(args.helper_ref)
    require_exact_terraform_tree_at_ref(args.helper_ref, args.terraform_dir)
    artifact = artifact_from_inputs(
        bucket=args.artifact_bucket,
        deploy_ref=args.source_ref,
        generation=args.source_generation,
        size_bytes=args.source_size_bytes,
        sha256=args.source_archive_sha256,
    )
    validate_reviewed_terraform_inputs(
        terraform_dir=args.terraform_dir,
        var_file=args.terraform_var_file,
        source_ref=args.source_ref,
        artifact=artifact,
        project_id=args.project,
        project_number=args.project_number,
        billing_account_id=args.billing_account_id,
        public_console_domain=args.public_console_domain,
        public_workspace_domain=args.public_workspace_domain,
    )
    validate_reviewed_target_identity(
        var_file=args.terraform_var_file,
        project=args.project,
        project_number=args.project_number,
        zone=args.zone,
        instance=args.instance,
        environment=args.environment,
    )
    if args.stage == "revoke":
        validate_live_ghcr_access(project=args.project, environment=args.environment)
        if not _project_wide_secret_access_exists(
            project=args.project, environment=args.environment
        ):
            raise RuntimeError("project-wide Secret Manager grant is already absent")
    plan_path = _require_plan_outside_repo(args.plan)
    if plan_path.exists():
        raise ValueError("refusing to overwrite an existing saved Terraform plan")
    if args.stage == "grants":
        targets = [
            "google_secret_manager_secret.ghcr_pull_credentials",
            "google_secret_manager_secret_iam_member.app_ghcr_pull_credentials_access",
            *[
                f'google_secret_manager_secret_iam_member.app_runtime_secret_access["{name}"]'
                for name in APP_HOST_SECRET_NAMES
            ],
        ]
    else:
        targets = [next(iter(GHCR_REVOKE_ACTIONS))]
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
            *[f"-target={target}" for target in targets],
            f"-out={plan_path}",
        ],
        timeout=600,
    )
    if result.returncode != 2 or not plan_path.is_file():
        raise RuntimeError(
            "GHCR target plan did not produce exactly the required changes"
        )
    plan_path.chmod(0o600)
    _fsync_directory(plan_path.parent)
    plan_sha256 = _verify_private_plan(plan_path)
    validate_ghcr_saved_plan(
        terraform_dir=args.terraform_dir,
        plan_path=plan_path,
        project=args.project,
        environment=args.environment,
        stage=args.stage,
    )
    print(
        json.dumps(
            {
                "status": "PASS",
                "plan": str(plan_path),
                "stage": args.stage,
                "plan_sha256": plan_sha256,
                "actions": GHCR_GRANTS_ACTIONS
                if args.stage == "grants"
                else GHCR_REVOKE_ACTIONS,
                "delete": 0 if args.stage == "grants" else 1,
                "replace": 0,
            },
            sort_keys=True,
        )
    )
    return 0


def _project_wide_secret_access_exists(*, project: str, environment: str) -> bool:
    member = f"serviceAccount:omega-{environment}-app@{project}.iam.gserviceaccount.com"
    policy = run(
        [
            "gcloud",
            "--quiet",
            "projects",
            "get-iam-policy",
            project,
            "--format=json(bindings)",
        ],
        timeout=120,
    )
    if policy.returncode != 0:
        raise RuntimeError("project IAM readback failed")
    try:
        bindings = json.loads(policy.stdout).get("bindings", [])
    except (TypeError, json.JSONDecodeError) as exc:
        raise RuntimeError("project IAM readback is malformed") from exc
    return any(
        item.get("role") == "roles/secretmanager.secretAccessor"
        and member in item.get("members", [])
        for item in bindings
    )


def validate_live_ghcr_access(
    *, project: str, environment: str, require_project_wide_absent: bool = False
) -> None:
    if not re.fullmatch(r"[a-z][a-z0-9-]{0,30}", environment):
        raise ValueError("GCP environment is invalid")
    member = f"serviceAccount:omega-{environment}-app@{project}.iam.gserviceaccount.com"
    for name in (*APP_HOST_SECRET_NAMES, "ghcr_pull_credentials"):
        secret = f"omega-{environment}-{name}"
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
        if policy.returncode != 0:
            raise RuntimeError(f"resource IAM readback failed for {name}")
        try:
            bindings = json.loads(policy.stdout).get("bindings", [])
        except (TypeError, json.JSONDecodeError) as exc:
            raise RuntimeError("resource IAM readback is malformed") from exc
        matching = [
            item
            for item in bindings
            if item.get("role") == "roles/secretmanager.secretAccessor"
            and member in item.get("members", [])
        ]
        if len(matching) != 1:
            raise RuntimeError(f"resource-scoped host access differs for {name}")
    if require_project_wide_absent:
        if _project_wide_secret_access_exists(project=project, environment=environment):
            raise RuntimeError("project-wide Secret Manager access still exists")
        inventory_result = run(
            [
                "gcloud",
                "--quiet",
                "secrets",
                "list",
                f"--project={project}",
                "--format=json(name)",
            ],
            timeout=120,
        )
        try:
            inventory_payload = json.loads(inventory_result.stdout)
        except json.JSONDecodeError as exc:
            raise RuntimeError("Secret Manager inventory is malformed") from exc
        if inventory_result.returncode != 0 or not isinstance(inventory_payload, list):
            raise RuntimeError("Secret Manager inventory readback failed")
        secret_ids: set[str] = set()
        for item in inventory_payload:
            if not isinstance(item, dict) or not isinstance(item.get("name"), str):
                raise RuntimeError("Secret Manager inventory is malformed")
            secret_id = item["name"].rsplit("/", 1)[-1]
            if not re.fullmatch(r"[A-Za-z][A-Za-z0-9_-]{0,254}", secret_id):
                raise RuntimeError("Secret Manager inventory contains an invalid id")
            secret_ids.add(secret_id)
        allowed_ids = {
            f"omega-{environment}-{name}"
            for name in (*APP_HOST_SECRET_NAMES, "ghcr_pull_credentials")
        }
        if not allowed_ids.issubset(secret_ids):
            raise RuntimeError("required host Secret Manager inventory is incomplete")
        principal = f"omega-{environment}-app@{project}.iam.gserviceaccount.com"
        granted_count = 0
        denied_count = 0
        for secret_id in sorted(secret_ids):
            resource = (
                f"//secretmanager.googleapis.com/projects/{project}/secrets/{secret_id}"
            )
            troubleshoot = run(
                [
                    "gcloud",
                    "--quiet",
                    "policy-intelligence",
                    "troubleshoot-policy",
                    "iam",
                    resource,
                    f"--principal-email={principal}",
                    "--permission=secretmanager.versions.access",
                    "--format=json(access)",
                ],
                timeout=120,
            )
            try:
                access = json.loads(troubleshoot.stdout).get("access")
            except (AttributeError, json.JSONDecodeError) as exc:
                raise RuntimeError(
                    "effective Secret Manager permission response is malformed"
                ) from exc
            expected_access = "GRANTED" if secret_id in allowed_ids else "NOT_GRANTED"
            if troubleshoot.returncode != 0 or access != expected_access:
                raise RuntimeError(
                    "effective VM Secret Manager access differs from the allowlist"
                )
            if expected_access == "GRANTED":
                granted_count += 1
            else:
                denied_count += 1
        if granted_count != len(allowed_ids) or granted_count + denied_count != len(
            secret_ids
        ):
            raise RuntimeError("effective Secret Manager inventory proof is incomplete")


def validate_effective_least_privilege(
    *, project: str, zone: str, instance: str, environment: str, timeout: int
) -> None:
    validate_live_ghcr_access(
        project=project,
        environment=environment,
        require_project_wide_absent=True,
    )
    effective = remote_script(
        project=project,
        zone=zone,
        instance=instance,
        script=REMOTE_ROOT / "verify-secret-access.sh",
        arguments=[project, environment, "revoke"],
        timeout=timeout,
    )
    if effective.returncode != 0:
        raise RuntimeError("effective least-privilege Secret Manager gate failed")


def command_apply_ghcr_access(args: argparse.Namespace) -> int:
    require_target(args)
    if not args.confirm and os.environ.get("CONFIRM_GCP_GHCR_ACCESS_APPLY") != "1":
        raise SystemExit("GHCR access apply requires explicit confirmation")
    if not SHA256_RE.fullmatch(args.plan_sha256):
        raise ValueError("saved Terraform plan SHA-256 is invalid")
    validate_candidate_main(args.helper_ref)
    require_local_file_at_ref(args.helper_ref, "scripts/gcp/verify-secret-access.sh")
    require_local_file_at_ref(args.helper_ref, "scripts/gcp/metadata-firewall.sh")
    require_local_file_at_ref(args.helper_ref, "scripts/gcp/safe_io.py")
    require_exact_terraform_tree_at_ref(args.helper_ref, args.terraform_dir)
    plan_path = _require_plan_outside_repo(args.plan)
    if not plan_path.is_file():
        raise ValueError("saved GHCR access plan does not exist")
    _verify_private_plan(plan_path, args.plan_sha256)
    validate_ghcr_saved_plan(
        terraform_dir=args.terraform_dir,
        plan_path=plan_path,
        project=args.project,
        environment=args.environment,
        stage=args.stage,
    )
    validate_reviewed_target_identity(
        var_file=args.terraform_var_file,
        project=args.project,
        project_number=args.project_number,
        zone=args.zone,
        instance=args.instance,
        environment=args.environment,
    )
    validate_live_canonical_target(
        project=args.project,
        project_number=args.project_number,
        zone=args.zone,
        instance=args.instance,
        instance_id=args.instance_id,
        environment=args.environment,
    )
    firewall = remote_script(
        project=args.project,
        zone=args.zone,
        instance=args.instance,
        script=REMOTE_ROOT / "metadata-firewall.sh",
        arguments=["install-and-verify-container"],
        timeout=args.timeout_seconds,
    )
    if firewall.returncode != 0:
        raise RuntimeError("container metadata isolation must pass before IAM mutation")
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
    validate_live_ghcr_access(
        project=args.project,
        environment=args.environment,
        require_project_wide_absent=args.stage == "revoke",
    )
    validate_live_canonical_target(
        project=args.project,
        project_number=args.project_number,
        zone=args.zone,
        instance=args.instance,
        instance_id=args.instance_id,
        environment=args.environment,
    )
    effective = remote_script(
        project=args.project,
        zone=args.zone,
        instance=args.instance,
        script=REMOTE_ROOT / "verify-secret-access.sh",
        arguments=[args.project, args.environment, args.stage],
        timeout=args.timeout_seconds,
    )
    if effective.returncode != 0:
        raise RuntimeError("effective VM Secret Manager permission verification failed")
    print(
        json.dumps(
            {
                "status": "PASS",
                "stage": args.stage,
                "plan_sha256": args.plan_sha256,
                "actions": GHCR_GRANTS_ACTIONS
                if args.stage == "grants"
                else GHCR_REVOKE_ACTIONS,
                "readback": "six resource grants; project-wide grant absent after revoke",
                "effective_vm_permissions": "verified without reading secret values",
            },
            sort_keys=True,
        )
    )
    return 0


def command_plan_backup_storage(args: argparse.Namespace) -> int:
    require_target(args)
    validate_candidate_main(args.helper_ref)
    require_startup_files_at_ref(args.helper_ref)
    require_exact_terraform_tree_at_ref(args.helper_ref, args.terraform_dir)
    artifact = artifact_from_inputs(
        bucket=args.artifact_bucket,
        deploy_ref=args.source_ref,
        generation=args.source_generation,
        size_bytes=args.source_size_bytes,
        sha256=args.source_archive_sha256,
    )
    validate_reviewed_terraform_inputs(
        terraform_dir=args.terraform_dir,
        var_file=args.terraform_var_file,
        source_ref=args.source_ref,
        artifact=artifact,
        project_id=args.project,
        project_number=args.project_number,
        billing_account_id=args.billing_account_id,
        public_console_domain=args.public_console_domain,
        public_workspace_domain=args.public_workspace_domain,
    )
    validate_reviewed_target_identity(
        var_file=args.terraform_var_file,
        project=args.project,
        project_number=args.project_number,
        zone=args.zone,
        instance=args.instance,
        environment=args.environment,
    )
    region = _tfvar_value(args.terraform_var_file, "region")
    plan_path = _require_plan_outside_repo(args.plan)
    if plan_path.exists():
        raise ValueError("refusing to overwrite an existing saved Terraform plan")
    targets = list(BACKUP_STORAGE_PLAN_ACTIONS)
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
            *[f"-target={target}" for target in targets],
            f"-out={plan_path}",
        ],
        timeout=600,
    )
    if result.returncode != 2 or not plan_path.is_file():
        raise RuntimeError("release-backup target plan did not produce three creates")
    plan_path.chmod(0o600)
    _fsync_directory(plan_path.parent)
    plan_sha256 = _verify_private_plan(plan_path)
    validate_backup_storage_saved_plan(
        terraform_dir=args.terraform_dir,
        plan_path=plan_path,
        project=args.project,
        project_number=args.project_number,
        environment=args.environment,
        region=region,
    )
    print(
        json.dumps(
            {
                "status": "PASS",
                "plan": str(plan_path),
                "plan_sha256": plan_sha256,
                "actions": BACKUP_STORAGE_PLAN_ACTIONS,
                "create": 3,
                "update": 0,
                "delete": 0,
                "replace": 0,
            },
            sort_keys=True,
        )
    )
    return 0


def command_apply_backup_storage(args: argparse.Namespace) -> int:
    require_target(args)
    if not args.confirm and os.environ.get("CONFIRM_GCP_BACKUP_STORAGE_APPLY") != "1":
        raise SystemExit("release-backup plan apply requires explicit confirmation")
    if not SHA256_RE.fullmatch(args.plan_sha256):
        raise ValueError("saved release-backup plan SHA-256 is invalid")
    validate_candidate_main(args.helper_ref)
    require_exact_terraform_tree_at_ref(args.helper_ref, args.terraform_dir)
    plan_path = _require_plan_outside_repo(args.plan)
    if not plan_path.is_file():
        raise ValueError("saved release-backup plan does not exist")
    _verify_private_plan(plan_path, args.plan_sha256)
    validate_backup_storage_saved_plan(
        terraform_dir=args.terraform_dir,
        plan_path=plan_path,
        project=args.project,
        project_number=args.project_number,
        environment=args.environment,
        region=args.region,
    )
    validate_reviewed_target_identity(
        var_file=args.terraform_var_file,
        project=args.project,
        project_number=args.project_number,
        zone=args.zone,
        instance=args.instance,
        environment=args.environment,
    )
    validate_live_canonical_target(
        project=args.project,
        project_number=args.project_number,
        zone=args.zone,
        instance=args.instance,
        instance_id=args.instance_id,
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
        raise RuntimeError("saved release-backup target plan apply failed")
    bucket = f"omega-{args.environment}-release-backups-{args.project_number}"
    controls = validate_backup_bucket_controls(
        project=args.project,
        project_number=args.project_number,
        environment=args.environment,
        bucket=bucket,
    )
    validate_live_canonical_target(
        project=args.project,
        project_number=args.project_number,
        zone=args.zone,
        instance=args.instance,
        instance_id=args.instance_id,
        environment=args.environment,
    )
    print(
        json.dumps(
            {
                "status": "PASS",
                "plan_sha256": args.plan_sha256,
                "bucket": bucket,
                "policy_sha256": controls["policy_sha256"],
                "actions": BACKUP_STORAGE_PLAN_ACTIONS,
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
    require_canonical_ghcr_owner(args.ghcr_owner)
    candidate_inputs = (args.candidate_workflow_run_id, args.candidate_workflow_run_attempt)
    rollback_inputs = (
        args.rollback_runtime_images_uri,
        args.rollback_runtime_images_generation,
        args.rollback_runtime_images_size_bytes,
        args.rollback_runtime_images_sha256,
        args.rollback_backup_manifest_uri,
        args.rollback_backup_manifest_generation,
        args.rollback_backup_manifest_size_bytes,
        args.rollback_backup_manifest_sha256,
    )
    legacy_identity: LegacyRollbackIdentity | None = None
    controller_authority: dict[str, Any]
    if any(rollback_inputs):
        validate_candidate_main(args.helper_ref)
        legacy_identity = validate_legacy_rollback_identity(
            target_tag=args.target_tag,
            runtime_source_ref=args.target_ref,
            helper_ref=args.helper_ref,
            purpose=args.purpose,
        )
        target_version, target_kind = legacy_identity.version, "published"
    else:
        target_version, target_kind = validate_image_target_identity(
            target_tag=args.target_tag,
            target_ref=args.target_ref,
            helper_ref=args.helper_ref,
            purpose=args.purpose,
        )
    authority_arguments: list[str]
    if target_kind == "candidate":
        if any(rollback_inputs):
            raise SystemExit("candidate preflight cannot use rollback image authority")
        candidate_authority = validate_candidate_workflow_authority(
            source_sha=args.target_ref,
            version=target_version,
            run_id=args.candidate_workflow_run_id,
            run_attempt=args.candidate_workflow_run_attempt,
        )
        authority_arguments = [
            candidate_authority.manifest_digest,
            candidate_authority.run_id,
            candidate_authority.run_attempt,
            candidate_authority.payload_sha256,
        ]
        controller_authority = {
            "mode": "candidate",
            "manifest_digest": candidate_authority.manifest_digest,
            "workflow_run_id": candidate_authority.run_id,
            "workflow_run_attempt": candidate_authority.run_attempt,
            "workflow_artifact_id": candidate_authority.artifact_id,
        }
    elif any(rollback_inputs):
        if args.purpose != "rollback" or any(candidate_inputs):
            raise SystemExit(
                "legacy rollback authority is only valid for rollback preflight"
            )
        if not all(rollback_inputs):
            raise SystemExit("legacy rollback runtime image authority is incomplete")
        expected_prefix = f"gs://{args.backup_bucket}/_omega_backups/"
        runtime_prefix = args.rollback_runtime_images_uri.removesuffix(
            "/runtime-images.json"
        )
        manifest_prefix = args.rollback_backup_manifest_uri.removesuffix(
            "/manifest.json"
        )
        if (
            not args.backup_bucket
            or not args.rollback_runtime_images_uri.startswith(expected_prefix)
            or re.fullmatch(
                rf"{re.escape(expected_prefix)}[^/]+/runtime-images\.json",
                args.rollback_runtime_images_uri,
            )
            is None
            or re.fullmatch(
                r"[1-9][0-9]*", args.rollback_runtime_images_generation
            )
            is None
            or re.fullmatch(
                r"[1-9][0-9]*", args.rollback_runtime_images_size_bytes
            )
            is None
            or SHA256_RE.fullmatch(args.rollback_runtime_images_sha256) is None
            or not args.rollback_backup_manifest_uri.startswith(expected_prefix)
            or re.fullmatch(
                rf"{re.escape(expected_prefix)}[^/]+/manifest\.json",
                args.rollback_backup_manifest_uri,
            )
            is None
            or re.fullmatch(
                r"[1-9][0-9]*", args.rollback_backup_manifest_generation
            )
            is None
            or re.fullmatch(
                r"[1-9][0-9]*", args.rollback_backup_manifest_size_bytes
            )
            is None
            or SHA256_RE.fullmatch(args.rollback_backup_manifest_sha256) is None
            or runtime_prefix != manifest_prefix
        ):
            raise SystemExit(
                "legacy rollback requires one exact canonical backup runtime inventory"
            )
        validate_backup_bucket_controls(
            project=args.project,
            project_number=args.project_number,
            environment=args.environment,
            bucket=args.backup_bucket,
        )
        assert legacy_identity is not None
        authority_arguments = [*rollback_inputs, legacy_identity.tag_commit]
        controller_authority = {
            "mode": "legacy-rollback",
            "legacy_tag": args.target_tag,
            "legacy_tag_object_sha": legacy_identity.tag_object_sha,
            "legacy_tag_commit": legacy_identity.tag_commit,
            "runtime_source_ref": legacy_identity.runtime_source_ref,
            "version": legacy_identity.version,
        }
    else:
        if any(candidate_inputs):
            raise SystemExit("published preflight cannot use candidate workflow inputs")
        release_identity = validate_bound_release_identity(
            args.target_tag,
            args.target_ref,
            require_main=args.purpose == "release",
        )
        if release_identity.version != target_version:
            raise RuntimeError("published preflight release identity differs")
        authority_arguments = [
            release_identity.manifest_digest,
            release_identity.tag_object_sha,
        ]
        controller_authority = {
            "mode": "published",
            "manifest_digest": release_identity.manifest_digest,
            "tag_object_sha": release_identity.tag_object_sha,
        }
    validate_candidate_main(args.helper_ref)
    require_local_file_at_ref(args.helper_ref, "scripts/gcp/image-preflight.sh")
    require_local_file_at_ref(args.helper_ref, "scripts/gcp/safe_io.py")
    require_local_file_at_ref(args.helper_ref, "scripts/gcp/verify-secret-access.sh")
    validate_live_canonical_target(
        project=args.project,
        project_number=args.project_number,
        zone=args.zone,
        instance=args.instance,
        instance_id=args.instance_id,
        environment=args.environment,
    )
    validate_live_public_edge(
        project=args.project,
        environment=args.environment,
        public_console_domain=args.public_console_domain,
        public_workspace_domain=args.public_workspace_domain,
    )
    validate_artifact_bucket_identity(
        project=args.project,
        project_number=args.project_number,
        bucket=args.artifact_bucket,
    )
    validate_effective_least_privilege(
        project=args.project,
        zone=args.zone,
        instance=args.instance,
        environment=args.environment,
        timeout=args.timeout_seconds,
    )
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
            helper_uri.uri,
            helper_uri.generation,
            str(helper_uri.size_bytes),
            helper_uri.sha256,
            args.target_tag,
            args.target_ref,
            target_version,
            target_kind,
            args.ghcr_owner,
            args.environment,
            args.ghcr_secret_version,
            args.purpose,
            *authority_arguments,
        ],
        timeout=args.timeout_seconds,
    )
    checks, payload = parse_remote(
        result.stdout,
        "OMEGA_GCP_IMAGE_PREFLIGHT_CHECK",
        "OMEGA_GCP_IMAGE_PREFLIGHT_JSON",
    )
    payload = {**payload, "controller_authority": controller_authority}
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
    """Publish the immutable candidate archive after all live target gates."""
    require_target(args)
    validate_candidate_main(args.candidate_ref)
    require_local_file_at_ref(args.candidate_ref, "scripts/gcp/backup.sh")
    if not FULL_SHA_RE.fullmatch(args.current_live_ref):
        raise SystemExit("GCP_CURRENT_LIVE_REF must be one full lowercase SHA")
    validate_reviewed_target_identity(
        var_file=args.terraform_var_file,
        project=args.project,
        project_number=args.project_number,
        zone=args.zone,
        instance=args.instance,
        environment=args.environment,
    )
    validate_live_canonical_target(
        project=args.project,
        project_number=args.project_number,
        zone=args.zone,
        instance=args.instance,
        instance_id=args.instance_id,
        environment=args.environment,
    )
    validate_live_public_edge(
        project=args.project,
        environment=args.environment,
        public_console_domain=args.public_console_domain,
        public_workspace_domain=args.public_workspace_domain,
    )
    validate_artifact_bucket_identity(
        project=args.project,
        project_number=args.project_number,
        bucket=args.artifact_bucket,
        var_file=args.terraform_var_file,
    )
    with tempfile.TemporaryDirectory(prefix="omega-gcp-prepare-artifacts-") as temp:
        archive = Path(temp) / "candidate.tar.gz"
        artifact_sha = create_archive(args.candidate_ref, archive)
        artifact = upload_artifact_immutable(
            archive, args.artifact_bucket, args.candidate_ref, artifact_sha
        )
        candidate = {
            "deploy_ref": args.candidate_ref,
            **artifact.as_dict(),
        }
    print(
        json.dumps(
            {
                "status": "PASS",
                "candidate": candidate,
                "live_runtime": {
                    "deploy_ref": args.current_live_ref,
                    "source_artifact_required": False,
                },
                "secrets_included": False,
            },
            indent=2,
            sort_keys=True,
        )
    )
    return 0


def validate_backup_result_payload(
    payload: dict[str, Any],
    *,
    purpose: str,
    backup_id: str,
    source_ref: str,
    source_version: str,
    backup_bucket: str,
    lakehouse_bucket: str,
) -> None:
    exact_keys = {
        "status",
        "purpose",
        "backup_id",
        "source_ref",
        "source_version",
        "manifest_uri",
        "manifest_sha256",
        "manifest_generation",
        "manifest_size_bytes",
        "runtime_images_uri",
        "runtime_images_sha256",
        "runtime_images_generation",
        "runtime_images_size_bytes",
        "object_count",
        "lakehouse_bucket",
        "backup_bucket",
    }
    prefix = f"gs://{backup_bucket}/_omega_backups/{backup_id}"
    if (
        set(payload) != exact_keys
        or payload.get("status") != "PASS"
        or payload.get("purpose") != purpose
        or payload.get("backup_id") != backup_id
        or payload.get("source_ref") != source_ref
        or payload.get("source_version") != source_version
        or payload.get("manifest_uri") != f"{prefix}/manifest.json"
        or payload.get("runtime_images_uri") != f"{prefix}/runtime-images.json"
        or payload.get("backup_bucket") != backup_bucket
        or payload.get("lakehouse_bucket") != lakehouse_bucket
        or SHA256_RE.fullmatch(str(payload.get("manifest_sha256", ""))) is None
        or SHA256_RE.fullmatch(str(payload.get("runtime_images_sha256", "")))
        is None
        or re.fullmatch(
            r"[1-9][0-9]*", str(payload.get("manifest_generation", ""))
        )
        is None
        or re.fullmatch(
            r"[1-9][0-9]*", str(payload.get("runtime_images_generation", ""))
        )
        is None
        or not isinstance(payload.get("manifest_size_bytes"), int)
        or payload["manifest_size_bytes"] <= 0
        or not isinstance(payload.get("runtime_images_size_bytes"), int)
        or payload["runtime_images_size_bytes"] <= 0
        or not isinstance(payload.get("object_count"), int)
        or payload["object_count"] <= 0
    ):
        raise ValueError("remote backup result is not exact source/manifest authority")


def command_backup(args: argparse.Namespace) -> int:
    require_target(args)
    if not args.confirm and os.environ.get("CONFIRM_GCP_BACKUP") != "1":
        raise SystemExit("backup requires --confirm or CONFIRM_GCP_BACKUP=1")
    backup_id = args.backup_id or f"{utc_stamp()}-{args.backup_phase}"
    if args.backup_phase == "rollback-baseline":
        validate_unpublished_release_identity(args.release_tag, args.candidate_ref)
    else:
        validate_bound_release_identity(args.release_tag, args.candidate_ref)
    validate_current_live_source(args.current_live_ref, args.current_live_version)
    require_exact_terraform_tree_at_ref(args.candidate_ref, args.terraform_dir)
    validate_reviewed_target_identity(
        var_file=args.terraform_var_file,
        project=args.project,
        project_number=args.project_number,
        zone=args.zone,
        instance=args.instance,
        environment=args.environment,
    )
    validate_live_canonical_target(
        project=args.project,
        project_number=args.project_number,
        zone=args.zone,
        instance=args.instance,
        instance_id=args.instance_id,
        environment=args.environment,
    )
    validate_live_public_edge(
        project=args.project,
        environment=args.environment,
        public_console_domain=args.public_console_domain,
        public_workspace_domain=args.public_workspace_domain,
    )
    validate_artifact_bucket_identity(
        project=args.project,
        project_number=args.project_number,
        bucket=args.artifact_bucket,
        var_file=args.terraform_var_file,
    )
    backup_controls = validate_backup_bucket_controls(
        project=args.project,
        project_number=args.project_number,
        environment=args.environment,
        bucket=args.backup_bucket,
    )
    validate_reviewed_lakehouse_identity(
        var_file=args.terraform_var_file,
        project_number=args.project_number,
        environment=args.environment,
        lakehouse_bucket=args.lakehouse_bucket,
    )
    validate_transfer_fence(
        project=args.project,
        project_number=args.project_number,
        lakehouse_bucket=args.lakehouse_bucket,
    )
    routing_raw, _routing = read_private_external_routing_scheduler_attestation(
        args.external_routing_scheduler_attestation,
        source_sha=args.candidate_ref,
        instance_id=args.instance_id,
        project=args.project,
        environment=args.environment,
        console_domain=args.public_console_domain,
        workspace_domain=args.public_workspace_domain,
    )
    require_zero_aws_writer_gate(_routing, operation="GCP pre-deploy backup")
    routing_attestation = upload_external_routing_scheduler_attestation_immutable(
        raw=routing_raw,
        bucket=args.artifact_bucket,
        source_sha=args.candidate_ref,
    )
    with tempfile.TemporaryDirectory(prefix="omega-gcp-backup-candidate-") as temp:
        archive = Path(temp) / "repo.tar.gz"
        artifact_sha = create_archive(args.candidate_ref, archive)
        artifact = upload_artifact_immutable(
            archive, args.artifact_bucket, args.candidate_ref, artifact_sha
        )
    validate_reviewed_terraform_inputs(
        terraform_dir=args.terraform_dir,
        var_file=args.terraform_var_file,
        source_ref=args.candidate_ref,
        artifact=artifact,
        project_id=args.project,
        project_number=args.project_number,
        billing_account_id=args.billing_account_id,
        public_console_domain=args.public_console_domain,
        public_workspace_domain=args.public_workspace_domain,
    )
    startup_sha = validate_terraform_source_contract(
        terraform_dir=args.terraform_dir,
        var_file=args.terraform_var_file,
        source_ref=args.candidate_ref,
        artifact=artifact,
        lakehouse_bucket=args.lakehouse_bucket,
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
            args.backup_bucket,
            backup_id,
            args.compose_project,
            args.candidate_ref,
            artifact.uri,
            artifact.generation,
            str(artifact.size_bytes),
            artifact.sha256,
            args.current_live_ref,
            str(backup_controls["policy_sha256"]),
            str(backup_controls["location"]),
            args.project,
            "backup",
            args.current_live_version,
            args.backup_phase,
        ],
        timeout=args.timeout_seconds,
    )
    checks, payload = parse_remote(
        result.stdout, "OMEGA_GCP_BACKUP_CHECK", "OMEGA_GCP_BACKUP_JSON"
    )
    try:
        validate_backup_result_payload(
            payload,
            purpose=args.backup_phase,
            backup_id=backup_id,
            source_ref=args.current_live_ref,
            source_version=args.current_live_version,
            backup_bucket=args.backup_bucket,
            lakehouse_bucket=args.lakehouse_bucket,
        )
    except ValueError as exc:
        checks.append(
            {
                "name": "exact backup result authority",
                "status": "FAIL",
                "evidence": str(exc),
            }
        )
        payload = {**payload, "status": "FAIL"}
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
            {
                "status": status,
                "backup_phase": args.backup_phase,
                "evidence_dir": str(evidence),
                "external_routing_scheduler_attestation": (
                    routing_attestation.as_dict()
                ),
                **payload,
            },
            indent=2,
        )
    )
    return 0 if status == "PASS" else 1


def command_reconcile_pipeline_runs(args: argparse.Namespace) -> int:
    """Apply one backup-bound, all-or-nothing stale-run transition manifest."""
    require_target(args)
    if (
        not args.confirm
        and os.environ.get("CONFIRM_GCP_PIPELINE_RUN_RECONCILIATION") != "1"
    ):
        raise SystemExit(
            "pipeline-run reconciliation requires --confirm or "
            "CONFIRM_GCP_PIPELINE_RUN_RECONCILIATION=1"
        )
    if (
        not isinstance(args.expected_count, int)
        or not 1 <= args.expected_count <= 1_000
    ):
        raise SystemExit("GCP_PIPELINE_RECONCILIATION_EXPECTED_COUNT is required")
    if (
        not args.approve_snapshot_to_fence_rpo
        and os.environ.get("CONFIRM_GCP_SNAPSHOT_TO_FENCE_RPO") != "1"
    ):
        raise SystemExit(
            "pipeline-run reconciliation requires explicit approval of any writes "
            "between fresh backup completion and the writer fence"
        )
    if not 300 <= args.handoff_timeout_seconds <= 3_600:
        raise SystemExit("reconciliation-to-deploy handoff must be 300..3600 seconds")
    release_identity = validate_bound_release_identity(
        args.release_tag, args.helper_ref
    )
    require_local_file_at_ref(args.helper_ref, "scripts/gcp/reconcile-pipeline-runs.sh")
    for relative in (
        "scripts/reconcile_pipeline_runs.py",
        "scripts/gcp/runtime_contract.py",
        "scripts/gcp/operation-watchdog.sh",
        "scripts/gcp/metadata-firewall.sh",
    ):
        require_local_file_at_ref(args.helper_ref, relative)
    require_exact_terraform_tree_at_ref(args.helper_ref, args.terraform_dir)
    validate_reviewed_target_identity(
        var_file=args.terraform_var_file,
        project=args.project,
        project_number=args.project_number,
        zone=args.zone,
        instance=args.instance,
        environment=args.environment,
    )
    validate_live_canonical_target(
        project=args.project,
        project_number=args.project_number,
        zone=args.zone,
        instance=args.instance,
        instance_id=args.instance_id,
        environment=args.environment,
    )
    validate_live_public_edge(
        project=args.project,
        environment=args.environment,
        public_console_domain=args.public_console_domain,
        public_workspace_domain=args.public_workspace_domain,
    )
    validate_artifact_bucket_identity(
        project=args.project,
        project_number=args.project_number,
        bucket=args.artifact_bucket,
        var_file=args.terraform_var_file,
    )
    backup_controls = validate_backup_bucket_controls(
        project=args.project,
        project_number=args.project_number,
        environment=args.environment,
        bucket=args.backup_bucket,
    )
    validate_reviewed_lakehouse_identity(
        var_file=args.terraform_var_file,
        project_number=args.project_number,
        environment=args.environment,
        lakehouse_bucket=args.lakehouse_bucket,
    )
    validate_transfer_fence(
        project=args.project,
        project_number=args.project_number,
        lakehouse_bucket=args.lakehouse_bucket,
    )
    if not FULL_SHA_RE.fullmatch(args.current_live_ref):
        raise SystemExit("GCP_CURRENT_LIVE_REF must be one full lowercase SHA")
    expected_backup_prefix = f"gs://{args.backup_bucket}/_omega_backups/"
    if (
        not args.backup_manifest_uri.startswith(expected_backup_prefix)
        or re.fullmatch(r"[1-9][0-9]*", args.backup_manifest_generation) is None
        or re.fullmatch(r"[1-9][0-9]*", args.backup_manifest_size_bytes) is None
        or SHA256_RE.fullmatch(args.backup_manifest_sha256) is None
    ):
        raise SystemExit("exact canonical pre-deploy backup manifest is required")

    manifest_bytes, manifest = read_private_reconciliation_manifest(
        args.manifest, expected_count=args.expected_count
    )
    routing_raw, routing = read_private_external_routing_scheduler_attestation(
        args.external_routing_scheduler_attestation,
        source_sha=args.helper_ref,
        instance_id=args.instance_id,
        project=args.project,
        environment=args.environment,
        console_domain=args.public_console_domain,
        workspace_domain=args.public_workspace_domain,
    )
    require_zero_aws_writer_gate(routing, operation="pipeline-run reconciliation")
    immutable = upload_reconciliation_manifest_immutable(
        manifest_bytes=manifest_bytes,
        manifest=manifest,
        bucket=args.artifact_bucket,
        helper_ref=args.helper_ref,
    )
    routing_attestation = upload_external_routing_scheduler_attestation_immutable(
        raw=routing_raw,
        bucket=args.artifact_bucket,
        source_sha=args.helper_ref,
    )
    result = remote_script(
        project=args.project,
        zone=args.zone,
        instance=args.instance,
        script=REMOTE_ROOT / "reconcile-pipeline-runs.sh",
        arguments=[
            immutable.uri,
            immutable.generation,
            str(immutable.size_bytes),
            immutable.sha256,
            str(args.expected_count),
            str(manifest["change_id"]),
            args.helper_ref,
            args.current_live_ref,
            args.backup_manifest_uri,
            args.backup_manifest_generation,
            args.backup_manifest_size_bytes,
            args.backup_manifest_sha256,
            args.backup_bucket,
            str(backup_controls["policy_sha256"]),
            args.project,
            args.artifact_bucket,
            args.compose_project,
            routing_attestation.uri,
            routing_attestation.generation,
            str(routing_attestation.size_bytes),
            routing_attestation.sha256,
            args.instance_id,
            args.release_tag,
            release_identity.manifest_digest,
            release_identity.tag_object_sha,
            str(args.handoff_timeout_seconds),
        ],
        timeout=args.timeout_seconds,
        companion_scripts={
            "reconcile_pipeline_runs.py": REPO
            / "scripts"
            / "reconcile_pipeline_runs.py",
            "runtime_contract.py": REMOTE_ROOT / "runtime_contract.py",
            "operation-watchdog.sh": REMOTE_ROOT / "operation-watchdog.sh",
            "metadata-firewall.sh": REMOTE_ROOT / "metadata-firewall.sh",
        },
    )
    checks, payload = parse_remote(
        result.stdout,
        "OMEGA_GCP_RECONCILE_CHECK",
        "OMEGA_GCP_RECONCILE_JSON",
    )
    evidence = (
        args.evidence_dir or EVIDENCE_ROOT / "reconcile-pipeline-runs-gcp" / utc_stamp()
    )
    status = write_evidence(
        evidence,
        operation="Pipeline Run Reconciliation",
        result=result,
        checks=checks,
        payload=payload,
    )
    print(
        json.dumps(
            {
                "status": status,
                "evidence_dir": str(evidence),
                "manifest": immutable.as_dict(),
                "external_routing_scheduler_attestation": (
                    routing_attestation.as_dict()
                ),
                **payload,
            },
            indent=2,
        )
    )
    return 0 if status == "PASS" else 1


def command_deploy(args: argparse.Namespace) -> int:
    require_target(args)
    if not args.confirm and os.environ.get("CONFIRM_GCP_DEPLOY") != "1":
        raise SystemExit("deploy requires --confirm or CONFIRM_GCP_DEPLOY=1")
    if not SHA256_RE.fullmatch(args.backup_manifest_sha256):
        raise SystemExit("GCP_BACKUP_MANIFEST_SHA256 must be an exact sha256")
    if not re.fullmatch(r"[1-9][0-9]*", args.backup_manifest_generation):
        raise SystemExit("GCP_BACKUP_MANIFEST_GENERATION must be explicit")
    if not re.fullmatch(r"[1-9][0-9]*", args.backup_manifest_size_bytes):
        raise SystemExit("GCP_BACKUP_MANIFEST_SIZE_BYTES must be explicit")
    if not re.fullmatch(r"[1-9][0-9]*", args.ghcr_secret_version):
        raise SystemExit("GCP_GHCR_SECRET_VERSION must be an explicit numeric version")
    require_canonical_ghcr_owner(args.ghcr_owner)
    release_identity = validate_bound_release_identity(args.tag, args.deploy_ref)
    version = release_identity.version
    require_startup_files_at_ref(args.deploy_ref)
    require_exact_terraform_tree_at_ref(args.deploy_ref, args.terraform_dir)
    require_local_file_at_ref(args.deploy_ref, "scripts/gcp/verify-secret-access.sh")
    validate_reviewed_target_identity(
        var_file=args.terraform_var_file,
        project=args.project,
        project_number=args.project_number,
        zone=args.zone,
        instance=args.instance,
        environment=args.environment,
    )
    validate_live_canonical_target(
        project=args.project,
        project_number=args.project_number,
        zone=args.zone,
        instance=args.instance,
        instance_id=args.instance_id,
        environment=args.environment,
    )
    validate_live_public_edge(
        project=args.project,
        environment=args.environment,
        public_console_domain=args.public_console_domain,
        public_workspace_domain=args.public_workspace_domain,
    )
    validate_artifact_bucket_identity(
        project=args.project,
        project_number=args.project_number,
        bucket=args.artifact_bucket,
        var_file=args.terraform_var_file,
    )
    backup_controls = validate_backup_bucket_controls(
        project=args.project,
        project_number=args.project_number,
        environment=args.environment,
        bucket=args.backup_bucket,
    )
    validate_reviewed_lakehouse_identity(
        var_file=args.terraform_var_file,
        project_number=args.project_number,
        environment=args.environment,
        lakehouse_bucket=args.lakehouse_bucket,
    )
    validate_transfer_fence(
        project=args.project,
        project_number=args.project_number,
        lakehouse_bucket=args.lakehouse_bucket,
    )
    routing_raw, routing = read_private_external_routing_scheduler_attestation(
        args.external_routing_scheduler_attestation,
        source_sha=args.deploy_ref,
        instance_id=args.instance_id,
        project=args.project,
        environment=args.environment,
        console_domain=args.public_console_domain,
        workspace_domain=args.public_workspace_domain,
    )
    require_zero_aws_writer_gate(routing, operation="GCP deploy")
    routing_attestation = upload_external_routing_scheduler_attestation_immutable(
        raw=routing_raw,
        bucket=args.artifact_bucket,
        source_sha=args.deploy_ref,
    )
    expected_manifest_prefix = f"gs://{args.backup_bucket}/_omega_backups/"
    if not args.backup_manifest_uri.startswith(expected_manifest_prefix):
        raise SystemExit(
            "backup manifest is outside the canonical release backup bucket"
        )
    validate_effective_least_privilege(
        project=args.project,
        zone=args.zone,
        instance=args.instance,
        environment=args.environment,
        timeout=args.timeout_seconds,
    )
    with tempfile.TemporaryDirectory(prefix="omega-gcp-artifact-") as temp:
        archive = Path(temp) / "repo.tar.gz"
        artifact_sha = create_archive(args.deploy_ref, archive)
        artifact = upload_artifact_immutable(
            archive, args.artifact_bucket, args.deploy_ref, artifact_sha
        )
    validate_reviewed_terraform_inputs(
        terraform_dir=args.terraform_dir,
        var_file=args.terraform_var_file,
        source_ref=args.deploy_ref,
        artifact=artifact,
        project_id=args.project,
        project_number=args.project_number,
        billing_account_id=args.billing_account_id,
        public_console_domain=args.public_console_domain,
        public_workspace_domain=args.public_workspace_domain,
    )
    validate_reviewed_target_identity(
        var_file=args.terraform_var_file,
        project=args.project,
        project_number=args.project_number,
        zone=args.zone,
        instance=args.instance,
        environment=args.environment,
    )
    validate_live_canonical_target(
        project=args.project,
        project_number=args.project_number,
        zone=args.zone,
        instance=args.instance,
        instance_id=args.instance_id,
        environment=args.environment,
    )
    validate_live_public_edge(
        project=args.project,
        environment=args.environment,
        public_console_domain=args.public_console_domain,
        public_workspace_domain=args.public_workspace_domain,
    )
    startup_sha = validate_terraform_source_contract(
        terraform_dir=args.terraform_dir,
        var_file=args.terraform_var_file,
        source_ref=args.deploy_ref,
        artifact=artifact,
        lakehouse_bucket=args.lakehouse_bucket,
    )
    validate_startup_metadata(
        project=args.project,
        zone=args.zone,
        instance=args.instance,
        expected_sha256=startup_sha,
    )
    require_local_file_at_ref(args.deploy_ref, "scripts/gcp/day2-release.sh")
    require_local_file_at_ref(args.deploy_ref, "scripts/gcp/safe_io.py")
    result = remote_script(
        project=args.project,
        zone=args.zone,
        instance=args.instance,
        script=REMOTE_ROOT / "day2-release.sh",
        arguments=[
            args.tag,
            args.deploy_ref,
            artifact.uri,
            artifact.generation,
            str(artifact.size_bytes),
            artifact.sha256,
            version,
            args.backup_manifest_uri,
            args.backup_manifest_generation,
            args.backup_manifest_size_bytes,
            args.backup_manifest_sha256,
            args.ghcr_owner,
            args.compose_project,
            args.environment,
            args.ghcr_secret_version,
            str(backup_controls["policy_sha256"]),
            args.project,
            release_identity.manifest_digest,
            release_identity.tag_object_sha,
        ],
        timeout=args.timeout_seconds,
    )
    deploy_completed_at = datetime.now(timezone.utc)
    checks, payload = parse_remote(
        result.stdout, "OMEGA_GCP_RELEASE_CHECK", "OMEGA_GCP_RELEASE_JSON"
    )
    evidence = args.evidence_dir or EVIDENCE_ROOT / "deploy-gcp" / utc_stamp()
    remote_status = write_evidence(
        evidence,
        operation="Deploy (post-deploy external gate pending)",
        result=result,
        checks=checks,
        payload=payload,
    )
    if remote_status != "PASS":
        print(
            json.dumps(
                {
                    **payload,
                    "status": "FAIL",
                    "evidence_dir": str(evidence),
                    "postdeploy_external_gate_attempted": False,
                },
                indent=2,
            )
        )
        return 1
    try:
        postdeploy_raw, postdeploy_routing_payload = (
            wait_for_fresh_postdeploy_routing_scheduler_attestation(
                args.postdeploy_external_routing_scheduler_attestation,
                predeploy_sha256=routing_attestation.sha256,
                not_before=deploy_completed_at,
                timeout_seconds=args.postdeploy_attestation_timeout_seconds,
                source_sha=args.deploy_ref,
                instance_id=args.instance_id,
                project=args.project,
                environment=args.environment,
                console_domain=args.public_console_domain,
                workspace_domain=args.public_workspace_domain,
            )
        )
        require_zero_aws_writer_gate(
            postdeploy_routing_payload, operation="post-deploy regression"
        )
    except (OSError, RuntimeError, ValueError, SystemExit) as exc:
        checks.append(
            {
                "name": "fresh post-deploy external zero-writer evidence",
                "status": "FAIL",
                "evidence": redact(str(exc)),
            }
        )
        failure_payload = {
            **payload,
            "status": "FAIL",
            "postdeploy_external_gate": "FAIL",
        }
        write_evidence(
            evidence,
            operation="Deploy",
            result=result,
            checks=checks,
            payload=failure_payload,
        )
        print(
            json.dumps(
                {
                    **payload,
                    "status": "FAIL",
                    "evidence_dir": str(evidence),
                    "postdeploy_external_gate": "FAIL",
                },
                indent=2,
            )
        )
        return 1
    postdeploy_attestation = upload_external_routing_scheduler_attestation_immutable(
        raw=postdeploy_raw,
        bucket=args.artifact_bucket,
        source_sha=args.deploy_ref,
    )
    validate_live_public_edge(
        project=args.project,
        environment=args.environment,
        public_console_domain=args.public_console_domain,
        public_workspace_domain=args.public_workspace_domain,
    )
    validate_transfer_fence(
        project=args.project,
        project_number=args.project_number,
        lakehouse_bucket=args.lakehouse_bucket,
    )
    postdeploy_routing = revalidate_external_routing_scheduler_live(
        postdeploy_routing_payload,
        console_domain=args.public_console_domain,
        workspace_domain=args.public_workspace_domain,
    )
    checks.append(
        {
            "name": "fresh post-deploy external zero-writer evidence",
            "status": "PASS",
            "evidence": "distinct post-completion attestation and live probes passed",
        }
    )
    payload = {**payload, "postdeploy_external_gate": "PASS"}
    status = write_evidence(
        evidence,
        operation="Deploy",
        result=result,
        checks=checks,
        payload=payload,
    )
    print(
        json.dumps(
            {
                "status": status,
                "evidence_dir": str(evidence),
                "external_routing_scheduler_attestation": (
                    routing_attestation.as_dict()
                ),
                "postdeploy_routing_scheduler_observation": postdeploy_routing,
                "postdeploy_routing_scheduler_attestation": (
                    postdeploy_attestation.as_dict()
                ),
                **payload,
            },
            indent=2,
        )
    )
    return 0 if status == "PASS" else 1


def command_rehearsal(args: argparse.Namespace) -> int:
    require_target(args)
    if not SHA256_RE.fullmatch(args.backup_manifest_sha256):
        raise SystemExit("GCP_BACKUP_MANIFEST_SHA256 must be an exact sha256")
    if not re.fullmatch(r"[1-9][0-9]*", args.backup_manifest_generation):
        raise SystemExit("GCP_BACKUP_MANIFEST_GENERATION must be explicit")
    if not re.fullmatch(r"[1-9][0-9]*", args.backup_manifest_size_bytes):
        raise SystemExit("GCP_BACKUP_MANIFEST_SIZE_BYTES must be explicit")
    if args.object_verify_mode != "all":
        raise SystemExit(
            "formal restore rehearsal requires verification of all objects"
        )
    if not FULL_SHA_RE.fullmatch(args.expected_source_ref):
        raise SystemExit("GCP_RESTORE_SOURCE_REF must be one exact commit SHA")
    if not re.fullmatch(
        r"[0-9]+\.[0-9]+\.[0-9]+(?:-[0-9A-Za-z][0-9A-Za-z.-]*)?",
        args.expected_source_version,
    ):
        raise SystemExit("GCP_RESTORE_SOURCE_VERSION must be exact")
    if not re.fullmatch(
        r"[0-9]{8}T[0-9]{6}Z-[a-z0-9][a-z0-9.-]{0,80}",
        args.expected_backup_id,
    ):
        raise SystemExit("GCP_RESTORE_BACKUP_ID must be exact")
    if (
        re.fullmatch(
            rf"gs://[a-z0-9][a-z0-9._-]{{1,61}}[a-z0-9]/_omega_backups/"
            rf"{re.escape(args.expected_backup_id)}/manifest\.json",
            args.backup_manifest_uri,
        )
        is None
    ):
        raise SystemExit("restore manifest URI differs from GCP_RESTORE_BACKUP_ID")
    if git("cat-file", "-t", args.expected_source_ref) != "commit":
        raise SystemExit("GCP_RESTORE_SOURCE_REF is not a local Git commit")
    source_version = git("show", f"{args.expected_source_ref}:VERSION").strip()
    if source_version != args.expected_source_version:
        raise SystemExit("restore source VERSION differs from the exact Git commit")
    backup_controls = validate_backup_bucket_controls(
        project=args.project,
        project_number=args.project_number,
        environment=args.environment,
        bucket=args.backup_bucket,
    )
    validate_live_canonical_target(
        project=args.project,
        project_number=args.project_number,
        zone=args.zone,
        instance=args.instance,
        instance_id=args.instance_id,
        environment=args.environment,
    )
    validate_live_public_edge(
        project=args.project,
        environment=args.environment,
        public_console_domain=args.public_console_domain,
        public_workspace_domain=args.public_workspace_domain,
    )
    validate_reviewed_lakehouse_identity(
        var_file=args.terraform_var_file,
        project_number=args.project_number,
        environment=args.environment,
        lakehouse_bucket=args.lakehouse_bucket,
    )
    validate_transfer_fence(
        project=args.project,
        project_number=args.project_number,
        lakehouse_bucket=args.lakehouse_bucket,
    )
    validate_candidate_main(args.helper_ref)
    require_local_file_at_ref(args.helper_ref, "scripts/gcp/restore-rehearsal.sh")
    require_local_file_at_ref(args.helper_ref, "scripts/gcp/safe_io.py")
    result = remote_script(
        project=args.project,
        zone=args.zone,
        instance=args.instance,
        script=REMOTE_ROOT / "restore-rehearsal.sh",
        arguments=[
            args.backup_manifest_uri,
            args.backup_manifest_generation,
            args.backup_manifest_size_bytes,
            args.backup_manifest_sha256,
            args.object_verify_mode,
            args.expected_source_ref,
            args.expected_source_version,
            args.expected_backup_id,
            str(backup_controls["policy_sha256"]),
            args.project,
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
    common_args(prepare)
    prepare.add_argument(
        "--terraform-var-file",
        type=Path,
        default=Path(os.environ.get("GCP_TERRAFORM_VAR_FILE", "")),
    )
    prepare.add_argument(
        "--project-number", default=os.environ.get("GCP_PROJECT_NUMBER", "")
    )
    prepare.add_argument(
        "--environment", default=os.environ.get("OMEGA_GCP_ENVIRONMENT", "")
    )
    prepare.add_argument(
        "--public-console-domain",
        default=os.environ.get("GCP_PUBLIC_CONSOLE_DOMAIN", ""),
    )
    prepare.add_argument(
        "--public-workspace-domain",
        default=os.environ.get("GCP_PUBLIC_WORKSPACE_DOMAIN", ""),
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
            "GCP_STARTUP_SOURCE_REF", os.environ.get("GCP_DEPLOY_REF", "")
        ),
    )
    startup.add_argument(
        "--current-live-ref", default=os.environ.get("GCP_CURRENT_LIVE_REF", "")
    )
    startup.add_argument(
        "--artifact-bucket", default=os.environ.get("GCP_SOURCE_BUCKET", "")
    )
    startup.add_argument(
        "--lakehouse-bucket", default=os.environ.get("GCP_LAKEHOUSE_BUCKET", "")
    )
    startup.add_argument(
        "--backup-bucket", default=os.environ.get("GCP_RELEASE_BACKUP_BUCKET", "")
    )
    startup.add_argument("--helper-ref", default=os.environ.get("GCP_DEPLOY_REF", ""))
    startup.add_argument(
        "--environment", default=os.environ.get("OMEGA_GCP_ENVIRONMENT", "")
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
        "--terraform-var-file",
        type=Path,
        default=Path(os.environ.get("GCP_TERRAFORM_VAR_FILE", "")),
    )
    startup_restore.add_argument(
        "--project-number", default=os.environ.get("GCP_PROJECT_NUMBER", "")
    )
    startup_restore.add_argument(
        "--environment", default=os.environ.get("OMEGA_GCP_ENVIRONMENT", "")
    )
    startup_restore.add_argument(
        "--public-console-domain",
        default=os.environ.get("GCP_PUBLIC_CONSOLE_DOMAIN", ""),
    )
    startup_restore.add_argument(
        "--public-workspace-domain",
        default=os.environ.get("GCP_PUBLIC_WORKSPACE_DOMAIN", ""),
    )
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
        help="save and validate one exact staged GHCR/host Secret Manager plan",
    )
    common_args(ghcr_plan)
    terraform_safety_args(ghcr_plan)
    ghcr_plan.add_argument(
        "--source-ref", default=os.environ.get("GCP_CURRENT_LIVE_REF", "")
    )
    ghcr_plan.add_argument("--helper-ref", default=os.environ.get("GCP_DEPLOY_REF", ""))
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
    ghcr_plan.add_argument(
        "--stage",
        choices=("grants", "revoke"),
        default=os.environ.get("GCP_GHCR_IAM_STAGE", "grants"),
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
        "--terraform-var-file",
        type=Path,
        default=Path(os.environ.get("GCP_TERRAFORM_VAR_FILE", "")),
    )
    ghcr_apply.add_argument(
        "--project-number", default=os.environ.get("GCP_PROJECT_NUMBER", "")
    )
    ghcr_apply.add_argument(
        "--plan",
        type=Path,
        default=Path(os.environ.get("GCP_GHCR_ACCESS_PLAN", "")),
    )
    ghcr_apply.add_argument(
        "--environment", default=os.environ.get("OMEGA_GCP_ENVIRONMENT", "")
    )
    ghcr_apply.add_argument(
        "--stage",
        choices=("grants", "revoke"),
        default=os.environ.get("GCP_GHCR_IAM_STAGE", "grants"),
    )
    ghcr_apply.add_argument(
        "--plan-sha256", default=os.environ.get("GCP_GHCR_ACCESS_PLAN_SHA256", "")
    )
    ghcr_apply.add_argument(
        "--helper-ref",
        default=os.environ.get(
            "GCP_DEPLOY_REF", os.environ.get("GCP_CURRENT_LIVE_REF", "")
        ),
    )
    ghcr_apply.add_argument("--confirm", action="store_true")
    ghcr_apply.set_defaults(handler=command_apply_ghcr_access)

    backup_plan = commands.add_parser(
        "plan-backup-storage",
        help="save and validate the exact three-create release-backup plan",
    )
    common_args(backup_plan)
    terraform_safety_args(backup_plan)
    backup_plan.add_argument(
        "--source-ref", default=os.environ.get("GCP_DEPLOY_REF", "")
    )
    backup_plan.add_argument(
        "--helper-ref", default=os.environ.get("GCP_DEPLOY_REF", "")
    )
    backup_plan.add_argument(
        "--artifact-bucket", default=os.environ.get("GCP_SOURCE_BUCKET", "")
    )
    backup_plan.add_argument(
        "--environment", default=os.environ.get("OMEGA_GCP_ENVIRONMENT", "")
    )
    backup_plan.add_argument(
        "--plan",
        type=Path,
        default=Path(os.environ.get("GCP_BACKUP_STORAGE_PLAN", "")),
    )
    backup_plan.set_defaults(handler=command_plan_backup_storage)

    backup_apply = commands.add_parser(
        "apply-backup-storage",
        help="apply only the prevalidated release-backup saved plan",
    )
    common_args(backup_apply)
    backup_apply.add_argument(
        "--terraform-dir",
        type=Path,
        default=Path(
            os.environ.get("GCP_TERRAFORM_DIR", str(REPO / "infra/terraform-gcp"))
        ),
    )
    backup_apply.add_argument(
        "--terraform-var-file",
        type=Path,
        default=Path(os.environ.get("GCP_TERRAFORM_VAR_FILE", "")),
    )
    backup_apply.add_argument(
        "--plan",
        type=Path,
        default=Path(os.environ.get("GCP_BACKUP_STORAGE_PLAN", "")),
    )
    backup_apply.add_argument(
        "--plan-sha256",
        default=os.environ.get("GCP_BACKUP_STORAGE_PLAN_SHA256", ""),
    )
    backup_apply.add_argument(
        "--helper-ref", default=os.environ.get("GCP_DEPLOY_REF", "")
    )
    backup_apply.add_argument(
        "--project-number", default=os.environ.get("GCP_PROJECT_NUMBER", "")
    )
    backup_apply.add_argument(
        "--environment", default=os.environ.get("OMEGA_GCP_ENVIRONMENT", "")
    )
    backup_apply.add_argument("--region", default=os.environ.get("GCP_REGION", ""))
    backup_apply.add_argument("--confirm", action="store_true")
    backup_apply.set_defaults(handler=command_apply_backup_storage)

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
        default=os.environ.get("GHCR_OWNER", CANONICAL_GHCR_OWNER),
    )
    image_preflight.add_argument(
        "--environment", default=os.environ.get("OMEGA_GCP_ENVIRONMENT", "")
    )
    image_preflight.add_argument(
        "--project-number", default=os.environ.get("GCP_PROJECT_NUMBER", "")
    )
    image_preflight.add_argument(
        "--public-console-domain",
        default=os.environ.get("GCP_PUBLIC_CONSOLE_DOMAIN", ""),
    )
    image_preflight.add_argument(
        "--public-workspace-domain",
        default=os.environ.get("GCP_PUBLIC_WORKSPACE_DOMAIN", ""),
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
    image_preflight.add_argument(
        "--candidate-workflow-run-id",
        default=os.environ.get("GCP_RELEASE_CANDIDATE_WORKFLOW_RUN_ID", ""),
    )
    image_preflight.add_argument(
        "--candidate-workflow-run-attempt",
        default=os.environ.get("GCP_RELEASE_CANDIDATE_WORKFLOW_RUN_ATTEMPT", ""),
    )
    image_preflight.add_argument(
        "--backup-bucket", default=os.environ.get("GCP_RELEASE_BACKUP_BUCKET", "")
    )
    image_preflight.add_argument(
        "--rollback-runtime-images-uri",
        default=os.environ.get("GCP_ROLLBACK_RUNTIME_IMAGES_URI", ""),
    )
    image_preflight.add_argument(
        "--rollback-runtime-images-generation",
        default=os.environ.get("GCP_ROLLBACK_RUNTIME_IMAGES_GENERATION", ""),
    )
    image_preflight.add_argument(
        "--rollback-runtime-images-size-bytes",
        default=os.environ.get("GCP_ROLLBACK_RUNTIME_IMAGES_SIZE_BYTES", ""),
    )
    image_preflight.add_argument(
        "--rollback-runtime-images-sha256",
        default=os.environ.get("GCP_ROLLBACK_RUNTIME_IMAGES_SHA256", ""),
    )
    image_preflight.add_argument(
        "--rollback-backup-manifest-uri",
        default=os.environ.get("GCP_ROLLBACK_BACKUP_MANIFEST_URI", ""),
    )
    image_preflight.add_argument(
        "--rollback-backup-manifest-generation",
        default=os.environ.get("GCP_ROLLBACK_BACKUP_MANIFEST_GENERATION", ""),
    )
    image_preflight.add_argument(
        "--rollback-backup-manifest-size-bytes",
        default=os.environ.get("GCP_ROLLBACK_BACKUP_MANIFEST_SIZE_BYTES", ""),
    )
    image_preflight.add_argument(
        "--rollback-backup-manifest-sha256",
        default=os.environ.get("GCP_ROLLBACK_BACKUP_MANIFEST_SHA256", ""),
    )
    image_preflight.add_argument("--confirm", action="store_true")
    image_preflight.set_defaults(handler=command_image_preflight)

    backup = commands.add_parser("backup", help="create a writer-fenced GCP backup")
    common_args(backup)
    terraform_safety_args(backup)
    backup.add_argument(
        "--backup-bucket", default=os.environ.get("GCP_RELEASE_BACKUP_BUCKET", "")
    )
    backup.add_argument(
        "--lakehouse-bucket", default=os.environ.get("GCP_LAKEHOUSE_BUCKET", "")
    )
    backup.add_argument(
        "--environment", default=os.environ.get("OMEGA_GCP_ENVIRONMENT", "")
    )
    backup.add_argument("--backup-id", default=os.environ.get("GCP_BACKUP_ID", ""))
    backup.add_argument(
        "--backup-phase",
        choices=("rollback-baseline", "predeploy"),
        default=os.environ.get("GCP_BACKUP_PHASE", "predeploy"),
    )
    backup.add_argument("--candidate-ref", default=os.environ.get("GCP_DEPLOY_REF", ""))
    backup.add_argument("--release-tag", default=os.environ.get("GCP_RELEASE_TAG", ""))
    backup.add_argument(
        "--artifact-bucket", default=os.environ.get("GCP_SOURCE_BUCKET", "")
    )
    backup.add_argument(
        "--external-routing-scheduler-attestation",
        type=Path,
        default=Path(os.environ.get("GCP_EXTERNAL_ROUTING_SCHEDULER_ATTESTATION", "")),
    )
    backup.add_argument(
        "--current-live-ref", default=os.environ.get("GCP_CURRENT_LIVE_REF", "")
    )
    backup.add_argument(
        "--current-live-version",
        default=os.environ.get("GCP_CURRENT_LIVE_VERSION", ""),
    )
    backup.add_argument("--confirm", action="store_true")
    backup.set_defaults(handler=command_backup)

    reconcile = commands.add_parser(
        "reconcile-pipeline-runs",
        help="atomically reconcile one exact backup-bound stale-run manifest",
    )
    common_args(reconcile)
    terraform_safety_args(reconcile)
    reconcile.add_argument(
        "--manifest",
        type=Path,
        default=Path(os.environ.get("GCP_PIPELINE_RECONCILIATION_MANIFEST", "")),
    )
    reconcile.add_argument(
        "--external-routing-scheduler-attestation",
        type=Path,
        default=Path(os.environ.get("GCP_EXTERNAL_ROUTING_SCHEDULER_ATTESTATION", "")),
    )
    reconcile.add_argument(
        "--expected-count",
        type=int,
        default=os.environ.get("GCP_PIPELINE_RECONCILIATION_EXPECTED_COUNT", "0"),
    )
    reconcile.add_argument("--helper-ref", default=os.environ.get("GCP_DEPLOY_REF", ""))
    reconcile.add_argument(
        "--release-tag", default=os.environ.get("GCP_RELEASE_TAG", "")
    )
    reconcile.add_argument(
        "--current-live-ref", default=os.environ.get("GCP_CURRENT_LIVE_REF", "")
    )
    reconcile.add_argument(
        "--artifact-bucket", default=os.environ.get("GCP_SOURCE_BUCKET", "")
    )
    reconcile.add_argument(
        "--backup-bucket", default=os.environ.get("GCP_RELEASE_BACKUP_BUCKET", "")
    )
    reconcile.add_argument(
        "--lakehouse-bucket", default=os.environ.get("GCP_LAKEHOUSE_BUCKET", "")
    )
    reconcile.add_argument(
        "--backup-manifest-uri",
        default=os.environ.get("GCP_BACKUP_MANIFEST_URI", ""),
    )
    reconcile.add_argument(
        "--backup-manifest-generation",
        default=os.environ.get("GCP_BACKUP_MANIFEST_GENERATION", ""),
    )
    reconcile.add_argument(
        "--backup-manifest-size-bytes",
        default=os.environ.get("GCP_BACKUP_MANIFEST_SIZE_BYTES", ""),
    )
    reconcile.add_argument(
        "--backup-manifest-sha256",
        default=os.environ.get("GCP_BACKUP_MANIFEST_SHA256", ""),
    )
    reconcile.add_argument(
        "--environment", default=os.environ.get("OMEGA_GCP_ENVIRONMENT", "")
    )
    reconcile.add_argument(
        "--handoff-timeout-seconds",
        type=int,
        default=int(os.environ.get("GCP_RECONCILIATION_HANDOFF_TIMEOUT", "1800")),
    )
    reconcile.add_argument(
        "--approve-snapshot-to-fence-rpo",
        action="store_true",
        help="acknowledge the bounded snapshot-completion to writer-fence RPO",
    )
    reconcile.add_argument("--confirm", action="store_true")
    reconcile.set_defaults(handler=command_reconcile_pipeline_runs)

    deploy = commands.add_parser("deploy", help="deploy one exact published release")
    common_args(deploy)
    terraform_safety_args(deploy)
    deploy.add_argument("--tag", default=os.environ.get("GCP_RELEASE_TAG", ""))
    deploy.add_argument("--deploy-ref", default=os.environ.get("GCP_DEPLOY_REF", ""))
    deploy.add_argument(
        "--artifact-bucket", default=os.environ.get("GCP_SOURCE_BUCKET", "")
    )
    deploy.add_argument(
        "--external-routing-scheduler-attestation",
        type=Path,
        default=Path(os.environ.get("GCP_EXTERNAL_ROUTING_SCHEDULER_ATTESTATION", "")),
    )
    deploy.add_argument(
        "--postdeploy-external-routing-scheduler-attestation",
        type=Path,
        default=Path(
            os.environ.get("GCP_POSTDEPLOY_EXTERNAL_ROUTING_SCHEDULER_ATTESTATION", "")
        ),
    )
    deploy.add_argument(
        "--postdeploy-attestation-timeout-seconds",
        type=int,
        default=int(os.environ.get("GCP_POSTDEPLOY_ATTESTATION_TIMEOUT", "300")),
    )
    deploy.add_argument(
        "--backup-bucket", default=os.environ.get("GCP_RELEASE_BACKUP_BUCKET", "")
    )
    deploy.add_argument(
        "--lakehouse-bucket", default=os.environ.get("GCP_LAKEHOUSE_BUCKET", "")
    )
    deploy.add_argument(
        "--backup-manifest-uri",
        default=os.environ.get("GCP_BACKUP_MANIFEST_URI", ""),
    )
    deploy.add_argument(
        "--backup-manifest-generation",
        default=os.environ.get("GCP_BACKUP_MANIFEST_GENERATION", ""),
    )
    deploy.add_argument(
        "--backup-manifest-size-bytes",
        default=os.environ.get("GCP_BACKUP_MANIFEST_SIZE_BYTES", ""),
    )
    deploy.add_argument(
        "--backup-manifest-sha256",
        default=os.environ.get("GCP_BACKUP_MANIFEST_SHA256", ""),
    )
    deploy.add_argument(
        "--ghcr-owner",
        default=os.environ.get("GHCR_OWNER", CANONICAL_GHCR_OWNER),
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
        "--backup-manifest-generation",
        default=os.environ.get("GCP_BACKUP_MANIFEST_GENERATION", ""),
    )
    rehearsal.add_argument(
        "--backup-manifest-size-bytes",
        default=os.environ.get("GCP_BACKUP_MANIFEST_SIZE_BYTES", ""),
    )
    rehearsal.add_argument(
        "--backup-manifest-sha256",
        default=os.environ.get("GCP_BACKUP_MANIFEST_SHA256", ""),
    )
    rehearsal.add_argument(
        "--object-verify-mode",
        choices=("all",),
        default=os.environ.get("GCP_OBJECT_VERIFY_MODE", "all"),
    )
    rehearsal.add_argument(
        "--expected-source-ref",
        default=os.environ.get("GCP_RESTORE_SOURCE_REF", ""),
    )
    rehearsal.add_argument(
        "--expected-source-version",
        default=os.environ.get("GCP_RESTORE_SOURCE_VERSION", ""),
    )
    rehearsal.add_argument(
        "--expected-backup-id",
        default=os.environ.get("GCP_RESTORE_BACKUP_ID", ""),
    )
    rehearsal.add_argument(
        "--backup-bucket", default=os.environ.get("GCP_RELEASE_BACKUP_BUCKET", "")
    )
    rehearsal.add_argument(
        "--lakehouse-bucket", default=os.environ.get("GCP_LAKEHOUSE_BUCKET", "")
    )
    rehearsal.add_argument(
        "--project-number", default=os.environ.get("GCP_PROJECT_NUMBER", "")
    )
    rehearsal.add_argument(
        "--terraform-var-file",
        type=Path,
        default=Path(os.environ.get("GCP_TERRAFORM_VAR_FILE", "")),
    )
    rehearsal.add_argument(
        "--public-console-domain",
        default=os.environ.get("GCP_PUBLIC_CONSOLE_DOMAIN", ""),
    )
    rehearsal.add_argument(
        "--public-workspace-domain",
        default=os.environ.get("GCP_PUBLIC_WORKSPACE_DOMAIN", ""),
    )
    rehearsal.add_argument(
        "--environment", default=os.environ.get("OMEGA_GCP_ENVIRONMENT", "")
    )
    rehearsal.add_argument("--helper-ref", default=os.environ.get("GCP_DEPLOY_REF", ""))
    rehearsal.set_defaults(handler=command_rehearsal)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return int(args.handler(args))


if __name__ == "__main__":
    raise SystemExit(main())
