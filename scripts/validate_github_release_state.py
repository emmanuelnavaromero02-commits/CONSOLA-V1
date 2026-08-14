#!/usr/bin/env python3
"""Validate the canonical GitHub Release metadata/assets at publication edges."""

from __future__ import annotations

import argparse
import json
import sys
from typing import Any

KEYS = {
    "schema_version", "state", "tag", "immutable", "title", "body",
    "prerelease", "target", "assets",
}


class ReleaseStateError(RuntimeError):
    pass


def validate(value: object, *, state: str, tag: str, source_sha: str) -> None:
    manifest = f"omega-release-manifest-{tag}.json"
    expected_assets = sorted((manifest, f"{manifest}.sha256"))
    expected_body = (
        "Automated OMEGA release manifest: 15 images bound to "
        f"{source_sha} and tested with exact source checkout bind mounts."
    )
    if (
        not isinstance(value, dict)
        or set(value) != KEYS
        or value.get("schema_version") != 1
        or value.get("state") != state
        or value.get("tag") != tag
        or value.get("immutable") is not (state == "present")
        or value.get("title") != tag
        or value.get("body") != expected_body
        or value.get("prerelease") != ("-" in tag)
        or value.get("target") != source_sha
        or not isinstance(value.get("assets"), list)
        or sorted(value["assets"]) != expected_assets
    ):
        raise ReleaseStateError("GitHub Release state is not canonical")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--state", required=True, choices=("draft", "present"))
    parser.add_argument("--tag", required=True)
    parser.add_argument("--source-sha", required=True)
    args = parser.parse_args(argv)
    try:
        value: Any = json.load(sys.stdin)
        validate(value, state=args.state, tag=args.tag, source_sha=args.source_sha)
    except (OSError, UnicodeError, json.JSONDecodeError, ReleaseStateError) as exc:
        print(f"GITHUB RELEASE STATE BLOCKED: {exc}", file=sys.stderr)
        return 1
    print(f"GITHUB RELEASE STATE PASS: {args.state}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
