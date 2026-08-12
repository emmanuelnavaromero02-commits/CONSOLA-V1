from __future__ import annotations

import ast
import hashlib
import importlib.util
import json
import os
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
        self.blob_puts: list[tuple[str, str]] = []
        self.gets: list[tuple[str, str]] = []

    def get_manifest(self, repository: str, reference: str):
        self.gets.append((repository, reference))
        return self.refs.get((repository, reference))

    def get_blob(self, repository: str, digest: str) -> bytes:
        payload = self.blobs[(repository, digest)]
        assert _digest(payload) == digest
        return payload

    def put_blob(self, repository: str, payload: bytes) -> str:
        digest = _digest(payload)
        self.blobs[(repository, digest)] = payload
        self.blob_puts.append((repository, digest))
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
GITHUB_RUN_ID = "987654321"
GITHUB_RUN_ATTEMPT = "1"


def _write_receipt(path: Path, record: release_images.ImageRecord) -> None:
    release_images.write_digest_receipt(
        path,
        record,
        github_run_id=GITHUB_RUN_ID,
        github_run_attempt=GITHUB_RUN_ATTEMPT,
    )


def _assemble_candidate(registry: FakeRegistry, **kwargs) -> str:
    output = kwargs["output"]
    receipts_dir = kwargs["receipts_dir"]
    intent = receipts_dir.parent / "release-candidate-intent.json"
    if not intent.exists():
        release_images.prepare_candidate_intent(
            registry,
            source_sha=kwargs["source_sha"],
            release_tag=kwargs["release_tag"],
            receipts_dir=receipts_dir,
            prior_intents_dir=output.parent / "prior-intents",
            output=intent,
            github_run_id=GITHUB_RUN_ID,
            github_run_attempt=GITHUB_RUN_ATTEMPT,
        )
    return release_images.assemble_candidate(
        registry,
        github_run_id=GITHUB_RUN_ID,
        github_run_attempt=GITHUB_RUN_ATTEMPT,
        intent=intent,
        **kwargs,
    )


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


def _digest_only_registry(
    tmp_path: Path, *, revision: str = SHA
) -> tuple[FakeRegistry, Path]:
    registry = FakeRegistry()
    receipts = tmp_path / "receipts"
    for image in release_images.IMAGES:
        record = _add_digest_image(
            registry, image.service, revision=revision, salt=image.service
        )
        _write_receipt(receipts / f"{image.service}.json", record)
    registry.visibility[release_images.MANIFEST_PACKAGE] = "private"
    return registry, receipts


def _assemble(
    tmp_path: Path, *, source_sha: str = SHA
) -> tuple[FakeRegistry, Path, str]:
    registry, receipts = _digest_only_registry(tmp_path, revision=source_sha)
    output = tmp_path / "release-candidate.json"
    digest = _assemble_candidate(
        registry,
        source_sha=source_sha,
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
        _assemble_candidate(
            registry,
            source_sha=SHA,
            release_tag=TAG,
            receipts_dir=receipts,
            output=tmp_path / "manifest.json",
        )

    assert registry.puts == []
    assert registry.blob_puts == []
    assert not any(
        reference == release_images.candidate_tag(SHA) for _, reference in registry.refs
    )


def test_candidate_receipt_inventory_rejects_hidden_extras_and_symlinks(
    tmp_path: Path,
) -> None:
    registry, receipts = _digest_only_registry(tmp_path)
    (receipts / ".untrusted").write_text("ignored data", encoding="utf-8")
    with pytest.raises(release_images.ReleaseImageError, match="exact 15-image"):
        _assemble_candidate(
            registry,
            source_sha=SHA,
            release_tag=TAG,
            receipts_dir=receipts,
            output=tmp_path / "hidden-extra.json",
        )

    (receipts / ".untrusted").unlink()
    target = receipts / "console.json"
    original = target.read_bytes()
    target.unlink()
    external = tmp_path / "external-receipt.json"
    external.write_bytes(original)
    target.symlink_to(external)
    with pytest.raises(release_images.ReleaseImageError, match="invalid candidate"):
        _assemble_candidate(
            registry,
            source_sha=SHA,
            release_tag=TAG,
            receipts_dir=receipts,
            output=tmp_path / "symlinked-receipt.json",
        )


def test_candidate_receipts_accept_exact_relative_workflow_directory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _registry, _receipts = _digest_only_registry(tmp_path)
    monkeypatch.chdir(tmp_path)

    records = release_images._load_digest_receipts(
        Path("receipts"),
        github_run_id=GITHUB_RUN_ID,
        github_run_attempt=GITHUB_RUN_ATTEMPT,
    )

    assert len(records) == 15


def test_candidate_aggregation_rejects_every_preexisting_service_tag(
    tmp_path: Path,
) -> None:
    registry, receipts = _digest_only_registry(tmp_path)
    conflicting = _add_digest_image(registry, "console", salt="different-content")
    registry.refs[
        (release_images.image_repository("console"), release_images.candidate_tag(SHA))
    ] = registry.refs[(release_images.image_repository("console"), conflicting.digest)]

    with pytest.raises(release_images.ReleaseImageError, match="service tag collision"):
        _assemble_candidate(
            registry,
            source_sha=SHA,
            release_tag=TAG,
            receipts_dir=receipts,
            output=tmp_path / "manifest.json",
        )

    assert registry.puts == []
    assert registry.blob_puts == []


def test_release_visibility_is_gated_before_and_revalidated_after_each_write(
    tmp_path: Path,
) -> None:
    registry, _, manifest_digest = _assemble(tmp_path)
    registry.puts.clear()
    registry.blob_puts.clear()
    registry.visibility["banxico"] = "public"
    with pytest.raises(
        release_images.ReleaseImageError, match="banxico must be private"
    ):
        release_images.promote_one(
            registry,
            service="console",
            source_sha=SHA,
            release_tag=TAG,
            version=VERSION,
            bound_manifest_digest=manifest_digest,
        )
    assert registry.puts == []
    assert registry.blob_puts == []

    registry.visibility["banxico"] = "private"
    original_put = registry.put_manifest

    def mutate_visibility_after_write(
        repository: str, reference: str, payload: bytes, media_type: str
    ) -> str:
        digest = original_put(repository, reference, payload, media_type)
        if reference == TAG:
            registry.visibility["banxico"] = "public"
        return digest

    registry.put_manifest = mutate_visibility_after_write  # type: ignore[method-assign]
    with pytest.raises(
        release_images.ReleaseImageError, match="banxico must be private"
    ):
        release_images.promote_one(
            registry,
            service="console",
            source_sha=SHA,
            release_tag=TAG,
            version=VERSION,
            bound_manifest_digest=manifest_digest,
        )
    assert [reference for _repository, reference, _digest_value in registry.puts] == [
        TAG
    ]


def test_candidate_aggregation_recovers_exact_partial_service_tags(
    tmp_path: Path,
) -> None:
    registry, receipts = _digest_only_registry(tmp_path)
    existing_services = ("console", "workspace", "banxico")
    for service in existing_services:
        receipt = json.loads((receipts / f"{service}.json").read_bytes())
        repository = release_images.image_repository(service)
        registry.refs[(repository, release_images.candidate_tag(SHA))] = registry.refs[
            (repository, receipt["digest"])
        ]

    digest = _assemble_candidate(
        registry,
        source_sha=SHA,
        release_tag=TAG,
        receipts_dir=receipts,
        output=tmp_path / "recovered-candidate.json",
    )

    assert release_images.DIGEST_RE.fullmatch(digest)
    service_tag_puts = [
        item
        for item in registry.puts
        if item[1] == release_images.candidate_tag(SHA)
        and item[0]
        != f"{release_images.CANONICAL_OWNER}/{release_images.MANIFEST_PACKAGE}"
    ]
    assert len(service_tag_puts) == 15 - len(existing_services)


def test_candidate_seal_recovers_from_durable_prior_attempt_intent(
    tmp_path: Path,
) -> None:
    registry, receipts, first_digest = _assemble(tmp_path)
    first = (tmp_path / "release-candidate.json").read_bytes()
    service_tag_puts = [
        item for item in registry.puts if item[1] == release_images.candidate_tag(SHA)
    ]
    assert len(service_tag_puts) == 16  # 15 images plus one sealed-manifest package.

    assert first_digest == _digest(first)
    registry.puts.clear()
    assert (
        _assemble_candidate(
            registry,
            source_sha=SHA,
            release_tag=TAG,
            receipts_dir=receipts,
            output=tmp_path / "release-candidate-second.json",
        )
        == first_digest
    )
    assert registry.puts == []
    assert (tmp_path / "release-candidate-second.json").read_bytes() == first

    second_attempt_receipts = tmp_path / "second-attempt-receipts"
    for image in release_images.IMAGES:
        state, digest = release_images.candidate_state(
            registry,
            service=image.service,
            source_sha=SHA,
            release_tag=TAG,
            receipt=second_attempt_receipts / f"{image.service}.json",
            github_run_id=GITHUB_RUN_ID,
            github_run_attempt="2",
        )
        assert state == "sealed"
        assert release_images.DIGEST_RE.fullmatch(digest)
    registry.puts.clear()
    with pytest.raises(
        release_images.ReleaseImageError,
        match="cannot prove whether durable prior-attempt authority exists",
    ):
        release_images.prepare_candidate_intent(
            registry,
            source_sha=SHA,
            release_tag=TAG,
            receipts_dir=second_attempt_receipts,
            prior_intents_dir=tmp_path / "missing-prior-intents",
            output=tmp_path / "must-not-recover.json",
            github_run_id=GITHUB_RUN_ID,
            github_run_attempt="2",
        )
    assert registry.puts == []
    prior_intent = (
        tmp_path
        / "prior-intents"
        / f"candidate-intent-{GITHUB_RUN_ID}-{GITHUB_RUN_ATTEMPT}"
    )
    prior_intent.mkdir(parents=True)
    (prior_intent / "release-candidate-intent.json").write_bytes(first)
    retry_intent = tmp_path / "retry-intent.json"
    assert (
        release_images.prepare_candidate_intent(
            registry,
            source_sha=SHA,
            release_tag=TAG,
            receipts_dir=second_attempt_receipts,
            prior_intents_dir=tmp_path / "prior-intents",
            output=retry_intent,
            github_run_id=GITHUB_RUN_ID,
            github_run_attempt="2",
        )
        == first_digest
    )
    registry.puts.clear()
    assert (
        release_images.assemble_candidate(
            registry,
            source_sha=SHA,
            release_tag=TAG,
            receipts_dir=second_attempt_receipts,
            output=tmp_path / "cross-attempt.json",
            intent=retry_intent,
            github_run_id=GITHUB_RUN_ID,
            github_run_attempt="2",
        )
        == first_digest
    )
    assert registry.puts == []
    document = json.loads(first)
    assert document["source_sha"] == SHA
    assert document["github_run_id"] == GITHUB_RUN_ID
    assert document["github_run_attempt"] == GITHUB_RUN_ATTEMPT
    assert document["release_tag"] == TAG
    assert len(document["images"]) == 15
    assert sum(row["visibility"] == "private" for row in document["images"]) == 3
    assert sum(row["visibility"] == "public" for row in document["images"]) == 12


def test_gcp_candidate_authority_is_payload_run_bound_and_digest_exact(
    tmp_path: Path,
) -> None:
    registry, _, manifest_digest = _assemble(tmp_path)
    output = tmp_path / "gcp-authority.json"

    receipt_digest = release_images.verify_bound_sealed_lock(
        registry,
        authority_mode="candidate",
        source_sha=SHA,
        release_tag=TAG,
        image_tag=release_images.candidate_tag(SHA),
        version=VERSION,
        output=output,
        bound_manifest_digest=manifest_digest,
        github_run_id=GITHUB_RUN_ID,
        github_run_attempt=GITHUB_RUN_ATTEMPT,
    )

    receipt = json.loads(output.read_bytes())
    assert receipt_digest == _digest(output.read_bytes())
    assert receipt["manifest_digest"] == manifest_digest
    assert receipt["candidate_workflow"] == {
        "head_sha": SHA,
        "run_attempt": GITHUB_RUN_ATTEMPT,
        "run_id": GITHUB_RUN_ID,
    }
    assert len(receipt["images"]) == 15
    assert receipt["labels_authoritative"] is False

    with pytest.raises(release_images.ReleaseImageError, match="required workflow run"):
        release_images.verify_bound_sealed_lock(
            registry,
            authority_mode="candidate",
            source_sha=SHA,
            release_tag=TAG,
            image_tag=release_images.candidate_tag(SHA),
            version=VERSION,
            output=tmp_path / "wrong-run.json",
            bound_manifest_digest=manifest_digest,
            github_run_id="987654322",
            github_run_attempt=GITHUB_RUN_ATTEMPT,
        )


def test_gcp_published_authority_rejects_tag_drift_and_live_privacy_drift(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    git_root = tmp_path / "git"
    work, source_sha = _release_git_repo(
        git_root, "sha256:" + "0" * 64, create_tag=False
    )
    registry, _, manifest_digest = _assemble(
        tmp_path / "registry", source_sha=source_sha
    )
    _git(
        work,
        "tag",
        "-a",
        TAG,
        "-m",
        f"OMEGA release\n\n{release_images.TAG_BINDING_PREFIX}{manifest_digest}",
    )
    _git(work, "push", "origin", f"refs/tags/{TAG}")
    tag_object_sha = _git(work, "rev-parse", f"refs/tags/{TAG}")
    monkeypatch.setattr(
        release_images, "CANONICAL_ORIGIN_URLS", {str(git_root / "remote.git")}
    )
    for image in release_images.IMAGES:
        repository = release_images.image_repository(image.service)
        registry.refs[(repository, TAG)] = registry.refs[
            (repository, release_images.candidate_tag(source_sha))
        ]
    proof_root = tmp_path / "release-authority"
    proof_parent = proof_root / source_sha
    proof_parent.mkdir(parents=True, mode=0o700)
    proof_root.chmod(0o755)
    proof = proof_parent / release_images.GCP_RELEASE_TAG_PROOF_NAME
    proof.write_bytes(
        subprocess.run(
            ["git", "cat-file", "tag", f"refs/tags/{TAG}"],
            cwd=work,
            check=True,
            capture_output=True,
        ).stdout
    )
    proof.chmod(0o400)
    monkeypatch.setattr(release_images, "GCP_RELEASE_TAG_PROOF_ROOT", proof_root)
    monkeypatch.setattr(release_images, "GCP_GHCR_AUTH_OWNER_UID", os.geteuid())
    monkeypatch.setattr(release_images, "GCP_GHCR_AUTH_OWNER_GID", os.getegid())

    with pytest.raises(release_images.ReleaseImageError, match="object SHA differs"):
        release_images.verify_bound_sealed_lock(
            registry,
            authority_mode="published",
            source_sha=source_sha,
            release_tag=TAG,
            image_tag=TAG,
            version=VERSION,
            output=tmp_path / "forged-tag-object.json",
            bound_manifest_digest=manifest_digest,
            tag_object_sha="f" * 40,
            annotated_tag_object=proof,
        )

    with pytest.raises(release_images.ReleaseImageError, match="one exact sealed"):
        release_images.verify_bound_sealed_lock(
            registry,
            authority_mode="published",
            source_sha=source_sha,
            release_tag=TAG,
            image_tag=TAG,
            version=VERSION,
            output=tmp_path / "forged-manifest-binding.json",
            bound_manifest_digest="sha256:" + "e" * 64,
            tag_object_sha=tag_object_sha,
            annotated_tag_object=proof,
        )

    release_images.verify_bound_sealed_lock(
        registry,
        authority_mode="published",
        source_sha=source_sha,
        release_tag=TAG,
        image_tag=TAG,
        version=VERSION,
        output=tmp_path / "published.json",
        bound_manifest_digest=manifest_digest,
        tag_object_sha=tag_object_sha,
        annotated_tag_object=proof,
    )

    # Published deployment authority is the immutable remote annotated tag,
    # not a moving main branch.  A later main commit must not invalidate it.
    _git(work, "checkout", "main")
    (work / "later.txt").write_text("later main\n", encoding="utf-8")
    _git(work, "add", "later.txt")
    _git(work, "commit", "-m", "later main")
    _git(work, "push", "origin", "main")
    _git(work, "fetch", "origin", "+refs/heads/main:refs/remotes/origin/main")
    _git(work, "checkout", "--detach", source_sha)
    assert _git(work, "rev-parse", "refs/remotes/origin/main") != source_sha
    release_images.verify_bound_sealed_lock(
        registry,
        authority_mode="published",
        source_sha=source_sha,
        release_tag=TAG,
        image_tag=TAG,
        version=VERSION,
        output=tmp_path / "published-after-main-moved.json",
        bound_manifest_digest=manifest_digest,
        tag_object_sha=tag_object_sha,
        annotated_tag_object=proof,
    )

    moved = _add_digest_image(registry, "console", salt="published-drift")
    repository = release_images.image_repository("console")
    registry.refs[(repository, TAG)] = registry.refs[(repository, moved.digest)]
    with pytest.raises(
        release_images.ReleaseImageError, match="registry manifest changed"
    ):
        release_images.verify_bound_sealed_lock(
            registry,
            authority_mode="published",
            source_sha=source_sha,
            release_tag=TAG,
            image_tag=TAG,
            version=VERSION,
            output=tmp_path / "drift.json",
            bound_manifest_digest=manifest_digest,
            tag_object_sha=tag_object_sha,
            annotated_tag_object=proof,
        )

    registry.refs[(repository, TAG)] = registry.refs[
        (repository, release_images.candidate_tag(source_sha))
    ]
    registry.visibility["banxico"] = "public"
    with pytest.raises(release_images.ReleaseImageError, match="must be private"):
        release_images.verify_bound_sealed_lock(
            registry,
            authority_mode="published",
            source_sha=source_sha,
            release_tag=TAG,
            image_tag=TAG,
            version=VERSION,
            output=tmp_path / "privacy.json",
            bound_manifest_digest=manifest_digest,
            tag_object_sha=tag_object_sha,
            annotated_tag_object=proof,
        )

    registry.visibility["banxico"] = "private"
    registry.visibility["console"] = "private"
    with pytest.raises(
        release_images.ReleaseImageError, match="console must be public"
    ):
        release_images.verify_bound_sealed_lock(
            registry,
            authority_mode="published",
            source_sha=source_sha,
            release_tag=TAG,
            image_tag=TAG,
            version=VERSION,
            output=tmp_path / "public-policy.json",
            bound_manifest_digest=manifest_digest,
            tag_object_sha=tag_object_sha,
            annotated_tag_object=proof,
        )


def test_gcp_published_authority_uses_safe_portable_proof_without_dot_git(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    registry, _, manifest_digest = _assemble(tmp_path / "registry")
    for image in release_images.IMAGES:
        repository = release_images.image_repository(image.service)
        registry.refs[(repository, TAG)] = registry.refs[
            (repository, release_images.candidate_tag(SHA))
        ]
    raw = (
        f"object {SHA}\n"
        "type commit\n"
        f"tag {TAG}\n"
        "tagger Release Test <release@example.invalid> 0 +0000\n\n"
        "OMEGA release\n\n"
        f"{release_images.TAG_BINDING_PREFIX}{manifest_digest}\n"
    ).encode()
    envelope = b"tag " + str(len(raw)).encode() + b"\0" + raw
    tag_object_sha = hashlib.sha1(  # noqa: S324
        envelope, usedforsecurity=False
    ).hexdigest()
    proof_root = tmp_path / "release-authority"
    proof_parent = proof_root / SHA
    proof_parent.mkdir(parents=True, mode=0o700)
    proof_root.chmod(0o755)
    proof = proof_parent / release_images.GCP_RELEASE_TAG_PROOF_NAME
    proof.write_bytes(raw)
    proof.chmod(0o400)
    archive = tmp_path / "release-archive"
    archive.mkdir()
    (archive / "VERSION").write_text(f"{VERSION}\n", encoding="utf-8")
    assert not (archive / ".git").exists()
    monkeypatch.setattr(release_images, "GCP_RELEASE_TAG_PROOF_ROOT", proof_root)
    monkeypatch.setattr(release_images, "GCP_GHCR_AUTH_OWNER_UID", os.geteuid())
    monkeypatch.setattr(release_images, "GCP_GHCR_AUTH_OWNER_GID", os.getegid())

    output = tmp_path / "published-from-archive.json"
    release_images.verify_bound_sealed_lock(
        registry,
        authority_mode="published",
        source_sha=SHA,
        release_tag=TAG,
        image_tag=TAG,
        version=VERSION,
        output=output,
        bound_manifest_digest=manifest_digest,
        tag_object_sha=tag_object_sha,
        annotated_tag_object=proof,
    )
    assert json.loads(output.read_bytes())["tag_proof_sha256"] == _digest(raw)

    proof.chmod(0o660)
    with pytest.raises(release_images.ReleaseImageError, match="proof is unsafe"):
        release_images.verify_bound_sealed_lock(
            registry,
            authority_mode="published",
            source_sha=SHA,
            release_tag=TAG,
            image_tag=TAG,
            version=VERSION,
            output=tmp_path / "unsafe-mode.json",
            bound_manifest_digest=manifest_digest,
            tag_object_sha=tag_object_sha,
            annotated_tag_object=proof,
        )
    proof.chmod(0o400)
    proof.unlink()
    proof.symlink_to(tmp_path / "attacker-tag.object")
    with pytest.raises(release_images.ReleaseImageError, match="proof is unsafe"):
        release_images.verify_bound_sealed_lock(
            registry,
            authority_mode="published",
            source_sha=SHA,
            release_tag=TAG,
            image_tag=TAG,
            version=VERSION,
            output=tmp_path / "symlink.json",
            bound_manifest_digest=manifest_digest,
            tag_object_sha=tag_object_sha,
            annotated_tag_object=proof,
        )


def test_gcp_legacy_rollback_uses_backup_digest_and_image_id_without_tag(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    registry = FakeRegistry()
    images = {}
    expected_ids = {}
    for index, image in enumerate(release_images.IMAGES, start=1):
        record = _add_digest_image(registry, image.service, salt=f"legacy-{index}")
        image_id = f"sha256:{index:064x}"
        expected_ids[image.service] = image_id
        images[image.service] = {
            "configured_ref": (
                f"ghcr.io/{release_images.CANONICAL_OWNER}/{image.service}:{TAG}"
            ),
            "repo_digest": record.reference,
            "image_id": image_id,
        }
    snapshot_root = tmp_path / "snapshot-root"
    snapshot_root.mkdir(mode=0o700)
    snapshot = snapshot_root / "omega-gcp-image-preflight.A1b2C3"
    snapshot.mkdir(mode=0o700)
    monkeypatch.setattr(release_images, "GCP_ROLLBACK_SNAPSHOT_ROOT", snapshot_root)
    monkeypatch.setattr(
        release_images,
        "GCP_ROLLBACK_SNAPSHOT_ROOT_MODE",
        0o700,
    )
    monkeypatch.setattr(
        release_images, "GCP_GHCR_AUTH_OWNER_UID", snapshot.stat().st_uid
    )
    monkeypatch.setattr(
        release_images, "GCP_GHCR_AUTH_OWNER_GID", snapshot.stat().st_gid
    )
    runtime_document = {
        "schema_version": 2,
        "source_release": {"deploy_ref": SHA, "version": VERSION},
        "images": images,
        "secret_values_included": False,
    }
    runtime_images = snapshot / "rollback-runtime-images.json"
    runtime_images.write_text(
        json.dumps(runtime_document, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    runtime_images.chmod(0o600)
    output = tmp_path / "rollback-authority.json"

    release_images.verify_bound_sealed_lock(
        registry,
        authority_mode="legacy-rollback",
        source_sha=SHA,
        release_tag=TAG,
        image_tag=TAG,
        version=VERSION,
        output=output,
        runtime_images=runtime_images,
        legacy_tag_commit=SHA,
    )

    receipt = json.loads(output.read_bytes())
    assert receipt["manifest_digest"] is None
    assert receipt["tag_object_sha"] is None
    assert receipt["legacy_image_ids"] == expected_ids
    assert receipt["legacy_tag_commit"] == SHA
    assert len(receipt["images"]) == 15
    assert {row["service"] for row in receipt["images"]} == {
        image.service for image in release_images.IMAGES
    }
    assert {
        row["service"] for row in receipt["images"] if row["visibility"] == "private"
    } == release_images.PROTECTED_PRIVATE_PACKAGES
    assert all("@sha256:" in row["reference"] for row in receipt["images"])
    assert not any(reference == TAG for _, reference in registry.gets)

    with pytest.raises(
        release_images.ReleaseImageError, match="rollback image authority inputs"
    ):
        release_images.verify_bound_sealed_lock(
            registry,
            authority_mode="legacy-rollback",
            source_sha=SHA,
            release_tag=TAG,
            image_tag=TAG,
            version=VERSION,
            output=tmp_path / "mismatched-legacy-tag.json",
            runtime_images=runtime_images,
            legacy_tag_commit="5" * 40,
        )

    linked = snapshot / "linked-runtime-images.json"
    linked.symlink_to(runtime_images)
    with pytest.raises(release_images.ReleaseImageError, match="confined snapshot"):
        release_images._records_from_runtime_images(
            linked,
            source_sha=SHA,
            version=VERSION,
        )


def test_seal_uses_validated_receipts_without_rereading_mutable_service_tags(
    tmp_path: Path,
) -> None:
    registry, receipts = _digest_only_registry(tmp_path)
    records = release_images._load_digest_receipts(
        receipts,
        github_run_id=GITHUB_RUN_ID,
        github_run_attempt=GITHUB_RUN_ATTEMPT,
    )
    digest_manifests = {
        record.service: registry.refs[
            (release_images.image_repository(record.service), record.digest)
        ]
        for record in records
    }
    for record in records:
        registry.refs[
            (
                release_images.image_repository(record.service),
                release_images.candidate_tag(SHA),
            )
        ] = digest_manifests[record.service]

    original_get = registry.get_manifest

    def mutate_if_service_tag_is_reread(repository: str, reference: str):
        if reference == release_images.candidate_tag(SHA) and repository != (
            f"{release_images.CANONICAL_OWNER}/" f"{release_images.MANIFEST_PACKAGE}"
        ):
            attacker = _add_digest_image(
                registry, repository.rsplit("/", 1)[-1], salt="seal-race"
            )
            registry.refs[(repository, reference)] = registry.refs[
                (repository, attacker.digest)
            ]
            raise AssertionError("seal reread a mutable service candidate tag")
        return original_get(repository, reference)

    registry.get_manifest = mutate_if_service_tag_is_reread  # type: ignore[method-assign]
    output = tmp_path / "receipt-rooted-seal.json"
    release_images.seal_candidate(
        registry,
        source_sha=SHA,
        release_tag=TAG,
        version=VERSION,
        output=output,
        records=records,
        digest_manifests=digest_manifests,
        github_run_id=GITHUB_RUN_ID,
        github_run_attempt=GITHUB_RUN_ATTEMPT,
    )

    sealed = json.loads(output.read_bytes())
    assert [row["digest"] for row in sealed["images"]] == [
        record.digest for record in records
    ]


def test_new_manifest_package_is_verified_private_before_candidate_is_usable(
    tmp_path: Path,
) -> None:
    registry, receipts = _digest_only_registry(tmp_path)
    registry.visibility.pop(release_images.MANIFEST_PACKAGE)

    digest = _assemble_candidate(
        registry,
        source_sha=SHA,
        release_tag=TAG,
        receipts_dir=receipts,
        output=tmp_path / "new-package.json",
    )

    assert digest.startswith("sha256:")
    assert registry.visibility[release_images.MANIFEST_PACKAGE] == "private"


def test_sealed_candidate_rejects_any_later_tag_movement(tmp_path: Path) -> None:
    registry, _, manifest_digest = _assemble(tmp_path)
    registry.puts.clear()
    moved = _add_digest_image(registry, "console", salt="moved-after-seal")
    repository = release_images.image_repository("console")
    registry.refs[(repository, release_images.candidate_tag(SHA))] = registry.refs[
        (repository, moved.digest)
    ]

    with pytest.raises(
        release_images.ReleaseImageError,
        match="current candidate images differ|changed from its sealed digest",
    ):
        release_images.promote_one(
            registry,
            service="console",
            source_sha=SHA,
            release_tag=TAG,
            version=VERSION,
            bound_manifest_digest=manifest_digest,
        )
    assert not any(reference == TAG for _, reference, _ in registry.puts)


def test_candidate_matrix_rebuilds_digest_when_partial_tag_already_exists(
    tmp_path: Path,
) -> None:
    registry = FakeRegistry()
    record = _add_digest_image(registry, "console", salt="identical")
    registry.refs[
        (release_images.image_repository("console"), release_images.candidate_tag(SHA))
    ] = registry.refs[(release_images.image_repository("console"), record.digest)]

    assert release_images.candidate_state(
        registry,
        service="console",
        source_sha=SHA,
        release_tag=TAG,
        receipt=tmp_path / "console.json",
        github_run_id=GITHUB_RUN_ID,
        github_run_attempt=GITHUB_RUN_ATTEMPT,
    ) == ("missing", "")
    assert not (tmp_path / "console.json").exists()


def test_candidate_matrix_recovers_prior_intent_without_rebuild(
    tmp_path: Path,
) -> None:
    registry, receipts = _digest_only_registry(tmp_path / "first")
    intent_root = (
        tmp_path
        / "prior-intents"
        / f"candidate-intent-{GITHUB_RUN_ID}-{GITHUB_RUN_ATTEMPT}"
    )
    intent_root.mkdir(parents=True)
    intent = intent_root / "release-candidate-intent.json"
    release_images.prepare_candidate_intent(
        registry,
        source_sha=SHA,
        release_tag=TAG,
        receipts_dir=receipts,
        prior_intents_dir=tmp_path / "unused",
        output=intent,
        github_run_id=GITHUB_RUN_ID,
        github_run_attempt=GITHUB_RUN_ATTEMPT,
    )
    console = json.loads((receipts / "console.json").read_bytes())
    repository = release_images.image_repository("console")
    registry.refs[(repository, release_images.candidate_tag(SHA))] = registry.refs[
        (repository, console["digest"])
    ]
    recovered = tmp_path / "recovered-console.json"

    assert release_images.recover_candidate_receipt(
        registry,
        service="console",
        source_sha=SHA,
        release_tag=TAG,
        receipt=recovered,
        prior_intents_dir=tmp_path / "prior-intents",
        github_run_id=GITHUB_RUN_ID,
        github_run_attempt="2",
    ) == ("recovered", console["digest"])
    document = json.loads(recovered.read_bytes())
    assert document["github_run_attempt"] == "2"
    assert document["digest"] == console["digest"]

    registry.refs[(repository, release_images.candidate_tag(SHA))] = registry.refs[
        (repository, _add_digest_image(registry, "console", salt="collision").digest)
    ]
    with pytest.raises(
        release_images.ReleaseImageError,
        match="partial candidate tag differs|registry manifest changed",
    ):
        release_images.recover_candidate_receipt(
            registry,
            service="console",
            source_sha=SHA,
            release_tag=TAG,
            receipt=tmp_path / "must-not-recover.json",
            prior_intents_dir=tmp_path / "prior-intents",
            github_run_id=GITHUB_RUN_ID,
            github_run_attempt="2",
        )


def test_candidate_rerun_without_intent_fails_closed_before_rebuild(
    tmp_path: Path,
) -> None:
    registry = FakeRegistry()
    with pytest.raises(
        release_images.ReleaseImageError,
        match="cannot prove whether durable prior-attempt authority exists",
    ):
        release_images.recover_candidate_receipt(
            registry,
            service="console",
            source_sha=SHA,
            release_tag=TAG,
            receipt=tmp_path / "must-not-exist.json",
            prior_intents_dir=tmp_path / "missing-intents",
            github_run_id=GITHUB_RUN_ID,
            github_run_attempt="2",
        )
    assert not (tmp_path / "must-not-exist.json").exists()

    record = _add_digest_image(registry, "console", salt="orphan-partial")
    repository = release_images.image_repository("console")
    registry.refs[(repository, release_images.candidate_tag(SHA))] = registry.refs[
        (repository, record.digest)
    ]
    with pytest.raises(
        release_images.ReleaseImageError,
        match="cannot prove whether durable prior-attempt authority exists",
    ):
        release_images.recover_candidate_receipt(
            registry,
            service="console",
            source_sha=SHA,
            release_tag=TAG,
            receipt=tmp_path / "must-not-recover-orphan.json",
            prior_intents_dir=tmp_path / "missing-intents",
            github_run_id=GITHUB_RUN_ID,
            github_run_attempt="2",
        )


def test_candidate_intent_output_is_exclusive_and_never_replaces_a_name(
    tmp_path: Path,
) -> None:
    registry, receipts = _digest_only_registry(tmp_path / "registry")
    existing = tmp_path / "release-candidate-intent.json"
    existing.write_bytes(b"durable-original")
    with pytest.raises(
        release_images.ReleaseImageError, match="already exists or is unsafe"
    ):
        release_images.prepare_candidate_intent(
            registry,
            source_sha=SHA,
            release_tag=TAG,
            receipts_dir=receipts,
            prior_intents_dir=tmp_path / "absent",
            output=existing,
            github_run_id=GITHUB_RUN_ID,
            github_run_attempt="1",
        )
    assert existing.read_bytes() == b"durable-original"

    existing.unlink()
    victim = tmp_path / "victim"
    victim.write_bytes(b"do-not-replace")
    existing.symlink_to(victim)
    with pytest.raises(
        release_images.ReleaseImageError, match="already exists or is unsafe"
    ):
        release_images.prepare_candidate_intent(
            registry,
            source_sha=SHA,
            release_tag=TAG,
            receipts_dir=receipts,
            prior_intents_dir=tmp_path / "absent",
            output=existing,
            github_run_id=GITHUB_RUN_ID,
            github_run_attempt="1",
        )
    assert victim.read_bytes() == b"do-not-replace"


def test_unsealed_rerun_reuses_durable_intent_and_rejects_new_digest(
    tmp_path: Path,
) -> None:
    registry, first_receipts = _digest_only_registry(tmp_path / "first")
    prior_root = (
        tmp_path
        / "prior-intents"
        / f"candidate-intent-{GITHUB_RUN_ID}-{GITHUB_RUN_ATTEMPT}"
    )
    prior_root.mkdir(parents=True)
    durable = prior_root / "release-candidate-intent.json"
    release_images.prepare_candidate_intent(
        registry,
        source_sha=SHA,
        release_tag=TAG,
        receipts_dir=first_receipts,
        prior_intents_dir=tmp_path / "absent",
        output=durable,
        github_run_id=GITHUB_RUN_ID,
        github_run_attempt="1",
    )
    durable_bytes = durable.read_bytes()
    assert not any(
        reference == release_images.candidate_tag(SHA)
        for _repository, reference in registry.refs
    )

    second_receipts = tmp_path / "second" / "receipts"
    second_receipts.mkdir(parents=True)
    for path in first_receipts.iterdir():
        document = json.loads(path.read_bytes())
        document["github_run_attempt"] = "2"
        (second_receipts / path.name).write_bytes(_canonical(document))
    reused = tmp_path / "reused-intent.json"
    release_images.prepare_candidate_intent(
        registry,
        source_sha=SHA,
        release_tag=TAG,
        receipts_dir=second_receipts,
        prior_intents_dir=tmp_path / "prior-intents",
        output=reused,
        github_run_id=GITHUB_RUN_ID,
        github_run_attempt="2",
    )
    assert reused.read_bytes() == durable_bytes

    replacement = _add_digest_image(registry, "console", salt="rerun-replacement")
    release_images.write_digest_receipt(
        second_receipts / "console.json",
        replacement,
        github_run_id=GITHUB_RUN_ID,
        github_run_attempt="2",
    )
    with pytest.raises(
        release_images.ReleaseImageError,
        match="would replace durable prior-attempt authority",
    ):
        release_images.prepare_candidate_intent(
            registry,
            source_sha=SHA,
            release_tag=TAG,
            receipts_dir=second_receipts,
            prior_intents_dir=tmp_path / "prior-intents",
            output=tmp_path / "must-not-replace.json",
            github_run_id=GITHUB_RUN_ID,
            github_run_attempt="2",
        )
    assert not (tmp_path / "must-not-replace.json").exists()


@pytest.mark.parametrize(
    ("field", "value"),
    [("github_run_id", "987654322"), ("github_run_attempt", "2")],
)
def test_candidate_aggregation_rejects_mixed_workflow_run_receipts(
    tmp_path: Path, field: str, value: str
) -> None:
    registry, receipts = _digest_only_registry(tmp_path)
    path = receipts / "salesforce.json"
    document = json.loads(path.read_bytes())
    document[field] = value
    path.write_bytes(_canonical(document))

    with pytest.raises(
        release_images.ReleaseImageError, match="mix workflow runs or attempts"
    ):
        _assemble_candidate(
            registry,
            source_sha=SHA,
            release_tag=TAG,
            receipts_dir=receipts,
            output=tmp_path / "must-not-seal.json",
        )
    assert registry.puts == []


def test_protected_private_visibility_and_exact_oci_labels_are_hard_gates(
    tmp_path: Path,
) -> None:
    registry, receipts = _digest_only_registry(tmp_path)
    registry.visibility["banxico"] = "public"
    with pytest.raises(
        release_images.ReleaseImageError, match="banxico must be private"
    ):
        _assemble_candidate(
            registry,
            source_sha=SHA,
            release_tag=TAG,
            receipts_dir=receipts,
            output=tmp_path / "manifest.json",
        )
    assert registry.puts == []
    assert registry.blob_puts == []

    # The release inventory has exactly three private packages.  Any private
    # drift in the other twelve fails closed; the workflow never changes it.
    registry, receipts = _digest_only_registry(tmp_path / "non-protected-private")
    private_console = _add_digest_image(
        registry, "console", salt="private-console", visibility="private"
    )
    _write_receipt(receipts / "console.json", private_console)
    with pytest.raises(
        release_images.ReleaseImageError,
        match="non-protected package receipt is not public",
    ):
        _assemble_candidate(
            registry,
            source_sha=SHA,
            release_tag=TAG,
            receipts_dir=receipts,
            output=tmp_path / "non-protected-private.json",
        )
    assert registry.visibility["console"] == "private"

    registry, receipts = _digest_only_registry(tmp_path / "bad-label")
    bad = _add_digest_image(registry, "inegi", revision="4" * 40, salt="wrong-label")
    _write_receipt(receipts / "inegi.json", bad)
    with pytest.raises(
        release_images.ReleaseImageError, match="incorrect OCI provenance"
    ):
        _assemble_candidate(
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
    registry.gets.clear()

    for image in release_images.IMAGES:
        release_images.promote_one(
            registry,
            service=image.service,
            source_sha=SHA,
            release_tag=TAG,
            version=VERSION,
            bound_manifest_digest=manifest_digest,
        )

    for image in release_images.IMAGES:
        repository = release_images.image_repository(image.service)
        digest = registry.refs[(repository, release_images.candidate_tag(SHA))].headers[
            "docker-content-digest"
        ]
        assert (repository, digest) in registry.gets

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

    portable_proof = tmp_path / "annotated-tag.object"
    version, parsed_binding, tag_object_sha = release_images.verify_release_tag(
        repo_root=work,
        source_sha=sha,
        release_tag=TAG,
        repository=release_images.CANONICAL_REPOSITORY,
        portable_tag_output=portable_proof,
    )
    assert version == VERSION
    assert parsed_binding == binding
    assert tag_object_sha == _git(work, "rev-parse", f"refs/tags/{TAG}")
    assert (
        portable_proof.read_bytes()
        == subprocess.run(
            ["git", "cat-file", "tag", f"refs/tags/{TAG}"],
            cwd=work,
            check=True,
            capture_output=True,
        ).stdout
    )
    assert portable_proof.stat().st_mode & 0o777 == 0o600

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


def test_private_remote_tag_revalidation_uses_confined_github_api_token(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    expected_tag_object = "a" * 40
    secret = "workflow-token-material-12345"
    requests: list[urllib.request.Request] = []
    handlers: list[object] = []

    class Response:
        status = 200
        headers: dict[str, str] = {}

        def __init__(self, body: bytes) -> None:
            self.body = body

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def read(self, maximum: int) -> bytes:
            assert maximum == release_images.MAX_API_JSON_BYTES + 1
            return self.body

    class Opener:
        tag_object = expected_tag_object

        def open(self, request: urllib.request.Request, *, timeout: int):
            assert timeout == 30
            requests.append(request)
            return Response(
                _canonical(
                    {
                        "object": {"sha": self.tag_object, "type": "tag"},
                        "ref": f"refs/tags/{TAG}",
                    }
                )
            )

    opener = Opener()

    def fake_build_opener(*supplied: object) -> Opener:
        handlers.extend(supplied)
        return opener

    monkeypatch.setenv("GITHUB_TOKEN", secret)
    monkeypatch.setenv("HTTPS_PROXY", "http://127.0.0.1:9")
    monkeypatch.setattr(urllib.request, "build_opener", fake_build_opener)

    assert (
        release_images.verify_remote_tag_object(
            repository=release_images.CANONICAL_REPOSITORY,
            release_tag=TAG,
            expected_tag_object_sha=expected_tag_object,
        )
        == expected_tag_object
    )
    assert len(handlers) == 3
    assert isinstance(handlers[0], urllib.request.ProxyHandler)
    assert handlers[0].proxies == {}
    assert isinstance(handlers[1], urllib.request.HTTPSHandler)
    assert isinstance(handlers[2], release_images._SafeRedirectHandler)
    assert len(requests) == 1
    assert requests[0].full_url == (
        "https://api.github.com/repos/"
        "emmanuelnavaromero02-commits/CONSOLA-V1/git/ref/tags/v1.45.207-beta"
    )
    assert requests[0].get_header("Authorization") == f"Bearer {secret}"

    opener.tag_object = "b" * 40
    with pytest.raises(
        release_images.ReleaseImageError, match="changed or disappeared"
    ) as caught:
        release_images.verify_remote_tag_object(
            repository=release_images.CANONICAL_REPOSITORY,
            release_tag=TAG,
            expected_tag_object_sha=expected_tag_object,
        )
    assert secret not in str(caught.value)


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


def test_registry_client_disables_environment_proxies_before_safe_redirects(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    for variable in ("ALL_PROXY", "HTTP_PROXY", "HTTPS_PROXY"):
        monkeypatch.setenv(variable, "http://127.0.0.1:9")
    monkeypatch.setenv("NO_PROXY", "")
    keylog = tmp_path / "hostile-tls-keys.log"
    monkeypatch.setenv("SSL_CERT_FILE", str(tmp_path / "hostile-ca.pem"))
    monkeypatch.setenv("SSL_CERT_DIR", str(tmp_path / "hostile-ca-dir"))
    monkeypatch.setenv("SSLKEYLOGFILE", str(keylog))

    supplied_handlers: list[object] = []
    real_build_opener = urllib.request.build_opener

    def capture_build_opener(*handlers: object):
        supplied_handlers.extend(handlers)
        return real_build_opener(*handlers)

    monkeypatch.setattr(urllib.request, "build_opener", capture_build_opener)
    client = release_images.RegistryClient(actor="release-reader", token="token")

    assert len(supplied_handlers) == 3
    proxy_handler, https_handler, redirect_handler = supplied_handlers
    assert isinstance(proxy_handler, urllib.request.ProxyHandler)
    assert proxy_handler.proxies == {}
    assert isinstance(https_handler, urllib.request.HTTPSHandler)
    assert https_handler._context is client._ssl_context
    assert client._ssl_context.keylog_filename is None
    assert isinstance(redirect_handler, release_images._SafeRedirectHandler)
    assert client._opener is not None
    assert not keylog.exists()


def test_registry_client_rejects_writable_or_linked_ca_bundle(tmp_path: Path) -> None:
    hostile = tmp_path / "writable-ca.pem"
    hostile.write_bytes(release_images.CANONICAL_CA_BUNDLE.read_bytes())
    hostile.chmod(0o666)
    with pytest.raises(release_images.ReleaseImageError, match="CA bundle is unsafe"):
        release_images.RegistryClient(
            actor="release-reader",
            token="workflow-token-material-12345",
            ca_bundle=hostile,
        )

    hostile.chmod(0o644)
    linked = tmp_path / "linked-ca.pem"
    linked.symlink_to(hostile)
    with pytest.raises(release_images.ReleaseImageError, match="CA bundle is unsafe"):
        release_images.RegistryClient(
            actor="release-reader",
            token="workflow-token-material-12345",
            ca_bundle=linked,
        )


def test_existing_blob_head_allows_exact_empty_body_without_reupload() -> None:
    requests: list[urllib.request.Request] = []
    payload = b"already-present"
    digest = _digest(payload)

    class EmptyHeadResponse:
        status = 200
        headers = {"Docker-Content-Digest": digest}

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def read(self, maximum: int) -> bytes:
            assert maximum == release_images.MAX_OCI_JSON_BYTES + 1
            return b""

    class HeadOnlyOpener:
        def open(self, request, *, timeout):
            assert timeout == 45
            assert request.get_method() == "HEAD"
            requests.append(request)
            return EmptyHeadResponse()

    client = release_images.RegistryClient(
        actor="release-reader", token="workflow-token-material-12345"
    )
    client._opener = HeadOnlyOpener()

    assert client.put_blob("owner/package", payload) == digest
    assert len(requests) == 1


@pytest.mark.parametrize("digest_header", [None, f"sha256:{'0' * 64}"])
def test_existing_blob_head_rejects_absent_or_mismatched_digest(
    digest_header: str | None,
) -> None:
    class EmptyHeadResponse:
        status = 200
        headers = (
            {} if digest_header is None else {"Docker-Content-Digest": digest_header}
        )

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def read(self, maximum: int) -> bytes:
            assert maximum == release_images.MAX_OCI_JSON_BYTES + 1
            return b""

    class HeadOnlyOpener:
        def open(self, request, *, timeout):
            assert timeout == 45
            assert request.get_method() == "HEAD"
            return EmptyHeadResponse()

    client = release_images.RegistryClient(
        actor="release-reader", token="workflow-token-material-12345"
    )
    client._opener = HeadOnlyOpener()

    with pytest.raises(release_images.ReleaseImageError, match="digest header"):
        client.put_blob("owner/package", b"already-present")


@pytest.mark.parametrize(
    ("method", "status", "url"),
    [
        ("POST", 202, "https://ghcr.io/v2/owner/package/blobs/uploads/"),
        ("PUT", 201, "https://ghcr.io/v2/owner/package/blobs/uploads/id?digest=x"),
        ("PUT", 201, "https://ghcr.io/v2/owner/package/manifests/candidate"),
    ],
)
def test_registry_writes_accept_only_expected_empty_success_responses(
    method: str, status: int, url: str
) -> None:
    class EmptyWriteResponse:
        headers: dict[str, str] = {}

        def __init__(self, response_status: int) -> None:
            self.status = response_status

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def read(self, maximum: int) -> bytes:
            assert maximum == release_images.MAX_OCI_JSON_BYTES + 1
            return b""

    class WriteOpener:
        def __init__(self, response_status: int) -> None:
            self.response_status = response_status

        def open(self, request, *, timeout):
            assert timeout == 45
            assert request.get_method() == method
            return EmptyWriteResponse(self.response_status)

    client = release_images.RegistryClient(
        actor="release-reader", token="workflow-token-material-12345"
    )
    client._opener = WriteOpener(status)
    response = client._open(
        method,
        url,
        body=b"payload",
        repository="owner/package",
        expected_status=status,
    )
    assert response is not None
    assert response.status == status
    assert response.body == b""

    client._opener = WriteOpener(200)
    with pytest.raises(release_images.ReleaseImageError, match="unexpected HTTP"):
        client._open(
            method,
            url,
            body=b"payload",
            repository="owner/package",
            expected_status=status,
        )


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
            f'Bearer realm="{realm}",service="ghcr.io",scope="repository:o/i:pull"',
            repository="o/i",
            method="GET",
        )


def test_bearer_challenge_accepts_only_exact_ghcr_token_endpoint() -> None:
    realm, params = release_images._parse_bearer_challenge(
        'Bearer realm="https://ghcr.io/token",service="ghcr.io",scope="repository:o/i:pull"',
        repository="o/i",
        method="GET",
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
        "verify-visibility-policy",
        "check-candidate",
        "verify-built-digest",
        "recover-candidate-receipt",
        "prepare-candidate-intent",
        "assemble-candidate",
        "verify-release-tag",
        "verify-remote-tag-object",
        "promotion-preflight",
        "promote-one",
        "verify-release",
        "verify-bound-sealed-lock",
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
    assert "Require an unsealed candidate namespace for this attempt" in candidate
    assert "Reuse only" not in candidate
    assert '--github-run-id "$GITHUB_RUN_ID"' in candidate
    assert '--github-run-attempt "$GITHUB_RUN_ATTEMPT"' in candidate
    assert (
        "candidate-digest-${{ github.run_id }}-${{ github.run_attempt }}-*" in candidate
    )
    assert (
        "release-candidate-${{ needs.validate-candidate.outputs.source_sha }}-"
        "${{ github.run_attempt }}" in candidate
    )
    assert (
        "--receipt"
        in candidate.split("check-candidate", 1)[1].split(
            "docker/setup-buildx-action", 1
        )[0]
    )
    assert "prepare-candidate-intent" in candidate
    assert "recover-candidate-receipt" in candidate
    assert candidate.count("verify-visibility-policy") == 4
    assert candidate.index(
        "Revalidate exact GHCR visibility immediately before digest push"
    ) < candidate.index("Build and push ${{ matrix.service }} by digest only")
    assert candidate.index(
        "Verify pushed digest and write canonical receipt"
    ) < candidate.index("Revalidate exact GHCR visibility after digest push")
    assert "continue-on-error: true" not in candidate
    assert (
        "candidate-intent-${{ github.run_id }}-${{ github.run_attempt }}" in candidate
    )
    assert candidate.index(
        "Prepare durable pre-seal candidate intent"
    ) < candidate.index("Aggregate 15 intent-bound digests")
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
    assert (
        "release-promotion-preflight-${{ steps.tag.outputs.source_sha }}-"
        "${{ github.run_attempt }}" in release
    )
    assert "release-images-${{ github.ref_name }}-${{ github.run_attempt }}" in release
    assert "release-playwright-report-${{ github.run_attempt }}" in release
    assert (
        "release-tag-proof-${{ steps.tag.outputs.source_sha }}-"
        "${{ github.run_attempt }}" in release
    )
    assert "origin/main" not in release
    assert "git ls-remote" not in release
    assert release.count("verify-remote-tag-object") == 2
    assert release.count("EXPECTED_TAG_OBJECT_SHA") >= 4

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

    for workflow in (candidate_doc, release_doc):
        for job in workflow["jobs"].values():
            for step in job.get("steps", []):
                if str(step.get("uses", "")).startswith("actions/upload-artifact@"):
                    assert "${{ github.run_attempt }}" in step["with"]["name"]

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

    assert {
        name: job["timeout-minutes"] for name, job in candidate_doc["jobs"].items()
    } == {
        "validate-candidate": 10,
        "build-candidate-digests": 120,
        "aggregate-and-seal-candidate": 30,
    }
    for name in (
        "detect-release-changes",
        "validate-release",
        "full-stack-release-gate",
        "verify-tag-and-candidate",
        "build-and-push",
        "verify-release-images",
    ):
        assert isinstance(release_doc["jobs"][name].get("timeout-minutes"), int)

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
        "persist-credentials": False,
        "ref": "${{ needs.verify-tag-and-candidate.outputs.source_sha }}",
    }

    # Package mutation is absent globally and granted only to the two jobs that
    # build digest content or aggregate exact candidate tags.
    assert candidate_doc.get("permissions") == {"contents": "read"}
    assert candidate_validate["permissions"] == {
        "contents": "read",
        "packages": "read",
    }
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
        "persist-credentials": False,
        "ref": "${{ needs.verify-tag-and-candidate.outputs.source_sha }}",
    }
    assert "tag_object_sha" in release_gate["outputs"]
    promote_run = release_promote["steps"][-1]["run"]
    verify_run = release_verify["steps"][-2]["run"]
    for run, command in (
        (promote_run, "python3 scripts/release_images.py promote-one"),
        (verify_run, "python3 scripts/release_images.py verify-release"),
    ):
        mutation = run.index(command)
        assert run.count("revalidate_remote_tag") == 3
        assert run.index("revalidate_remote_tag\n") < mutation
        assert run.rindex("revalidate_remote_tag") > mutation
        assert '--expected-tag-object-sha "$EXPECTED_TAG_OBJECT_SHA"' in run
    assert (
        release_doc["jobs"]["build-and-push"]["strategy"]["matrix"]["include"]
        == expected_matrix
    )


def test_ci_routes_and_scans_release_authority_implementation() -> None:
    lint = (REPO / ".github/workflows/lint.yml").read_text(encoding="utf-8")
    security = (REPO / ".github/workflows/security.yml").read_text(encoding="utf-8")
    control_room = (REPO / ".github/workflows/control-room-postgres-rls.yml").read_text(
        encoding="utf-8"
    )

    python_targets = (
        "scripts/release_images.py",
        "infra/terraform-gcp/release/publish-image-lock.py",
        "infra/terraform-gcp/release/secure-ghcr-session.py",
        "infra/terraform-gcp/release/validate-image-authority.py",
        "infra/terraform-gcp/release/validate-lock-output.py",
    )
    for target in python_targets:
        assert target in lint
        assert security.count(target) == 2

    for target in (
        "infra/terraform-gcp/release/ghcr-auth-run.sh",
        "infra/terraform-gcp/release/preflight-release-images.sh",
    ):
        assert target in lint
    assert "bash -n" in lint
    assert "tests/test_release_image_promotion.py" in control_room
