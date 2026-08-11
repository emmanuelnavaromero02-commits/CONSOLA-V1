from __future__ import annotations

import ast
import hashlib
import importlib.util
import json
import re
import subprocess
import sys
import urllib.request
from collections import Counter
from pathlib import Path

import pytest
import yaml

REPO = Path(__file__).resolve().parents[1]
MODULE_PATH = REPO / "scripts/release_images.py"
SPEC = importlib.util.spec_from_file_location("release_images", MODULE_PATH)
assert SPEC and SPEC.loader
release_images = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = release_images
SPEC.loader.exec_module(release_images)


def _canonical(value: object) -> bytes:
    return (json.dumps(value, sort_keys=True, separators=(",", ":")) + "\n").encode()


def _digest(value: bytes) -> str:
    return f"sha256:{hashlib.sha256(value).hexdigest()}"


class FakeRegistry:
    def __init__(self) -> None:
        self.refs: dict[tuple[str, str], release_images.RegistryResponse] = {}
        self.blobs: dict[tuple[str, str], bytes] = {}
        self.visibility: dict[str, str] = {}
        self.puts: list[tuple[str, str, str]] = []

    def get_manifest(self, repository: str, reference: str):
        return self.refs.get((repository, reference))

    def get_blob(self, repository: str, digest: str) -> bytes:
        payload = self.blobs[(repository, digest)]
        assert _digest(payload) == digest
        return payload

    def put_blob(self, repository: str, payload: bytes) -> str:
        digest = _digest(payload)
        self.blobs[(repository, digest)] = payload
        return digest

    def put_manifest(
        self, repository: str, reference: str, payload: bytes, media_type: str
    ) -> str:
        digest = _digest(payload)
        response = release_images.RegistryResponse(
            201,
            {"content-type": media_type, "docker-content-digest": digest},
            payload,
        )
        self.refs[(repository, reference)] = response
        self.refs[(repository, digest)] = response
        self.puts.append((repository, reference, digest))
        if (
            repository
            == f"{release_images.CANONICAL_OWNER}/{release_images.MANIFEST_PACKAGE}"
        ):
            self.visibility.setdefault(release_images.MANIFEST_PACKAGE, "private")
        return digest

    def package_visibility(self, package: str, *, allow_missing: bool = False):
        if package in self.visibility:
            return self.visibility[package]
        if allow_missing:
            return None
        raise release_images.ReleaseImageError("package visibility is missing")


SHA = "3" * 40
TAG = "v1.45.207-beta"
VERSION = "1.45.207-beta"


def _add_digest_image(
    registry: FakeRegistry,
    service: str,
    *,
    revision: str = SHA,
    version: str = VERSION,
    source: str = release_images.CANONICAL_SOURCE,
    salt: str = "",
    visibility: str | None = None,
) -> release_images.ImageRecord:
    repository = release_images.image_repository(service)
    config = _canonical(
        {
            "config": {
                "Labels": {
                    "org.opencontainers.image.revision": revision,
                    "org.opencontainers.image.source": source,
                    "org.opencontainers.image.version": version,
                }
            },
            "salt": salt,
        }
    )
    config_digest = _digest(config)
    manifest = _canonical(
        {
            "config": {
                "digest": config_digest,
                "mediaType": "application/vnd.oci.image.config.v1+json",
                "size": len(config),
            },
            "layers": [],
            "mediaType": release_images.OCI_MANIFEST_MEDIA_TYPE,
            "schemaVersion": 2,
        }
    )
    digest = _digest(manifest)
    response = release_images.RegistryResponse(
        200,
        {
            "content-type": release_images.OCI_MANIFEST_MEDIA_TYPE,
            "docker-content-digest": digest,
        },
        manifest,
    )
    registry.blobs[(repository, config_digest)] = config
    registry.refs[(repository, digest)] = response
    if visibility is None:
        visibility = (
            "private"
            if service in release_images.PROTECTED_PRIVATE_PACKAGES
            else "public"
        )
    registry.visibility[service] = visibility
    return release_images.ImageRecord(
        service=service,
        digest=digest,
        reference=f"ghcr.io/{repository}@{digest}",
        visibility=visibility,
    )


def _digest_only_registry(tmp_path: Path) -> tuple[FakeRegistry, Path]:
    registry = FakeRegistry()
    receipts = tmp_path / "receipts"
    for image in release_images.IMAGES:
        record = _add_digest_image(registry, image.service, salt=image.service)
        release_images.write_digest_receipt(receipts / f"{image.service}.json", record)
    registry.visibility[release_images.MANIFEST_PACKAGE] = "private"
    return registry, receipts


def _assemble(tmp_path: Path) -> tuple[FakeRegistry, Path, str]:
    registry, receipts = _digest_only_registry(tmp_path)
    output = tmp_path / "release-candidate.json"
    digest = release_images.assemble_candidate(
        registry,
        source_sha=SHA,
        release_tag=TAG,
        receipts_dir=receipts,
        output=output,
    )
    return registry, receipts, digest


def test_inventory_is_exactly_the_canonical_15_images() -> None:
    assert [item.service for item in release_images.IMAGES] == [
        "console",
        "workspace",
        "refinement",
        "vault",
        "mcp-infra",
        "airflow",
        "replicon",
        "hubspot",
        "banxico",
        "inegi",
        "sec_edgar",
        "sap_hcm",
        "sap_s4hana",
        "sap_successfactors",
        "salesforce",
    ]
    assert len({item.service for item in release_images.IMAGES}) == 15
    assert release_images.PROTECTED_PRIVATE_PACKAGES == {
        "banxico",
        "inegi",
        "sec_edgar",
    }


def test_candidate_aggregation_tags_only_after_all_digest_receipts_validate(
    tmp_path: Path,
) -> None:
    registry, receipts = _digest_only_registry(tmp_path)
    (receipts / "salesforce.json").unlink()

    with pytest.raises(release_images.ReleaseImageError, match="exact 15-image"):
        release_images.assemble_candidate(
            registry,
            source_sha=SHA,
            release_tag=TAG,
            receipts_dir=receipts,
            output=tmp_path / "manifest.json",
        )

    assert registry.puts == []
    assert not any(
        reference == release_images.candidate_tag(SHA) for _, reference in registry.refs
    )


def test_candidate_aggregation_never_overwrites_a_different_existing_tag(
    tmp_path: Path,
) -> None:
    registry, receipts = _digest_only_registry(tmp_path)
    conflicting = _add_digest_image(registry, "console", salt="different-content")
    registry.refs[
        (release_images.image_repository("console"), release_images.candidate_tag(SHA))
    ] = registry.refs[(release_images.image_repository("console"), conflicting.digest)]

    with pytest.raises(
        release_images.ReleaseImageError, match="existing candidate tag differs"
    ):
        release_images.assemble_candidate(
            registry,
            source_sha=SHA,
            release_tag=TAG,
            receipts_dir=receipts,
            output=tmp_path / "manifest.json",
        )

    assert registry.puts == []


def test_candidate_seal_is_deterministic_and_idempotent(tmp_path: Path) -> None:
    registry, receipts, first_digest = _assemble(tmp_path)
    first = (tmp_path / "release-candidate.json").read_bytes()
    service_tag_puts = [
        item for item in registry.puts if item[1] == release_images.candidate_tag(SHA)
    ]
    assert len(service_tag_puts) == 16  # 15 images plus one sealed-manifest package.

    registry.puts.clear()
    second_output = tmp_path / "release-candidate-second.json"
    second_digest = release_images.assemble_candidate(
        registry,
        source_sha=SHA,
        release_tag=TAG,
        receipts_dir=receipts,
        output=second_output,
    )

    assert second_digest == first_digest == _digest(first)
    assert second_output.read_bytes() == first
    assert registry.puts == []
    document = json.loads(first)
    assert document["source_sha"] == SHA
    assert document["release_tag"] == TAG
    assert len(document["images"]) == 15
    assert sum(row["visibility"] == "private" for row in document["images"]) == 3
    assert sum(row["visibility"] == "public" for row in document["images"]) == 12


def test_new_manifest_package_is_verified_private_before_candidate_is_usable(
    tmp_path: Path,
) -> None:
    registry, receipts = _digest_only_registry(tmp_path)
    registry.visibility.pop(release_images.MANIFEST_PACKAGE)

    digest = release_images.assemble_candidate(
        registry,
        source_sha=SHA,
        release_tag=TAG,
        receipts_dir=receipts,
        output=tmp_path / "new-package.json",
    )

    assert digest.startswith("sha256:")
    assert registry.visibility[release_images.MANIFEST_PACKAGE] == "private"


def test_sealed_candidate_rejects_any_later_tag_movement(tmp_path: Path) -> None:
    registry, _, _ = _assemble(tmp_path)
    moved = _add_digest_image(registry, "console", salt="moved-after-seal")
    repository = release_images.image_repository("console")
    registry.refs[(repository, release_images.candidate_tag(SHA))] = registry.refs[
        (repository, moved.digest)
    ]

    with pytest.raises(release_images.ReleaseImageError, match="changed after sealing"):
        release_images.candidate_state(
            registry,
            service="console",
            source_sha=SHA,
            release_tag=TAG,
        )


def test_protected_private_visibility_and_exact_oci_labels_are_hard_gates(
    tmp_path: Path,
) -> None:
    registry, receipts = _digest_only_registry(tmp_path)
    registry.visibility["banxico"] = "public"
    with pytest.raises(
        release_images.ReleaseImageError, match="must remain private: banxico"
    ):
        release_images.assemble_candidate(
            registry,
            source_sha=SHA,
            release_tag=TAG,
            receipts_dir=receipts,
            output=tmp_path / "manifest.json",
        )
    assert registry.puts == []

    # Non-protected packages may retain their existing public or private state;
    # the workflow records it and never changes visibility.
    registry, receipts = _digest_only_registry(tmp_path / "non-protected-private")
    private_console = _add_digest_image(
        registry, "console", salt="private-console", visibility="private"
    )
    release_images.write_digest_receipt(receipts / "console.json", private_console)
    release_images.assemble_candidate(
        registry,
        source_sha=SHA,
        release_tag=TAG,
        receipts_dir=receipts,
        output=tmp_path / "non-protected-private.json",
    )
    assert registry.visibility["console"] == "private"

    registry, receipts = _digest_only_registry(tmp_path / "bad-label")
    bad = _add_digest_image(registry, "inegi", revision="4" * 40, salt="wrong-label")
    release_images.write_digest_receipt(receipts / "inegi.json", bad)
    with pytest.raises(
        release_images.ReleaseImageError, match="incorrect OCI provenance"
    ):
        release_images.assemble_candidate(
            registry,
            source_sha=SHA,
            release_tag=TAG,
            receipts_dir=receipts,
            output=tmp_path / "bad-label.json",
        )
    assert registry.puts == []


def test_tag_binding_recomputed_then_exact_digests_promoted_without_rebuild(
    tmp_path: Path,
) -> None:
    registry, _, manifest_digest = _assemble(tmp_path)
    registry.puts.clear()
    preflight = tmp_path / "preflight.json"
    assert (
        release_images.promotion_preflight(
            registry,
            source_sha=SHA,
            release_tag=TAG,
            version=VERSION,
            bound_manifest_digest=manifest_digest,
            output=preflight,
        )
        == manifest_digest
    )

    for image in release_images.IMAGES:
        release_images.promote_one(
            registry,
            service=image.service,
            source_sha=SHA,
            release_tag=TAG,
            version=VERSION,
            bound_manifest_digest=manifest_digest,
        )

    output = tmp_path / "released.json"
    release_images.verify_release_images(
        registry,
        source_sha=SHA,
        release_tag=TAG,
        version=VERSION,
        bound_manifest_digest=manifest_digest,
        output=output,
    )
    release_puts = [item for item in registry.puts if item[1] == TAG]
    assert len(release_puts) == 15
    assert all(
        registry.refs[(repository, TAG)].body
        == registry.refs[(repository, release_images.candidate_tag(SHA))].body
        for repository, _, _ in release_puts
    )
    assert json.loads(output.read_bytes())["promotion"] == "exact-candidate-digests"


def test_wrong_tag_binding_and_existing_release_digest_fail_before_promotion(
    tmp_path: Path,
) -> None:
    registry, _, manifest_digest = _assemble(tmp_path)
    registry.puts.clear()
    with pytest.raises(
        release_images.ReleaseImageError, match="annotated tag binding differs"
    ):
        release_images.promotion_preflight(
            registry,
            source_sha=SHA,
            release_tag=TAG,
            version=VERSION,
            bound_manifest_digest="sha256:" + "0" * 64,
            output=tmp_path / "wrong-binding.json",
        )
    assert registry.puts == []

    conflicting = _add_digest_image(registry, "sec_edgar", salt="old-release")
    registry.refs[(release_images.image_repository("sec_edgar"), TAG)] = registry.refs[
        (release_images.image_repository("sec_edgar"), conflicting.digest)
    ]
    with pytest.raises(
        release_images.ReleaseImageError, match="existing release tag differs"
    ):
        release_images.promotion_preflight(
            registry,
            source_sha=SHA,
            release_tag=TAG,
            version=VERSION,
            bound_manifest_digest=manifest_digest,
            output=tmp_path / "conflict.json",
        )
    assert registry.puts == []


def _git(repo: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", *args],
        cwd=repo,
        check=True,
        text=True,
        capture_output=True,
    )
    return result.stdout.strip()


def _release_git_repo(
    tmp_path: Path,
    binding: str,
    *,
    annotated: bool = True,
    create_tag: bool = True,
) -> tuple[Path, str]:
    remote = tmp_path / "remote.git"
    work = tmp_path / "work"
    subprocess.run(
        ["git", "init", "--bare", str(remote)], check=True, capture_output=True
    )
    subprocess.run(
        ["git", "init", "-b", "main", str(work)], check=True, capture_output=True
    )
    _git(work, "config", "user.name", "Release Test")
    _git(work, "config", "user.email", "release@example.invalid")
    (work / "VERSION").write_text(f"{VERSION}\n", encoding="utf-8")
    _git(work, "add", "VERSION")
    _git(work, "commit", "-m", "release source")
    sha = _git(work, "rev-parse", "HEAD")
    _git(work, "remote", "add", "origin", str(remote))
    _git(work, "push", "-u", "origin", "main")
    if annotated and create_tag:
        _git(
            work,
            "tag",
            "-a",
            TAG,
            "-m",
            f"OMEGA release\n\n{release_images.TAG_BINDING_PREFIX}{binding}",
        )
    elif create_tag:
        _git(work, "tag", TAG)
    if create_tag:
        _git(work, "push", "origin", f"refs/tags/{TAG}")
    _git(work, "fetch", "origin", "+refs/heads/main:refs/remotes/origin/main")
    return work, sha


def test_release_tag_must_be_annotated_and_bind_exact_manifest(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    binding = "sha256:" + "a" * 64
    work, sha = _release_git_repo(tmp_path, binding)
    monkeypatch.setattr(
        release_images, "CANONICAL_ORIGIN_URLS", {str(tmp_path / "remote.git")}
    )

    version, parsed_binding, tag_object_sha = release_images.verify_release_tag(
        repo_root=work,
        source_sha=sha,
        release_tag=TAG,
        repository=release_images.CANONICAL_REPOSITORY,
    )
    assert version == VERSION
    assert parsed_binding == binding
    assert tag_object_sha == _git(work, "rev-parse", f"refs/tags/{TAG}")

    with pytest.raises(
        release_images.ReleaseImageError, match="changed after preflight"
    ):
        release_images.verify_release_tag(
            repo_root=work,
            source_sha=sha,
            release_tag=TAG,
            repository=release_images.CANONICAL_REPOSITORY,
            expected_tag_object_sha="f" * 40,
        )

    light_root = tmp_path / "light"
    light_root.mkdir()
    light, light_sha = _release_git_repo(light_root, binding, annotated=False)
    monkeypatch.setattr(
        release_images, "CANONICAL_ORIGIN_URLS", {str(light_root / "remote.git")}
    )
    with pytest.raises(release_images.ReleaseImageError, match="must be annotated"):
        release_images.verify_release_tag(
            repo_root=light,
            source_sha=light_sha,
            release_tag=TAG,
            repository=release_images.CANONICAL_REPOSITORY,
        )


def test_candidate_request_requires_current_main_version_and_absent_tag(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    binding = "sha256:" + "b" * 64
    work, sha = _release_git_repo(tmp_path, binding, create_tag=False)
    monkeypatch.setattr(
        release_images, "CANONICAL_ORIGIN_URLS", {str(tmp_path / "remote.git")}
    )

    assert (
        release_images.verify_candidate_request(
            repo_root=work,
            expected_sha=sha,
            release_tag=TAG,
            repository=release_images.CANONICAL_REPOSITORY,
        )
        == VERSION
    )
    (work / "VERSION").write_text("1.45.208-beta\n", encoding="utf-8")
    with pytest.raises(release_images.ReleaseImageError, match="VERSION"):
        release_images.verify_candidate_request(
            repo_root=work,
            expected_sha=sha,
            release_tag=TAG,
            repository=release_images.CANONICAL_REPOSITORY,
        )


def test_cross_host_redirect_drops_authorization_header() -> None:
    handler = release_images._SafeRedirectHandler()
    request = urllib.request.Request(
        "https://ghcr.io/v2/owner/image/blobs/sha256:abc",
        headers={"Authorization": "Bearer must-not-leak"},
    )
    redirected = handler.redirect_request(
        request,
        None,
        307,
        "Temporary Redirect",
        {},
        "https://objects.example.invalid/signed-blob",
    )
    assert redirected is not None
    assert redirected.get_header("Authorization") is None


@pytest.mark.parametrize(
    "realm",
    [
        "http://ghcr.io/token",
        "https://evil.example/token",
        "https://ghcr.io.evil.example/token",
        "https://attacker@ghcr.io/token",
        "https://ghcr.io:443/token",
        "https://ghcr.io:notaport/token",
        "https://ghcr.io/not-token",
        "https://ghcr.io/token?next=evil",
    ],
)
def test_bearer_challenge_rejects_every_noncanonical_realm(realm: str) -> None:
    with pytest.raises(
        release_images.ReleaseImageError, match="exact trusted endpoint"
    ):
        release_images._parse_bearer_challenge(
            f'Bearer realm="{realm}",service="ghcr.io",scope="repository:o/i:pull"'
        )


def test_bearer_challenge_accepts_only_exact_ghcr_token_endpoint() -> None:
    realm, params = release_images._parse_bearer_challenge(
        'Bearer realm="https://ghcr.io/token",service="ghcr.io",scope="repository:o/i:pull"'
    )
    assert realm == "https://ghcr.io/token"
    assert params == {"service": "ghcr.io", "scope": "repository:o/i:pull"}


def test_cross_host_redirect_refuses_github_token_credentials() -> None:
    handler = release_images._SafeRedirectHandler()
    basic = urllib.request.Request(
        "https://ghcr.io/token",
        headers={"Authorization": "Basic github-token-material"},
    )
    with pytest.raises(release_images.ReleaseImageError, match="GitHub credential"):
        handler.redirect_request(
            basic,
            None,
            307,
            "Temporary Redirect",
            {},
            "https://evil.example/token",
        )

    api = urllib.request.Request(
        "https://api.github.com/users/o/packages/container/i",
        headers={"Authorization": "Bearer github-token-material"},
    )
    with pytest.raises(release_images.ReleaseImageError, match="GitHub credential"):
        handler.redirect_request(
            api,
            None,
            307,
            "Temporary Redirect",
            {},
            "https://evil.example/package",
        )

    with pytest.raises(release_images.ReleaseImageError, match="unsafe"):
        handler.redirect_request(
            basic,
            None,
            307,
            "Temporary Redirect",
            {},
            "https://evil.example:notaport/token",
        )


def test_release_helper_has_unique_module_functions_and_cli_commands() -> None:
    tree = ast.parse(MODULE_PATH.read_text(encoding="utf-8"))
    names = [
        node.name
        for node in tree.body
        if isinstance(node, (ast.FunctionDef, ast.ClassDef))
    ]
    assert [name for name, count in Counter(names).items() if count > 1] == []

    choices = release_images.build_parser()._subparsers._group_actions[0].choices
    assert set(choices) == {
        "matrix",
        "verify-candidate-request",
        "check-candidate",
        "verify-built-digest",
        "seal-candidate",
        "assemble-candidate",
        "verify-release-tag",
        "promotion-preflight",
        "promote-one",
        "verify-release",
    }


def test_workflows_pin_actions_and_separate_build_from_digest_promotion() -> None:
    candidate = (REPO / ".github/workflows/release-candidate.yml").read_text(
        encoding="utf-8"
    )
    release = (REPO / ".github/workflows/release.yml").read_text(encoding="utf-8")

    for source in (candidate, release):
        uses = re.findall(r"^\s*-\s*uses:\s*([^\s#]+)", source, flags=re.MULTILINE)
        assert uses
        assert all(re.fullmatch(r"[^@]+@[0-9a-f]{40}", item) for item in uses)
        assert "secrets.GHCR" not in source
        assert "secrets.PAT" not in source
        assert ":latest" not in source
        assert "cancel-in-progress: false" in source

    assert "workflow_dispatch:" in candidate
    assert "push-by-digest=true" in candidate
    assert "assemble-candidate" in candidate
    assert (
        "tags: candidate-" not in candidate
    )  # the aggregate step owns tag construction.
    assert candidate.count("- service:") == 15
    assert "docker/build-push-action@" in candidate
    assert "OMEGA-Release-Candidate-Manifest-SHA256:" in candidate
    assert "group: release-images-${{ inputs.release_tag }}" in candidate

    assert release.startswith("name: Release Images\n")
    assert "promotion-preflight" in release
    assert "promote-one" in release
    assert "verify-release" in release
    assert "tests/test_release_image_promotion.py" in release
    assert release.count("- service:") == 15
    assert "docker/build-push-action@" not in release
    assert "org.opencontainers.image.revision=" not in release
    assert "group: release-images-${{ github.ref_name }}" in release

    expected_matrix = [
        {
            "service": item.service,
            "context": item.context,
            "dockerfile": item.dockerfile,
        }
        for item in release_images.IMAGES
    ]
    candidate_doc = yaml.safe_load(candidate)
    release_doc = yaml.safe_load(release)

    def checkout_step(job: dict) -> dict:
        return next(
            step
            for step in job["steps"]
            if str(step.get("uses", "")).startswith("actions/checkout@")
        )

    assert (
        candidate_doc["jobs"]["build-candidate-digests"]["strategy"]["matrix"][
            "include"
        ]
        == expected_matrix
    )

    # This repository is private.  Input is syntax-gated before any checkout;
    # only trusted main is then checked out to validate exact current main.
    # Subsequent mutation jobs use that validated SHA output, never raw input.
    candidate_validate = candidate_doc["jobs"]["validate-candidate"]
    candidate_build = candidate_doc["jobs"]["build-candidate-digests"]
    candidate_seal = candidate_doc["jobs"]["aggregate-and-seal-candidate"]
    release_gate = release_doc["jobs"]["verify-tag-and-candidate"]
    release_promote = release_doc["jobs"]["build-and-push"]

    assert "refs/heads/main" in candidate_validate["steps"][0]["run"]
    assert checkout_step(candidate_validate)["with"] == {
        "fetch-depth": 0,
        "persist-credentials": True,
        "ref": "refs/heads/main",
    }
    assert (
        "needs.validate-candidate.outputs.source_sha"
        in checkout_step(candidate_build)["with"]["ref"]
    )
    assert "inputs.expected_sha" not in checkout_step(candidate_build)["with"]["ref"]
    assert checkout_step(candidate_build)["with"]["persist-credentials"] is False
    assert (
        "needs.validate-candidate.outputs.source_sha"
        in checkout_step(candidate_seal)["with"]["ref"]
    )
    assert checkout_step(candidate_seal)["with"]["persist-credentials"] is True
    assert checkout_step(release_gate)["with"]["persist-credentials"] is True
    assert checkout_step(release_promote)["with"] == {
        "fetch-depth": 0,
        "persist-credentials": True,
    }

    # Package mutation is absent globally and granted only to the two jobs that
    # build digest content or aggregate exact candidate tags.
    assert candidate_doc.get("permissions") == {"contents": "read"}
    assert candidate_build["permissions"] == {
        "contents": "read",
        "packages": "write",
    }
    assert candidate_seal["permissions"] == {
        "contents": "read",
        "packages": "write",
    }
    assert release_doc.get("permissions") == {"contents": "read"}
    assert release_gate["permissions"] == {
        "contents": "read",
        "packages": "read",
    }
    assert release_promote["permissions"] == {
        "contents": "read",
        "packages": "write",
    }
    release_verify = release_doc["jobs"]["verify-release-images"]
    assert release_verify["permissions"] == {
        "contents": "read",
        "packages": "read",
    }
    assert checkout_step(release_verify)["with"] == {
        "fetch-depth": 0,
        "persist-credentials": True,
    }
    assert "tag_object_sha" in release_gate["outputs"]
    assert release_promote["steps"][-1]["run"].count("--expected-tag-object-sha") == 1
    assert release_verify["steps"][-2]["run"].count("--expected-tag-object-sha") == 1
    assert (
        release_doc["jobs"]["build-and-push"]["strategy"]["matrix"]["include"]
        == expected_matrix
    )
