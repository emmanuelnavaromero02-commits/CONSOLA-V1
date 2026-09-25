#!/usr/bin/env python3
"""Fail closed unless every canonical OMEGA release package is private.

The checker is read-only.  It obtains a GitHub token from an environment
variable, calls the GitHub Packages REST API, and never includes the token in
URLs or output.  The default inventory is the same 16-image inventory used by
the GCP release preflight; a JSON or newline-delimited inventory can be passed
explicitly, but it must describe exactly the canonical set.
"""

from __future__ import annotations

import argparse
import json
import math
import os
import re
import shlex
import sys
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlsplit
from urllib.request import Request, urlopen


CANONICAL_PACKAGE_NAMES: tuple[str, ...] = (
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
)
DEFAULT_INVENTORY_PATH = (
    Path(__file__).resolve().parents[1]
    / "infra"
    / "terraform-gcp"
    / "release"
    / "preflight-release-images.sh"
)
DEFAULT_API_URL = "https://api.github.com"
DEFAULT_API_VERSION = "2022-11-28"
MAX_RESPONSE_BYTES = 1_048_576

Fetcher = Callable[[str, Mapping[str, str], float], tuple[int, Any]]


class PackageVisibilityError(RuntimeError):
    """Raised for unsafe or invalid local checker configuration."""


@dataclass(frozen=True)
class PackageCheck:
    """A sanitized result for one package; it never contains credentials."""

    package: str
    private: bool
    reason: str
    owner_kind: str | None = None


def _parse_shell_inventory(text: str) -> list[str] | None:
    match = re.search(
        r"(?ms)^image_names=\(\s*\n(?P<body>.*?)^\)\s*$",
        text,
    )
    if match is None:
        return None

    names: list[str] = []
    for line in match.group("body").splitlines():
        tokens = shlex.split(line, comments=True, posix=True)
        if len(tokens) > 1:
            raise PackageVisibilityError(
                "inventory shell array must contain one package per line"
            )
        names.extend(tokens)
    return names


def _parse_json_inventory(value: Any) -> list[str]:
    if isinstance(value, dict):
        for key in ("images", "packages", "package_names"):
            if key in value:
                value = value[key]
                break
        else:
            raise PackageVisibilityError(
                "JSON inventory must contain images, packages, or package_names"
            )
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        raise PackageVisibilityError("JSON inventory must be a list of package names")
    return value


def _validate_inventory(names: Sequence[str]) -> tuple[str, ...]:
    normalized = tuple(name.strip() for name in names)
    if any(not name for name in normalized):
        raise PackageVisibilityError("inventory contains an empty package name")
    if len(normalized) != len(set(normalized)):
        raise PackageVisibilityError("inventory contains duplicate package names")

    expected = set(CANONICAL_PACKAGE_NAMES)
    actual = set(normalized)
    if len(normalized) != len(CANONICAL_PACKAGE_NAMES) or actual != expected:
        missing = sorted(expected - actual)
        unexpected = sorted(actual - expected)
        details: list[str] = []
        if missing:
            details.append(f"missing={','.join(missing)}")
        if unexpected:
            details.append(f"unexpected={','.join(unexpected)}")
        suffix = f" ({'; '.join(details)})" if details else ""
        raise PackageVisibilityError(
            "release inventory must contain exactly the canonical 16 packages"
            f"{suffix}"
        )

    # Query in one deterministic order even if a reviewed custom file uses a
    # different presentation order.
    return CANONICAL_PACKAGE_NAMES


def load_inventory(path: Path) -> tuple[str, ...]:
    """Load and validate a shell, JSON, or newline-delimited inventory."""

    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeError) as exc:
        raise PackageVisibilityError("release inventory cannot be read") from exc

    shell_names = _parse_shell_inventory(text)
    if shell_names is not None:
        return _validate_inventory(shell_names)

    stripped = text.lstrip()
    if stripped.startswith(("[", "{")):
        try:
            value = json.loads(text)
        except json.JSONDecodeError as exc:
            raise PackageVisibilityError("release inventory is invalid JSON") from exc
        return _validate_inventory(_parse_json_inventory(value))

    names = [
        line.strip()
        for line in text.splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    ]
    return _validate_inventory(names)


def _validate_owner(owner: str) -> str:
    normalized = owner.strip()
    if not re.fullmatch(
        r"(?=.{1,39}\Z)[A-Za-z0-9](?:[A-Za-z0-9-]*[A-Za-z0-9])?",
        normalized,
    ) or "--" in normalized:
        raise PackageVisibilityError("GitHub package owner is invalid")
    return normalized


def _validate_api_url(api_url: str) -> str:
    candidate = api_url.rstrip("/")
    parsed = urlsplit(candidate)
    if (
        parsed.scheme != "https"
        or not parsed.netloc
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
    ):
        raise PackageVisibilityError(
            "GitHub API URL must be HTTPS and contain no credentials, query, or fragment"
        )
    return candidate


def _fetch_json(url: str, headers: Mapping[str, str], timeout: float) -> tuple[int, Any]:
    request = Request(url, headers=dict(headers), method="GET")
    try:
        with urlopen(request, timeout=timeout) as response:  # noqa: S310
            status = int(response.status)
            raw = response.read(MAX_RESPONSE_BYTES + 1)
    except HTTPError as exc:
        # The response body is deliberately ignored: it is unnecessary for the
        # decision and may contain details that should not reach CI logs.
        return int(exc.code), None
    except (URLError, TimeoutError, OSError) as exc:
        raise PackageVisibilityError("GitHub Packages API request failed") from exc

    if len(raw) > MAX_RESPONSE_BYTES:
        return status, None
    try:
        return status, json.loads(raw.decode("utf-8"))
    except (UnicodeError, json.JSONDecodeError):
        return status, None


def _package_url(api_url: str, owner: str, owner_kind: str, package: str) -> str:
    namespace = "orgs" if owner_kind == "org" else "users"
    return (
        f"{api_url}/{namespace}/{quote(owner, safe='')}/packages/container/"
        f"{quote(package, safe='')}"
    )


def _check_package(
    *,
    api_url: str,
    owner: str,
    owner_kind: str,
    package: str,
    headers: Mapping[str, str],
    timeout: float,
    fetcher: Fetcher,
) -> PackageCheck:
    kinds = ("org", "user") if owner_kind == "auto" else (owner_kind,)
    for index, candidate_kind in enumerate(kinds):
        url = _package_url(api_url, owner, candidate_kind, package)
        try:
            status, payload = fetcher(url, headers, timeout)
        except Exception:  # A transport must never make this gate fail open.
            return PackageCheck(package, False, "transport_error", candidate_kind)

        if not isinstance(status, int):
            return PackageCheck(package, False, "invalid_status", candidate_kind)
        if status == 404 and index + 1 < len(kinds):
            continue
        if status != 200:
            return PackageCheck(package, False, f"http_{status}", candidate_kind)
        if not isinstance(payload, dict):
            return PackageCheck(package, False, "invalid_response", candidate_kind)
        if payload.get("name") != package:
            return PackageCheck(package, False, "package_name_mismatch", candidate_kind)
        if payload.get("package_type") != "container":
            return PackageCheck(package, False, "package_type_mismatch", candidate_kind)
        visibility = payload.get("visibility")
        if visibility != "private":
            if visibility in {"public", "internal"}:
                reason = f"visibility_{visibility}"
            elif visibility is None:
                reason = "visibility_missing"
            else:
                # Do not reflect arbitrary API response data into CI logs.
                reason = "visibility_invalid"
            return PackageCheck(package, False, reason, candidate_kind)
        return PackageCheck(package, True, "private", candidate_kind)

    return PackageCheck(package, False, "not_found", None)


def verify_release_packages(
    *,
    owner: str,
    token: str,
    package_names: Sequence[str] = CANONICAL_PACKAGE_NAMES,
    owner_kind: str = "auto",
    api_url: str = DEFAULT_API_URL,
    timeout: float = 10.0,
    fetcher: Fetcher = _fetch_json,
) -> tuple[PackageCheck, ...]:
    """Return a result for all 16 packages without changing GitHub state."""

    owner = _validate_owner(owner)
    token = token.strip()
    if not token:
        raise PackageVisibilityError("GitHub API token is missing")
    if owner_kind not in {"auto", "org", "user"}:
        raise PackageVisibilityError("owner kind must be auto, org, or user")
    api_url = _validate_api_url(api_url)
    if not math.isfinite(timeout) or timeout <= 0:
        raise PackageVisibilityError("timeout must be a positive finite number")
    packages = _validate_inventory(package_names)
    headers = {
        "Accept": "application/vnd.github+json",
        "Authorization": f"Bearer {token}",
        "X-GitHub-Api-Version": DEFAULT_API_VERSION,
        "User-Agent": "omega-release-package-visibility/1",
    }
    return tuple(
        _check_package(
            api_url=api_url,
            owner=owner,
            owner_kind=owner_kind,
            package=package,
            headers=headers,
            timeout=timeout,
            fetcher=fetcher,
        )
        for package in packages
    )


def _positive_float(raw: str) -> float:
    try:
        value = float(raw)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("must be a number") from exc
    if not math.isfinite(value) or value <= 0:
        raise argparse.ArgumentTypeError("must be a positive finite number")
    return value


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--owner",
        help="GitHub user or organization (default: GITHUB_REPOSITORY_OWNER)",
    )
    parser.add_argument(
        "--owner-kind",
        choices=("auto", "org", "user"),
        default="auto",
        help="GitHub owner namespace; auto tries organization then user",
    )
    parser.add_argument(
        "--inventory",
        type=Path,
        default=DEFAULT_INVENTORY_PATH,
        help="canonical shell, JSON, or newline-delimited package inventory",
    )
    parser.add_argument(
        "--token-env",
        default="GITHUB_TOKEN",
        help="environment variable containing the API token (default: GITHUB_TOKEN)",
    )
    parser.add_argument("--api-url", default=DEFAULT_API_URL)
    parser.add_argument("--timeout", type=_positive_float, default=10.0)
    return parser


def main(
    argv: Sequence[str] | None = None,
    *,
    environ: Mapping[str, str] | None = None,
    fetcher: Fetcher = _fetch_json,
) -> int:
    args = _parser().parse_args(argv)
    environment = os.environ if environ is None else environ
    owner = args.owner or environment.get("GITHUB_REPOSITORY_OWNER", "")
    token = environment.get(args.token_env, "")

    try:
        packages = load_inventory(args.inventory)
        checks = verify_release_packages(
            owner=owner,
            token=token,
            package_names=packages,
            owner_kind=args.owner_kind,
            api_url=args.api_url,
            timeout=args.timeout,
            fetcher=fetcher,
        )
    except PackageVisibilityError as exc:
        print(f"BLOCKED: {exc}", file=sys.stderr)
        return 2

    failed = [check for check in checks if not check.private]
    if failed:
        for check in failed:
            print(
                f"BLOCKED package={check.package} reason={check.reason}",
                file=sys.stderr,
            )
        print(
            f"BLOCKED release package privacy: {len(checks) - len(failed)}/16 private",
            file=sys.stderr,
        )
        return 1

    print(f"RELEASE_PACKAGE_PRIVACY PASS 16/16 owner={_validate_owner(owner)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
