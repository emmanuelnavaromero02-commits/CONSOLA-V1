#!/usr/bin/env python3
"""Fail-closed migration manifest, ledger, and SQL transaction guard."""

from __future__ import annotations

import argparse
from datetime import datetime, timedelta
import hashlib
import json
import os
import re
import ssl
import stat
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any
import urllib.error
import urllib.parse
import urllib.request
from urllib.parse import urlsplit


ROOT = Path(__file__).resolve().parents[1]
BASELINE_REF = "6b12883c5b5ea0537120279ccbee4947137998a2"
RELEASE_VERSION = "1.45.207-beta"
BASELINE_NAME = f"gcp-live-{BASELINE_REF}.json"
RELEASE_NAME = f"v{RELEASE_VERSION}.json"
RELEASE_ATTESTATION_NAME = ".omega-release.json"
MAX_RELEASE_ATTESTATION_BYTES = 65_536
MAX_DOCKER_INSPECT_BYTES = 1_048_576
MAX_AUTHORITY_BYTES = 8 * 1024 * 1024
MAX_GCP_RESPONSE_BYTES = 65_536
MAX_RELEASE_CLOCK_SKEW = timedelta(minutes=5)
PINNED_BASELINE_MANIFEST_SHA256 = (
    "b6cb33c9b1a0f93e13fe2eb68f2e8fff1fdeedb2979bbfb22840a2a35d2e4a18"
)
PINNED_RELEASE_MANIFEST_SHA256 = (
    "79a607d045b853fba26812311b930b21d84e676d6cbde43f05ad4ec8150700b2"
)
PENDING_OPERATIONAL = (
    "99zzt_analytic_app_dataset_grants.sql",
    "99zzu_analytic_app_manifest_registry.sql",
    "99zzv_sap_successfactors_apps_secure_refresh.sql",
)
# PostgreSQL's entrypoint executes every migration on a fresh volume, but a
# small, historically fixed set of those files does not self-register in
# ``schema_migrations``.  These are exact reviewed omissions observed from a
# clean pgvector/pgvector:pg15 + infra/init bootstrap (176 rows) and a clean
# postgres:15.18 + infra/init_gold bootstrap (9 rows).  They are not a generic
# "missing row" allowance: only the exact complements below are accepted, and
# only when the caller explicitly enables the local bootstrap path.
FRESH_BOOTSTRAP_MISSING = {
    "operational": (
        "65_replicon_mejoras_seed_refresh.sql",
        "66_replicon_mejoras_config_seed.sql",
        "67_data_catalog_upgrade_shape.sql",
        "68_agents_model_default.sql",
        "69_data_catalog_sequence_grants.sql",
        "70_replicon_audit_followups.sql",
        "71_rag_embedding_dim_1024.sql",
        "92_audit_request_id.sql",
        "93_salesforce_role_and_tables.sql",
        "94_hubspot_datasets_seed.sql",
        "94_salesforce_seed.sql",
        "95_banxico_role_and_seed.sql",
        "95_inegi_role_and_seed.sql",
        "95_sec_edgar_role_and_seed.sql",
        "96_salesforce_mcp_server_registry.sql",
        "99zp_sap_successfactors_tenant_aliases.sql",
        "99zr_banxico_entity_watermarks_rls.sql",
        "99zs_banxico_default_connection_id.sql",
        "99zu_inegi_entity_watermarks_rls.sql",
        "99zv_inegi_default_connection_id.sql",
        "99zx_sec_edgar_default_connection_id.sql",
        "99zy_sec_edgar_entity_watermarks_rls.sql",
        "99zzt_analytic_app_dataset_grants.sql",
        "99zzu_analytic_app_manifest_registry.sql",
        "99zzv_sap_successfactors_apps_secure_refresh.sql",
    ),
    "gold": (
        "gold/00_schema.sql",
        "gold/34_postgres_gold_role.sql",
        "gold/35_gold_native_rls.sql",
    ),
}
FRESH_BOOTSTRAP_COUNTS = {"operational": 176, "gold": 9}
BASELINE_EVIDENCE = "baseline_expected"
GUARDED_EVIDENCE = "guarded_transaction"
BASELINE_PROVENANCE = {
    "basis": "sha256_of_migration_files_in_git_tree_at_source_ref",
    "caveat": (
        "Expected bytes inferred from the pinned source tree; not "
        "contemporaneous proof of historical execution."
    ),
    "classification": BASELINE_EVIDENCE,
    "historical_execution_receipt": False,
    "source_ref": BASELINE_REF,
}
RELEASE_EVIDENCE_CONTRACT = {
    "baseline": BASELINE_PROVENANCE,
    "pending": {
        "basis": (
            "checksum_and_ledger_row_recorded_in_the_same_database_"
            "transaction_as_the_migration"
        ),
        "classification": GUARDED_EVIDENCE,
        "filenames": list(PENDING_OPERATIONAL),
    },
}

_SHA40 = re.compile(r"^[0-9a-f]{40}$")
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_GENERATION = re.compile(r"^[1-9][0-9]*$")
_BASE64_CRC32C = re.compile(r"^[A-Za-z0-9+/]{6}==$")
_BASE64_MD5 = re.compile(r"^[A-Za-z0-9+/]{22}==$")
_ACCESS_TOKEN = re.compile(r"^[A-Za-z0-9._~+/=-]{20,8192}$")
_CONTAINER_ID = re.compile(r"^[0-9a-f]{64}$")
_IMAGE_ID = re.compile(r"^sha256:[0-9a-f]{64}$")
_COMPOSE_PROJECT = re.compile(r"^[a-z0-9][a-z0-9_-]*$")
_VOLUME_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,254}$")
_SYSTEM_IDENTIFIER = re.compile(r"^[1-9][0-9]{18}$")
_UTC_TIMESTAMP = re.compile(
    r"^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}"
    r"(?:\.[0-9]{1,6})?\+00:00$"
)
_OP_NAME = re.compile(r"^[0-9]{2}[0-9a-z_]*\.sql$")
_GOLD_NAME = re.compile(r"^gold/[0-9]{2}[0-9a-z_]*\.sql$")
_TRANSACTION_TERMINATOR = re.compile(
    r"^\s*(?:BEGIN|COMMIT|ROLLBACK|START\s+TRANSACTION)\s*;",
    flags=re.IGNORECASE | re.MULTILINE,
)
_PSQL_META_COMMAND = re.compile(r"^\s*\\", flags=re.MULTILINE)
_MIGRATION_RUN_ID = re.compile(
    r"^omega_migration_([1-9][0-9]{0,19})_([1-9][0-9]{0,29})$"
)
_RECEIPT_NAME = re.compile(
    r"^migration-([0-9a-f]{40})-([0-9]{8}T[0-9]{6}Z)-([1-9][0-9]{0,19})$"
)
_RECEIPT_TIMESTAMP = re.compile(
    r"^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}Z$"
)

MIGRATION_RECEIPT_FIELDS = {
    "candidate_ref",
    "database_commit_state",
    "exit_code",
    "operation",
    "phase",
    "recorded_at",
    "release_manifest_sha256",
    "release_version",
    "run_id",
    "schema_version",
    "status",
}
MIGRATION_SUCCESS_EVENTS = {
    "00-launcher-intent.json": ("launcher_intent", "IN_PROGRESS", "none"),
    "05-runner-started.json": ("started", "IN_PROGRESS", "none"),
    "10-preflight-passed.json": ("preflight_passed", "IN_PROGRESS", "none"),
    "15-gold-commit-intent.json": (
        "gold_commit_in_progress",
        "IN_PROGRESS",
        "indeterminate",
    ),
    "20-gold-committed.json": (
        "gold_committed_operational_pending",
        "IN_PROGRESS",
        "gold_only",
    ),
    "25-operational-commit-intent.json": (
        "operational_commit_in_progress",
        "IN_PROGRESS",
        "indeterminate",
    ),
    "30-operational-committed.json": (
        "both_databases_committed_authority_pending",
        "IN_PROGRESS",
        "gold_and_operational",
    ),
    "40-operational-authority-committed.json": (
        "operational_authority_committed_gold_authority_pending",
        "IN_PROGRESS",
        "gold_and_operational",
    ),
    "50-gold-authority-committed.json": (
        "database_authority_committed_postflight_pending",
        "IN_PROGRESS",
        "gold_operational_and_authority",
    ),
    "60-postflight-passed.json": (
        "postflight_passed",
        "IN_PROGRESS",
        "gold_operational_and_authority",
    ),
    "90-terminal.json": (
        "postflight_passed",
        "PASS",
        "gold_operational_and_authority",
    ),
}

RELEASE_ATTESTATION_KEYS = {
    "schema_version",
    "deploy_ref",
    "tag",
    "artifact_uri",
    "artifact_generation",
    "artifact_metageneration",
    "artifact_size_bytes",
    "artifact_sha256",
    "artifact_crc32c",
    "artifact_md5",
    "version",
    "tree_sha256",
    "installed_at",
}


def _die(message: str) -> None:
    raise SystemExit(f"migration guard: {message}")


def _sha256(path: Path) -> str:
    return hashlib.sha256(
        _read_regular_snapshot(path, maximum=MAX_AUTHORITY_BYTES)
    ).hexdigest()


def _read_regular_snapshot(path: Path, *, maximum: int) -> bytes:
    """Read one owned, immutable regular-file descriptor exactly once."""
    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(path, flags)
    except OSError as exc:
        _die(f"cannot safely open {path}: {exc}")
    try:
        before = os.fstat(descriptor)
        if (
            not stat.S_ISREG(before.st_mode)
            or before.st_uid != os.geteuid()
            or before.st_nlink != 1
            or stat.S_IMODE(before.st_mode) & 0o022
            or not 1 <= before.st_size <= maximum
        ):
            _die(f"unsafe authoritative file descriptor: {path}")
        chunks: list[bytes] = []
        remaining = before.st_size
        while remaining:
            chunk = os.read(descriptor, min(remaining, 1024 * 1024))
            if not chunk:
                _die(f"short authoritative file read: {path}")
            chunks.append(chunk)
            remaining -= len(chunk)
        raw = b"".join(chunks)
        after = os.fstat(descriptor)
        identity = lambda info: (  # noqa: E731 - compact immutable projection
            info.st_dev,
            info.st_ino,
            info.st_size,
            info.st_mtime_ns,
            info.st_ctime_ns,
        )
        if len(raw) != before.st_size or identity(before) != identity(after):
            _die(f"authoritative file changed while reading: {path}")
        return raw
    finally:
        os.close(descriptor)


def _decode_unique_json_object(raw: bytes, label: str) -> dict[str, Any]:
    def unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in pairs:
            if key in result:
                _die(f"{label} contains a duplicate JSON key: {key}")
            result[key] = value
        return result

    try:
        value = json.loads(raw.decode("utf-8"), object_pairs_hook=unique_object)
    except (UnicodeError, json.JSONDecodeError) as exc:
        _die(f"{label} is not canonical UTF-8 JSON: {exc}")
    if not isinstance(value, dict):
        _die(f"{label} must contain one JSON object")
    return value


def _validate_release_attestation_payload(
    payload: dict[str, Any], contract: "Contract", tree_sha256: str
) -> None:
    if set(payload) != RELEASE_ATTESTATION_KEYS:
        _die("release attestation keys are not exact")
    if type(payload.get("schema_version")) is not int or payload["schema_version"] != 1:
        _die("release attestation schema version is not 1")
    expected = {
        "deploy_ref": contract.candidate_ref,
        "tag": f"v{contract.release_version}",
        "version": contract.release_version,
        "tree_sha256": tree_sha256,
    }
    for key, value in expected.items():
        if payload.get(key) != value:
            _die(f"release attestation does not bind exact {key}")
    generation = payload.get("artifact_generation")
    if not isinstance(generation, str) or not _GENERATION.fullmatch(generation):
        _die("release attestation artifact generation is invalid")
    metageneration = payload.get("artifact_metageneration")
    if not isinstance(metageneration, str) or not _GENERATION.fullmatch(metageneration):
        _die("release attestation artifact metageneration is invalid")
    artifact_size = payload.get("artifact_size_bytes")
    if type(artifact_size) is not int or not 1 <= artifact_size <= (2**63 - 1):
        _die("release attestation artifact size is invalid")
    artifact_sha = payload.get("artifact_sha256")
    if not isinstance(artifact_sha, str) or not _SHA256.fullmatch(artifact_sha):
        _die("release attestation artifact hash is invalid")
    crc32c = payload.get("artifact_crc32c")
    if not isinstance(crc32c, str) or not _BASE64_CRC32C.fullmatch(crc32c):
        _die("release attestation artifact CRC32C is invalid")
    md5 = payload.get("artifact_md5")
    if not isinstance(md5, str) or not _BASE64_MD5.fullmatch(md5):
        _die("release attestation artifact MD5 is invalid")
    artifact_uri = payload.get("artifact_uri")
    if not isinstance(artifact_uri, str):
        _die("release attestation artifact URI is invalid")
    parsed_uri = urlsplit(artifact_uri)
    bucket = parsed_uri.netloc
    key = parsed_uri.path.removeprefix("/")
    expected_key = f"deploy-artifacts/{contract.candidate_ref}/repo.tar.gz"
    if (
        parsed_uri.scheme != "gs"
        or parsed_uri.query
        or parsed_uri.fragment
        or parsed_uri.path != f"/{key}"
        or not re.fullmatch(r"[a-z0-9][a-z0-9._-]{1,61}[a-z0-9]", bucket)
        or ".." in bucket
        or not key
        or key != expected_key
        or "//" in key
        or "\\" in key
        or "%" in key
        or any(part in {"", ".", ".."} for part in key.split("/"))
        or any(ord(character) < 0x21 or ord(character) > 0x7E for character in key)
    ):
        _die("release attestation artifact URI is not a canonical gs://bucket/key")
    installed_at = payload.get("installed_at")
    if not isinstance(installed_at, str) or not _UTC_TIMESTAMP.fullmatch(installed_at):
        _die("release attestation installed_at is not canonical UTC")
    try:
        parsed = datetime.fromisoformat(installed_at)
    except ValueError:
        _die("release attestation installed_at is invalid")
    if parsed.utcoffset() != timedelta(0):
        _die("release attestation installed_at is not UTC")
    if parsed > datetime.now(tz=parsed.tzinfo) + MAX_RELEASE_CLOCK_SKEW:
        _die("release attestation installed_at is in the future")


def _validate_release_attestation_file_info(info: os.stat_result) -> None:
    if (
        not stat.S_ISREG(info.st_mode)
        or info.st_uid != 0
        or info.st_gid != 0
        or stat.S_IMODE(info.st_mode) != 0o400
        or info.st_size < 1
        or info.st_size > MAX_RELEASE_ATTESTATION_BYTES
    ):
        _die("release attestation must be root:root mode 0400 and bounded")


def _validate_release_attestation(
    path: Path, contract: "Contract", tree_sha256: str
) -> dict[str, Any]:
    if not _SHA256.fullmatch(tree_sha256 or ""):
        _die("release tree hash must be one lowercase SHA-256")
    canonical = ROOT / RELEASE_ATTESTATION_NAME
    if Path(os.path.abspath(path)) != canonical:
        _die("release attestation path is not ROOT/.omega-release.json")
    release_root = ROOT.parent
    managed_root = release_root.parent
    if ROOT.name != contract.candidate_ref or release_root.name != "releases":
        _die("release directory is not the exact releases/CANDIDATE_REF path")
    for directory, label in (
        (managed_root, "managed application root"),
        (release_root, "managed release root"),
        (ROOT, "immutable release directory"),
    ):
        try:
            directory_info = directory.lstat()
        except OSError as exc:
            _die(f"{label} is unavailable: {exc}")
        if (
            not stat.S_ISDIR(directory_info.st_mode)
            or directory_info.st_uid != 0
            or directory_info.st_gid != 0
            or stat.S_IMODE(directory_info.st_mode) & 0o022
            or directory.resolve(strict=True) != Path(os.path.abspath(directory))
        ):
            _die(f"{label} is linked, escaped, unowned, or writable")
    if stat.S_IMODE(ROOT.lstat().st_mode) & 0o200:
        _die("immutable release directory remains owner-writable")
    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(path, flags)
    except OSError as exc:
        _die(f"cannot safely open release attestation: {exc}")
    try:
        before = os.fstat(descriptor)
        _validate_release_attestation_file_info(before)
        raw = b""
        while len(raw) <= MAX_RELEASE_ATTESTATION_BYTES:
            chunk = os.read(
                descriptor, min(65_536, MAX_RELEASE_ATTESTATION_BYTES + 1 - len(raw))
            )
            if not chunk:
                break
            raw += chunk
        after = os.fstat(descriptor)
        if (
            len(raw) > MAX_RELEASE_ATTESTATION_BYTES
            or (before.st_dev, before.st_ino, before.st_size)
            != (after.st_dev, after.st_ino, after.st_size)
            or len(raw) != before.st_size
        ):
            _die("release attestation changed while it was read")
    finally:
        os.close(descriptor)
    payload = _decode_unique_json_object(raw, "release attestation")
    _validate_release_attestation_payload(payload, contract, tree_sha256)
    return payload


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *_args: Any, **_kwargs: Any) -> None:
        return None


def _gcp_opener() -> Any:
    context = ssl.create_default_context()
    context.minimum_version = ssl.TLSVersion.TLSv1_2
    return urllib.request.build_opener(
        urllib.request.ProxyHandler({}),
        _NoRedirect(),
        urllib.request.HTTPSHandler(context=context),
    )


def _bounded_http_json(
    opener: Any,
    request: urllib.request.Request,
    *,
    label: str,
    timeout: int,
    require_metadata_flavor: bool = False,
) -> dict[str, Any]:
    try:
        with opener.open(request, timeout=timeout) as response:
            if (
                require_metadata_flavor
                and response.headers.get("Metadata-Flavor") != "Google"
            ):
                _die("GCP metadata response flavor is invalid")
            raw = response.read(MAX_GCP_RESPONSE_BYTES + 1)
    except urllib.error.HTTPError as exc:
        _die(f"{label} rejected request with HTTP {exc.code}")
    except (urllib.error.URLError, TimeoutError, ssl.SSLError) as exc:
        _die(f"{label} request failed: {type(exc).__name__}")
    if not 1 <= len(raw) <= MAX_GCP_RESPONSE_BYTES:
        _die(f"{label} response exceeds its bound")
    return _decode_unique_json_object(raw, label)


def _verify_live_gcs_release_object(
    attestation: dict[str, Any], *, opener: Any | None = None
) -> dict[str, str]:
    """Bind the local release receipt to one live immutable GCS generation."""
    client = opener or _gcp_opener()
    token_request = urllib.request.Request(
        "http://metadata.google.internal/computeMetadata/v1/instance/"
        "service-accounts/default/token",
        headers={"Metadata-Flavor": "Google", "Accept": "application/json"},
    )
    token_payload = _bounded_http_json(
        client,
        token_request,
        label="GCP metadata token",
        timeout=5,
        require_metadata_flavor=True,
    )
    if set(token_payload) != {"access_token", "expires_in", "token_type"}:
        _die("GCP metadata token shape is not exact")
    token = token_payload.get("access_token")
    expires_in = token_payload.get("expires_in")
    if (
        not isinstance(token, str)
        or not _ACCESS_TOKEN.fullmatch(token)
        or token_payload.get("token_type") != "Bearer"
        or type(expires_in) is not int
        or not 1 <= expires_in <= 3600
    ):
        _die("GCP metadata token response is invalid")

    parsed = urlsplit(attestation["artifact_uri"])
    bucket = parsed.netloc
    name = parsed.path.removeprefix("/")
    fields = "bucket,name,generation,metageneration,size,metadata,crc32c,md5Hash"
    query = urllib.parse.urlencode(
        {"generation": attestation["artifact_generation"], "fields": fields}
    )
    object_url = (
        "https://storage.googleapis.com/storage/v1/b/"
        f"{urllib.parse.quote(bucket, safe='')}/o/"
        f"{urllib.parse.quote(name, safe='')}?{query}"
    )
    object_request = urllib.request.Request(
        object_url,
        headers={
            "Authorization": f"Bearer {token}",
            "Accept": "application/json",
        },
    )
    live = _bounded_http_json(
        client,
        object_request,
        label="GCS release object metadata",
        timeout=20,
    )
    expected_keys = {
        "bucket",
        "name",
        "generation",
        "metageneration",
        "size",
        "metadata",
        "crc32c",
        "md5Hash",
    }
    if set(live) != expected_keys:
        _die("GCS release object metadata shape is not exact")
    metadata = live.get("metadata")
    expected_metadata = {
        "omega-artifact-sha256": attestation["artifact_sha256"],
        "omega-deploy-ref": attestation["deploy_ref"],
    }
    expected = {
        "bucket": bucket,
        "name": name,
        "generation": attestation["artifact_generation"],
        "metageneration": attestation["artifact_metageneration"],
        "size": str(attestation["artifact_size_bytes"]),
        "crc32c": attestation["artifact_crc32c"],
        "md5Hash": attestation["artifact_md5"],
    }
    if metadata != expected_metadata or any(
        live.get(key) != value for key, value in expected.items()
    ):
        _die("live GCS release object differs from the release attestation")
    return {
        "artifact_uri": attestation["artifact_uri"],
        "artifact_generation": attestation["artifact_generation"],
        "artifact_metageneration": attestation["artifact_metageneration"],
        "artifact_sha256": attestation["artifact_sha256"],
    }


def _validate_read_only_mounts(raw: bytes, expected_source: Path, target: str) -> None:
    if len(raw) > MAX_DOCKER_INSPECT_BYTES:
        _die("Docker mount inspection exceeds the bounded input size")
    try:
        mounts = json.loads(raw.decode("utf-8"))
    except (UnicodeError, json.JSONDecodeError) as exc:
        _die(f"Docker mount inspection is invalid: {exc}")
    if not isinstance(mounts, list):
        _die("Docker mount inspection must be one JSON list")
    matching = [
        mount
        for mount in mounts
        if isinstance(mount, dict) and mount.get("Destination") == target
    ]
    if len(matching) != 1:
        _die(f"container must have exactly one {target} mount")
    mount = matching[0]
    source = mount.get("Source")
    if (
        mount.get("Type") != "bind"
        or mount.get("RW") is not False
        or not isinstance(source, str)
        or Path(source).resolve() != expected_source.resolve()
    ):
        _die(f"container {target} mount is not the exact read-only release directory")


def _validate_database_target(
    raw: bytes,
    *,
    expected_container_id: str,
    expected_service: str,
    expected_project: str,
    expected_init_source: Path,
    expected_image_reference: str,
) -> dict[str, str]:
    """Seal one running PostgreSQL container and its persistent data target."""
    if len(raw) > MAX_DOCKER_INSPECT_BYTES:
        _die("Docker target inspection exceeds the bounded input size")
    try:
        values = json.loads(raw.decode("utf-8", errors="strict"))
    except (UnicodeError, json.JSONDecodeError) as exc:
        _die(f"Docker target inspection is invalid: {exc}")
    if (
        not isinstance(values, list)
        or len(values) != 1
        or not isinstance(values[0], dict)
    ):
        _die("Docker target inspection must contain exactly one object")
    value = values[0]
    if (
        not _CONTAINER_ID.fullmatch(expected_container_id)
        or expected_service not in {"postgres", "postgres_gold"}
        or not _COMPOSE_PROJECT.fullmatch(expected_project)
        or value.get("Id") != expected_container_id
        or value.get("State", {}).get("Running") is not True
        or value.get("State", {}).get("Status") != "running"
        or value.get("RestartCount") != 0
    ):
        _die("running PostgreSQL container identity differs")
    config = value.get("Config")
    labels = config.get("Labels") if isinstance(config, dict) else None
    if (
        not isinstance(config, dict)
        or config.get("Image") != expected_image_reference
        or not _IMAGE_ID.fullmatch(value.get("Image", ""))
        or not isinstance(labels, dict)
        or labels.get("com.docker.compose.project") != expected_project
        or labels.get("com.docker.compose.service") != expected_service
        or labels.get("com.docker.compose.container-number") != "1"
        or labels.get("com.docker.compose.oneoff") != "False"
        or labels.get("com.docker.compose.image") != value.get("Image")
    ):
        _die("PostgreSQL image or Compose identity differs")
    mounts = value.get("Mounts")
    if not isinstance(mounts, list) or len(mounts) != 2:
        _die("PostgreSQL mount inventory is not exact")
    init_mount = [
        m
        for m in mounts
        if isinstance(m, dict) and m.get("Destination") == "/docker-entrypoint-initdb.d"
    ]
    data_mount = [
        m
        for m in mounts
        if isinstance(m, dict) and m.get("Destination") == "/var/lib/postgresql/data"
    ]
    if len(init_mount) != 1 or len(data_mount) != 1:
        _die("PostgreSQL mount destinations are not exact")
    init = init_mount[0]
    data = data_mount[0]
    volume = data.get("Name")
    if (
        init.get("Type") != "bind"
        or init.get("RW") is not False
        or not isinstance(init.get("Source"), str)
        or Path(init["Source"]).resolve() != expected_init_source.resolve()
        or data.get("Type") != "volume"
        or data.get("RW") is not True
        or not isinstance(volume, str)
        or not _VOLUME_NAME.fullmatch(volume)
    ):
        _die("PostgreSQL migration source or persistent volume differs")
    return {
        "container_id": expected_container_id,
        "image_id": value["Image"],
        "volume": volume,
    }


def _validate_database_system_identity(raw: bytes, *, expected_database: str) -> str:
    try:
        line = raw.decode("ascii", errors="strict")
    except UnicodeDecodeError:
        _die("PostgreSQL system identity output is not ASCII")
    lines = line.splitlines()
    if len(lines) != 1 or expected_database not in {
        "modecissions",
        "modecissions_gold",
    }:
        _die("PostgreSQL system identity output shape differs")
    fields = lines[0].split("|")
    if (
        len(fields) != 4
        or fields[0] != expected_database
        or fields[1] != "postgres"
        or fields[2] != "postgres"
        or not _SYSTEM_IDENTIFIER.fullmatch(fields[3])
    ):
        _die("PostgreSQL database/system identifier differs")
    return fields[3]


def _verified_pending_sql_snapshot(contract: "Contract", filename: str) -> bytes:
    path = _migration_path("operational", filename)
    payload = _read_regular_snapshot(path, maximum=MAX_AUTHORITY_BYTES)
    if hashlib.sha256(payload).hexdigest() != contract.release["operational"][filename]:
        _die(f"pending migration changed while snapshotting: {filename}")
    try:
        text = payload.decode("utf-8")
    except UnicodeError:
        _die(f"pending migration is not UTF-8: {filename}")
    if (
        not payload.endswith(b"\n")
        or "\x00" in text
        or _TRANSACTION_TERMINATOR.search(text)
    ):
        _die(f"pending migration contains a transaction terminator: {filename}")
    if _PSQL_META_COMMAND.search(text):
        _die(f"pending migration contains a psql meta-command: {filename}")
    return payload


def _load_json(path: Path) -> dict[str, Any]:
    return _decode_unique_json_object(
        _read_regular_snapshot(path, maximum=MAX_AUTHORITY_BYTES), str(path)
    )


def _read_receipt_event(
    directory_descriptor: int,
    filename: str,
    *,
    expected_uid: int,
    expected_gid: int,
) -> dict[str, Any]:
    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(filename, flags, dir_fd=directory_descriptor)
    except OSError as exc:
        _die(f"cannot safely open migration receipt event {filename}: {exc}")
    try:
        before = os.fstat(descriptor)
        if (
            not stat.S_ISREG(before.st_mode)
            or before.st_uid != expected_uid
            or before.st_gid != expected_gid
            or stat.S_IMODE(before.st_mode) != 0o400
            or before.st_nlink != 1
            or not 1 <= before.st_size <= 4096
        ):
            _die(f"migration receipt event is not immutable: {filename}")
        chunks: list[bytes] = []
        remaining = before.st_size
        while remaining:
            chunk = os.read(descriptor, min(remaining, 4096))
            if not chunk:
                _die(f"migration receipt event was truncated: {filename}")
            chunks.append(chunk)
            remaining -= len(chunk)
        after = os.fstat(descriptor)
        identity = lambda value: (  # noqa: E731
            value.st_dev,
            value.st_ino,
            value.st_size,
            value.st_mtime_ns,
            value.st_ctime_ns,
        )
        if identity(before) != identity(after):
            _die(f"migration receipt event changed while reading: {filename}")
        return _decode_unique_json_object(b"".join(chunks), filename)
    finally:
        os.close(descriptor)


def _validate_migration_success_receipt(
    receipt_directory: Path,
    *,
    candidate_ref: str,
    release_version: str,
    release_manifest_sha256: str,
    run_id: str,
    canonical_root: Path = Path("/opt/modecissions/shared/operation-receipts"),
    expected_uid: int = 0,
    expected_gid: int = 0,
) -> dict[str, Any]:
    """Validate one exact, immutable, successful migration receipt directory."""
    if _SHA40.fullmatch(candidate_ref) is None:
        _die("migration receipt candidate ref is invalid")
    if release_version != RELEASE_VERSION:
        _die("migration receipt release version is invalid")
    if _SHA256.fullmatch(release_manifest_sha256) is None:
        _die("migration receipt manifest hash is invalid")
    run_match = _MIGRATION_RUN_ID.fullmatch(run_id)
    if run_match is None:
        _die("migration receipt run identity is invalid")
    if (
        not receipt_directory.is_absolute()
        or receipt_directory.parent != canonical_root
    ):
        _die("migration receipt directory is outside the canonical root")
    name_match = _RECEIPT_NAME.fullmatch(receipt_directory.name)
    if (
        name_match is None
        or name_match.group(1) != candidate_ref
        or name_match.group(3) != run_match.group(1)
    ):
        _die("migration receipt directory identity differs")

    directory_flags = (
        os.O_RDONLY
        | os.O_DIRECTORY
        | getattr(os, "O_CLOEXEC", 0)
        | getattr(os, "O_NOFOLLOW", 0)
    )
    descriptors: list[int] = []
    try:
        try:
            root_descriptor = os.open(canonical_root, directory_flags)
        except OSError as exc:
            _die(f"cannot safely open migration receipt root: {exc}")
        descriptors.append(root_descriptor)
        root_info = os.fstat(root_descriptor)
        if (
            not stat.S_ISDIR(root_info.st_mode)
            or root_info.st_uid != expected_uid
            or root_info.st_gid != expected_gid
            or stat.S_IMODE(root_info.st_mode) != 0o700
        ):
            _die("migration receipt root ownership or mode differs")
        try:
            receipt_descriptor = os.open(
                receipt_directory.name, directory_flags, dir_fd=root_descriptor
            )
        except OSError as exc:
            _die(f"cannot safely open migration receipt directory: {exc}")
        descriptors.append(receipt_descriptor)
        receipt_info = os.fstat(receipt_descriptor)
        if (
            not stat.S_ISDIR(receipt_info.st_mode)
            or receipt_info.st_uid != expected_uid
            or receipt_info.st_gid != expected_gid
            or stat.S_IMODE(receipt_info.st_mode) != 0o700
        ):
            _die("migration receipt directory ownership or mode differs")

        inventory = set(os.listdir(receipt_descriptor))
        if inventory != set(MIGRATION_SUCCESS_EVENTS):
            _die("migration receipt inventory is not the exact success sequence")

        previous_timestamp: datetime | None = None
        for filename, (
            phase,
            status_value,
            commit_state,
        ) in MIGRATION_SUCCESS_EVENTS.items():
            payload = _read_receipt_event(
                receipt_descriptor,
                filename,
                expected_uid=expected_uid,
                expected_gid=expected_gid,
            )
            if set(payload) != MIGRATION_RECEIPT_FIELDS:
                _die(f"migration receipt event fields differ: {filename}")
            if (
                type(payload["schema_version"]) is not int
                or payload["schema_version"] != 1
                or type(payload["exit_code"]) is not int
                or payload["exit_code"] != 0
                or payload["operation"] != "gcp_day2_database_migration"
                or payload["candidate_ref"] != candidate_ref
                or payload["release_version"] != release_version
                or payload["release_manifest_sha256"] != release_manifest_sha256
                or payload["run_id"] != run_id
                or payload["phase"] != phase
                or payload["status"] != status_value
                or payload["database_commit_state"] != commit_state
            ):
                _die(f"migration receipt event contract differs: {filename}")
            recorded_at = payload["recorded_at"]
            if not isinstance(recorded_at, str) or not _RECEIPT_TIMESTAMP.fullmatch(
                recorded_at
            ):
                _die(f"migration receipt timestamp is invalid: {filename}")
            try:
                parsed_timestamp = datetime.strptime(recorded_at, "%Y-%m-%dT%H:%M:%SZ")
            except ValueError:
                _die(f"migration receipt timestamp is invalid: {filename}")
            if previous_timestamp is not None and parsed_timestamp < previous_timestamp:
                _die("migration receipt timestamps are not monotonic")
            previous_timestamp = parsed_timestamp
    finally:
        for descriptor in reversed(descriptors):
            os.close(descriptor)

    return {
        "candidate_ref": candidate_ref,
        "database_commit_state": "gold_operational_and_authority",
        "receipt_dir": str(receipt_directory),
        "release_manifest_sha256": release_manifest_sha256,
        "release_version": release_version,
        "run_id": run_id,
        "status": "PASS",
    }


def _manifest_map(
    manifest: dict[str, Any], database: str, *, expected_count: int
) -> dict[str, str]:
    databases = manifest.get("databases")
    if not isinstance(databases, dict) or set(databases) != {"operational", "gold"}:
        _die("manifest database keys must be exactly operational and gold")
    migrations = databases.get(database)
    if not isinstance(migrations, dict) or len(migrations) != expected_count:
        _die(
            f"{database} manifest count is not exactly {expected_count}: "
            f"{len(migrations) if isinstance(migrations, dict) else 'invalid'}"
        )
    matcher = _GOLD_NAME if database == "gold" else _OP_NAME
    for filename, checksum in migrations.items():
        if not isinstance(filename, str) or not matcher.fullmatch(filename):
            _die(f"unsafe {database} migration filename: {filename!r}")
        if not isinstance(checksum, str) or not _SHA256.fullmatch(checksum):
            _die(f"invalid checksum for {filename}")
    return dict(migrations)


@dataclass(frozen=True)
class Contract:
    old_ref: str
    candidate_ref: str
    release_version: str
    baseline_path: Path
    release_path: Path
    baseline_sha256: str
    release_sha256: str
    baseline: dict[str, dict[str, str]]
    release: dict[str, dict[str, str]]

    def as_plan_fields(self) -> dict[str, Any]:
        return {
            "old_ref": self.old_ref,
            "candidate_ref": self.candidate_ref,
            "release_version": self.release_version,
            "baseline_path": str(self.baseline_path),
            "release_path": str(self.release_path),
            "baseline_sha256": self.baseline_sha256,
            "release_sha256": self.release_sha256,
        }


def _migration_path(database: str, filename: str) -> Path:
    if database == "gold":
        return ROOT / "infra" / "init_gold" / filename.removeprefix("gold/")
    return ROOT / "infra" / "init" / filename


def _validate_contract(args: argparse.Namespace) -> Contract:
    if not _SHA40.fullmatch(args.old_ref or ""):
        _die("OLD_REF must be one exact lowercase 40-character SHA")
    if not _SHA40.fullmatch(args.candidate_ref or ""):
        _die("candidate ref must be one exact lowercase 40-character SHA")
    if args.release_version != RELEASE_VERSION:
        _die(f"release version must be exactly {RELEASE_VERSION}")
    if args.old_ref != BASELINE_REF:
        _die(f"old ref must match the audited GCP baseline {BASELINE_REF}")

    manifest_root = (ROOT / "infra" / "migrations" / "manifests").resolve()
    baseline_path = Path(args.baseline_manifest).resolve()
    release_path = Path(args.release_manifest).resolve()
    if baseline_path != manifest_root / BASELINE_NAME:
        _die("baseline manifest path is not the canonical release artifact path")
    if release_path != manifest_root / RELEASE_NAME:
        _die("release manifest path is not the canonical release artifact path")
    if args.baseline_manifest_sha256 != PINNED_BASELINE_MANIFEST_SHA256:
        _die("baseline manifest hash does not match the reviewed runner pin")
    if args.release_manifest_sha256 != PINNED_RELEASE_MANIFEST_SHA256:
        _die("release manifest hash does not match the reviewed runner pin")
    baseline_raw = _read_regular_snapshot(baseline_path, maximum=MAX_AUTHORITY_BYTES)
    release_raw = _read_regular_snapshot(release_path, maximum=MAX_AUTHORITY_BYTES)
    if hashlib.sha256(baseline_raw).hexdigest() != args.baseline_manifest_sha256:
        _die("baseline manifest bytes do not match the supplied hash")
    if hashlib.sha256(release_raw).hexdigest() != args.release_manifest_sha256:
        _die("release manifest bytes do not match the supplied hash")

    baseline_json = _decode_unique_json_object(baseline_raw, "baseline manifest")
    release_json = _decode_unique_json_object(release_raw, "release manifest")
    if baseline_json.get("schema_version") != 1:
        _die("baseline manifest schema version is not 1")
    if baseline_json.get("kind") != "omega_database_migration_baseline":
        _die("baseline manifest kind is invalid")
    if baseline_json.get("environment") != "gcp-canonical":
        _die("baseline environment is not gcp-canonical")
    if baseline_json.get("source_ref") != args.old_ref:
        _die("baseline manifest is not anchored to OLD_REF")
    if baseline_json.get("checksum_provenance") != BASELINE_PROVENANCE:
        _die("baseline checksum provenance is not the reviewed expected-byte caveat")
    if release_json.get("schema_version") != 1:
        _die("release manifest schema version is not 1")
    if release_json.get("kind") != "omega_database_migration_release_lock":
        _die("release manifest kind is invalid")
    if release_json.get("release_version") != args.release_version:
        _die("release manifest version does not match VERSION")
    if release_json.get("candidate_ref_binding") != "runtime_exact_40_hex_sha":
        _die("release manifest does not require an exact runtime candidate SHA")
    if release_json.get("baseline_source_ref") != args.old_ref:
        _die("release manifest baseline does not match OLD_REF")
    if release_json.get("evidence_contract") != RELEASE_EVIDENCE_CONTRACT:
        _die(
            "release evidence contract does not separate baseline and pending evidence"
        )
    if release_json.get("allowed_new_migrations") != {
        "operational": list(PENDING_OPERATIONAL),
        "gold": [],
    }:
        _die("release manifest pending set is not the audited three-file set")

    baseline = {
        "operational": _manifest_map(baseline_json, "operational", expected_count=198),
        "gold": _manifest_map(baseline_json, "gold", expected_count=12),
    }
    release = {
        "operational": _manifest_map(release_json, "operational", expected_count=201),
        "gold": _manifest_map(release_json, "gold", expected_count=12),
    }

    for database in ("operational", "gold"):
        missing = set(FRESH_BOOTSTRAP_MISSING[database])
        release_names = set(release[database])
        if not missing < release_names:
            _die(f"{database} fresh-bootstrap omission set is invalid")
        if len(release_names - missing) != FRESH_BOOTSTRAP_COUNTS[database]:
            _die(f"{database} fresh-bootstrap reviewed row count drifted")

    for database in ("operational", "gold"):
        changed = sorted(
            filename
            for filename, checksum in baseline[database].items()
            if release[database].get(filename) != checksum
        )
        missing = sorted(set(baseline[database]) - set(release[database]))
        added = sorted(set(release[database]) - set(baseline[database]))
        allowed = sorted(PENDING_OPERATIONAL if database == "operational" else ())
        if changed or missing or added != allowed:
            _die(
                f"{database} release drift: changed={changed} missing={missing} "
                f"added={added} allowed={allowed}"
            )
        disk_names = {
            path.name if database == "operational" else f"gold/{path.name}"
            for path in (
                ROOT
                / ("infra/init" if database == "operational" else "infra/init_gold")
            ).glob(
                "[0-9][0-9]*_*.sql" if database == "operational" else "[0-9][0-9]_*.sql"
            )
        }
        if disk_names != set(release[database]):
            _die(f"{database} migration directory is not the release manifest set")
        for filename, expected in release[database].items():
            migration = _migration_path(database, filename)
            migration_raw = _read_regular_snapshot(
                migration, maximum=MAX_AUTHORITY_BYTES
            )
            if hashlib.sha256(migration_raw).hexdigest() != expected:
                _die(f"release migration bytes differ from lock: {filename}")
            if database == "operational" and filename in PENDING_OPERATIONAL:
                try:
                    text = migration_raw.decode("utf-8", errors="strict")
                except UnicodeDecodeError:
                    _die(f"pending migration is not UTF-8: {filename}")
                if _TRANSACTION_TERMINATOR.search(text):
                    _die(
                        f"pending migration contains a transaction terminator: {filename}"
                    )
                if _PSQL_META_COMMAND.search(text):
                    _die(f"pending migration contains a psql meta-command: {filename}")

    version_path = ROOT / "VERSION"
    if version_path.read_text(encoding="utf-8").strip() != args.release_version:
        _die("VERSION does not match the release migration lock")

    return Contract(
        old_ref=args.old_ref,
        candidate_ref=args.candidate_ref,
        release_version=args.release_version,
        baseline_path=baseline_path,
        release_path=release_path,
        baseline_sha256=args.baseline_manifest_sha256,
        release_sha256=args.release_manifest_sha256,
        baseline=baseline,
        release=release,
    )


@dataclass(frozen=True)
class LedgerRow:
    checksum: str | None
    source_ref: str | None
    manifest_sha256: str | None
    evidence_kind: str | None
    guarded: bool


def _load_ledger(path: Path, database: str) -> dict[str, LedgerRow]:
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeError) as exc:
        _die(f"cannot read {database} ledger: {exc}")
    if not lines:
        _die(f"{database} schema_migrations ledger is empty")
    result: dict[str, LedgerRow] = {}
    matcher = _GOLD_NAME if database == "gold" else _OP_NAME
    for number, line in enumerate(lines, 1):
        try:
            fields = json.loads(line)
        except json.JSONDecodeError:
            _die(f"{database} ledger line {number} is malformed")
        if not isinstance(fields, dict) or set(fields) != {
            "filename",
            "checksum",
            "source_ref",
            "manifest_sha256",
            "evidence_kind",
            "guarded",
        }:
            _die(f"{database} ledger line {number} has unexpected fields")
        filename = fields["filename"]
        checksum_value = fields["checksum"]
        source_value = fields["source_ref"]
        manifest_value = fields["manifest_sha256"]
        evidence_value = fields["evidence_kind"]
        guarded = fields["guarded"]
        if not isinstance(filename, str):
            _die(f"{database} ledger filename has an invalid type")
        if not matcher.fullmatch(filename) or filename in result:
            _die(f"{database} ledger filename is unsafe or duplicated: {filename!r}")
        if checksum_value is not None and (
            not isinstance(checksum_value, str) or not _SHA256.fullmatch(checksum_value)
        ):
            _die(f"{database} ledger checksum is invalid: {filename}")
        if source_value is not None and (
            not isinstance(source_value, str) or not _SHA40.fullmatch(source_value)
        ):
            _die(f"{database} ledger source ref is invalid: {filename}")
        if manifest_value is not None and (
            not isinstance(manifest_value, str) or not _SHA256.fullmatch(manifest_value)
        ):
            _die(f"{database} ledger manifest hash is invalid: {filename}")
        if evidence_value is not None and evidence_value not in {
            BASELINE_EVIDENCE,
            GUARDED_EVIDENCE,
        }:
            _die(f"{database} ledger evidence kind is invalid: {filename}")
        if not isinstance(guarded, bool):
            _die(f"{database} ledger guarded receipt flag is invalid: {filename}")
        if checksum_value is None and (
            source_value is not None
            or manifest_value is not None
            or evidence_value is not None
            or guarded
        ):
            _die(f"{database} ledger has provenance without a checksum: {filename}")
        if guarded != (evidence_value == GUARDED_EVIDENCE):
            _die(f"{database} ledger evidence/receipt mismatch: {filename}")
        result[filename] = LedgerRow(
            checksum=checksum_value,
            source_ref=source_value,
            manifest_sha256=manifest_value,
            evidence_kind=evidence_value,
            guarded=guarded,
        )
    return result


def _expected_provenance(
    contract: Contract, database: str, filename: str
) -> tuple[str, str]:
    if database == "operational" and filename in PENDING_OPERATIONAL:
        return contract.candidate_ref, contract.release_sha256
    return contract.old_ref, contract.baseline_sha256


def _row_is_blank(row: LedgerRow) -> bool:
    return (
        row.checksum is None
        and row.source_ref is None
        and row.manifest_sha256 is None
        and row.evidence_kind is None
        and not row.guarded
    )


def _row_matches(
    contract: Contract,
    database: str,
    filename: str,
    row: LedgerRow,
    *,
    pending_evidence: str,
) -> bool:
    source_ref, manifest_sha256 = _expected_provenance(contract, database, filename)
    is_pending = database == "operational" and filename in PENDING_OPERATIONAL
    evidence_kind = pending_evidence if is_pending else BASELINE_EVIDENCE
    return (
        row.checksum == contract.release[database][filename]
        and row.source_ref == source_ref
        and row.manifest_sha256 == manifest_sha256
        and row.evidence_kind == evidence_kind
        and row.guarded == (evidence_kind == GUARDED_EVIDENCE)
    )


def _rows_match(
    contract: Contract,
    database: str,
    rows: dict[str, LedgerRow],
    *,
    pending_evidence: str,
) -> bool:
    return all(
        _row_matches(
            contract,
            database,
            filename,
            row,
            pending_evidence=pending_evidence,
        )
        for filename, row in rows.items()
    )


def _validate_ledger(
    contract: Contract,
    database: str,
    rows: dict[str, LedgerRow],
    *,
    require_release: bool,
    allow_bootstrap_release: bool = False,
    expected_pending_evidence: str = GUARDED_EVIDENCE,
) -> str:
    if expected_pending_evidence not in {BASELINE_EVIDENCE, GUARDED_EVIDENCE}:
        _die("expected pending evidence kind is invalid")
    names = set(rows)
    baseline_names = set(contract.baseline[database])
    release_names = set(contract.release[database])
    fresh_bootstrap_names = release_names - set(FRESH_BOOTSTRAP_MISSING[database])
    fresh_bootstrap = names == fresh_bootstrap_names
    if (
        names != baseline_names
        and names != release_names
        and not (allow_bootstrap_release and fresh_bootstrap)
    ):
        unknown = sorted(names - release_names)
        missing_baseline = sorted(baseline_names - names)
        partial_pending = sorted(names & set(PENDING_OPERATIONAL))
        _die(
            f"{database} ledger is partial/tampered: unknown={unknown} "
            f"missing_baseline={missing_baseline} pending_present={partial_pending}"
        )

    expected = contract.release[database]
    for filename, row in rows.items():
        if row.checksum is not None and row.checksum != expected[filename]:
            _die(f"{database} ledger checksum mismatch: {filename}")
        expected_ref, expected_manifest = _expected_provenance(
            contract, database, filename
        )
        if row.source_ref is not None and row.source_ref != expected_ref:
            _die(f"{database} ledger source provenance mismatch: {filename}")
        if row.manifest_sha256 is not None and row.manifest_sha256 != expected_manifest:
            _die(f"{database} ledger manifest provenance mismatch: {filename}")

    fully_blank = all(_row_is_blank(row) for row in rows.values())
    fully_baseline_expected = _rows_match(
        contract,
        database,
        rows,
        pending_evidence=BASELINE_EVIDENCE,
    )
    fully_guarded = _rows_match(
        contract,
        database,
        rows,
        pending_evidence=GUARDED_EVIDENCE,
    )

    if fresh_bootstrap:
        if not fully_blank:
            _die(
                f"{database} fresh-bootstrap ledger is not the exact blank "
                "entrypoint profile"
            )
        state = "fresh_bootstrap"
    elif database == "gold" or names == baseline_names:
        if fully_blank:
            state = "baseline"
        elif fully_baseline_expected:
            state = "baseline_expected"
        else:
            _die(
                f"{database} ledger is neither uniformly blank nor exact "
                f"{BASELINE_EVIDENCE} evidence"
            )
    elif fully_guarded:
        state = "release_guarded"
    elif allow_bootstrap_release and fully_baseline_expected:
        state = "release_expected"
    elif allow_bootstrap_release and fully_blank:
        state = "bootstrap_release"
    else:
        _die(
            "operational release filenames lack the exact guarded transaction "
            "receipt; refusing to bless a partial or source-inferred pending set"
        )

    if require_release:
        expected_state = (
            "release_guarded"
            if expected_pending_evidence == GUARDED_EVIDENCE
            else "release_expected"
        )
        if database == "gold":
            expected_state = "baseline_expected"
        if state != expected_state:
            _die(
                f"{database} ledger evidence profile is {state}, "
                f"expected {expected_state}"
            )
    return state


def _write_plan(
    contract: Contract,
    operational_state: str,
    gold_state: str,
    path: Path,
) -> None:
    expected_pending_evidence = _expected_pending_evidence_for_state(operational_state)
    value = {
        "schema_version": 2,
        **contract.as_plan_fields(),
        "operational_state": operational_state,
        "gold_state": gold_state,
        "expected_pending_evidence": expected_pending_evidence,
    }
    path.write_text(json.dumps(value, sort_keys=True) + "\n", encoding="utf-8")


def _args_from_plan(plan: dict[str, Any]) -> argparse.Namespace:
    required = {
        "old_ref",
        "candidate_ref",
        "release_version",
        "baseline_path",
        "release_path",
        "baseline_sha256",
        "release_sha256",
    }
    expected_keys = required | {
        "schema_version",
        "operational_state",
        "gold_state",
        "expected_pending_evidence",
    }
    if plan.get("schema_version") != 2 or set(plan) != expected_keys:
        _die("migration plan is malformed")
    if plan.get("expected_pending_evidence") not in {
        BASELINE_EVIDENCE,
        GUARDED_EVIDENCE,
    }:
        _die("migration plan pending evidence profile is invalid")
    return argparse.Namespace(
        old_ref=plan["old_ref"],
        candidate_ref=plan["candidate_ref"],
        release_version=plan["release_version"],
        baseline_manifest=plan["baseline_path"],
        release_manifest=plan["release_path"],
        baseline_manifest_sha256=plan["baseline_sha256"],
        release_manifest_sha256=plan["release_sha256"],
    )


def _expected_pending_evidence_for_state(operational_state: str) -> str:
    if operational_state in {
        "fresh_bootstrap",
        "bootstrap_release",
        "release_expected",
    }:
        return BASELINE_EVIDENCE
    if operational_state in {"baseline", "baseline_expected", "release_guarded"}:
        return GUARDED_EVIDENCE
    _die("migration plan operational state is invalid")


def _validate_plan(
    plan: dict[str, Any], *, expected_contract: Contract | None = None
) -> Contract:
    contract = _validate_contract(_args_from_plan(plan))
    if expected_contract is not None and (
        contract.as_plan_fields() != expected_contract.as_plan_fields()
    ):
        _die("migration plan contract does not match postflight contract")
    operational_state = plan.get("operational_state")
    gold_state = plan.get("gold_state")
    if gold_state not in {"baseline", "baseline_expected", "fresh_bootstrap"}:
        _die("migration plan Gold state is invalid")
    expected_evidence = _expected_pending_evidence_for_state(operational_state)
    if plan.get("expected_pending_evidence") != expected_evidence:
        _die("migration plan pending evidence was not derived from preflight state")
    return contract


def _sql_literal(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def _ledger_authority_assertion_lines() -> list[str]:
    """Return fail-closed SQL assertions for the server-owned ledger."""

    return [
        "DO $omega_ledger_authority_assert$",
        "BEGIN",
        "  IF session_user <> 'postgres' OR current_user <> 'postgres' THEN",
        "    RAISE EXCEPTION 'migration ledger verification requires postgres';",
        "  END IF;",
        "  IF to_regclass('public.schema_migrations') IS NULL",
        "     OR (SELECT tableowner FROM pg_tables",
        "          WHERE schemaname='public' AND tablename='schema_migrations') <> 'postgres'",
        "     OR NOT has_table_privilege('postgres', 'public.schema_migrations', 'INSERT,UPDATE,DELETE,TRUNCATE,TRIGGER,REFERENCES') THEN",
        "    RAISE EXCEPTION 'postgres does not own and control the migration ledger';",
        "  END IF;",
        "  IF EXISTS (",
        "    SELECT 1",
        "      FROM pg_class relation",
        "      CROSS JOIN LATERAL aclexplode(COALESCE(relation.relacl, acldefault('r', relation.relowner))) privilege",
        "     WHERE relation.oid = 'public.schema_migrations'::regclass",
        "       AND privilege.grantee <> (SELECT oid FROM pg_roles WHERE rolname='postgres')",
        "  ) THEN",
        "    RAISE EXCEPTION 'a non-postgres table ACL remains on the migration ledger';",
        "  END IF;",
        "  IF EXISTS (",
        "    SELECT 1",
        "      FROM pg_class relation",
        "      JOIN pg_attribute attribute ON attribute.attrelid=relation.oid",
        "      CROSS JOIN LATERAL aclexplode(COALESCE(attribute.attacl, acldefault('c', relation.relowner))) privilege",
        "     WHERE relation.oid = 'public.schema_migrations'::regclass",
        "       AND attribute.attnum > 0 AND NOT attribute.attisdropped",
        "       AND privilege.grantee <> (SELECT oid FROM pg_roles WHERE rolname='postgres')",
        "       AND NOT (privilege.grantee = COALESCE((SELECT oid FROM pg_roles WHERE rolname='omega_console'), 0::oid)",
        "                AND privilege.privilege_type = 'SELECT'",
        "                AND attribute.attname IN ('filename','applied_at','checksum'))",
        "  ) THEN",
        "    RAISE EXCEPTION 'an unexpected column ACL remains on the migration ledger';",
        "  END IF;",
        "  IF EXISTS (",
        "    SELECT 1 FROM pg_roles",
        "     WHERE rolname LIKE 'omega\\_%' ESCAPE '\\'",
        "       AND (rolsuper OR rolcreaterole",
        "         OR has_table_privilege(rolname, 'public.schema_migrations', 'INSERT')",
        "         OR has_table_privilege(rolname, 'public.schema_migrations', 'UPDATE')",
        "         OR has_table_privilege(rolname, 'public.schema_migrations', 'DELETE')",
        "         OR has_table_privilege(rolname, 'public.schema_migrations', 'TRUNCATE')",
        "         OR pg_has_role(oid, 'pg_write_all_data', 'MEMBER')",
        "         OR pg_has_role(oid, 'pg_read_all_data', 'MEMBER')",
        "         OR pg_has_role(oid, 'postgres', 'MEMBER'))",
        "  ) THEN",
        "    RAISE EXCEPTION 'an application role bypasses exact migration ledger authority';",
        "  END IF;",
        "  IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname='omega_console') THEN",
        "    IF NOT has_column_privilege('omega_console', 'public.schema_migrations', 'filename', 'SELECT')",
        "       OR NOT has_column_privilege('omega_console', 'public.schema_migrations', 'applied_at', 'SELECT')",
        "       OR NOT has_column_privilege('omega_console', 'public.schema_migrations', 'checksum', 'SELECT')",
        "       OR pg_has_role('omega_console', 'pg_read_all_data', 'MEMBER')",
        "       OR EXISTS (",
        "         SELECT 1 FROM pg_attribute",
        "          WHERE attrelid='public.schema_migrations'::regclass",
        "            AND attnum > 0 AND NOT attisdropped",
        "            AND attname NOT IN ('filename','applied_at','checksum')",
        "            AND has_column_privilege('omega_console', 'public.schema_migrations', attname, 'SELECT')",
        "       ) THEN",
        "      RAISE EXCEPTION 'omega_console migration ledger projection is not exact';",
        "    END IF;",
        "  END IF;",
        "  IF to_regclass('public.schema_migrations_id_seq') IS NOT NULL THEN",
        "    IF (SELECT sequenceowner FROM pg_sequences",
        "         WHERE schemaname='public' AND sequencename='schema_migrations_id_seq') <> 'postgres'",
        "       OR NOT has_sequence_privilege('postgres', 'public.schema_migrations_id_seq', 'USAGE,SELECT,UPDATE') THEN",
        "      RAISE EXCEPTION 'postgres does not own and control the migration ledger sequence';",
        "    END IF;",
        "    IF EXISTS (",
        "      SELECT 1",
        "        FROM pg_class relation",
        "        CROSS JOIN LATERAL aclexplode(COALESCE(relation.relacl, acldefault('S', relation.relowner))) privilege",
        "       WHERE relation.oid = 'public.schema_migrations_id_seq'::regclass",
        "         AND privilege.grantee <> (SELECT oid FROM pg_roles WHERE rolname='postgres')",
        "         AND privilege.privilege_type IN ('USAGE','SELECT','UPDATE')",
        "    ) THEN",
        "      RAISE EXCEPTION 'a non-postgres ACL can use the migration ledger sequence';",
        "    END IF;",
        "  END IF;",
        "END",
        "$omega_ledger_authority_assert$;",
    ]


def _values_sql(
    contract: Contract,
    database: str,
    filenames: list[str],
    *,
    pending_evidence: str,
) -> str:
    rows = []
    for filename in filenames:
        source_ref, manifest_sha = _expected_provenance(contract, database, filename)
        is_pending = database == "operational" and filename in PENDING_OPERATIONAL
        evidence_kind = pending_evidence if is_pending else BASELINE_EVIDENCE
        rows.append(
            "("
            + ", ".join(
                _sql_literal(value)
                for value in (
                    filename,
                    contract.release[database][filename],
                    source_ref,
                    manifest_sha,
                    evidence_kind,
                )
            )
            + f", {str(evidence_kind == GUARDED_EVIDENCE).lower()}"
            + ")"
        )
    return ",\n  ".join(rows)


def _render_sql_bytes(contract: Contract, database: str, state: str) -> bytes:
    if database not in {"operational", "gold"}:
        _die("database must be operational or gold")
    if state not in {
        "baseline",
        "baseline_expected",
        "fresh_bootstrap",
        "bootstrap_release",
        "release_expected",
        "release_guarded",
    }:
        _die("plan database state is invalid")
    if database == "gold" and state not in {
        "baseline",
        "baseline_expected",
        "fresh_bootstrap",
    }:
        _die("Gold plan state is invalid")

    pending_evidence = (
        BASELINE_EVIDENCE
        if state in {"fresh_bootstrap", "bootstrap_release", "release_expected"}
        else GUARDED_EVIDENCE
    )
    blank_preflight = state in {"baseline", "fresh_bootstrap", "bootstrap_release"}

    if state == "fresh_bootstrap":
        pre_names = sorted(
            set(contract.release[database]) - set(FRESH_BOOTSTRAP_MISSING[database])
        )
    elif database == "operational" and state in {"baseline", "baseline_expected"}:
        pre_names = sorted(contract.baseline[database])
    else:
        pre_names = sorted(contract.release[database])
    final_names = sorted(contract.release[database])
    pre_values = _values_sql(
        contract,
        database,
        pre_names,
        pending_evidence=pending_evidence,
    )
    final_values = _values_sql(
        contract,
        database,
        final_names,
        pending_evidence=pending_evidence,
    )
    lock_key = "1" if database == "operational" else "2"
    parts = [
        "\\set ON_ERROR_STOP on",
        "SET default_transaction_read_only=off;",
        "BEGIN;",
        "SET TRANSACTION READ WRITE;",
        "SET LOCAL lock_timeout = '30s';",
        "SET LOCAL statement_timeout = '15min';",
        "SET LOCAL idle_in_transaction_session_timeout = '60s';",
        f"SELECT pg_advisory_xact_lock(145207, {lock_key});",
        "CREATE TEMP TABLE omega_expected_pre (",
        "  filename text PRIMARY KEY, checksum text NOT NULL,",
        "  source_ref text NOT NULL, manifest_sha256 text NOT NULL,",
        "  evidence_kind text NOT NULL, guarded_required boolean NOT NULL",
        ") ON COMMIT DROP;",
        "INSERT INTO omega_expected_pre VALUES",
        f"  {pre_values};",
        "DO $omega_filename$",
        "BEGIN",
        "  IF EXISTS (SELECT filename FROM public.schema_migrations EXCEPT SELECT filename FROM omega_expected_pre)",
        "     OR EXISTS (SELECT filename FROM omega_expected_pre EXCEPT SELECT filename FROM public.schema_migrations) THEN",
        "    RAISE EXCEPTION 'schema_migrations changed after two-database preflight';",
        "  END IF;",
        "END",
        "$omega_filename$;",
        "ALTER TABLE public.schema_migrations",
        "  ADD COLUMN IF NOT EXISTS checksum text,",
        "  ADD COLUMN IF NOT EXISTS checksum_source_ref text,",
        "  ADD COLUMN IF NOT EXISTS checksum_manifest_sha256 text,",
        "  ADD COLUMN IF NOT EXISTS checksum_evidence_kind text,",
        "  ADD COLUMN IF NOT EXISTS checksum_guarded_at timestamptz;",
        "DO $omega_preflight$",
        "BEGIN",
    ]

    if blank_preflight:
        parts.extend(
            [
                "  IF EXISTS (",
                "    SELECT 1 FROM public.schema_migrations sm",
                "    JOIN omega_expected_pre e USING (filename)",
                "    WHERE sm.checksum IS NOT NULL",
                "       OR sm.checksum_source_ref IS NOT NULL",
                "       OR sm.checksum_manifest_sha256 IS NOT NULL",
                "       OR sm.checksum_evidence_kind IS NOT NULL",
                "       OR sm.checksum_guarded_at IS NOT NULL",
                "  ) THEN",
                "    RAISE EXCEPTION 'blank schema_migrations profile changed after preflight';",
                "  END IF;",
            ]
        )

    else:
        parts.extend(
            [
                "  IF EXISTS (",
                "    SELECT 1 FROM public.schema_migrations sm",
                "    JOIN omega_expected_pre e USING (filename)",
                "    WHERE sm.checksum IS DISTINCT FROM e.checksum",
                "       OR sm.checksum_source_ref IS DISTINCT FROM e.source_ref",
                "       OR sm.checksum_manifest_sha256 IS DISTINCT FROM e.manifest_sha256",
                "       OR sm.checksum_evidence_kind IS DISTINCT FROM e.evidence_kind",
                "       OR ((sm.checksum_guarded_at IS NOT NULL) IS DISTINCT FROM e.guarded_required)",
                "  ) THEN",
                "    RAISE EXCEPTION 'schema_migrations evidence changed after preflight';",
                "  END IF;",
            ]
        )
    parts.extend(["END", "$omega_preflight$;"])

    if blank_preflight:
        parts.extend(
            [
                "UPDATE public.schema_migrations sm",
                "   SET checksum = e.checksum,",
                "       checksum_source_ref = e.source_ref,",
                "       checksum_manifest_sha256 = e.manifest_sha256,",
                "       checksum_evidence_kind = e.evidence_kind,",
                "       checksum_guarded_at = NULL",
                "  FROM omega_expected_pre e",
                " WHERE sm.filename = e.filename;",
            ]
        )

    if state == "fresh_bootstrap":
        missing_values = _values_sql(
            contract,
            database,
            sorted(FRESH_BOOTSTRAP_MISSING[database]),
            pending_evidence=BASELINE_EVIDENCE,
        )
        parts.extend(
            [
                "CREATE TEMP TABLE omega_expected_missing (",
                "  filename text PRIMARY KEY, checksum text NOT NULL,",
                "  source_ref text NOT NULL, manifest_sha256 text NOT NULL,",
                "  evidence_kind text NOT NULL, guarded_required boolean NOT NULL",
                ") ON COMMIT DROP;",
                "INSERT INTO omega_expected_missing VALUES",
                f"  {missing_values};",
                "INSERT INTO public.schema_migrations",
                "  (filename, applied_at, checksum, checksum_source_ref,",
                "   checksum_manifest_sha256, checksum_evidence_kind,",
                "   checksum_guarded_at)",
                "SELECT filename, clock_timestamp(), checksum, source_ref,",
                "       manifest_sha256, evidence_kind, NULL",
                "  FROM omega_expected_missing",
                " ORDER BY filename;",
            ]
        )

    pending_snapshots: list[tuple[bytes, bytes]] = []
    if database == "operational" and state in {"baseline", "baseline_expected"}:
        for filename in PENDING_OPERATIONAL:
            checksum = contract.release[database][filename]
            migration_sql = _verified_pending_sql_snapshot(contract, filename)
            token = f"__OMEGA_PENDING_SQL_{checksum}__"
            begin_marker = f"-- BEGIN verified immutable snapshot: {filename}"
            end_marker = f"-- END verified immutable snapshot: {filename}"
            token_raw = token.encode("ascii")
            if (
                token_raw in migration_sql
                or begin_marker.encode("utf-8") in migration_sql
                or end_marker.encode("utf-8") in migration_sql
            ):
                _die(f"pending migration conflicts with wrapper markers: {filename}")
            pending_snapshots.append((token_raw, migration_sql))
            parts.extend(
                [
                    f"\\echo [migrate] apply {filename}",
                    f"{begin_marker}\n{token}{end_marker}",
                    "INSERT INTO public.schema_migrations",
                    "  (filename, applied_at, checksum, checksum_source_ref,",
                    "   checksum_manifest_sha256, checksum_evidence_kind,",
                    "   checksum_guarded_at)",
                    "VALUES (",
                    f"  {_sql_literal(filename)}, clock_timestamp(), {_sql_literal(checksum)},",
                    f"  {_sql_literal(contract.candidate_ref)}, {_sql_literal(contract.release_sha256)},",
                    f"  {_sql_literal(GUARDED_EVIDENCE)}, clock_timestamp()",
                    ");",
                ]
            )

    # The application role defaults historically granted omega_console DML on
    # every public table.  The migration ledger is control-plane evidence, so
    # its table (and optional identity sequence) must remain owned and writable
    # only by the PostgreSQL server owner.  Remove direct membership shortcuts
    # for application roles, then fail closed if an indirect postgres or
    # pg_write_all_data path still exists.
    parts.extend(
        [
            "DO $omega_ledger_authority$",
            "DECLARE",
            "  role_row record;",
            "  column_row record;",
            "  membership_row record;",
            "BEGIN",
            "  IF session_user <> 'postgres' OR current_user <> 'postgres' THEN",
            "    RAISE EXCEPTION 'migration ledger hardening requires postgres';",
            "  END IF;",
            "  ALTER TABLE public.schema_migrations OWNER TO postgres;",
            "  REVOKE ALL PRIVILEGES ON TABLE public.schema_migrations FROM PUBLIC;",
            "  FOR role_row IN SELECT rolname FROM pg_roles WHERE rolname <> 'postgres' LOOP",
            "    EXECUTE format('REVOKE ALL PRIVILEGES ON TABLE public.schema_migrations FROM %I', role_row.rolname);",
            "  END LOOP;",
            "  FOR column_row IN",
            "    SELECT attname FROM pg_attribute",
            "     WHERE attrelid='public.schema_migrations'::regclass",
            "       AND attnum > 0 AND NOT attisdropped",
            "  LOOP",
            "    EXECUTE format('REVOKE ALL PRIVILEGES (%I) ON TABLE public.schema_migrations FROM PUBLIC', column_row.attname);",
            "    FOR role_row IN SELECT rolname FROM pg_roles WHERE rolname <> 'postgres' LOOP",
            "      EXECUTE format('REVOKE ALL PRIVILEGES (%I) ON TABLE public.schema_migrations FROM %I', column_row.attname, role_row.rolname);",
            "    END LOOP;",
            "  END LOOP;",
            "  IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname='omega_console') THEN",
            "    GRANT SELECT (filename, applied_at, checksum)",
            "      ON TABLE public.schema_migrations TO omega_console;",
            "  END IF;",
            "  IF to_regclass('public.schema_migrations_id_seq') IS NOT NULL THEN",
            "    ALTER SEQUENCE public.schema_migrations_id_seq OWNER TO postgres;",
            "    REVOKE ALL PRIVILEGES ON SEQUENCE public.schema_migrations_id_seq FROM PUBLIC;",
            "    FOR role_row IN SELECT rolname FROM pg_roles WHERE rolname <> 'postgres' LOOP",
            "      EXECUTE format('REVOKE ALL PRIVILEGES ON SEQUENCE public.schema_migrations_id_seq FROM %I', role_row.rolname);",
            "    END LOOP;",
            "  END IF;",
            "  FOR membership_row IN",
            "    SELECT parent.rolname AS parent_name, member.rolname AS member_name",
            "      FROM pg_auth_members membership",
            "      JOIN pg_roles parent ON parent.oid = membership.roleid",
            "      JOIN pg_roles member ON member.oid = membership.member",
            "     WHERE parent.rolname IN ('postgres', 'pg_write_all_data', 'pg_read_all_data')",
            "       AND member.rolname LIKE 'omega\\_%' ESCAPE '\\'",
            "  LOOP",
            "    EXECUTE format('REVOKE %I FROM %I', membership_row.parent_name, membership_row.member_name);",
            "  END LOOP;",
            "  IF (SELECT tableowner FROM pg_tables",
            "       WHERE schemaname='public' AND tablename='schema_migrations') <> 'postgres'",
            "     OR NOT has_table_privilege('postgres', 'public.schema_migrations', 'INSERT,UPDATE,DELETE,TRUNCATE,TRIGGER,REFERENCES') THEN",
            "    RAISE EXCEPTION 'postgres does not exclusively own the migration ledger';",
            "  END IF;",
            "  IF EXISTS (",
            "    SELECT 1 FROM pg_roles",
            "     WHERE rolname LIKE 'omega\\_%' ESCAPE '\\'",
            "       AND (has_table_privilege(rolname, 'public.schema_migrations', 'INSERT')",
            "         OR has_table_privilege(rolname, 'public.schema_migrations', 'UPDATE')",
            "         OR has_table_privilege(rolname, 'public.schema_migrations', 'DELETE')",
            "         OR has_table_privilege(rolname, 'public.schema_migrations', 'TRUNCATE')",
            "         OR pg_has_role(oid, 'pg_write_all_data', 'MEMBER')",
            "         OR pg_has_role(oid, 'pg_read_all_data', 'MEMBER')",
            "         OR pg_has_role(oid, 'postgres', 'MEMBER'))",
            "  ) THEN",
            "    RAISE EXCEPTION 'an application role bypasses exact migration ledger authority';",
            "  END IF;",
            "  IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname='omega_console') THEN",
            "    IF NOT has_column_privilege('omega_console', 'public.schema_migrations', 'filename', 'SELECT')",
            "       OR NOT has_column_privilege('omega_console', 'public.schema_migrations', 'applied_at', 'SELECT')",
            "       OR NOT has_column_privilege('omega_console', 'public.schema_migrations', 'checksum', 'SELECT')",
            "       OR pg_has_role('omega_console', 'pg_read_all_data', 'MEMBER')",
            "       OR EXISTS (",
            "         SELECT 1 FROM pg_attribute",
            "          WHERE attrelid='public.schema_migrations'::regclass",
            "            AND attnum > 0 AND NOT attisdropped",
            "            AND attname NOT IN ('filename','applied_at','checksum')",
            "            AND has_column_privilege('omega_console', 'public.schema_migrations', attname, 'SELECT')",
            "       ) THEN",
            "      RAISE EXCEPTION 'omega_console migration ledger projection is not exact';",
            "    END IF;",
            "  END IF;",
            "END",
            "$omega_ledger_authority$;",
        ]
    )
    parts.extend(_ledger_authority_assertion_lines())

    parts.extend(
        [
            "CREATE TEMP TABLE omega_expected_final (",
            "  filename text PRIMARY KEY, checksum text NOT NULL,",
            "  source_ref text NOT NULL, manifest_sha256 text NOT NULL,",
            "  evidence_kind text NOT NULL, guarded_required boolean NOT NULL",
            ") ON COMMIT DROP;",
            "INSERT INTO omega_expected_final VALUES",
            f"  {final_values};",
            "DO $omega_final$",
            "BEGIN",
            "  IF EXISTS (SELECT filename FROM public.schema_migrations EXCEPT SELECT filename FROM omega_expected_final)",
            "     OR EXISTS (SELECT filename FROM omega_expected_final EXCEPT SELECT filename FROM public.schema_migrations) THEN",
            "    RAISE EXCEPTION 'final schema_migrations filename set is not exact';",
            "  END IF;",
            "  IF EXISTS (",
            "    SELECT 1 FROM public.schema_migrations sm",
            "    JOIN omega_expected_final e USING (filename)",
            "    WHERE sm.checksum IS DISTINCT FROM e.checksum",
            "       OR sm.checksum_source_ref IS DISTINCT FROM e.source_ref",
            "       OR sm.checksum_manifest_sha256 IS DISTINCT FROM e.manifest_sha256",
            "       OR sm.checksum_evidence_kind IS DISTINCT FROM e.evidence_kind",
            "       OR ((sm.checksum_guarded_at IS NOT NULL) IS DISTINCT FROM e.guarded_required)",
            "  ) THEN",
            "    RAISE EXCEPTION 'final schema_migrations evidence profile is incomplete';",
            "  END IF;",
            "END",
            "$omega_final$;",
            "COMMIT;",
            "",
        ]
    )
    rendered = "\n".join(parts).encode("utf-8")
    for token, migration_sql in pending_snapshots:
        if rendered.count(token) != 1:
            _die("pending migration wrapper token is not unique")
        rendered = rendered.replace(token, migration_sql)
    return rendered


def _render_sql(contract: Contract, database: str, state: str) -> str:
    """Compatibility wrapper for unit callers; CLI output remains byte exact."""
    return _render_sql_bytes(contract, database, state).decode("utf-8", errors="strict")


def _add_contract_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--old-ref", required=True)
    parser.add_argument("--candidate-ref", required=True)
    parser.add_argument("--release-version", required=True)
    parser.add_argument("--baseline-manifest", required=True)
    parser.add_argument("--baseline-manifest-sha256", required=True)
    parser.add_argument("--release-manifest", required=True)
    parser.add_argument("--release-manifest-sha256", required=True)


def main() -> int:
    parser = argparse.ArgumentParser()
    commands = parser.add_subparsers(dest="command", required=True)

    verify = commands.add_parser("verify-manifests")
    _add_contract_arguments(verify)

    preflight = commands.add_parser("preflight")
    _add_contract_arguments(preflight)
    preflight.add_argument("--operational-ledger", required=True)
    preflight.add_argument("--gold-ledger", required=True)
    preflight.add_argument("--plan", required=True)
    preflight.add_argument("--allow-bootstrap-release-ledger", action="store_true")

    postflight = commands.add_parser("postflight")
    _add_contract_arguments(postflight)
    postflight.add_argument("--operational-ledger", required=True)
    postflight.add_argument("--gold-ledger", required=True)
    postflight.add_argument("--plan", required=True)

    classify = commands.add_parser("classify-ledger")
    _add_contract_arguments(classify)
    classify.add_argument("--database", required=True, choices=("operational", "gold"))
    classify.add_argument("--ledger", required=True)

    render = commands.add_parser("render-sql")
    render.add_argument("--plan", required=True)
    render.add_argument("--database", required=True, choices=("operational", "gold"))

    authority = commands.add_parser("render-authority-sql")

    attestation = commands.add_parser("verify-release-attestation")
    _add_contract_arguments(attestation)
    attestation.add_argument("--attestation", required=True)
    attestation.add_argument("--tree-sha256", required=True)

    mount = commands.add_parser("verify-read-only-mount")
    mount.add_argument("--expected-source", required=True)
    mount.add_argument("--target", required=True)

    database_target = commands.add_parser("verify-database-target")
    database_target.add_argument("--container-id", required=True)
    database_target.add_argument(
        "--service", required=True, choices=("postgres", "postgres_gold")
    )
    database_target.add_argument("--compose-project", required=True)
    database_target.add_argument("--expected-init-source", required=True)
    database_target.add_argument("--expected-image-reference", required=True)

    database_system = commands.add_parser("verify-database-system-identity")
    database_system.add_argument(
        "--database", required=True, choices=("modecissions", "modecissions_gold")
    )

    plan_field = commands.add_parser("plan-field")
    plan_field.add_argument("--plan", required=True)
    plan_field.add_argument(
        "--field", required=True, choices=("expected_pending_evidence",)
    )

    receipt = commands.add_parser("validate-success-receipt")
    receipt.add_argument("--receipt-dir", required=True)
    receipt.add_argument("--candidate-ref", required=True)
    receipt.add_argument("--release-version", required=True)
    receipt.add_argument("--release-manifest-sha256", required=True)
    receipt.add_argument("--run-id", required=True)

    args = parser.parse_args()
    if args.command == "verify-read-only-mount":
        raw = sys.stdin.buffer.read(MAX_DOCKER_INSPECT_BYTES + 1)
        _validate_read_only_mounts(raw, Path(args.expected_source), args.target)
        print("read-only migration mount verified")
        return 0
    if args.command == "verify-database-target":
        raw = sys.stdin.buffer.read(MAX_DOCKER_INSPECT_BYTES + 1)
        receipt = _validate_database_target(
            raw,
            expected_container_id=args.container_id,
            expected_service=args.service,
            expected_project=args.compose_project,
            expected_init_source=Path(args.expected_init_source),
            expected_image_reference=args.expected_image_reference,
        )
        print(json.dumps(receipt, sort_keys=True, separators=(",", ":")))
        return 0
    if args.command == "verify-database-system-identity":
        raw = sys.stdin.buffer.read(4097)
        identifier = _validate_database_system_identity(
            raw, expected_database=args.database
        )
        print(identifier)
        return 0
    if args.command == "validate-success-receipt":
        validation = _validate_migration_success_receipt(
            Path(args.receipt_dir),
            candidate_ref=args.candidate_ref,
            release_version=args.release_version,
            release_manifest_sha256=args.release_manifest_sha256,
            run_id=args.run_id,
        )
        print(json.dumps(validation, sort_keys=True, separators=(",", ":")))
        return 0
    if args.command == "render-authority-sql":
        sys.stdout.write(
            "\n".join(
                [
                    "\\set ON_ERROR_STOP on",
                    "SET default_transaction_read_only=off;",
                    *_ledger_authority_assertion_lines(),
                    "",
                ]
            )
        )
        return 0
    if args.command == "classify-ledger":
        contract = _validate_contract(args)
        rows = _load_ledger(Path(args.ledger), args.database)
        print(
            _validate_ledger(
                contract,
                args.database,
                rows,
                require_release=False,
                allow_bootstrap_release=True,
            )
        )
        return 0
    if args.command == "plan-field":
        plan = _load_json(Path(args.plan))
        _validate_plan(plan)
        print(plan[args.field])
        return 0
    if args.command == "render-sql":
        plan = _load_json(Path(args.plan))
        contract = _validate_plan(plan)
        state = plan.get(f"{args.database}_state")
        sys.stdout.buffer.write(_render_sql_bytes(contract, args.database, state))
        return 0

    contract = _validate_contract(args)
    if args.command == "verify-release-attestation":
        release_attestation = _validate_release_attestation(
            Path(args.attestation), contract, args.tree_sha256
        )
        _verify_live_gcs_release_object(release_attestation)
        print("immutable release attestation and live GCS generation verified")
        return 0
    if args.command == "verify-manifests":
        print(
            "migration manifests verified: "
            "baseline=198+12 release=201+12 pending=99zzt,99zzu,99zzv"
        )
        return 0

    operational = _load_ledger(Path(args.operational_ledger), "operational")
    gold = _load_ledger(Path(args.gold_ledger), "gold")
    require_release = args.command == "postflight"
    expected_pending_evidence = GUARDED_EVIDENCE
    if require_release:
        plan = _load_json(Path(args.plan))
        _validate_plan(plan, expected_contract=contract)
        expected_pending_evidence = plan["expected_pending_evidence"]
    operational_state = _validate_ledger(
        contract,
        "operational",
        operational,
        require_release=require_release,
        allow_bootstrap_release=(
            (args.command == "preflight" and args.allow_bootstrap_release_ledger)
            or (
                args.command == "postflight"
                and expected_pending_evidence == BASELINE_EVIDENCE
            )
        ),
        expected_pending_evidence=expected_pending_evidence,
    )
    gold_state = _validate_ledger(
        contract,
        "gold",
        gold,
        require_release=require_release,
        allow_bootstrap_release=(
            args.command == "preflight" and args.allow_bootstrap_release_ledger
        ),
    )
    if args.command == "preflight":
        _write_plan(contract, operational_state, gold_state, Path(args.plan))
        print(
            "two-database preflight verified: "
            f"operational={operational_state} gold={gold_state}"
        )
    else:
        baseline_count = sum(
            row.evidence_kind == BASELINE_EVIDENCE
            for row in [*operational.values(), *gold.values()]
        )
        guarded_count = sum(
            row.evidence_kind == GUARDED_EVIDENCE
            for row in [*operational.values(), *gold.values()]
        )
        print(
            "migration postflight verified: operational=201 gold=12 "
            f"baseline_expected={baseline_count} "
            f"guarded_transaction={guarded_count} null_checksums=0"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
