from __future__ import annotations

import json
import re
from collections.abc import Mapping
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urlsplit

import pytest

from scripts.verify_release_package_visibility import (
    CANONICAL_PACKAGE_NAMES,
    DEFAULT_INVENTORY_PATH,
    PackageVisibilityError,
    load_inventory,
    main,
    verify_release_packages,
)


def _package_from_url(url: str) -> str:
    return unquote(urlsplit(url).path.rsplit("/", 1)[-1])


def _private_fetcher(
    calls: list[tuple[str, Mapping[str, str], float]],
):
    def fetch(
        url: str, headers: Mapping[str, str], timeout: float
    ) -> tuple[int, Any]:
        calls.append((url, headers, timeout))
        package = _package_from_url(url)
        return 200, {
            "name": package,
            "package_type": "container",
            "visibility": "private",
        }

    return fetch


def test_canonical_inventory_matches_release_preflight() -> None:
    assert len(CANONICAL_PACKAGE_NAMES) == 16
    assert len(set(CANONICAL_PACKAGE_NAMES)) == 16
    assert load_inventory(DEFAULT_INVENTORY_PATH) == CANONICAL_PACKAGE_NAMES


def test_verifies_every_package_without_putting_token_in_urls() -> None:
    calls: list[tuple[str, Mapping[str, str], float]] = []
    token = "github_pat_secret-test-value"

    checks = verify_release_packages(
        owner="omega-owner",
        token=token,
        owner_kind="org",
        fetcher=_private_fetcher(calls),
    )

    assert len(checks) == len(calls) == 16
    assert all(check.private for check in checks)
    assert [_package_from_url(call[0]) for call in calls] == list(
        CANONICAL_PACKAGE_NAMES
    )
    assert all(token not in url for url, _headers, _timeout in calls)
    assert all(headers["Authorization"] == f"Bearer {token}" for _, headers, _ in calls)


@pytest.mark.parametrize(
    ("payload_override", "expected_reason"),
    [
        ({"visibility": "public"}, "visibility_public"),
        ({"visibility": "internal"}, "visibility_internal"),
        ({"visibility": None}, "visibility_missing"),
        ({"visibility": "unexpected-secret-like-value"}, "visibility_invalid"),
        ({"name": "different"}, "package_name_mismatch"),
        ({"package_type": "npm"}, "package_type_mismatch"),
    ],
)
def test_fails_closed_for_non_private_or_ambiguous_payloads(
    payload_override: dict[str, object], expected_reason: str
) -> None:
    def fetch(url: str, _headers: Mapping[str, str], _timeout: float):
        package = _package_from_url(url)
        payload: dict[str, object] = {
            "name": package,
            "package_type": "container",
            "visibility": "private",
        }
        payload.update(payload_override)
        return 200, payload

    checks = verify_release_packages(
        owner="omega-owner", token="secret", owner_kind="org", fetcher=fetch
    )

    assert checks[0].private is False
    assert checks[0].reason == expected_reason


@pytest.mark.parametrize("status", [401, 403, 404, 429, 500])
def test_fails_closed_for_github_api_errors(status: int) -> None:
    def fetch(_url: str, _headers: Mapping[str, str], _timeout: float):
        return status, None

    checks = verify_release_packages(
        owner="omega-owner", token="secret", owner_kind="org", fetcher=fetch
    )

    assert all(not check.private for check in checks)
    assert {check.reason for check in checks} == {f"http_{status}"}


def test_auto_owner_falls_back_from_organization_to_user() -> None:
    calls: list[str] = []

    def fetch(url: str, _headers: Mapping[str, str], _timeout: float):
        calls.append(url)
        if "/orgs/" in url:
            return 404, None
        package = _package_from_url(url)
        return 200, {
            "name": package,
            "package_type": "container",
            "visibility": "private",
        }

    checks = verify_release_packages(
        owner="omega-user", token="secret", owner_kind="auto", fetcher=fetch
    )

    assert all(check.private and check.owner_kind == "user" for check in checks)
    assert len(calls) == 2 * len(CANONICAL_PACKAGE_NAMES)


def test_inventory_file_must_be_the_exact_reviewed_set(tmp_path: Path) -> None:
    inventory = tmp_path / "packages.json"
    inventory.write_text(
        json.dumps({"packages": list(CANONICAL_PACKAGE_NAMES[:-1])}),
        encoding="utf-8",
    )

    with pytest.raises(PackageVisibilityError, match="canonical 16"):
        load_inventory(inventory)


def test_cli_passes_offline_and_does_not_print_token(capsys: pytest.CaptureFixture[str]) -> None:
    calls: list[tuple[str, Mapping[str, str], float]] = []
    token = "never-print-this-token"

    status = main(
        ["--owner", "omega-owner", "--owner-kind", "org"],
        environ={"GITHUB_TOKEN": token},
        fetcher=_private_fetcher(calls),
    )

    captured = capsys.readouterr()
    assert status == 0
    assert captured.out == "RELEASE_PACKAGE_PRIVACY PASS 16/16 owner=omega-owner\n"
    assert captured.err == ""
    assert token not in captured.out + captured.err


def test_cli_failure_is_sanitized_and_never_echoes_token(
    capsys: pytest.CaptureFixture[str],
) -> None:
    token = "never-print-this-token"

    def exploding_fetcher(
        _url: str, _headers: Mapping[str, str], _timeout: float
    ) -> tuple[int, Any]:
        raise RuntimeError(f"transport accidentally included {token}")

    status = main(
        ["--owner", "omega-owner", "--owner-kind", "org"],
        environ={"GITHUB_TOKEN": token},
        fetcher=exploding_fetcher,
    )

    captured = capsys.readouterr()
    assert status == 1
    assert "transport_error" in captured.err
    assert token not in captured.out + captured.err


def test_cli_blocks_before_transport_when_token_is_missing(
    capsys: pytest.CaptureFixture[str],
) -> None:
    def unexpected_fetcher(*_args: object) -> tuple[int, Any]:
        raise AssertionError("transport must not run")

    status = main(
        ["--owner", "omega-owner"],
        environ={},
        fetcher=unexpected_fetcher,
    )

    captured = capsys.readouterr()
    assert status == 2
    assert re.search(r"token is missing", captured.err)
