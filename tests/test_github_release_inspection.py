from __future__ import annotations

from collections.abc import Mapping
from typing import Any

import pytest

import scripts.inspect_github_release as release_inspector
from scripts.inspect_github_release import ReleaseInspectionError, inspect_release


TAG = "v1.45.210-beta"
SHA = "a" * 40


def _release(
    *,
    release_id: int = 210,
    tag: str = TAG,
    draft: bool = False,
    immutable: bool | None = None,
) -> dict[str, Any]:
    return {
        "id": release_id,
        "tag_name": tag,
        "draft": draft,
        "immutable": (not draft) if immutable is None else immutable,
        "name": tag,
        "body": "canonical",
        "prerelease": True,
        "target_commitish": SHA,
        "assets": [{"name": "manifest.json"}],
    }


def _other(release_id: int) -> dict[str, Any]:
    return {
        "id": release_id,
        "tag_name": f"legacy-{release_id}",
        "draft": False,
    }


def _fetch_sequence(
    *responses: tuple[int, Any],
) -> tuple[
    list[tuple[str, Mapping[str, str]]],
    Any,
]:
    calls: list[tuple[str, Mapping[str, str]]] = []
    pending = list(responses)

    def fetch(url: str, headers: Mapping[str, str]) -> tuple[int, Any]:
        calls.append((url, headers))
        if not pending:
            raise AssertionError(f"unexpected GitHub API call: {url}")
        return pending.pop(0)

    return calls, fetch


def test_404_plus_exhaustive_empty_list_is_absent() -> None:
    calls, fetch = _fetch_sequence((404, None), (200, []), (404, None))

    result = inspect_release(
        repository="omega-owner/omega",
        tag=TAG,
        token="secret",
        fetcher=fetch,
    )

    assert result == {
        "schema_version": 1,
        "state": "absent",
        "tag": TAG,
        "immutable": None,
        "title": None,
        "body": None,
        "prerelease": None,
        "target": None,
        "assets": [],
    }
    assert "/releases/tags/" in calls[0][0]
    assert calls[1][0].endswith("/releases?per_page=100&page=1")
    assert calls[2][0] == calls[0][0]
    assert len(calls) == 3


def test_draft_on_later_page_is_found_and_normalized() -> None:
    first_page = [_other(release_id) for release_id in range(1, 101)]
    draft = _release(release_id=101, draft=True)
    calls, fetch = _fetch_sequence(
        (404, None),
        (200, first_page),
        (200, [draft]),
        (404, None),
    )

    result = inspect_release(
        repository="omega-owner/omega", tag=TAG, token="secret", fetcher=fetch
    )

    assert result == {
        "schema_version": 1,
        "state": "draft",
        "tag": TAG,
        "immutable": False,
        "title": TAG,
        "body": "canonical",
        "prerelease": True,
        "target": SHA,
        "assets": ["manifest.json"],
    }
    assert calls[2][0].endswith("/releases?per_page=100&page=2")
    assert calls[3][0] == calls[0][0]
    assert len(calls) == 4


def test_exactly_one_full_page_requires_and_accepts_terminal_empty_page() -> None:
    page = [_other(release_id) for release_id in range(1, 101)]
    calls, fetch = _fetch_sequence(
        (404, None), (200, page), (200, []), (404, None)
    )

    result = inspect_release(
        repository="omega-owner/omega", tag=TAG, token="secret", fetcher=fetch
    )

    assert result["state"] == "absent"
    assert calls[2][0].endswith("/releases?per_page=100&page=2")
    assert calls[3][0] == calls[0][0]
    assert len(calls) == 4


def test_published_release_fast_path_is_unchanged_and_does_not_list() -> None:
    calls, fetch = _fetch_sequence((200, _release()))

    result = inspect_release(
        repository="omega-owner/omega", tag=TAG, token="secret", fetcher=fetch
    )

    assert result["state"] == "present"
    assert result["immutable"] is True
    assert result["assets"] == ["manifest.json"]
    assert len(calls) == 1
    assert "secret" not in calls[0][0]
    assert calls[0][1]["Authorization"] == "Bearer secret"


def test_tag_endpoint_cannot_manufacture_a_draft_state() -> None:
    with pytest.raises(ReleaseInspectionError, match="published endpoint.*draft"):
        inspect_release(
            repository="omega-owner/omega",
            tag=TAG,
            token="secret",
            fetcher=lambda _url, _headers: (200, _release(draft=True)),
        )


def test_publish_race_after_list_resolves_to_present_for_the_same_id() -> None:
    draft = _release(draft=True)
    published = _release(draft=False)
    calls, fetch = _fetch_sequence(
        (404, None), (200, [draft]), (200, published)
    )

    result = inspect_release(
        repository="omega-owner/omega", tag=TAG, token="secret", fetcher=fetch
    )

    assert result["state"] == "present"
    assert result["immutable"] is True
    assert calls[2][0] == calls[0][0]
    assert len(calls) == 3


def test_publish_race_ignores_api_asset_order_but_preserves_duplicates() -> None:
    draft = _release(draft=True)
    draft["assets"] = [{"name": "z.json"}, {"name": "a.json"}]
    published = _release(draft=False)
    published["assets"] = [{"name": "a.json"}, {"name": "z.json"}]
    _calls, fetch = _fetch_sequence(
        (404, None), (200, [draft]), (200, published)
    )

    result = inspect_release(
        repository="omega-owner/omega", tag=TAG, token="secret", fetcher=fetch
    )

    assert result["state"] == "present"
    assert result["assets"] == ["a.json", "z.json"]


def test_publish_race_after_empty_list_resolves_to_present() -> None:
    calls, fetch = _fetch_sequence(
        (404, None), (200, []), (200, _release(draft=False))
    )

    result = inspect_release(
        repository="omega-owner/omega", tag=TAG, token="secret", fetcher=fetch
    )

    assert result["state"] == "present"
    assert calls[2][0] == calls[0][0]
    assert len(calls) == 3


def test_publish_race_with_a_different_release_id_is_ambiguous() -> None:
    _calls, fetch = _fetch_sequence(
        (404, None),
        (200, [_release(release_id=210, draft=True)]),
        (200, _release(release_id=211, draft=False)),
    )

    with pytest.raises(ReleaseInspectionError, match="identity changed"):
        inspect_release(
            repository="omega-owner/omega", tag=TAG, token="secret", fetcher=fetch
        )


def test_listed_published_release_requires_a_matching_published_recheck() -> None:
    _calls, fetch = _fetch_sequence(
        (404, None), (200, [_release(draft=False)]), (404, None)
    )

    with pytest.raises(ReleaseInspectionError, match="publication state is inconsistent"):
        inspect_release(
            repository="omega-owner/omega", tag=TAG, token="secret", fetcher=fetch
        )


def test_listed_published_release_can_settle_on_matching_recheck() -> None:
    published = _release(draft=False)
    _calls, fetch = _fetch_sequence(
        (404, None), (200, [published]), (200, published)
    )

    result = inspect_release(
        repository="omega-owner/omega", tag=TAG, token="secret", fetcher=fetch
    )

    assert result["state"] == "present"


@pytest.mark.parametrize("status", [0, 401, 403, 429, 500])
def test_initial_non_success_http_statuses_are_ambiguous(status: int) -> None:
    with pytest.raises(ReleaseInspectionError, match=f"HTTP {status}"):
        inspect_release(
            repository="omega-owner/omega",
            tag=TAG,
            token="secret",
            fetcher=lambda _url, _headers: (status, None),
        )


@pytest.mark.parametrize("status", [0, 401, 403, 404, 429, 500])
def test_list_non_success_http_statuses_are_never_absence(status: int) -> None:
    _calls, fetch = _fetch_sequence((404, None), (status, None))
    with pytest.raises(ReleaseInspectionError, match=f"HTTP {status}"):
        inspect_release(
            repository="omega-owner/omega", tag=TAG, token="secret", fetcher=fetch
        )


@pytest.mark.parametrize(
    "page",
    [
        {},
        "not-a-list",
        ["not-an-object"],
        [{"id": True, "tag_name": "v1.0.0", "draft": False}],
        [{"id": 0, "tag_name": "v1.0.0", "draft": False}],
        [{"id": "1", "tag_name": "v1.0.0", "draft": False}],
        [{"id": 1, "tag_name": "", "draft": False}],
        [{"id": 1, "tag_name": "bad\ntag", "draft": False}],
        [{"id": 1, "tag_name": "v1.0.0", "draft": "false"}],
    ],
)
def test_malformed_list_pages_and_entries_block(page: Any) -> None:
    _calls, fetch = _fetch_sequence((404, None), (200, page))
    with pytest.raises(ReleaseInspectionError, match="release list"):
        inspect_release(
            repository="omega-owner/omega", tag=TAG, token="secret", fetcher=fetch
        )


def test_oversized_list_page_blocks() -> None:
    page = [_other(release_id) for release_id in range(1, 102)]
    _calls, fetch = _fetch_sequence((404, None), (200, page))
    with pytest.raises(ReleaseInspectionError, match="page size"):
        inspect_release(
            repository="omega-owner/omega", tag=TAG, token="secret", fetcher=fetch
        )


def test_repeated_id_across_pages_is_nonadvancing_and_blocks() -> None:
    first = [_other(release_id) for release_id in range(1, 101)]
    second = [_other(100)]
    _calls, fetch = _fetch_sequence((404, None), (200, first), (200, second))
    with pytest.raises(ReleaseInspectionError, match="did not advance"):
        inspect_release(
            repository="omega-owner/omega", tag=TAG, token="secret", fetcher=fetch
        )


def test_repeated_id_within_one_page_blocks() -> None:
    _calls, fetch = _fetch_sequence(
        (404, None), (200, [_other(1), {**_other(1), "tag_name": "changed"}])
    )
    with pytest.raises(ReleaseInspectionError, match="did not advance"):
        inspect_release(
            repository="omega-owner/omega", tag=TAG, token="secret", fetcher=fetch
        )


def test_duplicate_exact_tag_under_distinct_ids_blocks() -> None:
    _calls, fetch = _fetch_sequence(
        (404, None),
        (
            200,
            [
                _release(release_id=210, draft=True),
                _release(release_id=211, draft=True),
            ],
        ),
    )
    with pytest.raises(ReleaseInspectionError, match="multiple releases"):
        inspect_release(
            repository="omega-owner/omega", tag=TAG, token="secret", fetcher=fetch
        )


def test_target_on_full_page_does_not_short_circuit_duplicate_scan() -> None:
    first = [_other(release_id) for release_id in range(1, 100)]
    first.append(_release(release_id=210, draft=True))
    _calls, fetch = _fetch_sequence(
        (404, None), (200, first), (200, [_release(release_id=211, draft=True)])
    )

    with pytest.raises(ReleaseInspectionError, match="multiple releases"):
        inspect_release(
            repository="omega-owner/omega", tag=TAG, token="secret", fetcher=fetch
        )


def test_full_page_at_pagination_cap_blocks(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(release_inspector, "MAX_RELEASE_PAGES", 2)
    first = [_other(release_id) for release_id in range(1, 101)]
    second = [_other(release_id) for release_id in range(101, 201)]
    _calls, fetch = _fetch_sequence((404, None), (200, first), (200, second))

    with pytest.raises(ReleaseInspectionError, match="pagination cap"):
        inspect_release(
            repository="omega-owner/omega", tag=TAG, token="secret", fetcher=fetch
        )


@pytest.mark.parametrize(
    ("immutable", "remove"),
    [
        (True, False),
        (None, False),
        ("false", False),
        (None, True),
    ],
)
def test_draft_immutable_must_be_exactly_false(
    immutable: object, remove: bool
) -> None:
    draft = _release(draft=True)
    if remove:
        del draft["immutable"]
    else:
        draft["immutable"] = immutable
    _calls, fetch = _fetch_sequence((404, None), (200, [draft]))

    with pytest.raises(ReleaseInspectionError, match="draft is immutable"):
        inspect_release(
            repository="omega-owner/omega", tag=TAG, token="secret", fetcher=fetch
        )


@pytest.mark.parametrize(
    ("mutation", "message"),
    [
        ({"name": None}, "metadata"),
        ({"body": None}, "metadata"),
        ({"prerelease": "true"}, "metadata"),
        ({"target_commitish": ""}, "metadata"),
        ({"assets": {}}, "assets"),
        ({"assets": [{}]}, "asset name"),
    ],
)
def test_malformed_exact_draft_metadata_and_assets_block(
    mutation: dict[str, Any], message: str
) -> None:
    draft = _release(draft=True)
    draft.update(mutation)
    _calls, fetch = _fetch_sequence((404, None), (200, [draft]))

    with pytest.raises(ReleaseInspectionError, match=message):
        inspect_release(
            repository="omega-owner/omega", tag=TAG, token="secret", fetcher=fetch
        )


def test_duplicate_asset_names_remain_visible_to_downstream_validation() -> None:
    draft = _release(draft=True)
    draft["assets"] = [{"name": "manifest.json"}, {"name": "manifest.json"}]
    calls, fetch = _fetch_sequence((404, None), (200, [draft]), (404, None))

    result = inspect_release(
        repository="omega-owner/omega", tag=TAG, token="secret", fetcher=fetch
    )

    assert result["assets"] == ["manifest.json", "manifest.json"]
    assert calls[2][0] == calls[0][0]
    assert len(calls) == 3


@pytest.mark.parametrize(
    ("mutation", "message"),
    [
        ({"tag_name": "v9.9.9"}, "identity mismatch"),
        ({"id": None}, "release id"),
        ({"id": False}, "release id"),
        ({"name": None}, "metadata"),
        ({"assets": None}, "assets"),
        ({"assets": [{}]}, "asset name"),
    ],
)
def test_published_endpoint_uses_the_same_strict_normalizer(
    mutation: dict[str, Any], message: str
) -> None:
    published = _release()
    published.update(mutation)

    with pytest.raises(ReleaseInspectionError, match=message):
        inspect_release(
            repository="omega-owner/omega",
            tag=TAG,
            token="secret",
            fetcher=lambda _url, _headers: (200, published),
        )


def test_initial_transport_failure_fails_closed() -> None:
    def explode(_url: str, _headers: Mapping[str, str]) -> tuple[int, Any]:
        raise RuntimeError("connection reset")

    with pytest.raises(ReleaseInspectionError, match="lookup failed"):
        inspect_release(
            repository="omega-owner/omega", tag=TAG, token="secret", fetcher=explode
        )


@pytest.mark.parametrize(
    "response",
    [None, [], (200,), (200, {}, "extra"), (True, {}), ("200", {})],
)
def test_malformed_fetcher_response_is_always_structured_failure(
    response: object,
) -> None:
    with pytest.raises(ReleaseInspectionError, match="response"):
        inspect_release(
            repository="omega-owner/omega",
            tag=TAG,
            token="secret",
            fetcher=lambda _url, _headers: response,  # type: ignore[arg-type,return-value]
        )


def test_transport_failure_during_fallback_fails_closed() -> None:
    calls = 0

    def fetch(_url: str, _headers: Mapping[str, str]) -> tuple[int, Any]:
        nonlocal calls
        calls += 1
        if calls == 1:
            return 404, None
        raise RuntimeError("connection reset")

    with pytest.raises(ReleaseInspectionError, match="lookup failed"):
        inspect_release(
            repository="omega-owner/omega", tag=TAG, token="secret", fetcher=fetch
        )


def test_missing_publication_state_is_ambiguous() -> None:
    payload = _release()
    del payload["draft"]

    with pytest.raises(ReleaseInspectionError, match="publication state"):
        inspect_release(
            repository="omega-owner/omega",
            tag=TAG,
            token="secret",
            fetcher=lambda _url, _headers: (200, payload),
        )


@pytest.mark.parametrize("immutable", [False, None])
def test_mutable_or_unknown_published_release_cannot_be_authority(
    immutable: bool | None,
) -> None:
    payload = _release(immutable=immutable)
    if immutable is None:
        del payload["immutable"]

    with pytest.raises(ReleaseInspectionError, match="not immutable"):
        inspect_release(
            repository="omega-owner/omega",
            tag=TAG,
            token="secret",
            fetcher=lambda _url, _headers: (200, payload),
        )
