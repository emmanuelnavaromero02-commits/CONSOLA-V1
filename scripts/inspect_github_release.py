#!/usr/bin/env python3
"""Inspect one GitHub Release with structured HTTP absence semantics."""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from collections.abc import Callable, Mapping
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import quote
from urllib.request import Request, urlopen

API_URL = "https://api.github.com"
API_VERSION = "2022-11-28"
MAX_RESPONSE_BYTES = 1_048_576
Fetcher = Callable[[str, Mapping[str, str]], tuple[int, Any]]


class ReleaseInspectionError(RuntimeError):
    """The release lookup was ambiguous or malformed."""


def _fetch_json(url: str, headers: Mapping[str, str]) -> tuple[int, Any]:
    request = Request(url, headers=dict(headers), method="GET")
    try:
        with urlopen(request, timeout=15.0) as response:
            status = int(response.status)
            raw = response.read(MAX_RESPONSE_BYTES + 1)
    except HTTPError as exc:
        return int(exc.code), None
    except (URLError, TimeoutError, OSError) as exc:
        raise ReleaseInspectionError("GitHub Release API transport failed") from exc
    if len(raw) > MAX_RESPONSE_BYTES:
        raise ReleaseInspectionError("GitHub Release API response is oversized")
    try:
        return status, json.loads(raw.decode("utf-8"))
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise ReleaseInspectionError(
            "GitHub Release API response is invalid JSON"
        ) from exc


def inspect_release(
    *, repository: str, tag: str, token: str, fetcher: Fetcher = _fetch_json
) -> dict[str, object]:
    if not re.fullmatch(
        r"[A-Za-z0-9](?:[A-Za-z0-9-]{0,38})/[A-Za-z0-9_.-]{1,100}", repository
    ):
        raise ReleaseInspectionError("GitHub repository identity is invalid")
    if not re.fullmatch(
        r"v(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)"
        r"(?:-[0-9A-Za-z][0-9A-Za-z.-]*)?",
        tag,
    ):
        raise ReleaseInspectionError("GitHub release tag is invalid")
    token = token.strip()
    if not token:
        raise ReleaseInspectionError("GitHub token is missing")
    owner, name = repository.split("/", 1)
    url = (
        f"{API_URL}/repos/{quote(owner, safe='')}/{quote(name, safe='')}"
        f"/releases/tags/{quote(tag, safe='')}"
    )
    headers = {
        "Accept": "application/vnd.github+json",
        "Authorization": f"Bearer {token}",
        "X-GitHub-Api-Version": API_VERSION,
        "User-Agent": "omega-release-inspector/1",
    }
    try:
        status, payload = fetcher(url, headers)
    except Exception as exc:
        raise ReleaseInspectionError("GitHub Release API lookup failed") from exc
    if status == 404:
        return {
            "schema_version": 1,
            "state": "absent",
            "tag": tag,
            "immutable": None,
            "title": None,
            "body": None,
            "prerelease": None,
            "target": None,
            "assets": [],
        }
    if status != 200:
        raise ReleaseInspectionError(f"GitHub Release API returned HTTP {status}")
    if not isinstance(payload, dict) or payload.get("tag_name") != tag:
        raise ReleaseInspectionError("GitHub Release API identity mismatch")
    draft = payload.get("draft")
    if not isinstance(draft, bool):
        raise ReleaseInspectionError(
            "GitHub Release has no authoritative publication state"
        )
    if not draft and payload.get("immutable") is not True:
        raise ReleaseInspectionError("GitHub Release is not immutable")
    assets = payload.get("assets")
    if not isinstance(assets, list):
        raise ReleaseInspectionError("GitHub Release API assets are invalid")
    names: list[str] = []
    for asset in assets:
        asset_name = asset.get("name") if isinstance(asset, dict) else None
        if (
            not isinstance(asset_name, str)
            or not asset_name
            or len(asset_name) > 255
            or "\n" in asset_name
            or "\r" in asset_name
        ):
            raise ReleaseInspectionError("GitHub Release API asset name is invalid")
        names.append(asset_name)
    title = payload.get("name")
    body = payload.get("body")
    prerelease = payload.get("prerelease")
    target = payload.get("target_commitish")
    if (
        not isinstance(title, str)
        or not isinstance(body, str)
        or not isinstance(prerelease, bool)
        or not isinstance(target, str)
        or not target
    ):
        raise ReleaseInspectionError("GitHub Release metadata is invalid")
    return {
        "schema_version": 1,
        "state": "draft" if draft else "present",
        "tag": tag,
        "immutable": False if draft else True,
        "title": title,
        "body": body,
        "prerelease": prerelease,
        "target": target,
        "assets": names,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repository", required=True)
    parser.add_argument("--tag", required=True)
    args = parser.parse_args(argv)
    try:
        result = inspect_release(
            repository=args.repository,
            tag=args.tag,
            token=os.environ.get("GH_TOKEN", ""),
        )
    except ReleaseInspectionError as exc:
        print(f"GITHUB_RELEASE_INSPECTION BLOCKED: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(result, sort_keys=True, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
