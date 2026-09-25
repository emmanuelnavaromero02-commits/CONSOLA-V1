from __future__ import annotations

import copy
import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest
import yaml
import scripts.release_digest_chain as digest_chain

from scripts.release_digest_chain import (
    IMAGE_KEYS,
    MANIFEST_KEYS,
    PackageVersionClient,
    artifact_name,
    receipt_filename,
    release_config_labels,
    settle_built_candidate,
    verify_candidate,
    write_manifest,
)
from scripts.release_digest_env import EXPECTED_COMPOSE, ManifestError, load_manifest
from scripts.release_image_promotion import (
    CANONICAL_SERVICES,
    DOCKER_MANIFEST,
    DOCKER_MANIFEST_LIST,
    HttpResult,
    OCI_CONFIG,
    OCI_INDEX,
    OCI_MANIFEST,
    PromotionError,
    RegistryClient,
    ReleaseIdentity,
)

REPO = Path(__file__).resolve().parents[1]
WORKFLOW = REPO / ".github" / "workflows" / "release.yml"
BUILDX_FIXTURE = REPO / "tests" / "fixtures" / "buildx-v0.36.1-single-platform-oci.json"
LAYER_MEDIA_TYPE = "application/vnd.oci.image.layer.v1.tar+gzip"
PINNED_ACTIONS = {
    "actions/checkout": "11d5960a326750d5838078e36cf38b85af677262",
    "actions/download-artifact": "d3f86a106a0bac45b974a628896c90dbdf5c8093",
    "actions/setup-node": "49933ea5288caeca8642d1e84afbd3f7d6820020",
    "actions/setup-python": "a26af69be951a213d495a4c3e4e4022e16d87065",
    "actions/upload-artifact": "ea165f8d65b6e75b540449e92b4886f43607fa02",
    "docker/build-push-action": "10e90e3645eae34f1e60eeb005ba3a3d33f178e8",
    "docker/login-action": "c94ce9fb468520275223c153574b00df6fe4bcc9",
    "docker/setup-buildx-action": "8d2750c68a42422c14e847fe6c8ac0403b4cbd6f",
    "hashicorp/setup-terraform": "b9cd54a3c349d3f38e8881555d616ced269862dd",
}
PINNED_BUILDKIT = "moby/buildkit@sha256:28a898719c18a33f4e8000685287fa36fd0dd9560c6440227d3a732d79bb41d8"


def _digest(body: bytes) -> str:
    return f"sha256:{hashlib.sha256(body).hexdigest()}"


def _compact(value: object) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":")).encode()


@pytest.fixture
def identity() -> ReleaseIdentity:
    return ReleaseIdentity.create(
        repository="omega-owner/omega",
        owner="omega-owner",
        source_sha="a" * 40,
        release_tag="v1.46.0-rc.1",
        run_id=99123,
    )


class CandidateRegistry:
    def __init__(self, identity: ReleaseIdentity) -> None:
        self.identity = identity
        self.manifests: dict[str, HttpResult] = {}
        self.configs: dict[str, bytes] = {}
        self.layers: dict[str, bytes] = {}
        self.heads: list[tuple[str, str]] = []
        self.recovery_lookups: list[tuple[bool, frozenset[str] | None]] = []
        for service in CANONICAL_SERVICES:
            self.add(service)

    def add(self, service: str) -> str:
        annotations = {
            **self.identity.annotations(service),
            "io.omega.release.run-id": str(self.identity.run_id),
        }
        config = _compact(
            {
                "architecture": "amd64",
                "os": "linux",
                "config": {"Labels": release_config_labels(self.identity, service)},
            }
        )
        layer = f"layer-{service}".encode()
        config_descriptor = {
            "mediaType": OCI_CONFIG,
            "digest": _digest(config),
            "size": len(config),
        }
        layer_descriptor = {
            "mediaType": LAYER_MEDIA_TYPE,
            "digest": _digest(layer),
            "size": len(layer),
        }
        manifest = _compact(
            {
                "schemaVersion": 2,
                "mediaType": OCI_MANIFEST,
                "annotations": annotations,
                "config": config_descriptor,
                "layers": [layer_descriptor],
            }
        )
        digest = _digest(manifest)
        self.manifests[digest] = HttpResult(
            200,
            manifest,
            {"Content-Type": OCI_MANIFEST, "Docker-Content-Digest": digest},
        )
        self.configs[config_descriptor["digest"]] = config
        self.layers[layer_descriptor["digest"]] = layer
        return digest

    def digest(self, service: str) -> str:
        expected = self.identity.annotations(service)
        matches = [
            digest
            for digest, result in self.manifests.items()
            if json.loads(result.body)["annotations"].items() >= expected.items()
        ]
        assert len(matches) == 1
        return matches[0]

    def get_manifest(
        self,
        _service: str,
        digest: str,
        *,
        allow_absent: bool = False,
        accepted_media_types: frozenset[str] | None = None,
    ) -> HttpResult | None:
        if allow_absent or accepted_media_types is not None:
            self.recovery_lookups.append((allow_absent, accepted_media_types))
        result = self.manifests.get(digest)
        if result is None and allow_absent:
            return None
        if result is None:
            raise PromotionError("candidate digest is absent")
        return result

    def get_blob(self, _service: str, descriptor: dict[str, object]) -> bytes:
        return self.configs[str(descriptor["digest"])]

    def head_blob(self, service: str, descriptor: dict[str, object]) -> None:
        digest = str(descriptor["digest"])
        self.heads.append((service, digest))
        if digest not in self.layers:
            raise PromotionError("GHCR layer HEAD returned HTTP 404")
        if len(self.layers[digest]) != descriptor["size"]:
            raise PromotionError("GHCR layer HEAD size does not match descriptor")


def test_candidate_is_single_runnable_manifest_and_heads_every_layer(
    identity: ReleaseIdentity,
) -> None:
    registry = CandidateRegistry(identity)
    receipt = verify_candidate(
        registry, service="console", digest=registry.digest("console")
    )
    assert receipt["schema_version"] == 2
    assert receipt["build_run_id"] == identity.run_id
    assert receipt["manifest_media_type"] == OCI_MANIFEST
    assert registry.heads == [("console", receipt["layer_digests"][0])]


def test_candidate_rejects_descriptor_for_missing_layer(
    identity: ReleaseIdentity,
) -> None:
    registry = CandidateRegistry(identity)
    digest = registry.digest("console")
    layer = json.loads(registry.manifests[digest].body)["layers"][0]["digest"]
    del registry.layers[layer]
    with pytest.raises(PromotionError, match="HEAD returned HTTP 404"):
        verify_candidate(registry, service="console", digest=digest)


@pytest.mark.parametrize(
    "mutation",
    [
        lambda manifest: manifest.update(subject={"digest": "sha256:" + "0" * 64}),
        lambda manifest: manifest["config"].update(urls=["https://attacker.example"]),
        lambda manifest: manifest["layers"][0].update(data="embedded"),
        lambda manifest: manifest["layers"][0].update(
            mediaType="application/vnd.oci.image.layer.v1.tar+invented"
        ),
        lambda manifest: manifest["layers"].append(dict(manifest["layers"][0])),
    ],
)
def test_candidate_rejects_noncanonical_manifest_and_descriptors(
    identity: ReleaseIdentity, mutation: object
) -> None:
    registry = CandidateRegistry(identity)
    old_digest = registry.digest("console")
    manifest = json.loads(registry.manifests.pop(old_digest).body)
    assert callable(mutation)
    mutation(manifest)
    body = _compact(manifest)
    digest = _digest(body)
    registry.manifests[digest] = HttpResult(
        200,
        body,
        {"Content-Type": OCI_MANIFEST, "Docker-Content-Digest": digest},
    )

    with pytest.raises(PromotionError):
        verify_candidate(registry, service="console", digest=digest)


@pytest.mark.parametrize(
    ("responses", "message"),
    [
        ([HttpResult(404, b"", {})], "HTTP 404"),
        ([HttpResult(200, b"", {"Content-Length": "3"})], "digest"),
        (
            [
                HttpResult(
                    200,
                    b"",
                    {"Content-Length": "3", "Docker-Content-Digest": "sha256:" + "0" * 64},
                )
            ],
            "digest",
        ),
        (
            [
                HttpResult(
                    200,
                    b"",
                    {"Content-Length": "4", "Docker-Content-Digest": "sha256:" + "1" * 64},
                )
            ],
            "size",
        ),
    ],
)
def test_layer_head_fails_closed_on_gc_header_digest_and_size_attacks(
    identity: ReleaseIdentity,
    responses: list[HttpResult],
    message: str,
) -> None:
    calls: list[tuple[str, str, dict[str, str]]] = []

    def transport(method: str, url: str, headers: dict[str, str], *_args: object) -> HttpResult:
        calls.append((method, url, headers))
        return responses.pop(0)

    client = RegistryClient(
        identity=identity,
        username="omega-bot",
        github_token="token",
        transport=transport,
    )
    client._tokens[("console", "pull")] = "registry-token"
    descriptor = {
        "mediaType": LAYER_MEDIA_TYPE,
        "digest": "sha256:" + "1" * 64,
        "size": 3,
    }

    with pytest.raises(PromotionError, match=message):
        client.head_blob("console", descriptor)
    assert calls[0][0] == "HEAD"


def test_layer_head_allows_only_trusted_redirect_without_forwarding_token(
    identity: ReleaseIdentity,
) -> None:
    digest = "sha256:" + "1" * 64
    responses = [
        HttpResult(
            307,
            b"",
            {
                "Location": "https://pkg-containers.githubusercontent.com/blob-token",
                "Docker-Content-Digest": digest,
            },
        ),
        HttpResult(200, b"", {"Content-Length": "3"}),
    ]
    calls: list[tuple[str, str, dict[str, str]]] = []

    def transport(method: str, url: str, headers: dict[str, str], *_args: object) -> HttpResult:
        calls.append((method, url, headers))
        return responses.pop(0)

    client = RegistryClient(
        identity=identity,
        username="omega-bot",
        github_token="token",
        transport=transport,
    )
    client._tokens[("console", "pull")] = "registry-token"

    client.head_blob(
        "console",
        {"mediaType": LAYER_MEDIA_TYPE, "digest": digest, "size": 3},
    )

    assert len(calls) == 2
    assert calls[1][1].startswith("https://pkg-containers.githubusercontent.com/")
    assert "Authorization" not in calls[1][2]


def test_layer_head_rejects_untrusted_redirect(identity: ReleaseIdentity) -> None:
    digest = "sha256:" + "1" * 64
    client = RegistryClient(
        identity=identity,
        username="omega-bot",
        github_token="token",
        transport=lambda *_args: HttpResult(
            307,
            b"",
            {
                "Location": "https://attacker.example/blob",
                "Docker-Content-Digest": digest,
            },
        ),
    )
    client._tokens[("console", "pull")] = "registry-token"

    with pytest.raises(PromotionError, match="redirect target is not trusted"):
        client.head_blob(
            "console",
            {"mediaType": LAYER_MEDIA_TYPE, "digest": digest, "size": 3},
        )


class ReceiptArtifacts:
    def __init__(self, receipts: dict[str, dict[str, object]]) -> None:
        self.receipts = receipts

    def load(self, *, name: str, filename: str) -> dict[str, object] | None:
        service = name.removeprefix("release-image-candidate-")
        assert name == artifact_name(service)
        assert filename == receipt_filename(service)
        return copy.deepcopy(self.receipts.get(service))


def _write_v2(tmp_path: Path, identity: ReleaseIdentity) -> tuple[Path, Path, dict]:
    registry = CandidateRegistry(identity)
    receipts = {
        service: verify_candidate(
            registry, service=service, digest=registry.digest(service)
        )
        for service in CANONICAL_SERVICES
    }
    manifest_path, checksum_path = write_manifest(
        ReceiptArtifacts(receipts), registry, output_dir=tmp_path / "manifest"
    )
    return manifest_path, checksum_path, json.loads(manifest_path.read_text())


def test_manifest_v2_is_exact_tagless_canonical_and_checksum_bound(
    tmp_path: Path, identity: ReleaseIdentity
) -> None:
    manifest_path, checksum_path, manifest = _write_v2(tmp_path, identity)
    assert set(manifest) == MANIFEST_KEYS
    assert manifest["kind"] == "omega-release-manifest"
    assert [entry["service"] for entry in manifest["images"]] == list(
        CANONICAL_SERVICES
    )
    assert all(set(entry) == IMAGE_KEYS for entry in manifest["images"])
    assert all("release_reference" not in entry for entry in manifest["images"])
    assert checksum_path.read_text() == (
        f"{hashlib.sha256(manifest_path.read_bytes()).hexdigest()}  "
        f"{manifest_path.name}\n"
    )
    loaded = load_manifest(
        manifest_path,
        checksum_path=checksum_path,
        repository=identity.repository,
        release_tag=identity.release_tag,
        source_sha=identity.source_sha,
        build_run_id=identity.run_id,
    )
    assert loaded["by_service"]["console"].startswith(
        "ghcr.io/omega-owner/console@sha256:"
    )


@pytest.mark.parametrize(
    "mutation",
    [
        lambda value: value.update(repository="other/repo"),
        lambda value: value.update(release_tag="v9.9.9"),
        lambda value: value.update(source_sha="b" * 40),
        lambda value: value.update(build_run_id=44),
        lambda value: value["images"][0].update(image="evil.example/console"),
        lambda value: value["images"][0].update(manifest_media_type="text/plain"),
        lambda value: value["images"][0].update(manifest_size=0),
        lambda value: value["images"].reverse(),
    ],
)
def test_manifest_parser_rejects_cross_identity_descriptor_and_order_attacks(
    tmp_path: Path, identity: ReleaseIdentity, mutation: object
) -> None:
    manifest_path, checksum_path, value = _write_v2(tmp_path, identity)
    assert callable(mutation)
    mutation(value)
    body = (json.dumps(value, indent=2, sort_keys=True) + "\n").encode()
    manifest_path.write_bytes(body)
    checksum_path.write_text(
        f"{hashlib.sha256(body).hexdigest()}  {manifest_path.name}\n"
    )
    with pytest.raises(ManifestError):
        load_manifest(
            manifest_path,
            checksum_path=checksum_path,
            repository=identity.repository,
            release_tag=identity.release_tag,
            source_sha=identity.source_sha,
            build_run_id=identity.run_id,
        )


def test_manifest_parser_rejects_duplicate_keys_noncanonical_bytes_and_checksum(
    tmp_path: Path, identity: ReleaseIdentity
) -> None:
    manifest_path, checksum_path, _value = _write_v2(tmp_path, identity)
    original = manifest_path.read_bytes()
    attacks = [
        original.replace(b'"schema_version": 2,', b'"schema_version": 2,"schema_version": 2,', 1),
        original.rstrip(),
    ]
    for body in attacks:
        manifest_path.write_bytes(body)
        checksum_path.write_text(
            f"{hashlib.sha256(body).hexdigest()}  {manifest_path.name}\n"
        )
        with pytest.raises(ManifestError):
            load_manifest(
                manifest_path,
                checksum_path=checksum_path,
                repository=identity.repository,
                release_tag=identity.release_tag,
                source_sha=identity.source_sha,
                build_run_id=identity.run_id,
            )
    manifest_path.write_bytes(original)
    checksum_path.write_text(f"{'0' * 64}  {manifest_path.name}\n")
    with pytest.raises(ManifestError, match="checksum"):
        load_manifest(
            manifest_path,
            checksum_path=checksum_path,
            repository=identity.repository,
            release_tag=identity.release_tag,
            source_sha=identity.source_sha,
            build_run_id=identity.run_id,
        )


def _run_compose_lock(
    tmp_path: Path, identity: ReleaseIdentity, extra_services: dict, mutate=None
) -> subprocess.CompletedProcess[str]:
    manifest_path, checksum_path, manifest = _write_v2(tmp_path, identity)
    by_service = {entry["service"]: entry["digest_reference"] for entry in manifest["images"]}
    services = {
        name: {"image": by_service[service], "pull_policy": "never"}
        for name, service in EXPECTED_COMPOSE.items()
    }
    services["postgres"] = {"image": "postgres:16"}
    services.update(extra_services)
    if mutate is not None:
        mutate(services, by_service)
    compose = tmp_path / "compose.json"
    compose.write_text(json.dumps({"services": services}), encoding="utf-8")
    return subprocess.run(
        [
            sys.executable,
            "scripts/release_digest_env.py",
            "--manifest",
            str(manifest_path),
            "--checksum",
            str(checksum_path),
            "--repository",
            identity.repository,
            "--release-tag",
            identity.release_tag,
            "--source-sha",
            identity.source_sha,
            "--build-run-id",
            str(identity.run_id),
            "--compose-config",
            str(compose),
        ],
        cwd=REPO,
        text=True,
        capture_output=True,
        check=False,
    )


@pytest.mark.parametrize(
    "attack",
    [
        "build",
        "tag",
        "extra-release-consumer",
        "other-repository-in-the-namespace",
        "mirror-lookalike",
        "mirror-name-in-another-namespace-is-fine-but-not-a-release-digest",
    ],
)
def test_compose_lock_rejects_build_tag_and_extra_consumer(
    tmp_path: Path, identity: ReleaseIdentity, attack: str
) -> None:
    owner = identity.owner

    def mutate(services: dict, by_service: dict) -> None:
        if attack == "build":
            services["console"]["build"] = "."
        elif attack == "tag":
            services["console"]["image"] = f"ghcr.io/{owner}/console:latest"
        elif attack == "extra-release-consumer":
            services["unexpected"] = {"image": by_service["console"]}
        elif attack == "other-repository-in-the-namespace":
            services["unexpected"] = {"image": f"ghcr.io/{owner}/omega-evil:1"}
        elif attack == "mirror-lookalike":
            services["unexpected"] = {"image": f"ghcr.io/{owner}/omega-minio-evil:1"}
        else:
            services["unexpected"] = {"image": by_service["sap_b1"]}

    result = _run_compose_lock(tmp_path, identity, {}, mutate)

    assert result.returncode != 0
    assert "RELEASE DIGEST ENV BLOCKED" in result.stderr


def test_compose_lock_accepts_the_infrastructure_mirrors_hosted_in_the_namespace(
    tmp_path: Path, identity: ReleaseIdentity
) -> None:
    owner = identity.owner
    result = _run_compose_lock(
        tmp_path,
        identity,
        {
            "minio": {"image": f"ghcr.io/{owner}/omega-minio:RELEASE.2024-12-18T13-15-44Z"},
            "minio-init": {"image": f"ghcr.io/{owner}/omega-mc:RELEASE.2024-11-21T17-21-54Z"},
        },
    )

    assert result.returncode == 0, result.stderr
    assert "RELEASE DIGEST ENV PASS" in result.stdout


def test_package_recovery_pages_and_requires_unique_exact_run_candidate(
    identity: ReleaseIdentity,
) -> None:
    registry = CandidateRegistry(identity)
    digest = registry.digest("console")
    client = PackageVersionClient(identity=identity, github_token="token")
    distractors = [
        {"name": f"not-a-digest-{index}", "metadata": {"container": {"tags": ["tag"]}}}
        for index in range(100)
    ]
    pages = {
        1: distractors,
        2: [{"name": digest, "metadata": {"container": {"tags": []}}}],
    }
    client._list_page = lambda _service, page: pages.get(page, [])  # type: ignore[method-assign]
    recovered = client.recover(registry, service="console")
    assert recovered is not None and recovered["digest"] == digest


def test_package_recovery_skips_all_valid_historical_types_and_structured_absence(
    identity: ReleaseIdentity,
) -> None:
    registry = CandidateRegistry(identity)
    candidate_digest = registry.digest("console")
    historical_digests: list[str] = []
    for index, media_type in enumerate(
        (DOCKER_MANIFEST, DOCKER_MANIFEST_LIST, OCI_INDEX), start=1
    ):
        historical = _compact(
            {"schemaVersion": 2, "mediaType": media_type, "legacy": index}
        )
        historical_digest = _digest(historical)
        historical_digests.append(historical_digest)
        registry.manifests[historical_digest] = HttpResult(
            200,
            historical,
            {
                "Content-Type": media_type,
                "Docker-Content-Digest": historical_digest,
            },
        )
    absent_digest = "sha256:" + "f" * 64
    client = PackageVersionClient(identity=identity, github_token="token")
    client._list_page = lambda _service, page: (  # type: ignore[method-assign]
        [
            *({"name": digest} for digest in historical_digests),
            {"name": absent_digest},
            {"name": candidate_digest},
        ]
        if page == 1
        else []
    )

    recovered = client.recover(registry, service="console")

    assert recovered is not None
    assert recovered["digest"] == candidate_digest
    assert all(allow_absent for allow_absent, _media in registry.recovery_lookups)
    assert all(
        media == digest_chain.RECOVERY_MANIFEST_MEDIA_TYPES
        for _allow_absent, media in registry.recovery_lookups
    )


def test_tagged_exact_identity_candidate_cannot_hide_an_orphan(
    identity: ReleaseIdentity, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    registry = CandidateRegistry(identity)
    digest = registry.digest("console")
    client = PackageVersionClient(identity=identity, github_token="token")
    client._list_page = lambda _service, page: (  # type: ignore[method-assign]
        [{"name": digest, "metadata": {"container": {"tags": ["attacker-hides-orphan"]}}}]
        if page == 1
        else []
    )

    recovered = client.recover(registry, service="console")

    assert recovered is not None and recovered["digest"] == digest

    class NoArtifact:
        def load(self, **_kwargs: object) -> None:
            return None

    monkeypatch.setattr(
        digest_chain,
        "_clients",
        lambda _args: (NoArtifact(), registry, client),
    )
    receipt = tmp_path / "console.release-candidate.json"
    github_output = tmp_path / "github-output"
    result = digest_chain.main(
        [
            "candidate-state",
            "--repository", identity.repository,
            "--owner", identity.owner,
            "--source-sha", identity.source_sha,
            "--release-tag", identity.release_tag,
            "--run-id", str(identity.run_id),
            "--username", "omega-bot",
            "--service", "console",
            "--receipt", str(receipt),
            "--github-output", str(github_output),
        ]
    )
    assert result == 1
    assert not receipt.exists()
    assert not github_output.exists()


def test_public_metadata_candidate_without_run_artifact_is_never_adopted(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    class NoArtifact:
        def load(self, **_kwargs: object) -> None:
            return None

    class InjectedPackage:
        def recover(self, *_args: object, **_kwargs: object) -> dict[str, object]:
            return {"digest": "sha256:" + "1" * 64}

    monkeypatch.setattr(
        digest_chain,
        "_clients",
        lambda _args: (NoArtifact(), object(), InjectedPackage()),
    )
    receipt = tmp_path / "console.release-candidate.json"
    github_output = tmp_path / "github-output"

    result = digest_chain.main(
        [
            "candidate-state",
            "--repository",
            "omega-owner/omega",
            "--owner",
            "omega-owner",
            "--source-sha",
            "a" * 40,
            "--release-tag",
            "v1.46.0-rc.1",
            "--run-id",
            "99123",
            "--username",
            "omega-bot",
            "--service",
            "console",
            "--receipt",
            str(receipt),
            "--github-output",
            str(github_output),
        ]
    )

    assert result == 1
    assert not receipt.exists()
    assert not github_output.exists()


def test_write_receipt_rechecks_unique_package_version_after_build(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    built_digest = "sha256:" + "1" * 64

    class RacedPackage:
        def recover(self, *_args: object, **_kwargs: object) -> dict[str, object]:
            return {"digest": "sha256:" + "2" * 64}

    monkeypatch.setattr(
        digest_chain,
        "_clients",
        lambda _args: (object(), object(), RacedPackage()),
    )
    monkeypatch.setattr(
        digest_chain,
        "verify_candidate",
        lambda *_args, **_kwargs: {"digest": built_digest},
    )
    receipt = tmp_path / "console.release-candidate.json"

    result = digest_chain.main(
        [
            "write-receipt",
            "--repository",
            "omega-owner/omega",
            "--owner",
            "omega-owner",
            "--source-sha",
            "a" * 40,
            "--release-tag",
            "v1.46.0-rc.1",
            "--run-id",
            "99123",
            "--username",
            "omega-bot",
            "--service",
            "console",
            "--digest",
            built_digest,
            "--receipt",
            str(receipt),
        ]
    )

    assert result == 1
    assert not receipt.exists()


def test_post_build_package_visibility_retries_only_until_exact_digest(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    digest = "sha256:" + "1" * 64

    class SettlingPackage:
        def __init__(self) -> None:
            self.values = iter((None, None, {"digest": digest}))

        def recover(self, *_args: object, **_kwargs: object) -> object:
            return next(self.values)

    sleeps: list[float] = []
    monkeypatch.setattr(digest_chain.time, "sleep", sleeps.append)
    observed = settle_built_candidate(
        SettlingPackage(),  # type: ignore[arg-type]
        object(),  # type: ignore[arg-type]
        service="console",
        digest=digest,
    )
    assert observed["digest"] == digest
    assert sleeps == [digest_chain.PACKAGE_SETTLE_SECONDS] * 2


def test_post_build_package_visibility_timeout_stays_fail_closed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class MissingPackage:
        def recover(self, *_args: object, **_kwargs: object) -> None:
            return None

    monkeypatch.setattr(digest_chain.time, "sleep", lambda _seconds: None)
    with pytest.raises(PromotionError, match="did not settle"):
        settle_built_candidate(
            MissingPackage(),  # type: ignore[arg-type]
            object(),  # type: ignore[arg-type]
            service="console",
            digest="sha256:" + "1" * 64,
        )


def test_release_workflow_is_tagless_pinned_and_digest_gate_authoritative() -> None:
    workflow = yaml.safe_load(WORKFLOW.read_text())
    source = WORKFLOW.read_text()
    build = workflow["jobs"]["build-and-push"]
    digest_gate = workflow["jobs"]["digest-full-stack-gate"]
    publish = workflow["jobs"]["publish-release-manifest"]
    build_step = next(step for step in build["steps"] if step.get("id") == "build")
    assert "push-by-digest=true" in build_step["with"]["outputs"]
    assert build_step["with"]["provenance"] is False
    assert "manifest:io.omega.release.service=" in build_step["with"]["annotations"]
    assert "manifest[" not in build_step["with"]["annotations"]
    assert "needs.digest-full-stack-gate.result == 'success'" in publish["if"]
    assert "needs.digest-full-stack-gate.result == 'skipped'" not in publish["if"]
    assert publish["permissions"]["packages"] == "read"
    assert "release_image_promotion.py" not in source
    assert "prepare-intent" not in source and "verify-final" not in source
    assert "--no-build --pull never" in source
    assert any(
        step.get("name") == "Render and start hybrid digest/source release stack"
        for step in digest_gate["steps"]
    )
    assert "for ref in postgres:15 postgres:15.18" in source
    assert "release_docker_lock.py" in source
    assert "omega-release-image-inventory.lock" in source
    assert "omega-release-image-inventory.post-gates" not in source
    assert "scripts/secure_release_output.py" in source
    assert 'FINAL_OUTPUT_DIR="$(/usr/bin/mktemp -d' in source
    assert "{{.Repository}}\\t{{.Tag}}\\t{{.Digest}}\\t{{.ID}}" in source
    assert "{{json .}}" not in source
    assert "OMEGA_RELEASE_E2E_ENV_SHA256" in source
    assert "steps.bootstrap.outputs.e2e_env_sha256" in source
    assert "OMEGA_RELEASE_E2E_ENV_SHA256=" not in source
    assert "steps.manifest.outputs.manifest_sha256" in source
    assert "steps.manifest.outputs.checksum_sha256" in source
    assert "steps.runtime_lock.outputs.compose_sha256" in source
    assert "steps.runtime_lock.outputs.image_inventory_sha256" in source
    assert "verify_authority_sha256 manifest" in source
    assert "authority bytes changed during release gates" in source
    assert "final release output differs from its Actions authority" in (
        REPO / "scripts/secure_release_output.py"
    ).read_text(encoding="utf-8")
    assert "omega-release-compose-final.json" not in source
    assert '--compose-config "${FINAL_COMPOSE}"' in source
    assert "published Release did not become immutable and canonical" in source
    assert source.count("+refs/tags/${RELEASE_TAG}:${tag_check_ref}") == 2
    assert "git fetch --force --tags origin" not in source
    for job in workflow["jobs"].values():
        for step in job.get("steps", []):
            uses = step.get("uses")
            if uses:
                action, pin = uses.rsplit("@", 1)
                assert PINNED_ACTIONS[action] == pin
            if step.get("uses", "").startswith("docker/setup-buildx-action@"):
                assert step["with"] == {
                    "version": "v0.36.1",
                    "driver-opts": f"image={PINNED_BUILDKIT}",
                }
    download = next(
        step for step in publish["steps"] if step.get("uses", "").startswith("actions/download-artifact@")
    )
    assert download["with"]["name"] == "${{ needs.assemble-release-manifest.outputs.artifact_name }}"


def test_buildx_real_single_platform_fixture_matches_release_contract() -> None:
    fixture = json.loads(BUILDX_FIXTURE.read_text(encoding="utf-8"))
    assert fixture["buildx_version"] == "v0.36.1"
    assert fixture["buildkit_image"] == PINNED_BUILDKIT
    assert fixture["options"] == {
        "annotation_syntax": "manifest:<key>=<value>",
        "oci_mediatypes": True,
        "platform": "linux/amd64",
        "provenance": False,
    }
    observed = fixture["observed"]
    assert observed["root_descriptor_media_type"] == OCI_MANIFEST
    assert observed["root_manifest_media_type"] == OCI_MANIFEST
    assert observed["config_media_type"] == OCI_CONFIG
    assert observed["layer_count"] > 0
    assert observed["manifest_annotations"] == {
        "io.omega.release.run-id": "99123",
        "io.omega.release.service": "probe",
        "org.opencontainers.image.revision": "0123456789abcdef0123456789abcdef01234567",
    }


@pytest.mark.parametrize(
    "argument",
    [
        "-pno:scripts.verify_test_skip_policy",
        "--disable-plugin-autoload",
        "--deselect",
        "--ignore",
        "--rootdir",
        "--override-ini",
        "-qopython_files=test_select_previous_release.py",
    ],
)
def test_release_pytest_launcher_rejects_authority_and_selection_overrides(
    argument: str,
) -> None:
    result = subprocess.run(
        [sys.executable, "-I", "scripts/run_release_pytest.py", argument, "tests/test_select_previous_release.py"],
        cwd=REPO,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode != 0
    assert "RELEASE PYTEST BLOCKED" in result.stderr


def test_release_pytest_launcher_rejects_environment_and_policy_overrides() -> None:
    for key, value in (
        ("OMEGA_RELEASE_TEST_SKIP_ENVIRONMENT", "dev"),
        ("OMEGA_RELEASE_TEST_SKIP_POLICY", "/tmp/attacker.json"),
    ):
        result = subprocess.run(
            [sys.executable, "-I", "scripts/run_release_pytest.py", "tests/test_select_previous_release.py"],
            cwd=REPO,
            env={**os.environ, key: value},
            capture_output=True,
            text=True,
            check=False,
        )
        assert result.returncode != 0
        assert "RELEASE PYTEST BLOCKED" in result.stderr
