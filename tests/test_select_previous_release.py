from __future__ import annotations

import hashlib
import json
import subprocess
from pathlib import Path

import pytest

import scripts.select_previous_release as selector
from scripts.select_previous_release import (
    CANONICAL_SERVICES,
    TRANSITION_AUTHORITY_ROOTS,
    TRANSITION_RELEASES,
    ReleaseTrustError,
    SemVer,
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
        "v1.45.218-beta": (
            "v1.45.209-beta",
            "713b2801a43c725eab68a31db858c1b5ec10e5cc",
            "21b6274ec6e416d2d808efe30cda19ce8176611f",
            (
                (
                    "v1.45.210-beta",
                    "6c70e0067eb44dd991d355b3e5cab663300c790b",
                    "2429e9a2bdab13ff00740fe318009fd5b101850d",
                ),
                (
                    "v1.45.211-beta",
                    "cadf0b28b771257bc6cb9129cf8b4cd72ef5adff",
                    "cc0873e4d86bb5bd2f183a003d43ff8a0970df8c",
                ),
                (
                    "v1.45.212-beta",
                    "5e3bbc3d1475486bbc0ddabb57b440210ac0782c",
                    "dd882bc08bb445d1446f9cbbe313448b24827720",
                ),
                (
                    "v1.45.213-beta",
                    "926330d8e1e2067e4e56429fb91e4585ffa4eb43",
                    "4bcfda1811d4cbe0511624e0d5cd9c1f5205926b",
                ),
                (
                    "v1.45.214-beta",
                    "cea2ec51314248daf26710cfe8a94a483d7287eb",
                    "325170ff109e88860df857c8615f05b059698fec",
                ),
                (
                    "v1.45.215-beta",
                    "f40a3ab516689343514411806318cffd4f67c3bd",
                    "82a7e45adff10b4877b1bfb0e6acab4206744c60",
                ),
                (
                    "v1.45.216-beta",
                    "0f47139b7c3e8ba2b907804d0b2a3673da3a000c",
                    "1544f511cb49375120e75d04e6f8b18c7564f3e7",
                ),
                (
                    "v1.45.217-beta",
                    "58e0958e5b1609fa3f3184ae7ccc33994516c7a3",
                    "ea3bf6b13c2af884c621310047be43e4a4132362",
                ),
            ),
        )
    }
    assert TRANSITION_AUTHORITY_ROOTS == {
        "v1.45.218-beta": "refs/omega-release-authority/v1.45.218-beta"
    }


def _transition_history(
    repo: Path,
    monkeypatch: pytest.MonkeyPatch,
    *,
    gap_between_failed_markers: bool = False,
) -> dict[str, str]:
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
    subprocess.run(
        ["git", "config", "user.email", "ci@example.com"], cwd=repo, check=True
    )
    subprocess.run(["git", "config", "user.name", "CI"], cwd=repo, check=True)
    base = _commit(repo, "trusted base")
    subprocess.run(
        ["git", "tag", "-a", "v1.45.209-beta", "-m", "trusted base"],
        cwd=repo,
        check=True,
    )
    base_object = _git(repo, "rev-parse", "refs/tags/v1.45.209-beta")
    _commit(repo, "intervening source")
    failed_0 = _commit(repo, "failed release .210")
    subprocess.run(
        ["git", "tag", "-a", "v1.45.210-beta", "-m", "failed release .210"],
        cwd=repo,
        check=True,
    )
    failed_0_object = _git(repo, "rev-parse", "refs/tags/v1.45.210-beta")
    if gap_between_failed_markers:
        _commit(repo, "unexpected gap between failed releases")
    failed_1 = _commit(repo, "failed release .211")
    subprocess.run(
        ["git", "tag", "-a", "v1.45.211-beta", "-m", "failed release .211"],
        cwd=repo,
        check=True,
    )
    failed_1_object = _git(repo, "rev-parse", "refs/tags/v1.45.211-beta")
    failed_2 = _commit(repo, "failed release .212")
    subprocess.run(
        ["git", "tag", "-a", "v1.45.212-beta", "-m", "failed release .212"],
        cwd=repo,
        check=True,
    )
    failed_2_object = _git(repo, "rev-parse", "refs/tags/v1.45.212-beta")
    failed_3 = _commit(repo, "failed release .213")
    subprocess.run(
        ["git", "tag", "-a", "v1.45.213-beta", "-m", "failed release .213"],
        cwd=repo,
        check=True,
    )
    failed_3_object = _git(repo, "rev-parse", "refs/tags/v1.45.213-beta")
    failed_4 = _commit(repo, "failed release .214")
    subprocess.run(
        ["git", "tag", "-a", "v1.45.214-beta", "-m", "failed release .214"],
        cwd=repo,
        check=True,
    )
    failed_4_object = _git(repo, "rev-parse", "refs/tags/v1.45.214-beta")
    failed_5 = _commit(repo, "failed release .215")
    subprocess.run(
        ["git", "tag", "-a", "v1.45.215-beta", "-m", "failed release .215"],
        cwd=repo,
        check=True,
    )
    failed_5_object = _git(repo, "rev-parse", "refs/tags/v1.45.215-beta")
    failed_6 = _commit(repo, "failed release .216")
    subprocess.run(
        ["git", "tag", "-a", "v1.45.216-beta", "-m", "failed release .216"],
        cwd=repo,
        check=True,
    )
    failed_6_object = _git(repo, "rev-parse", "refs/tags/v1.45.216-beta")
    failed_7 = _commit(repo, "failed release .217")
    subprocess.run(
        ["git", "tag", "-a", "v1.45.217-beta", "-m", "failed release .217"],
        cwd=repo,
        check=True,
    )
    failed_7_object = _git(repo, "rev-parse", "refs/tags/v1.45.217-beta")
    recovery = _commit(repo, "recovery release .218")
    subprocess.run(
        ["git", "tag", "-a", "v1.45.218-beta", "-m", "recovery release .218"],
        cwd=repo,
        check=True,
    )
    authority = "refs/omega-release-authority/v1.45.218-beta"
    for name, tag in (
        ("base", "v1.45.209-beta"),
        ("failed-0", "v1.45.210-beta"),
        ("failed-1", "v1.45.211-beta"),
        ("failed-2", "v1.45.212-beta"),
        ("failed-3", "v1.45.213-beta"),
        ("failed-4", "v1.45.214-beta"),
        ("failed-5", "v1.45.215-beta"),
        ("failed-6", "v1.45.216-beta"),
        ("failed-7", "v1.45.217-beta"),
        ("current", "v1.45.218-beta"),
    ):
        subprocess.run(
            ["git", "update-ref", f"{authority}/{name}", f"refs/tags/{tag}"],
            cwd=repo,
            check=True,
        )
    monkeypatch.setattr(
        selector,
        "TRANSITION_RELEASES",
        {
            "v1.45.218-beta": (
                "v1.45.209-beta",
                base_object,
                base,
                (
                    ("v1.45.210-beta", failed_0_object, failed_0),
                    ("v1.45.211-beta", failed_1_object, failed_1),
                    ("v1.45.212-beta", failed_2_object, failed_2),
                    ("v1.45.213-beta", failed_3_object, failed_3),
                    ("v1.45.214-beta", failed_4_object, failed_4),
                    ("v1.45.215-beta", failed_5_object, failed_5),
                    ("v1.45.216-beta", failed_6_object, failed_6),
                    ("v1.45.217-beta", failed_7_object, failed_7),
                ),
            )
        },
    )
    monkeypatch.setattr(
        selector,
        "TRANSITION_AUTHORITY_ROOTS",
        {"v1.45.218-beta": authority},
    )
    return {
        "authority": authority,
        "base": base,
        "base_object": base_object,
        "failed_0": failed_0,
        "failed_0_object": failed_0_object,
        "failed_1": failed_1,
        "failed_1_object": failed_1_object,
        "failed_2": failed_2,
        "failed_2_object": failed_2_object,
        "failed_3": failed_3,
        "failed_3_object": failed_3_object,
        "failed_4": failed_4,
        "failed_4_object": failed_4_object,
        "failed_5": failed_5,
        "failed_5_object": failed_5_object,
        "failed_6": failed_6,
        "failed_6_object": failed_6_object,
        "failed_7": failed_7,
        "failed_7_object": failed_7_object,
        "recovery": recovery,
    }


def test_transition_bridge_requires_all_eight_exact_failed_markers_without_evidence(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    history = _transition_history(tmp_path, monkeypatch)
    checked: list[tuple[str, str]] = []

    def trust(tag: str, commit: str) -> bool:
        checked.append((tag, commit))
        return False

    assert select_previous_release(
        history["recovery"],
        "v1.45.218-beta",
        repo=tmp_path,
        trust_verifier=trust,
    ) == (history["base"], "v1.45.209-beta")
    assert checked == [
        ("v1.45.210-beta", history["failed_0"]),
        ("v1.45.211-beta", history["failed_1"]),
        ("v1.45.212-beta", history["failed_2"]),
        ("v1.45.213-beta", history["failed_3"]),
        ("v1.45.214-beta", history["failed_4"]),
        ("v1.45.215-beta", history["failed_5"]),
        ("v1.45.216-beta", history["failed_6"]),
        ("v1.45.217-beta", history["failed_7"]),
    ]


@pytest.mark.parametrize("marker_index", [0, 1, 2, 3, 4, 5, 6, 7])
def test_transition_bridge_blocks_a_missing_failed_marker(
    marker_index: int, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    history = _transition_history(tmp_path, monkeypatch)
    subprocess.run(
        [
            "git",
            "update-ref",
            "-d",
            f"{history['authority']}/failed-{marker_index}",
        ],
        cwd=tmp_path,
        check=True,
    )

    with pytest.raises(ReleaseTrustError, match="failed release marker is missing"):
        select_previous_release(
            history["recovery"],
            "v1.45.218-beta",
            repo=tmp_path,
            trust_verifier=lambda _tag, _commit: False,
        )


@pytest.mark.parametrize("marker_index", [0, 1, 2, 3, 4, 5, 6, 7])
def test_transition_bridge_blocks_a_recreated_annotated_marker(
    marker_index: int, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    history = _transition_history(tmp_path, monkeypatch)
    tag = f"v1.45.21{marker_index}-beta"
    failed = history[f"failed_{marker_index}"]
    failed_object = history[f"failed_{marker_index}_object"]
    subprocess.run(["git", "tag", "-d", tag], cwd=tmp_path, check=True)
    subprocess.run(
        ["git", "tag", "-a", tag, "-m", "recreated", failed],
        cwd=tmp_path,
        check=True,
    )
    assert _git(tmp_path, "rev-parse", f"refs/tags/{tag}") != failed_object
    subprocess.run(
        [
            "git",
            "update-ref",
            f"{history['authority']}/failed-{marker_index}",
            f"refs/tags/{tag}",
        ],
        cwd=tmp_path,
        check=True,
    )

    with pytest.raises(ReleaseTrustError, match="tag object differs"):
        select_previous_release(
            history["recovery"],
            "v1.45.218-beta",
            repo=tmp_path,
            trust_verifier=lambda _tag, _commit: False,
        )


@pytest.mark.parametrize("marker_index", [0, 1, 2, 3, 4, 5, 6, 7])
def test_transition_bridge_blocks_a_lightweight_failed_marker(
    marker_index: int, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    history = _transition_history(tmp_path, monkeypatch)
    subprocess.run(
        [
            "git",
            "update-ref",
            f"{history['authority']}/failed-{marker_index}",
            history[f"failed_{marker_index}"],
        ],
        cwd=tmp_path,
        check=True,
    )

    with pytest.raises(ReleaseTrustError, match="tag object differs"):
        select_previous_release(
            history["recovery"],
            "v1.45.218-beta",
            repo=tmp_path,
            trust_verifier=lambda _tag, _commit: False,
        )


def test_transition_bridge_ignores_stale_checkout_tags_after_remote_binding(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    history = _transition_history(tmp_path, monkeypatch)
    subprocess.run(
        [
            "git",
            "tag",
            "-d",
            "v1.45.209-beta",
            "v1.45.210-beta",
            "v1.45.211-beta",
            "v1.45.212-beta",
            "v1.45.213-beta",
            "v1.45.214-beta",
            "v1.45.215-beta",
            "v1.45.216-beta",
            "v1.45.218-beta",
        ],
        cwd=tmp_path,
        check=True,
    )

    assert select_previous_release(
        history["recovery"],
        "v1.45.218-beta",
        repo=tmp_path,
        trust_verifier=lambda _tag, _commit: False,
    ) == (history["base"], "v1.45.209-beta")


@pytest.mark.parametrize("marker_index", [0, 1, 2, 3, 4, 5, 6, 7])
def test_transition_bridge_blocks_trusted_evidence_for_each_failed_marker(
    marker_index: int, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    history = _transition_history(tmp_path, monkeypatch)
    failed_tag = f"v1.45.21{marker_index}-beta"
    failed_commit = history[f"failed_{marker_index}"]

    with pytest.raises(ReleaseTrustError, match="contradictory canonical evidence"):
        select_previous_release(
            history["recovery"],
            "v1.45.218-beta",
            repo=tmp_path,
            trust_verifier=lambda tag, commit: (
                tag == failed_tag and commit == failed_commit
            ),
        )


@pytest.mark.parametrize("marker_index", [0, 1, 2, 3, 4, 5, 6, 7])
def test_transition_bridge_blocks_ambiguous_failed_marker_evidence(
    marker_index: int, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    history = _transition_history(tmp_path, monkeypatch)
    ambiguous_tag = f"v1.45.21{marker_index}-beta"

    def ambiguous(tag: str, _commit: str) -> bool:
        if tag == ambiguous_tag:
            raise ReleaseTrustError("failed marker evidence is ambiguous")
        return False

    with pytest.raises(ReleaseTrustError, match="ambiguous"):
        select_previous_release(
            history["recovery"],
            "v1.45.218-beta",
            repo=tmp_path,
            trust_verifier=ambiguous,
        )


def test_transition_bridge_requires_current_tag_to_resolve_to_head(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    history = _transition_history(tmp_path, monkeypatch)
    subprocess.run(
        [
            "git",
            "update-ref",
            f"{history['authority']}/current",
            "refs/tags/v1.45.209-beta",
        ],
        cwd=tmp_path,
        check=True,
    )

    with pytest.raises(ReleaseTrustError, match="annotated tag identity is invalid"):
        select_previous_release(
            history["recovery"],
            "v1.45.218-beta",
            repo=tmp_path,
            trust_verifier=lambda _tag, _commit: False,
        )


def test_transition_bridge_rejects_lightweight_current_marker(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    history = _transition_history(tmp_path, monkeypatch)
    subprocess.run(
        [
            "git",
            "update-ref",
            f"{history['authority']}/current",
            history["recovery"],
        ],
        cwd=tmp_path,
        check=True,
    )

    with pytest.raises(ReleaseTrustError, match="not an annotated tag"):
        select_previous_release(
            history["recovery"],
            "v1.45.218-beta",
            repo=tmp_path,
            trust_verifier=lambda _tag, _commit: False,
        )


def test_transition_bridge_rejects_wrong_embedded_current_tag_name(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    history = _transition_history(tmp_path, monkeypatch)
    subprocess.run(
        [
            "git",
            "tag",
            "-a",
            "v9.9.9-beta",
            "-m",
            "wrong embedded identity",
            history["recovery"],
        ],
        cwd=tmp_path,
        check=True,
    )
    subprocess.run(
        [
            "git",
            "update-ref",
            f"{history['authority']}/current",
            "refs/tags/v9.9.9-beta",
        ],
        cwd=tmp_path,
        check=True,
    )

    with pytest.raises(ReleaseTrustError, match="annotated tag identity is invalid"):
        select_previous_release(
            history["recovery"],
            "v1.45.218-beta",
            repo=tmp_path,
            trust_verifier=lambda _tag, _commit: False,
        )


def test_transition_bridge_rejects_current_tag_targeting_another_tag(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    history = _transition_history(tmp_path, monkeypatch)
    subprocess.run(
        [
            "git",
            "tag",
            "-a",
            "v1.45.216-nested-source",
            "-m",
            "nested target",
            history["recovery"],
        ],
        cwd=tmp_path,
        check=True,
    )
    nested_object = subprocess.run(
        [
            "git",
            "mktag",
        ],
        cwd=tmp_path,
        input=(
            "object "
            + _git(tmp_path, "rev-parse", "refs/tags/v1.45.216-nested-source")
            + "\n"
            "type tag\n"
            "tag v1.45.218-beta\n"
            "tagger CI <ci@example.com> 1 +0000\n\n"
            "nested current marker\n"
        ),
        text=True,
        capture_output=True,
        check=True,
    ).stdout.strip()
    subprocess.run(
        [
            "git",
            "update-ref",
            f"{history['authority']}/current",
            nested_object,
        ],
        cwd=tmp_path,
        check=True,
    )

    with pytest.raises(ReleaseTrustError, match="annotated tag identity is invalid"):
        select_previous_release(
            history["recovery"],
            "v1.45.218-beta",
            repo=tmp_path,
            trust_verifier=lambda _tag, _commit: False,
        )


def test_transition_bridge_rejects_shallow_repository(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    history = _transition_history(tmp_path, monkeypatch)
    shallow_path = Path(_git(tmp_path, "rev-parse", "--git-path", "shallow"))
    if not shallow_path.is_absolute():
        shallow_path = tmp_path / shallow_path
    shallow_path.write_text(f"{history['base']}\n", encoding="utf-8")

    with pytest.raises(ReleaseTrustError, match="complete non-shallow history"):
        select_previous_release(
            history["recovery"],
            "v1.45.218-beta",
            repo=tmp_path,
            trust_verifier=lambda _tag, _commit: False,
        )


def test_transition_bridge_requires_latest_failed_marker_as_the_direct_parent(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    history = _transition_history(tmp_path, monkeypatch)
    non_direct = _commit(tmp_path, "non-direct recovery")
    subprocess.run(
        [
            "git",
            "tag",
            "-f",
            "-a",
            "v1.45.218-beta",
            "-m",
            "non-direct recovery",
            non_direct,
        ],
        cwd=tmp_path,
        check=True,
    )
    subprocess.run(
        [
            "git",
            "update-ref",
            f"{history['authority']}/current",
            "refs/tags/v1.45.218-beta",
        ],
        cwd=tmp_path,
        check=True,
    )

    with pytest.raises(ReleaseTrustError, match="not directly atop"):
        select_previous_release(
            non_direct,
            "v1.45.218-beta",
            repo=tmp_path,
            trust_verifier=lambda _tag, _commit: False,
        )


def test_transition_bridge_requires_direct_ordered_failed_chain(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    history = _transition_history(
        tmp_path, monkeypatch, gap_between_failed_markers=True
    )

    with pytest.raises(ReleaseTrustError, match="failed release chain is not direct"):
        select_previous_release(
            history["recovery"],
            "v1.45.218-beta",
            repo=tmp_path,
            trust_verifier=lambda _tag, _commit: False,
        )


def test_transition_bridge_blocks_swapped_failed_authorities(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    history = _transition_history(tmp_path, monkeypatch)
    subprocess.run(
        [
            "git",
            "update-ref",
            f"{history['authority']}/failed-0",
            "refs/tags/v1.45.211-beta",
        ],
        cwd=tmp_path,
        check=True,
    )
    subprocess.run(
        [
            "git",
            "update-ref",
            f"{history['authority']}/failed-1",
            "refs/tags/v1.45.210-beta",
        ],
        cwd=tmp_path,
        check=True,
    )

    with pytest.raises(ReleaseTrustError, match="tag object differs"):
        select_previous_release(
            history["recovery"],
            "v1.45.218-beta",
            repo=tmp_path,
            trust_verifier=lambda _tag, _commit: False,
        )


def test_transition_bridge_requires_exact_annotated_base_object(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    history = _transition_history(tmp_path, monkeypatch)
    subprocess.run(["git", "tag", "-d", "v1.45.209-beta"], cwd=tmp_path, check=True)
    subprocess.run(
        [
            "git",
            "tag",
            "-a",
            "v1.45.209-beta",
            "-m",
            "recreated base",
            history["base"],
        ],
        cwd=tmp_path,
        check=True,
    )
    subprocess.run(
        [
            "git",
            "update-ref",
            f"{history['authority']}/base",
            "refs/tags/v1.45.209-beta",
        ],
        cwd=tmp_path,
        check=True,
    )

    with pytest.raises(ReleaseTrustError, match="base tag object differs"):
        select_previous_release(
            history["recovery"],
            "v1.45.218-beta",
            repo=tmp_path,
            trust_verifier=lambda _tag, _commit: False,
        )


@pytest.mark.parametrize("marker_index", [0, 1, 2, 3, 4, 5, 6, 7])
def test_transition_bridge_requires_exact_failed_peeled_commits(
    marker_index: int,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    history = _transition_history(tmp_path, monkeypatch)
    base_tag, base_object, base_commit, markers = selector.TRANSITION_RELEASES[
        "v1.45.218-beta"
    ]
    altered = list(markers)
    tag, tag_object, _commit_hash = altered[marker_index]
    altered[marker_index] = (tag, tag_object, base_commit)
    monkeypatch.setattr(
        selector,
        "TRANSITION_RELEASES",
        {
            "v1.45.218-beta": (
                base_tag,
                base_object,
                base_commit,
                tuple(altered),
            )
        },
    )

    with pytest.raises(ReleaseTrustError, match="peeled commit differs"):
        select_previous_release(
            history["recovery"],
            "v1.45.218-beta",
            repo=tmp_path,
            trust_verifier=lambda _tag, _commit: False,
        )


def test_transition_bridge_requires_exact_base_peeled_commit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    history = _transition_history(tmp_path, monkeypatch)
    base_tag, base_object, _base_commit, markers = selector.TRANSITION_RELEASES[
        "v1.45.218-beta"
    ]
    monkeypatch.setattr(
        selector,
        "TRANSITION_RELEASES",
        {
            "v1.45.218-beta": (
                base_tag,
                base_object,
                history["failed_0"],
                markers,
            )
        },
    )

    with pytest.raises(ReleaseTrustError, match="base tag peeled commit differs"):
        select_previous_release(
            history["recovery"],
            "v1.45.218-beta",
            repo=tmp_path,
            trust_verifier=lambda _tag, _commit: False,
        )


@pytest.mark.parametrize("marker_count", [7, 9])
def test_transition_bridge_rejects_any_non_seven_marker_cardinality(
    marker_count: int,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    history = _transition_history(tmp_path, monkeypatch)
    base_tag, base_object, base_commit, markers = selector.TRANSITION_RELEASES[
        "v1.45.218-beta"
    ]
    altered = markers[:-1] if marker_count == 7 else (*markers, markers[-1])
    monkeypatch.setattr(
        selector,
        "TRANSITION_RELEASES",
        {
            "v1.45.218-beta": (
                base_tag,
                base_object,
                base_commit,
                altered,
            )
        },
    )

    with pytest.raises(ReleaseTrustError, match="exactly eight failed markers"):
        select_previous_release(
            history["recovery"],
            "v1.45.218-beta",
            repo=tmp_path,
            trust_verifier=lambda _tag, _commit: False,
        )


def test_transition_bridge_requires_base_to_ancestor_first_failed_marker(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    history = _transition_history(tmp_path, monkeypatch)
    tree = _git(tmp_path, "rev-parse", f"{history['base']}^{{tree}}")
    unrelated_base = subprocess.check_output(
        ["git", "commit-tree", tree, "-m", "unrelated base"],
        cwd=tmp_path,
        text=True,
    ).strip()
    subprocess.run(["git", "tag", "-d", "v1.45.209-beta"], cwd=tmp_path, check=True)
    subprocess.run(
        [
            "git",
            "tag",
            "-a",
            "v1.45.209-beta",
            "-m",
            "unrelated base",
            unrelated_base,
        ],
        cwd=tmp_path,
        check=True,
    )
    unrelated_base_object = _git(tmp_path, "rev-parse", "refs/tags/v1.45.209-beta")
    subprocess.run(
        [
            "git",
            "update-ref",
            f"{history['authority']}/base",
            "refs/tags/v1.45.209-beta",
        ],
        cwd=tmp_path,
        check=True,
    )
    _base_tag, _base_object, _base_commit, markers = selector.TRANSITION_RELEASES[
        "v1.45.218-beta"
    ]
    monkeypatch.setattr(
        selector,
        "TRANSITION_RELEASES",
        {
            "v1.45.218-beta": (
                "v1.45.209-beta",
                unrelated_base_object,
                unrelated_base,
                markers,
            )
        },
    )

    with pytest.raises(ReleaseTrustError, match="base is not an ancestor"):
        select_previous_release(
            history["recovery"],
            "v1.45.218-beta",
            repo=tmp_path,
            trust_verifier=lambda _tag, _commit: False,
        )


def test_transition_bridge_requires_a_single_parent_head(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    history = _transition_history(tmp_path, monkeypatch)
    tree = _git(tmp_path, "rev-parse", f"{history['recovery']}^{{tree}}")
    merge_head = subprocess.check_output(
        [
            "git",
            "commit-tree",
            tree,
            "-p",
            history["recovery"],
            "-p",
            history["failed_0"],
            "-m",
            "merge-shaped recovery",
        ],
        cwd=tmp_path,
        text=True,
    ).strip()
    subprocess.run(
        ["git", "update-ref", f"{history['authority']}/current", merge_head],
        cwd=tmp_path,
        check=True,
    )

    with pytest.raises(ReleaseTrustError, match="must have exactly one parent"):
        select_previous_release(
            merge_head,
            "v1.45.218-beta",
            repo=tmp_path,
            trust_verifier=lambda _tag, _commit: False,
        )
