from __future__ import annotations

from collections.abc import Mapping
from typing import Any

import pytest

from scripts.inspect_github_release import ReleaseInspectionError, inspect_release


def test_structured_http_404_is_the_only_absence_signal() -> None:
    result = inspect_release(
        repository="omega-owner/omega",
        tag="v1.45.210-beta",
        token="secret",
        fetcher=lambda _url, _headers: (404, None),
    )

    assert result == {
        "schema_version": 1,
        "state": "absent",
        "tag": "v1.45.210-beta",
        "immutable": None,
        "title": None,
        "body": None,
        "prerelease": None,
        "target": None,
        "assets": [],
    }


@pytest.mark.parametrize("status", [0, 401, 403, 429, 500])
def test_all_other_http_failures_are_ambiguous(status: int) -> None:
    with pytest.raises(ReleaseInspectionError, match=f"HTTP {status}"):
        inspect_release(
            repository="omega-owner/omega",
            tag="v1.45.210-beta",
            token="secret",
            fetcher=lambda _url, _headers: (status, None),
        )


def test_present_release_exports_only_validated_asset_names() -> None:
    calls: list[tuple[str, Mapping[str, str]]] = []

    def fetch(url: str, headers: Mapping[str, str]) -> tuple[int, Any]:
        calls.append((url, headers))
        return 200, {
            "tag_name": "v1.45.210-beta",
            "draft": False,
            "immutable": True,
            "name": "v1.45.210-beta",
            "body": "canonical",
            "prerelease": True,
            "target_commitish": "a" * 40,
            "assets": [{"name": "manifest.json"}, {"name": "manifest.json.sha256"}],
        }

    result = inspect_release(
        repository="omega-owner/omega",
        tag="v1.45.210-beta",
        token="secret",
        fetcher=fetch,
    )

    assert result["state"] == "present"
    assert result["immutable"] is True
    assert result["assets"] == ["manifest.json", "manifest.json.sha256"]
    assert "secret" not in calls[0][0]
    assert calls[0][1]["Authorization"] == "Bearer secret"


def test_missing_publication_state_is_ambiguous() -> None:
    payload: dict[str, Any] = {
        "tag_name": "v1.45.210-beta",
        "assets": [],
    }

    with pytest.raises(ReleaseInspectionError, match="publication state"):
        inspect_release(
            repository="omega-owner/omega",
            tag="v1.45.210-beta",
            token="secret",
            fetcher=lambda _url, _headers: (200, payload),
        )


def test_draft_release_is_explicit_recoverable_non_authority() -> None:
    result = inspect_release(
        repository="omega-owner/omega",
        tag="v1.45.210-beta",
        token="secret",
        fetcher=lambda _url, _headers: (
            200,
            {
                "tag_name": "v1.45.210-beta",
                "draft": True,
                "immutable": False,
                "name": "v1.45.210-beta",
                "body": "canonical",
                "prerelease": True,
                "target_commitish": "a" * 40,
                "assets": [{"name": "manifest.json"}],
            },
        ),
    )

    assert result == {
        "schema_version": 1,
        "state": "draft",
        "tag": "v1.45.210-beta",
        "immutable": False,
        "title": "v1.45.210-beta",
        "body": "canonical",
        "prerelease": True,
        "target": "a" * 40,
        "assets": ["manifest.json"],
    }


def test_transport_and_malformed_payloads_fail_closed() -> None:
    def explode(_url: str, _headers: Mapping[str, str]) -> tuple[int, Any]:
        raise RuntimeError("release not found")

    with pytest.raises(ReleaseInspectionError, match="lookup failed"):
        inspect_release(
            repository="omega-owner/omega",
            tag="v1.45.210-beta",
            token="secret",
            fetcher=explode,
        )
    with pytest.raises(ReleaseInspectionError, match="identity mismatch"):
        inspect_release(
            repository="omega-owner/omega",
            tag="v1.45.210-beta",
            token="secret",
            fetcher=lambda _url, _headers: (
                200,
                {"tag_name": "v9.9.9", "assets": []},
            ),
        )


@pytest.mark.parametrize("immutable", [False, None])
def test_mutable_or_unknown_release_cannot_be_authority(
    immutable: bool | None,
) -> None:
    payload: dict[str, Any] = {
        "tag_name": "v1.45.210-beta",
        "draft": False,
        "name": "v1.45.210-beta",
        "body": "canonical",
        "prerelease": True,
        "target_commitish": "a" * 40,
        "assets": [],
    }
    if immutable is not None:
        payload["immutable"] = immutable

    with pytest.raises(ReleaseInspectionError, match="not immutable"):
        inspect_release(
            repository="omega-owner/omega",
            tag="v1.45.210-beta",
            token="secret",
            fetcher=lambda _url, _headers: (200, payload),
        )
