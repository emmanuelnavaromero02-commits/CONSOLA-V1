#!/usr/bin/python3 -I
"""Validate the exact successful migration receipt handed to day-2 release."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import re
import stat
import subprocess
import sys
from typing import Any, Callable


MAX_STDOUT_BYTES = 1024 * 1024
MARKER_PREFIX = "OMEGA_MIGRATION_RECEIPT\t"
FULL_SHA = re.compile(r"^[0-9a-f]{40}$")
SHA256 = re.compile(r"^[0-9a-f]{64}$")
RUN_ID = re.compile(r"^omega_migration_([1-9][0-9]{0,19})_[1-9][0-9]{0,29}$")
RECEIPT = re.compile(
    r"^/opt/modecissions/shared/operation-receipts/"
    r"migration-([0-9a-f]{40})-[0-9]{8}T[0-9]{6}Z-([1-9][0-9]{0,19})$"
)
MARKER_KEYS = {
    "candidate_ref",
    "database_commit_state",
    "receipt_dir",
    "release_manifest_sha256",
    "release_version",
    "run_id",
    "status",
}


def _die(message: str) -> None:
    raise SystemExit(f"migration handoff: {message}")


def _strict_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate JSON key: {key}")
        result[key] = value
    return result


def _read_snapshot(
    path: Path, *, expected_mode: int, maximum: int, label: str
) -> bytes:
    if not path.is_absolute():
        _die(f"{label} path is not absolute")
    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(path, flags)
    except OSError:
        _die(f"{label} cannot be opened safely")
    try:
        before = os.fstat(descriptor)
        if (
            not stat.S_ISREG(before.st_mode)
            or before.st_uid != os.geteuid()
            or before.st_gid != os.getegid()
            or stat.S_IMODE(before.st_mode) != expected_mode
            or before.st_nlink != 1
            or not 1 <= before.st_size <= maximum
        ):
            _die(f"{label} ownership, mode, links, or size differ")
        chunks: list[bytes] = []
        remaining = before.st_size
        while remaining:
            chunk = os.read(descriptor, min(remaining, 65_536))
            if not chunk:
                _die(f"{label} read was short")
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
            _die(f"{label} changed while reading")
        return b"".join(chunks)
    finally:
        os.close(descriptor)


def _parse_marker(
    raw: bytes, *, candidate_ref: str, release_version: str, manifest_sha: str
) -> tuple[dict[str, Any], str]:
    try:
        lines = raw.decode("utf-8", errors="strict").splitlines()
    except UnicodeError:
        _die("migration stdout is not strict UTF-8")
    markers = [
        line.removeprefix(MARKER_PREFIX)
        for line in lines
        if line.startswith(MARKER_PREFIX)
    ]
    if len(markers) != 1:
        _die("migration stdout does not contain exactly one receipt marker")
    try:
        payload = json.loads(markers[0], object_pairs_hook=_strict_object)
    except (json.JSONDecodeError, ValueError):
        _die("migration receipt marker is malformed")
    if not isinstance(payload, dict) or set(payload) != MARKER_KEYS:
        _die("migration receipt marker fields differ")
    run_id = payload.get("run_id")
    receipt_dir = payload.get("receipt_dir")
    run_match = RUN_ID.fullmatch(run_id) if isinstance(run_id, str) else None
    receipt_match = (
        RECEIPT.fullmatch(receipt_dir) if isinstance(receipt_dir, str) else None
    )
    if (
        payload.get("candidate_ref") != candidate_ref
        or payload.get("release_version") != release_version
        or payload.get("release_manifest_sha256") != manifest_sha
        or payload.get("database_commit_state") != "gold_operational_and_authority"
        or payload.get("status") != "PASS"
        or run_match is None
        or receipt_match is None
        or receipt_match.group(1) != candidate_ref
        or receipt_match.group(2) != run_match.group(1)
    ):
        _die("migration receipt marker identity or terminal state differs")
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    if markers[0] != canonical:
        _die("migration receipt marker is not canonical JSON")
    return payload, canonical


def validate_handoff(
    stdout_path: Path,
    guard_path: Path,
    *,
    candidate_ref: str,
    release_version: str,
    manifest_sha: str,
    runner: Callable[..., subprocess.CompletedProcess[bytes]] = subprocess.run,
) -> dict[str, Any]:
    if FULL_SHA.fullmatch(candidate_ref) is None:
        _die("candidate ref is invalid")
    if release_version != "1.45.207-beta":
        _die("release version is invalid")
    if SHA256.fullmatch(manifest_sha) is None:
        _die("release manifest hash is invalid")
    raw = _read_snapshot(
        stdout_path,
        expected_mode=0o400,
        maximum=MAX_STDOUT_BYTES,
        label="migration stdout",
    )
    payload, canonical = _parse_marker(
        raw,
        candidate_ref=candidate_ref,
        release_version=release_version,
        manifest_sha=manifest_sha,
    )
    _read_snapshot(
        guard_path,
        expected_mode=0o555,
        maximum=8 * 1024 * 1024,
        label="migration guard",
    )
    environment = {
        "HOME": "/var/empty",
        "LANG": "C",
        "LC_ALL": "C",
        "PATH": "/usr/sbin:/usr/bin:/sbin:/bin",
        "PYTHONHASHSEED": "0",
        "PYTHONIOENCODING": "utf-8:strict",
        "PYTHONNOUSERSITE": "1",
        "PYTHONSAFEPATH": "1",
    }
    try:
        validation = runner(
            [
                sys.executable,
                "-I",
                str(guard_path),
                "validate-success-receipt",
                "--receipt-dir",
                payload["receipt_dir"],
                "--candidate-ref",
                candidate_ref,
                "--release-version",
                release_version,
                "--release-manifest-sha256",
                manifest_sha,
                "--run-id",
                payload["run_id"],
            ],
            env=environment,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
            timeout=30,
        )
    except (OSError, subprocess.TimeoutExpired):
        _die("migration receipt revalidation could not execute")
    if validation.returncode != 0 or validation.stdout != (canonical + "\n").encode():
        _die("migration receipt directory did not revalidate exactly")
    return payload


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--stdout", required=True)
    parser.add_argument("--guard", required=True)
    parser.add_argument("--candidate-ref", required=True)
    parser.add_argument("--release-version", required=True)
    parser.add_argument("--release-manifest-sha256", required=True)
    args = parser.parse_args()
    validate_handoff(
        Path(args.stdout),
        Path(args.guard),
        candidate_ref=args.candidate_ref,
        release_version=args.release_version,
        manifest_sha=args.release_manifest_sha256,
    )
    print("migration handoff receipt verified")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
