#!/usr/bin/env python3
"""Bind the F2 hybrid digest stack to one clean, reviewed source checkout."""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path
from typing import Any

REPO = Path(__file__).resolve().parents[1]
ALLOWLIST = REPO / "tests" / "fixtures" / "release-compose-mount-allowlist.json"
SOURCE_ROOTS = (
    "VERSION",
    "airflow/dags",
    "airflow/plugins",
    "cartridges",
    "console/app/static",
    "infra/init",
    "infra/init_dev",
    "infra/init_gold",
    "infra/terraform/deploy/superset_config",
)
class SourceCheckoutError(RuntimeError):
    pass


def _git(*args: str) -> str:
    result = subprocess.run(
        ["git", *args], cwd=REPO, text=True, capture_output=True, check=False
    )
    if result.returncode != 0:
        raise SourceCheckoutError(f"git {' '.join(args)} failed")
    return result.stdout


def _normalize_mount(service: str, mount: object) -> dict[str, object]:
    if not isinstance(mount, dict):
        raise SourceCheckoutError("Compose mount is not an object")
    source = mount.get("source")
    if isinstance(source, str) and Path(source).is_absolute():
        try:
            source = Path(source).resolve().relative_to(REPO).as_posix()
        except ValueError:
            pass
    return {
        "service": service,
        "type": mount.get("type"),
        "source": source,
        "target": mount.get("target"),
        "read_only": mount.get("read_only") is True,
    }


def verify(*, source_sha: str, compose_path: Path) -> None:
    if _git("rev-parse", "HEAD").strip() != source_sha:
        raise SourceCheckoutError("checkout HEAD differs from the release source SHA")
    for staged in (False, True):
        args = ["diff", "--quiet"]
        if staged:
            args.append("--cached")
        args.extend(["--", *SOURCE_ROOTS])
        result = subprocess.run(["git", *args], cwd=REPO, check=False)
        if result.returncode != 0:
            raise SourceCheckoutError("tracked release bind source differs from HEAD")
    untracked = set(
        _git("ls-files", "--others", "--exclude-standard", "--", *SOURCE_ROOTS).splitlines()
    )
    untracked.update(
        _git(
            "ls-files",
            "--others",
            "--ignored",
            "--exclude-standard",
            "--",
            *SOURCE_ROOTS,
        ).splitlines()
    )
    unsafe = sorted(untracked)
    if unsafe:
        raise SourceCheckoutError(f"untracked executable bind source: {unsafe[0]}")
    try:
        compose = json.loads(compose_path.read_text(encoding="utf-8"))
        allowlist = json.loads(ALLOWLIST.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise SourceCheckoutError("Compose or mount allowlist is unreadable") from exc
    services = compose.get("services") if isinstance(compose, dict) else None
    expected = allowlist.get("mounts") if isinstance(allowlist, dict) else None
    if allowlist.get("schema_version") != 1 or not isinstance(expected, list):
        raise SourceCheckoutError("mount allowlist schema is invalid")
    if not isinstance(services, dict):
        raise SourceCheckoutError("Compose services are invalid")
    actual = [
        _normalize_mount(service, mount)
        for service, config in sorted(services.items())
        for mount in (config.get("volumes", []) if isinstance(config, dict) else [])
    ]
    if actual != expected:
        raise SourceCheckoutError("Compose mount inventory differs from exact allowlist")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source-sha", required=True)
    parser.add_argument("--compose-config", required=True, type=Path)
    args = parser.parse_args(argv)
    try:
        verify(source_sha=args.source_sha, compose_path=args.compose_config)
    except SourceCheckoutError as exc:
        print(f"RELEASE SOURCE CHECKOUT BLOCKED: {exc}", file=sys.stderr)
        return 1
    print("RELEASE SOURCE CHECKOUT PASS: exact SHA and mount allowlist")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
