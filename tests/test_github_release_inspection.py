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
            "assets": [{"name": "manifest.json"}, {"name": "manifest.json.sha256"}],
        }

    result = inspect_release(
        repository="omega-owner/omega",
        tag="v1.45.210-beta",
        token="secret",
        fetcher=fetch,
    )

    assert result["state"] == "present"
    assert result["assets"] == ["manifest.json", "manifest.json.sha256"]
    assert "secret" not in calls[0][0]
    assert calls[0][1]["Authorization"] == "Bearer secret"


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
            fetcher=lambda _url, _headers: (200, {"tag_name": "v9.9.9", "assets": []}),
        )
