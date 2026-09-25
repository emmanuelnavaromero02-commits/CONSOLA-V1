#!/usr/bin/env python3
"""Prove that both canonical release tags are absent from GHCR.

Docker Buildx presents registry failures as human-readable messages which do
not preserve enough information to distinguish a missing manifest from auth,
routing, or transport failures.  This checker uses authenticated OCI API
responses and accepts only HTTP 404 plus the structured MANIFEST_UNKNOWN code.
"""

from __future__ import annotations

import argparse
import base64
import json
import math
import os
import re
import sys
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlencode
from urllib.request import HTTPRedirectHandler, Request, build_opener

GHCR_ORIGIN = "https://ghcr.io"
GHCR_TOKEN_URL = f"{GHCR_ORIGIN}/token"
MAX_RESPONSE_BYTES = 1_048_576
OCI_ACCEPT = (
    "application/vnd.oci.image.index.v1+json, "
    "application/vnd.oci.image.manifest.v1+json, "
    "application/vnd.docker.distribution.manifest.list.v2+json, "
    "application/vnd.docker.distribution.manifest.v2+json"
)
CANONICAL_SERVICES = frozenset(
    {
        "airflow",
        "banxico",
        "console",
        "hubspot",
        "inegi",
        "mcp-infra",
        "refinement",
        "replicon",
        "salesforce",
        "sap_b1",
        "sap_hcm",
        "sap_s4hana",
        "sap_successfactors",
        "sec_edgar",
        "vault",
        "workspace",
    }
)
STRICT_RELEASE_TAG = re.compile(
    r"^v(?:0|[1-9][0-9]*)\."
    r"(?:0|[1-9][0-9]*)\."
    r"(?:0|[1-9][0-9]*)"
    r"(?:-(?:0|[1-9][0-9]*|[0-9A-Za-z-]*[A-Za-z-][0-9A-Za-z-]*)"
    r"(?:\.(?:0|[1-9][0-9]*|[0-9A-Za-z-]*[A-Za-z-][0-9A-Za-z-]*))*)?$"
)


class ImageAbsenceError(RuntimeError):
    pass


@dataclass(frozen=True)
class HttpResult:
    status: int
    body: bytes
    headers: Mapping[str, str]


Fetcher = Callable[[str, Mapping[str, str], float], HttpResult]


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(
        self,
        req: Request,
        fp: Any,
        code: int,
        msg: str,
        headers: Any,
        newurl: str,
    ) -> None:
        return None


def _read_bounded(response: Any) -> bytes:
    try:
        body = response.read(MAX_RESPONSE_BYTES + 1)
    except OSError as exc:
        raise ImageAbsenceError("GHCR response body could not be read") from exc
    if len(body) > MAX_RESPONSE_BYTES:
        raise ImageAbsenceError("GHCR response is oversized")
    return body


def _fetch_bytes(url: str, headers: Mapping[str, str], timeout: float) -> HttpResult:
    request = Request(url, headers=dict(headers), method="GET")
    opener = build_opener(_NoRedirect)
    try:
        with opener.open(request, timeout=timeout) as response:
            return HttpResult(
                int(response.status),
                _read_bounded(response),
                dict(response.headers.items()),
            )
    except HTTPError as exc:
        return HttpResult(int(exc.code), _read_bounded(exc), dict(exc.headers.items()))
    except (URLError, TimeoutError, OSError) as exc:
        raise ImageAbsenceError("GHCR transport failed") from exc


def _validate_owner(owner: str) -> str:
    owner = owner.strip()
    if (
        not re.fullmatch(
            r"(?=.{1,39}\Z)[A-Za-z0-9](?:[A-Za-z0-9-]*[A-Za-z0-9])?", owner
        )
        or "--" in owner
    ):
        raise ImageAbsenceError("GHCR owner is invalid")
    return owner


def _validate_username(username: str) -> str:
    username = username.strip()
    if (
        not username
        or len(username) > 256
        or ":" in username
        or any(ord(character) < 33 or ord(character) == 127 for character in username)
    ):
        raise ImageAbsenceError("GHCR username is invalid")
    return username


def _validate_identity(
    *, owner: str, service: str, source_sha: str, release_tag: str
) -> tuple[str, str, str, str]:
    owner = _validate_owner(owner)
    if service not in CANONICAL_SERVICES:
        raise ImageAbsenceError("release service is not canonical")
    if not re.fullmatch(r"[0-9a-f]{40}", source_sha):
        raise ImageAbsenceError("release source SHA is invalid")
    if STRICT_RELEASE_TAG.fullmatch(release_tag) is None:
        raise ImageAbsenceError("release tag is not strict SemVer")
    return owner, service, source_sha, release_tag


def _json_object(result: HttpResult, *, label: str) -> dict[str, Any]:
    content_type = next(
        (
            value
            for key, value in result.headers.items()
            if key.lower() == "content-type"
        ),
        "",
    )
    if content_type.split(";", 1)[0].strip().lower() != "application/json":
        raise ImageAbsenceError(f"{label} has an invalid content type")
    try:
        value = json.loads(result.body.decode("utf-8"))
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise ImageAbsenceError(f"{label} is not valid JSON") from exc
    if not isinstance(value, dict):
        raise ImageAbsenceError(f"{label} must be a JSON object")
    return value


def _registry_token(
    *,
    owner: str,
    service: str,
    username: str,
    github_token: str,
    timeout: float,
    fetcher: Fetcher,
) -> str:
    github_token = github_token.strip()
    if (
        not github_token
        or len(github_token) > 16_384
        or any(
            ord(character) < 33 or ord(character) == 127 for character in github_token
        )
    ):
        raise ImageAbsenceError("GitHub token is missing or invalid")
    credentials = base64.b64encode(f"{username}:{github_token}".encode()).decode(
        "ascii"
    )
    scope = f"repository:{owner}/{service}:pull"
    token_url = f"{GHCR_TOKEN_URL}?{urlencode({'service': 'ghcr.io', 'scope': scope})}"
    result = fetcher(
        token_url,
        {
            "Accept": "application/json",
            "Authorization": f"Basic {credentials}",
            "User-Agent": "omega-release-image-absence/1",
        },
        timeout,
    )
    if result.status != 200:
        raise ImageAbsenceError(f"GHCR token exchange returned HTTP {result.status}")
    payload = _json_object(result, label="GHCR token response")
    registry_token = payload.get("token")
    if (
        not isinstance(registry_token, str)
        or not registry_token
        or len(registry_token) > 16_384
        or any(
            ord(character) < 33 or ord(character) == 127 for character in registry_token
        )
    ):
        raise ImageAbsenceError("GHCR token response has no valid token")
    return registry_token


def _require_manifest_absent(
    *,
    owner: str,
    service: str,
    tag: str,
    registry_token: str,
    timeout: float,
    fetcher: Fetcher,
) -> None:
    repository = f"{quote(owner, safe='')}/{quote(service, safe='')}"
    url = f"{GHCR_ORIGIN}/v2/{repository}/manifests/{quote(tag, safe='')}"
    result = fetcher(
        url,
        {
            "Accept": OCI_ACCEPT,
            "Authorization": f"Bearer {registry_token}",
            "User-Agent": "omega-release-image-absence/1",
        },
        timeout,
    )
    reference = f"ghcr.io/{owner}/{service}:{tag}"
    if result.status == 200:
        raise ImageAbsenceError(
            f"pre-existing release image tag is forbidden: {reference}"
        )
    if result.status != 404:
        raise ImageAbsenceError(
            f"GHCR manifest lookup returned HTTP {result.status}: {reference}"
        )
    payload = _json_object(result, label="GHCR manifest error")
    errors = payload.get("errors")
    if (
        not isinstance(errors, list)
        or len(errors) != 1
        or not isinstance(errors[0], dict)
        or errors[0].get("code") != "MANIFEST_UNKNOWN"
    ):
        raise ImageAbsenceError(
            f"GHCR 404 was not structured manifest absence: {reference}"
        )


def verify_release_image_tags_absent(
    *,
    owner: str,
    service: str,
    username: str,
    github_token: str,
    source_sha: str,
    release_tag: str,
    timeout: float = 15.0,
    fetcher: Fetcher = _fetch_bytes,
) -> tuple[str, str]:

    owner, service, source_sha, release_tag = _validate_identity(
        owner=owner,
        service=service,
        source_sha=source_sha,
        release_tag=release_tag,
    )
    username = _validate_username(username)
    if not math.isfinite(timeout) or timeout <= 0:
        raise ImageAbsenceError("timeout must be a positive finite number")
    registry_token = _registry_token(
        owner=owner,
        service=service,
        username=username,
        github_token=github_token,
        timeout=timeout,
        fetcher=fetcher,
    )
    tags = (f"sha-{source_sha}", release_tag)
    for tag in tags:
        _require_manifest_absent(
            owner=owner,
            service=service,
            tag=tag,
            registry_token=registry_token,
            timeout=timeout,
            fetcher=fetcher,
        )
    image = f"ghcr.io/{owner}/{service}"
    return (f"{image}:{tags[0]}", f"{image}:{tags[1]}")


def _positive_float(raw: str) -> float:
    try:
        value = float(raw)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("must be a number") from exc
    if not math.isfinite(value) or value <= 0:
        raise argparse.ArgumentTypeError("must be a positive finite number")
    return value


def main(
    argv: Sequence[str] | None = None,
    *,
    environ: Mapping[str, str] | None = None,
    fetcher: Fetcher = _fetch_bytes,
) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--owner", required=True)
    parser.add_argument("--service", required=True)
    parser.add_argument("--username", required=True)
    parser.add_argument("--source-sha", required=True)
    parser.add_argument("--release-tag", required=True)
    parser.add_argument("--token-env", default="GITHUB_TOKEN")
    parser.add_argument("--timeout", type=_positive_float, default=15.0)
    args = parser.parse_args(argv)
    environment = os.environ if environ is None else environ
    try:
        references = verify_release_image_tags_absent(
            owner=args.owner,
            service=args.service,
            username=args.username,
            github_token=environment.get(args.token_env, ""),
            source_sha=args.source_sha,
            release_tag=args.release_tag,
            timeout=args.timeout,
            fetcher=fetcher,
        )
    except ImageAbsenceError as exc:
        print(f"RELEASE_IMAGE_ABSENCE BLOCKED: {exc}", file=sys.stderr)
        return 1
    except Exception:  # noqa: BLE001 - fail closed without leaking transport details
        print(
            "RELEASE_IMAGE_ABSENCE BLOCKED: unexpected verifier failure",
            file=sys.stderr,
        )
        return 1
    print("RELEASE_IMAGE_ABSENCE PASS references=" + ",".join(references))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
