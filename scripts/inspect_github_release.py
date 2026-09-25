#!/usr/bin/env python3

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
RELEASE_PAGE_SIZE = 100
MAX_RELEASE_PAGES = 100
Fetcher = Callable[[str, Mapping[str, str]], tuple[int, Any]]


class ReleaseInspectionError(RuntimeError):
    pass


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


def _fetch(
    fetcher: Fetcher, url: str, headers: Mapping[str, str]
) -> tuple[int, Any]:
    try:
        response = fetcher(url, headers)
    except Exception as exc:
        raise ReleaseInspectionError("GitHub Release API lookup failed") from exc
    if not isinstance(response, tuple) or len(response) != 2:
        raise ReleaseInspectionError("GitHub Release API response shape is invalid")
    status, payload = response
    if not isinstance(status, int) or isinstance(status, bool):
        raise ReleaseInspectionError("GitHub Release API response status is invalid")
    return status, payload


def _release_id(payload: object, *, context: str = "") -> int:
    release_id = payload.get("id") if isinstance(payload, dict) else None
    if (
        not isinstance(release_id, int)
        or isinstance(release_id, bool)
        or release_id <= 0
    ):
        raise ReleaseInspectionError(
            f"GitHub Release API {context}release id is invalid"
        )
    return release_id


def _normalize_release(
    payload: object, *, expected_tag: str, expected_draft: bool
) -> tuple[int, dict[str, object]]:
    if not isinstance(payload, dict) or payload.get("tag_name") != expected_tag:
        raise ReleaseInspectionError("GitHub Release API identity mismatch")
    release_id = _release_id(payload)
    draft = payload.get("draft")
    if not isinstance(draft, bool):
        raise ReleaseInspectionError(
            "GitHub Release has no authoritative publication state"
        )
    if draft is not expected_draft:
        raise ReleaseInspectionError(
            "GitHub Release API publication state is inconsistent"
        )
    immutable = payload.get("immutable")
    if draft:
        if immutable is not False:
            raise ReleaseInspectionError("GitHub Release draft is immutable or unknown")
    elif immutable is not True:
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
    names.sort()
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
    return release_id, {
        "schema_version": 1,
        "state": "draft" if draft else "present",
        "tag": expected_tag,
        "immutable": False if draft else True,
        "title": title,
        "body": body,
        "prerelease": prerelease,
        "target": target,
        "assets": names,
    }


def _absent_release(tag: str) -> dict[str, object]:
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


def _validate_list_entry(payload: object) -> tuple[int, str, bool]:
    if not isinstance(payload, dict):
        raise ReleaseInspectionError(
            "GitHub Release API release list entry is invalid"
        )
    release_id = _release_id(payload, context="release list ")
    tag_name = payload.get("tag_name")
    if (
        not isinstance(tag_name, str)
        or not tag_name
        or len(tag_name) > 255
        or "\n" in tag_name
        or "\r" in tag_name
    ):
        raise ReleaseInspectionError(
            "GitHub Release API release list tag is invalid"
        )
    draft = payload.get("draft")
    if not isinstance(draft, bool):
        raise ReleaseInspectionError(
            "GitHub Release API release list publication state is invalid"
        )
    return release_id, tag_name, draft


def _same_metadata(left: dict[str, object], right: dict[str, object]) -> bool:
    keys = ("tag", "title", "body", "prerelease", "target", "assets")
    return all(left[key] == right[key] for key in keys)


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
    repository_url = (
        f"{API_URL}/repos/{quote(owner, safe='')}/{quote(name, safe='')}"
    )
    tag_url = f"{repository_url}/releases/tags/{quote(tag, safe='')}"
    headers = {
        "Accept": "application/vnd.github+json",
        "Authorization": f"Bearer {token}",
        "X-GitHub-Api-Version": API_VERSION,
        "User-Agent": "omega-release-inspector/1",
    }
    status, payload = _fetch(fetcher, tag_url, headers)
    if status == 200:
        if isinstance(payload, dict) and payload.get("draft") is True:
            raise ReleaseInspectionError(
                "GitHub Release published endpoint returned a draft"
            )
        return _normalize_release(
            payload, expected_tag=tag, expected_draft=False
        )[1]
    if status != 200:
        if status != 404:
            raise ReleaseInspectionError(
                f"GitHub Release API returned HTTP {status}"
            )

    seen_ids: set[int] = set()
    exact_matches: list[tuple[int, bool, dict[str, Any]]] = []
    terminal_page = False
    for page_number in range(1, MAX_RELEASE_PAGES + 1):
        list_url = (
            f"{repository_url}/releases?per_page={RELEASE_PAGE_SIZE}"
            f"&page={page_number}"
        )
        list_status, page = _fetch(fetcher, list_url, headers)
        if list_status != 200:
            raise ReleaseInspectionError(
                f"GitHub Release API returned HTTP {list_status} for release list"
            )
        if not isinstance(page, list):
            raise ReleaseInspectionError("GitHub Release API release list is invalid")
        if len(page) > RELEASE_PAGE_SIZE:
            raise ReleaseInspectionError(
                "GitHub Release API release list page size is invalid"
            )
        for entry in page:
            release_id, tag_name, draft = _validate_list_entry(entry)
            if release_id in seen_ids:
                raise ReleaseInspectionError(
                    "GitHub Release API release list did not advance"
                )
            seen_ids.add(release_id)
            if tag_name == tag:
                exact_matches.append((release_id, draft, entry))
        if len(page) < RELEASE_PAGE_SIZE:
            terminal_page = True
            break
    if not terminal_page:
        raise ReleaseInspectionError(
            "GitHub Release API release list reached the pagination cap"
        )
    if len(exact_matches) > 1:
        raise ReleaseInspectionError(
            "GitHub Release API returned multiple releases for the exact tag"
        )

    listed_id: int | None = None
    listed_draft: bool | None = None
    listed_release: dict[str, object] | None = None
    if exact_matches:
        listed_id, listed_draft, listed_payload = exact_matches[0]
        normalized_id, listed_release = _normalize_release(
            listed_payload, expected_tag=tag, expected_draft=listed_draft
        )
        if normalized_id != listed_id:
            raise ReleaseInspectionError("GitHub Release API identity changed")

    settled_status, settled_payload = _fetch(fetcher, tag_url, headers)
    if settled_status == 200:
        if (
            isinstance(settled_payload, dict)
            and settled_payload.get("draft") is True
        ):
            raise ReleaseInspectionError(
                "GitHub Release published endpoint returned a draft"
            )
        settled_id, settled_release = _normalize_release(
            settled_payload, expected_tag=tag, expected_draft=False
        )
        if listed_id is not None and settled_id != listed_id:
            raise ReleaseInspectionError("GitHub Release API identity changed")
        if listed_release is not None and not _same_metadata(
            listed_release, settled_release
        ):
            raise ReleaseInspectionError("GitHub Release API metadata changed")
        return settled_release
    if settled_status != 404:
        raise ReleaseInspectionError(
            f"GitHub Release API returned HTTP {settled_status}"
        )
    if listed_release is None:
        return _absent_release(tag)
    if listed_draft is not True:
        raise ReleaseInspectionError(
            "GitHub Release API publication state is inconsistent"
        )
    return listed_release


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
