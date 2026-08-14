from __future__ import annotations

import json
from collections.abc import Mapping
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, unquote, urlsplit

import pytest
import yaml

from scripts.verify_release_image_absence import (
    HttpResult,
    ImageAbsenceError,
    main,
    verify_release_image_tags_absent,
)

REPO = Path(__file__).resolve().parents[1]
WORKFLOW = REPO / ".github" / "workflows" / "release.yml"
OWNER = "omega-owner"
SERVICE = "console"
USERNAME = "release-actor"
GITHUB_TOKEN = "github-secret-token"
REGISTRY_TOKEN = "registry-token=="
SOURCE_SHA = "a" * 40
RELEASE_TAG = "v1.45.210-beta"
SHA_TAG = f"sha-{SOURCE_SHA}"


def _json_result(status: int, payload: Any) -> HttpResult:
    return HttpResult(
        status=status,
        body=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
    )


def _manifest_tag(url: str) -> str:
    return unquote(urlsplit(url).path.rsplit("/", 1)[-1])


def _fetcher(
    *,
    tag_results: Mapping[str, HttpResult] | None = None,
    token_result: HttpResult | None = None,
    calls: list[tuple[str, Mapping[str, str], float]] | None = None,
):
    missing = _json_result(
        404, {"errors": [{"code": "MANIFEST_UNKNOWN", "message": "manifest unknown"}]}
    )

    def fetch(url: str, headers: Mapping[str, str], timeout: float) -> HttpResult:
        if calls is not None:
            calls.append((url, headers, timeout))
        if url.startswith("https://ghcr.io/token?"):
            return token_result or _json_result(200, {"token": REGISTRY_TOKEN})
        return (tag_results or {}).get(_manifest_tag(url), missing)

    return fetch


def _verify(**overrides: object) -> tuple[str, str]:
    values: dict[str, object] = {
        "owner": OWNER,
        "service": SERVICE,
        "username": USERNAME,
        "github_token": GITHUB_TOKEN,
        "source_sha": SOURCE_SHA,
        "release_tag": RELEASE_TAG,
        "fetcher": _fetcher(),
    }
    values.update(overrides)
    return verify_release_image_tags_absent(**values)  # type: ignore[arg-type]


def test_accepts_only_two_exact_reference_bound_structured_absences() -> None:
    calls: list[tuple[str, Mapping[str, str], float]] = []

    references = _verify(fetcher=_fetcher(calls=calls))

    assert references == (
        f"ghcr.io/{OWNER}/{SERVICE}:{SHA_TAG}",
        f"ghcr.io/{OWNER}/{SERVICE}:{RELEASE_TAG}",
    )
    assert len(calls) == 3
    token_url, token_headers, _timeout = calls[0]
    token_query = parse_qs(urlsplit(token_url).query)
    assert token_query == {
        "service": ["ghcr.io"],
        "scope": [f"repository:{OWNER}/{SERVICE}:pull"],
    }
    assert token_headers["Authorization"].startswith("Basic ")
    assert GITHUB_TOKEN not in token_url
    for (url, headers, _timeout), expected_tag in zip(
        calls[1:], (SHA_TAG, RELEASE_TAG), strict=True
    ):
        assert url == (f"https://ghcr.io/v2/{OWNER}/{SERVICE}/manifests/{expected_tag}")
        assert headers["Authorization"] == f"Bearer {REGISTRY_TOKEN}"


def test_existing_sha_tag_blocks_before_release_tag_lookup() -> None:
    calls: list[tuple[str, Mapping[str, str], float]] = []
    present = HttpResult(
        200,
        b"{}",
        {"Content-Type": "application/vnd.oci.image.index.v1+json"},
    )

    with pytest.raises(ImageAbsenceError, match="pre-existing release image tag"):
        _verify(fetcher=_fetcher(tag_results={SHA_TAG: present}, calls=calls))

    assert len(calls) == 2


def test_existing_release_tag_blocks_after_sha_absence() -> None:
    present = HttpResult(
        200,
        b"{}",
        {"Content-Type": "application/vnd.oci.image.index.v1+json"},
    )

    with pytest.raises(ImageAbsenceError, match=RELEASE_TAG):
        _verify(fetcher=_fetcher(tag_results={RELEASE_TAG: present}))


@pytest.mark.parametrize("status", [301, 302, 401, 403, 404, 429, 500, 503])
def test_token_exchange_redirect_auth_absence_and_server_errors_fail_closed(
    status: int,
) -> None:
    with pytest.raises(ImageAbsenceError, match=f"HTTP {status}"):
        _verify(
            fetcher=_fetcher(
                token_result=_json_result(
                    status, {"errors": [{"code": "UNAUTHORIZED"}]}
                )
            )
        )


@pytest.mark.parametrize("status", [301, 302, 401, 403, 429, 500, 503])
def test_manifest_redirect_auth_rate_limit_and_server_errors_fail_closed(
    status: int,
) -> None:
    with pytest.raises(ImageAbsenceError, match=f"HTTP {status}"):
        _verify(
            fetcher=_fetcher(
                tag_results={
                    SHA_TAG: _json_result(
                        status, {"errors": [{"code": "UNAUTHORIZED"}]}
                    )
                }
            )
        )


@pytest.mark.parametrize(
    "payload",
    [
        {"errors": [{"code": "NAME_UNKNOWN", "message": "repository missing"}]},
        {"errors": [{"code": "UNAUTHORIZED", "message": "authentication failed"}]},
        {"errors": []},
        {
            "errors": [
                {"code": "MANIFEST_UNKNOWN"},
                {"code": "MANIFEST_UNKNOWN"},
            ]
        },
        {},
    ],
)
def test_only_one_structured_manifest_unknown_error_proves_404_absence(
    payload: object,
) -> None:
    with pytest.raises(ImageAbsenceError, match="not structured manifest absence"):
        _verify(fetcher=_fetcher(tag_results={SHA_TAG: _json_result(404, payload)}))


@pytest.mark.parametrize(
    "result",
    [
        HttpResult(404, b"not-json", {"Content-Type": "application/json"}),
        HttpResult(
            404,
            b'{"errors":[{"code":"MANIFEST_UNKNOWN"}]}',
            {"Content-Type": "text/plain"},
        ),
    ],
)
def test_malformed_or_non_json_404_never_proves_absence(
    result: HttpResult,
) -> None:
    with pytest.raises(ImageAbsenceError):
        _verify(fetcher=_fetcher(tag_results={SHA_TAG: result}))


def test_transport_exception_fails_closed_without_leaking_token(
    capsys: pytest.CaptureFixture[str],
) -> None:
    def explode(_url: str, _headers: Mapping[str, str], _timeout: float) -> HttpResult:
        raise RuntimeError(f"transport included {GITHUB_TOKEN}")

    status = main(
        [
            "--owner",
            OWNER,
            "--service",
            SERVICE,
            "--username",
            USERNAME,
            "--source-sha",
            SOURCE_SHA,
            "--release-tag",
            RELEASE_TAG,
        ],
        environ={"GITHUB_TOKEN": GITHUB_TOKEN},
        fetcher=explode,
    )

    captured = capsys.readouterr()
    assert status == 1
    assert "unexpected verifier failure" in captured.err
    assert GITHUB_TOKEN not in captured.out + captured.err


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("owner", "../owner"),
        ("service", "not-canonical"),
        ("username", "actor:name"),
        ("source_sha", "abc"),
        ("release_tag", "v1.02.3"),
        ("release_tag", "v1.2.3+build"),
    ],
)
def test_invalid_identity_blocks_before_transport(field: str, value: str) -> None:
    def unexpected(*_args: object) -> HttpResult:
        raise AssertionError("transport must not run")

    with pytest.raises(ImageAbsenceError):
        _verify(**{field: value, "fetcher": unexpected})


def test_cli_output_is_sanitized_and_never_prints_credentials(
    capsys: pytest.CaptureFixture[str],
) -> None:
    status = main(
        [
            "--owner",
            OWNER,
            "--service",
            SERVICE,
            "--username",
            USERNAME,
            "--source-sha",
            SOURCE_SHA,
            "--release-tag",
            RELEASE_TAG,
        ],
        environ={"GITHUB_TOKEN": GITHUB_TOKEN},
        fetcher=_fetcher(),
    )

    captured = capsys.readouterr()
    assert status == 0
    assert "RELEASE_IMAGE_ABSENCE PASS" in captured.out
    assert captured.err == ""
    assert GITHUB_TOKEN not in captured.out + captured.err
    assert REGISTRY_TOKEN not in captured.out + captured.err


def test_real_buildx_not_found_prose_is_not_used_as_absence_evidence() -> None:
    real_buildx_output = (
        "ERROR: ghcr.io/emmanuelnavaromero02-commits/console:"
        "omega-redteam-definitely-absent-20260814: not found"
    )
    workflow = yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))
    step = next(
        item
        for item in workflow["jobs"]["build-and-push"]["steps"]
        if item.get("name") == "Refuse pre-existing image tags"
    )
    source = step["run"]

    assert real_buildx_output.endswith(": not found")
    assert "docker buildx imagetools inspect" not in source
    assert "not found" not in source.lower()
    assert "manifest unknown" not in source.lower()
    assert "scripts/verify_release_image_absence.py" in source
