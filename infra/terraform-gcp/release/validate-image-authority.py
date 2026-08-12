#!/usr/bin/env python3
"""Validate a secret-free GCP image-authority receipt and emit digest rows."""

from __future__ import annotations

import argparse
import json
import os
import re
import stat
from pathlib import Path
from typing import Any


SERVICES = (
    "console",
    "workspace",
    "refinement",
    "vault",
    "mcp-infra",
    "airflow",
    "replicon",
    "hubspot",
    "banxico",
    "inegi",
    "sec_edgar",
    "sap_hcm",
    "sap_s4hana",
    "sap_successfactors",
    "salesforce",
)
DIGEST = re.compile(r"sha256:[0-9a-f]{64}")
SHA = re.compile(r"[0-9a-f]{40}")
RUN_VALUE = re.compile(r"[1-9][0-9]{0,19}")
PRIVATE_SERVICES = frozenset({"banxico", "inegi", "sec_edgar"})
CANONICAL_OWNER = "emmanuelnavaromero02-commits"
MAX_AUTHORITY_BYTES = 1_048_576


def _exact_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    value: dict[str, Any] = {}
    for key, item in pairs:
        if key in value:
            raise ValueError("duplicate JSON key")
        value[key] = item
    return value


def _read_authority(path: Path) -> tuple[dict[str, Any], bytes]:
    info = path.lstat()
    if (
        not stat.S_ISREG(info.st_mode)
        or info.st_uid != os.geteuid()
        or info.st_gid != os.getegid()
        or info.st_nlink != 1
        or stat.S_IMODE(info.st_mode) != 0o600
        or not 2 <= info.st_size <= MAX_AUTHORITY_BYTES
    ):
        raise ValueError("release image authority file is unsafe")
    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    descriptor = os.open(path, flags)
    try:
        current = os.fstat(descriptor)
        if (current.st_dev, current.st_ino, current.st_size) != (
            info.st_dev,
            info.st_ino,
            info.st_size,
        ):
            raise ValueError("release image authority changed before read")
        raw = os.read(descriptor, MAX_AUTHORITY_BYTES + 1)
        if len(raw) != info.st_size:
            raise ValueError("release image authority changed while reading")
    finally:
        os.close(descriptor)
    value = json.loads(raw, object_pairs_hook=_exact_object)
    if not isinstance(value, dict):
        raise ValueError("release image authority must be an object")
    canonical = (
        json.dumps(value, sort_keys=True, separators=(",", ":")) + "\n"
    ).encode()
    if raw != canonical:
        raise ValueError("release image authority must be canonical JSON")
    return value, raw


def _write_digests(path: Path, payload: bytes) -> None:
    flags = os.O_WRONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    descriptor = os.open(path, flags)
    try:
        info = os.fstat(descriptor)
        if (
            not stat.S_ISREG(info.st_mode)
            or info.st_uid != os.geteuid()
            or info.st_gid != os.getegid()
            or info.st_nlink != 1
            or stat.S_IMODE(info.st_mode) != 0o600
        ):
            raise ValueError("digest output file is unsafe")
        path_info = path.lstat()
        if (info.st_dev, info.st_ino) != (path_info.st_dev, path_info.st_ino):
            raise ValueError("digest output file changed before write")
        os.ftruncate(descriptor, 0)
        offset = 0
        while offset < len(payload):
            written = os.write(descriptor, payload[offset:])
            if written <= 0:
                raise OSError("short digest output write")
            offset += written
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--authority", type=Path, required=True)
    parser.add_argument("--digests", type=Path, required=True)
    parser.add_argument("--mode", required=True)
    parser.add_argument("--source-sha", required=True)
    parser.add_argument("--version", required=True)
    parser.add_argument("--image-tag", required=True)
    args = parser.parse_args()
    try:
        payload, _raw = _read_authority(args.authority)
    except (OSError, UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
        raise SystemExit(f"release image authority is invalid: {exc}") from exc
    if (
        set(payload)
        != {
            "schema_version",
            "authority_mode",
            "source_sha",
            "release_tag",
            "image_tag",
            "version",
            "manifest_digest",
            "tag_object_sha",
            "tag_proof_sha256",
            "candidate_workflow",
            "legacy_image_ids",
            "legacy_tag_commit",
            "images",
            "labels_authoritative",
            "secrets_included",
        }
        or payload.get("schema_version") != 1
        or payload.get("authority_mode") != args.mode
        or payload.get("source_sha") != args.source_sha
        or payload.get("release_tag") != f"v{args.version}"
        or payload.get("image_tag") != args.image_tag
        or payload.get("version") != args.version
        or payload.get("labels_authoritative") is not False
        or payload.get("secrets_included") is not False
        or not isinstance(payload.get("images"), list)
        or [row.get("service") for row in payload["images"]] != list(SERVICES)
    ):
        raise SystemExit("release image authority identity differs")
    if args.mode in {"candidate", "published"}:
        if DIGEST.fullmatch(str(payload.get("manifest_digest", ""))) is None:
            raise SystemExit("sealed manifest digest is missing")
        if payload.get("legacy_image_ids") is not None:
            raise SystemExit("sealed image authority contains legacy ImageIDs")
        if payload.get("legacy_tag_commit") is not None:
            raise SystemExit("sealed image authority contains a legacy tag commit")
    else:
        image_ids = payload.get("legacy_image_ids")
        if (
            payload.get("manifest_digest") is not None
            or payload.get("tag_object_sha") is not None
            or payload.get("tag_proof_sha256") is not None
            or not isinstance(image_ids, dict)
            or set(image_ids) != set(SERVICES)
            or SHA.fullmatch(str(payload.get("legacy_tag_commit", ""))) is None
        ):
            raise SystemExit("legacy rollback authority is ambiguous")
    workflow = payload.get("candidate_workflow")
    if args.mode == "candidate":
        if (
            not isinstance(workflow, dict)
            or set(workflow) != {"run_id", "run_attempt", "head_sha"}
            or RUN_VALUE.fullmatch(str(workflow.get("run_id", ""))) is None
            or RUN_VALUE.fullmatch(str(workflow.get("run_attempt", ""))) is None
            or workflow.get("head_sha") != args.source_sha
            or payload.get("tag_object_sha") is not None
            or payload.get("tag_proof_sha256") is not None
        ):
            raise SystemExit("candidate workflow authority differs")
    elif args.mode == "published":
        if (
            not isinstance(workflow, dict)
            or set(workflow) != {"run_id", "run_attempt", "head_sha"}
            or RUN_VALUE.fullmatch(str(workflow.get("run_id", ""))) is None
            or RUN_VALUE.fullmatch(str(workflow.get("run_attempt", ""))) is None
            or workflow.get("head_sha") != args.source_sha
            or SHA.fullmatch(str(payload.get("tag_object_sha", ""))) is None
            or DIGEST.fullmatch(str(payload.get("tag_proof_sha256", ""))) is None
        ):
            raise SystemExit("published workflow authority differs")
    elif workflow is not None:
        raise SystemExit("legacy authority must not claim a workflow")

    lines = []
    for row in payload["images"]:
        if set(row) != {"service", "digest", "reference", "visibility"}:
            raise SystemExit("release image authority row shape differs")
        digest = row.get("digest", "")
        if DIGEST.fullmatch(digest) is None:
            raise SystemExit("release image authority digest is invalid")
        service = row["service"]
        expected_reference = f"ghcr.io/{CANONICAL_OWNER}/{service}@{digest}"
        expected_visibility = (
            "private"
            if service in PRIVATE_SERVICES
            else ("unknown" if args.mode == "legacy-rollback" else "public")
        )
        if (
            row.get("reference") != expected_reference
            or row.get("visibility") != expected_visibility
        ):
            raise SystemExit("release image authority reference or privacy differs")
        image_id = "-"
        if args.mode == "legacy-rollback":
            image_id = payload["legacy_image_ids"].get(row["service"], "")
            if DIGEST.fullmatch(image_id) is None:
                raise SystemExit("legacy rollback ImageID is invalid")
        lines.append(f"{service}\t{digest}\t{image_id}\n")
    if {
        row["service"] for row in payload["images"] if row["visibility"] == "private"
    } != PRIVATE_SERVICES:
        raise SystemExit("release image authority private inventory differs from 3/3")
    try:
        _write_digests(args.digests, "".join(lines).encode())
    except (OSError, ValueError) as exc:
        raise SystemExit(f"digest output is invalid: {exc}") from exc
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
