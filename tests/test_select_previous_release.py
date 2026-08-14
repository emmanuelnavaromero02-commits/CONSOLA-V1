from __future__ import annotations

import hashlib
import json
import subprocess
from pathlib import Path

import pytest

from scripts.select_previous_release import (
    CANONICAL_SERVICES,
    ReleaseTrustError,
    SemVer,
    TRANSITION_RELEASES,
    select_previous_release,
    verify_github_release_manifest,
)


def _git(repo: Path, *args: str) -> str:
    return subprocess.check_output(["git", *args], cwd=repo, text=True).strip()


def _commit(repo: Path, name: str) -> str:
    (repo / "history.txt").write_text(name, encoding="utf-8")
    subprocess.run(["git", "add", "history.txt"], cwd=repo, check=True)
    subprocess.run(
        ["git", "commit", "-m", name], cwd=repo, check=True, capture_output=True
    )
    return _git(repo, "rev-parse", "HEAD")


def test_semver_prerelease_precedence_is_strict() -> None:
    assert SemVer.parse("v1.2.3") > SemVer.parse("v1.2.3-rc.1")
    assert SemVer.parse("v1.2.3-rc.2") > SemVer.parse("v1.2.3-rc.1")
    assert SemVer.parse("v1.2") is None
    assert SemVer.parse("release-v1.2.3") is None
    assert SemVer.parse("v01.2.3") is None
    assert SemVer.parse("v1.2.3-rc.01") is None


def test_selector_ignores_auxiliary_and_non_ancestral_tags(tmp_path: Path) -> None:
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    subprocess.run(
        ["git", "config", "user.email", "ci@example.com"], cwd=tmp_path, check=True
    )
    subprocess.run(["git", "config", "user.name", "CI"], cwd=tmp_path, check=True)
    first = _commit(tmp_path, "first")
    subprocess.run(["git", "tag", "v1.0.0"], cwd=tmp_path, check=True)
    subprocess.run(["git", "tag", "latest"], cwd=tmp_path, check=True)
    second = _commit(tmp_path, "second")
    subprocess.run(["git", "tag", "audit-2026"], cwd=tmp_path, check=True)
    subprocess.run(["git", "tag", "v99.0.0"], cwd=tmp_path, check=True)
    head = _commit(tmp_path, "release")
    subprocess.run(["git", "tag", "v1.1.0-beta"], cwd=tmp_path, check=True)

    checked: list[tuple[str, str]] = []

    def trust(tag: str, commit: str) -> bool:
        checked.append((tag, commit))
        return tag == "v1.0.0" and commit == first

    base, tag = select_previous_release(
        head,
        "v1.1.0-beta",
        repo=tmp_path,
        trust_verifier=trust,
    )

    assert base == first
    assert tag == "v1.0.0"
    assert checked == [("v99.0.0", second), ("v1.0.0", first)]


def test_selector_blocks_when_no_semver_tag_has_release_evidence(
    tmp_path: Path,
) -> None:
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    subprocess.run(
        ["git", "config", "user.email", "ci@example.com"], cwd=tmp_path, check=True
    )
    subprocess.run(["git", "config", "user.name", "CI"], cwd=tmp_path, check=True)
    _commit(tmp_path, "previous")
    subprocess.run(["git", "tag", "v9.9.9"], cwd=tmp_path, check=True)
    head = _commit(tmp_path, "release")

    with pytest.raises(ReleaseTrustError, match="no trusted previous release"):
        select_previous_release(
            head,
            "v10.0.0",
            repo=tmp_path,
            trust_verifier=lambda _tag, _commit: False,
        )


def _manifest(repository: str, tag: str, commit: str) -> bytes:
    owner = repository.split("/", 1)[0]
    images = []
    for index, service in enumerate(CANONICAL_SERVICES, start=1):
        image = f"ghcr.io/{owner}/{service}"
        digest = f"sha256:{index:064x}"
        images.append(
            {
                "schema_version": 1,
                "service": service,
                "image": image,
                "release_tag": tag,
                "source_sha": commit,
                "digest": digest,
                "release_reference": f"{image}:{tag}@{digest}",
                "sha_reference": f"{image}:sha-{commit}@{digest}",
            }
        )
    return (
        json.dumps(
            {
                "schema_version": 1,
                "repository": repository,
                "release_tag": tag,
                "source_sha": commit,
                "images": images,
            },
            indent=2,
            sort_keys=True,
        )
        + "\n"
    ).encode()


def _manifest_v2(repository: str, tag: str, commit: str) -> bytes:
    owner = repository.split("/", 1)[0]
    run_id = 987654
    images = []
    for index, service in enumerate(CANONICAL_SERVICES, start=1):
        image = f"ghcr.io/{owner}/{service}"
        digest = f"sha256:{index:064x}"
        images.append(
            {
                "schema_version": 2,
                "service": service,
                "image": image,
                "release_tag": tag,
                "source_sha": commit,
                "build_run_id": run_id,
                "digest": digest,
                "digest_reference": f"{image}@{digest}",
                "manifest_media_type": "application/vnd.oci.image.manifest.v1+json",
                "manifest_size": 512 + index,
            }
        )
    return (
        json.dumps(
            {
                "schema_version": 2,
                "kind": "omega-release-manifest",
                "repository": repository,
                "release_tag": tag,
                "source_sha": commit,
                "build_run_id": run_id,
                "images": images,
            },
            indent=2,
            sort_keys=True,
        )
        + "\n"
    ).encode()


def test_previous_release_requires_checksum_valid_manifest_bound_to_tag_and_sha() -> (
    None
):
    repository = "owner/repo"
    tag = "v1.45.210-beta"
    commit = "a" * 40
    manifest = _manifest(repository, tag, commit)
    checksum = f"{hashlib.sha256(manifest).hexdigest()}  omega-release-manifest-{tag}.json\n".encode()
    release = json.dumps(
        {
            "tag_name": tag,
            "draft": False,
            "immutable": True,
            "assets": [
                {"id": 1, "name": f"omega-release-manifest-{tag}.json"},
                {"id": 2, "name": f"omega-release-manifest-{tag}.json.sha256"},
            ],
        }
    ).encode()

    def fetch(url: str, _headers: object) -> tuple[int, bytes, dict[str, str]]:
        if "/releases/tags/" in url:
            return 200, release, {}
        if url.endswith("/1"):
            return 200, manifest, {}
        if url.endswith("/2"):
            return 200, checksum, {}
        raise AssertionError(url)

    assert verify_github_release_manifest(
        repository=repository,
        tag=tag,
        commit=commit,
        token="token",
        fetcher=fetch,
    )
    assert not verify_github_release_manifest(
        repository=repository,
        tag=tag,
        commit="b" * 40,
        token="token",
        fetcher=fetch,
    )


@pytest.mark.parametrize("manifest_factory", [_manifest, _manifest_v2])
def test_previous_release_accepts_legacy_v1_and_tagless_v2_chain(
    manifest_factory: object,
) -> None:
    repository = "owner/repo"
    tag = "v1.45.210-beta"
    commit = "a" * 40
    assert callable(manifest_factory)
    manifest = manifest_factory(repository, tag, commit)
    checksum = (
        f"{hashlib.sha256(manifest).hexdigest()}  "
        f"omega-release-manifest-{tag}.json\n"
    ).encode()
    release = json.dumps(
        {
            "tag_name": tag,
            "draft": False,
            "immutable": True,
            "assets": [
                {"id": 1, "name": f"omega-release-manifest-{tag}.json"},
                {"id": 2, "name": f"omega-release-manifest-{tag}.json.sha256"},
            ],
        }
    ).encode()

    def fetch(url: str, _headers: object) -> tuple[int, bytes, dict[str, str]]:
        if "/releases/tags/" in url:
            return 200, release, {}
        return (200, manifest, {}) if url.endswith("/1") else (200, checksum, {})

    assert verify_github_release_manifest(
        repository=repository,
        tag=tag,
        commit=commit,
        token="token",
        fetcher=fetch,
    )


def test_previous_release_lookup_treats_only_structural_404_as_untrusted() -> None:
    kwargs = {
        "repository": "owner/repo",
        "tag": "v1.45.210-beta",
        "commit": "a" * 40,
        "token": "token",
    }
    assert not verify_github_release_manifest(
        **kwargs,
        fetcher=lambda _url, _headers: (404, b"not found", {}),
    )
    with pytest.raises(ReleaseTrustError, match="HTTP 403"):
        verify_github_release_manifest(
            **kwargs,
            fetcher=lambda _url, _headers: (403, b"forbidden", {}),
        )


@pytest.mark.parametrize("immutable", [False, None])
def test_previous_release_rejects_mutable_or_unknown_release_authority(
    immutable: bool | None,
) -> None:
    repository = "owner/repo"
    tag = "v1.45.210-beta"
    commit = "a" * 40
    payload: dict[str, object] = {
        "tag_name": tag,
        "draft": False,
        "assets": [],
    }
    if immutable is not None:
        payload["immutable"] = immutable

    assert not verify_github_release_manifest(
        repository=repository,
        tag=tag,
        commit=commit,
        token="token",
        fetcher=lambda _url, _headers: (
            200,
            json.dumps(payload).encode(),
            {},
        ),
    )


def test_release_asset_redirect_never_forwards_bearer_token() -> None:
    repository = "owner/repo"
    tag = "v1.45.210-beta"
    commit = "a" * 40
    manifest = _manifest(repository, tag, commit)
    checksum = (
        f"{hashlib.sha256(manifest).hexdigest()}  "
        f"omega-release-manifest-{tag}.json\n"
    ).encode()
    release = json.dumps(
        {
            "tag_name": tag,
            "draft": False,
            "immutable": True,
            "assets": [
                {"id": 1, "name": f"omega-release-manifest-{tag}.json"},
                {"id": 2, "name": f"omega-release-manifest-{tag}.json.sha256"},
            ],
        }
    ).encode()
    cdn_payloads = {"manifest": manifest, "checksum": checksum}

    def fetch(url: str, headers: object) -> tuple[int, bytes, dict[str, str]]:
        assert isinstance(headers, dict)
        if "/releases/tags/" in url:
            return 200, release, {}
        if "/releases/assets/" in url:
            suffix = "manifest" if url.endswith("/1") else "checksum"
            return (
                302,
                b"",
                {"Location": f"https://release-assets.githubusercontent.com/{suffix}"},
            )
        assert "Authorization" not in headers
        return 200, cdn_payloads[url.rsplit("/", 1)[-1]], {}

    assert verify_github_release_manifest(
        repository=repository,
        tag=tag,
        commit=commit,
        token="secret-token",
        fetcher=fetch,
    )


def test_transition_ledger_is_exact_and_has_one_bridge() -> None:
    assert TRANSITION_RELEASES == {
        "v1.45.210-beta": (
            "v1.45.209-beta",
            "21b6274ec6e416d2d808efe30cda19ce8176611f",
        )
    }
