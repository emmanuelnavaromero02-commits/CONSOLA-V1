"""Adversarial contracts for recoverable OCI release promotion."""

from __future__ import annotations

import copy
import hashlib
import io
import json
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlsplit
from zipfile import ZIP_STORED, ZipFile, ZipInfo

import pytest

from scripts.release_image_promotion import (
    ArtifactClient,
    CANONICAL_SERVICES,
    EXPECTED_INHERITED_LABELS,
    GHCR_ORIGIN,
    GITHUB_API,
    IN_TOTO,
    INTENT_ARTIFACT,
    INTENT_FILENAME,
    OCI_ATTESTATION,
    OCI_CONFIG,
    OCI_EMPTY,
    OCI_INDEX,
    OCI_MANIFEST,
    PromotionError,
    RegistryClient,
    ReleaseIdentity,
    _json_bytes,
    candidate_artifact_name,
    candidate_receipt_filename,
    confirm_intent,
    load_receipt,
    prepare_intent,
    promote,
    verify_candidate,
    verify_final,
)

REPO = Path(__file__).resolve().parents[1]
WORKFLOW = REPO / ".github/workflows/release.yml"


def _digest(body: bytes) -> str:
    return f"sha256:{hashlib.sha256(body).hexdigest()}"


def _json(value: object) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":")).encode()


def _descriptor(body: bytes, media_type: str, **extra: object) -> dict[str, object]:
    return {
        "mediaType": media_type,
        "digest": _digest(body),
        "size": len(body),
        **extra,
    }


def _artifact_zip(filename: str, payload: bytes) -> bytes:
    output = io.BytesIO()
    with ZipFile(output, "w", compression=ZIP_STORED) as bundle:
        info = ZipInfo(filename, date_time=(2026, 1, 1, 0, 0, 0))
        info.compress_type = ZIP_STORED
        info.external_attr = 0o100644 << 16
        bundle.writestr(info, payload)
    return output.getvalue()


class FakeReleaseHTTP:
    def __init__(self, identity: ReleaseIdentity) -> None:
        self.identity = identity
        self.manifests: dict[tuple[str, str], tuple[bytes, str]] = {}
        self.blobs: dict[str, bytes] = {}
        self.artifacts: dict[str, dict[str, object]] = {}
        self.archives: dict[int, bytes] = {}
        self.next_artifact_id = 1
        self.put_calls: list[tuple[str, str]] = []
        self.overwrite_calls: list[tuple[str, str]] = []
        self.calls: list[tuple[str, str, dict[str, str]]] = []
        self.manifest_override: dict[tuple[str, str], tuple[int, bytes, dict[str, str]]] = {}
        for service in CANONICAL_SERVICES:
            self._add_candidate(service)

    def _add_candidate(self, service: str) -> str:
        annotations = self.identity.annotations(service)
        config_labels = {
            **EXPECTED_INHERITED_LABELS.get(service, {}),
            **annotations,
        }
        config = _json(
            {
                "architecture": "amd64",
                "os": "linux",
                "config": {"Labels": config_labels},
            }
        )
        config_descriptor = _descriptor(config, OCI_CONFIG)
        layer = b"synthetic-layer"
        runnable = _json(
            {
                "schemaVersion": 2,
                "mediaType": OCI_MANIFEST,
                "annotations": annotations,
                "config": config_descriptor,
                "layers": [_descriptor(layer, "application/vnd.oci.image.layer.v1.tar")],
            }
        )
        runnable_descriptor = _descriptor(
            runnable,
            OCI_MANIFEST,
            platform={"architecture": "amd64", "os": "linux"},
            annotations=annotations,
        )
        statement = _json(
            {
                "_type": "https://in-toto.io/Statement/v0.1",
                "predicateType": "https://slsa.dev/provenance/v1",
                "subject": [
                    {
                        "name": self.identity.image(service),
                        "digest": {
                            "sha256": runnable_descriptor["digest"].split(":", 1)[1]
                        },
                    }
                ],
                "predicate": {
                    "buildDefinition": {
                        "externalParameters": {
                            "root": {
                                "request": {
                                    "args": [
                                        f"vcs:source={self.identity.source_url}",
                                        f"vcs:revision={self.identity.source_sha}",
                                    ]
                                }
                            }
                        }
                    },
                    "runDetails": {
                        "builder": {
                            "id": (
                                f"{self.identity.source_url}/actions/runs/"
                                f"{self.identity.run_id}/attempts/2"
                            )
                        }
                    },
                },
            }
        )
        empty = b"{}"
        attestation = _json(
            {
                "schemaVersion": 2,
                "mediaType": OCI_MANIFEST,
                "artifactType": OCI_ATTESTATION,
                "config": _descriptor(empty, OCI_EMPTY),
                "layers": [_descriptor(statement, IN_TOTO)],
                "subject": {
                    key: runnable_descriptor[key]
                    for key in ("mediaType", "digest", "size")
                },
            }
        )
        attestation_descriptor = _descriptor(
            attestation,
            OCI_MANIFEST,
            platform={"architecture": "unknown", "os": "unknown"},
            annotations={
                "vnd.docker.reference.digest": runnable_descriptor["digest"],
                "vnd.docker.reference.type": "attestation-manifest",
            },
        )
        index = _json(
            {
                "schemaVersion": 2,
                "mediaType": OCI_INDEX,
                "annotations": annotations,
                "manifests": [runnable_descriptor, attestation_descriptor],
            }
        )
        for body, media_type in (
            (index, OCI_INDEX),
            (runnable, OCI_MANIFEST),
            (attestation, OCI_MANIFEST),
        ):
            self.manifests[(service, _digest(body))] = (body, media_type)
        for body in (config, empty, statement):
            self.blobs[_digest(body)] = body
        return _digest(index)

    def candidate_digest(self, service: str) -> str:
        matches = [
            reference
            for (candidate_service, reference), (body, media_type) in self.manifests.items()
            if candidate_service == service
            and media_type == OCI_INDEX
            and json.loads(body).get("annotations")
            == self.identity.annotations(service)
        ]
        assert len(matches) == 1
        return matches[0]

    def add_artifact(self, name: str, filename: str, payload: bytes) -> None:
        archive = _artifact_zip(filename, payload)
        artifact_id = self.next_artifact_id
        self.next_artifact_id += 1
        self.archives[artifact_id] = archive
        self.artifacts[name] = {
            "id": artifact_id,
            "name": name,
            "expired": False,
            "size_in_bytes": len(archive),
            "digest": _digest(archive),
            "workflow_run": {
                "id": self.identity.run_id,
                "head_sha": self.identity.source_sha,
            },
        }

    def add_receipts(self, registry: RegistryClient) -> list[dict[str, object]]:
        receipts = []
        for service in CANONICAL_SERVICES:
            receipt = verify_candidate(
                registry, service=service, digest=self.candidate_digest(service)
            )
            receipts.append(receipt)
            self.add_artifact(
                candidate_artifact_name(service),
                candidate_receipt_filename(service),
                _json_bytes(receipt),
            )
        return receipts

    def __call__(
        self,
        method: str,
        url: str,
        headers: dict[str, str],
        body: bytes | None,
        _timeout: float,
        _limit: int,
    ):
        from scripts.release_image_promotion import HttpResult

        headers = dict(headers)
        self.calls.append((method, url, headers))
        parts = urlsplit(url)
        if url.startswith(f"{GHCR_ORIGIN}/token?"):
            return HttpResult(200, b'{"token":"registry-token"}', {"Content-Type": "application/json"})
        if parts.hostname == "pkg-containers.githubusercontent.com":
            assert "Authorization" not in headers
            digest = unquote(parts.path.lstrip("/"))
            blob = self.blobs.get(digest)
            if blob is None:
                return HttpResult(404, b"", {})
            return HttpResult(200, blob, {"Content-Type": "application/octet-stream"})
        if url.startswith(f"{GHCR_ORIGIN}/v2/"):
            path = unquote(parts.path)
            match = __import__("re").fullmatch(
                r"/v2/[^/]+/([^/]+)/(manifests|blobs)/(.+)", path
            )
            assert match, path
            service, kind, reference = match.groups()
            if kind == "blobs":
                assert headers.get("Authorization") == "Bearer registry-token"
                return HttpResult(
                    307,
                    b"",
                    {
                        "Location": (
                            "https://pkg-containers.githubusercontent.com/"
                            f"{reference}"
                        )
                    },
                )
            override = self.manifest_override.get((service, reference))
            if override is not None:
                status, override_body, override_headers = override
                return HttpResult(status, override_body, override_headers)
            if method == "PUT":
                assert body is not None
                if (service, reference) in self.manifests:
                    self.overwrite_calls.append((service, reference))
                self.manifests[(service, reference)] = (
                    body,
                    headers["Content-Type"],
                )
                self.put_calls.append((service, reference))
                return HttpResult(
                    201,
                    b"",
                    {"Docker-Content-Digest": _digest(body)},
                )
            manifest = self.manifests.get((service, reference))
            if manifest is None:
                return HttpResult(
                    404,
                    b'{"errors":[{"code":"MANIFEST_UNKNOWN"}]}',
                    {"Content-Type": "application/json"},
                )
            manifest_body, media_type = manifest
            return HttpResult(
                200,
                manifest_body,
                {
                    "Content-Type": media_type,
                    "Docker-Content-Digest": _digest(manifest_body),
                },
            )
        if url.startswith(f"{GITHUB_API}/repos/") and "/actions/runs/" in url:
            query = parse_qs(parts.query)
            name = query.get("name", [""])[0]
            artifact = self.artifacts.get(name)
            values = [] if artifact is None else [artifact]
            return HttpResult(
                200,
                _json({"total_count": len(values), "artifacts": values}),
                {"Content-Type": "application/vnd.github+json"},
            )
        if url.startswith(f"{GITHUB_API}/repos/") and "/actions/artifacts/" in url:
            artifact_id = int(parts.path.split("/")[-2])
            return HttpResult(
                302,
                b"",
                {
                    "Location": (
                        "https://omega-results.blob.core.windows.net/"
                        f"{artifact_id}"
                    )
                },
            )
        if parts.hostname == "omega-results.blob.core.windows.net":
            assert "Authorization" not in headers
            archive = self.archives[int(parts.path.lstrip("/"))]
            return HttpResult(200, archive, {"Content-Type": "application/zip"})
        raise AssertionError((method, url))


@pytest.fixture
def identity() -> ReleaseIdentity:
    return ReleaseIdentity.create(
        repository="omega-owner/omega",
        owner="omega-owner",
        source_sha="a" * 40,
        release_tag="v1.46.0-rc.1",
        run_id=99123,
    )


@pytest.fixture
def release_system(identity: ReleaseIdentity):
    fake = FakeReleaseHTTP(identity)
    registry = RegistryClient(
        identity=identity,
        username="omega-bot",
        github_token="github-token",
        transport=fake,
    )
    artifacts = ArtifactClient(
        identity=identity,
        github_token="github-token",
        transport=fake,
    )
    return fake, registry, artifacts


def test_candidate_verifies_index_config_and_attestation_with_safe_blob_redirect(
    release_system,
):
    fake, registry, _artifacts = release_system
    receipt = verify_candidate(
        registry, service="console", digest=fake.candidate_digest("console")
    )

    assert receipt["repository"] == "omega-owner/omega"
    assert receipt["run_id"] == 99123
    assert receipt["release_tag"] == "v1.46.0-rc.1"
    redirected = [
        headers
        for _method, url, headers in fake.calls
        if urlsplit(url).hostname == "pkg-containers.githubusercontent.com"
    ]
    assert redirected
    assert all("Authorization" not in headers for headers in redirected)


def test_candidate_rejects_any_top_level_release_annotation_drift(release_system):
    fake, registry, _artifacts = release_system
    digest = fake.candidate_digest("console")
    body, media_type = fake.manifests.pop(("console", digest))
    index = json.loads(body)
    index["annotations"]["org.opencontainers.image.version"] = "v9.9.9"
    changed = _json(index)
    changed_digest = _digest(changed)
    fake.manifests[("console", changed_digest)] = (changed, media_type)

    with pytest.raises(PromotionError, match="index annotations"):
        verify_candidate(registry, service="console", digest=changed_digest)


def test_candidate_requires_exact_config_label_allowlist(release_system):
    fake, registry, _artifacts = release_system
    original = registry.get_blob

    def injected_labels(service, descriptor):
        body = original(service, descriptor)
        if descriptor.get("mediaType") == OCI_CONFIG:
            config = json.loads(body)
            config["config"]["Labels"]["unapproved.extra"] = "bypass"
            return _json(config)
        return body

    registry.get_blob = injected_labels
    with pytest.raises(PromotionError, match="labels are not exact"):
        verify_candidate(
            registry, service="console", digest=fake.candidate_digest("console")
        )


@pytest.mark.parametrize("status", [301, 302, 307, 308])
def test_manifest_lookup_never_follows_redirects(release_system, status):
    fake, registry, _artifacts = release_system
    tag = registry.identity.release_tag
    fake.manifest_override[("console", tag)] = (
        status,
        b"",
        {"Location": "https://pkg-containers.githubusercontent.com/confused"},
    )

    with pytest.raises(PromotionError, match=f"HTTP {status}"):
        registry.get_manifest("console", tag, allow_absent=True)


@pytest.mark.parametrize("status", [401, 403, 429, 500, 503])
def test_tag_absence_fails_closed_on_auth_rate_limit_and_server_errors(
    release_system, status
):
    fake, registry, _artifacts = release_system
    tag = registry.identity.release_tag
    fake.manifest_override[("console", tag)] = (
        status,
        b'{"errors":[]}',
        {"Content-Type": "application/json"},
    )

    with pytest.raises(PromotionError, match=f"HTTP {status}"):
        registry.get_manifest("console", tag, allow_absent=True)


@pytest.mark.parametrize(
    "body,content_type",
    [
        (b"not json", "application/json"),
        (b'{"errors":[{"code":"DENIED"}]}', "application/json"),
        (b'{"errors":[{"code":"MANIFEST_UNKNOWN"}]}', "text/plain"),
        (b'{"errors":[]}', "application/json"),
    ],
)
def test_only_structured_manifest_unknown_is_absence(
    release_system, body, content_type
):
    fake, registry, _artifacts = release_system
    tag = registry.identity.release_tag
    fake.manifest_override[("console", tag)] = (
        404,
        body,
        {"Content-Type": content_type},
    )

    with pytest.raises(PromotionError):
        registry.get_manifest("console", tag, allow_absent=True)


def test_actual_buildx_not_found_prose_has_zero_release_authority(release_system):
    actual_buildx_output = (
        "ERROR: ghcr.io/omega-owner/console:v1.46.0-rc.1: not found"
    )
    fake, registry, _artifacts = release_system

    assert "not found" in actual_buildx_output
    assert "not found" not in WORKFLOW.read_text(encoding="utf-8").lower()
    assert registry.get_manifest(
        "console", registry.identity.release_tag, allow_absent=True
    ) is None


def test_matrix_candidate_operations_cannot_create_release_or_sha_tags(
    release_system,
):
    fake, registry, artifacts = release_system
    assert load_receipt(artifacts, registry, service="console") is None
    verify_candidate(
        registry, service="console", digest=fake.candidate_digest("console")
    )

    assert fake.put_calls == []
    assert ("console", registry.identity.release_tag) not in fake.manifests
    assert (
        "console",
        f"sha-{registry.identity.source_sha}",
    ) not in fake.manifests


def test_artifact_download_redirect_strips_github_token(release_system):
    fake, registry, artifacts = release_system
    receipt = verify_candidate(
        registry, service="console", digest=fake.candidate_digest("console")
    )
    fake.add_artifact(
        candidate_artifact_name("console"),
        candidate_receipt_filename("console"),
        _json_bytes(receipt),
    )

    assert load_receipt(artifacts, registry, service="console") == receipt
    redirected = [
        headers
        for _method, url, headers in fake.calls
        if urlsplit(url).hostname == "omega-results.blob.core.windows.net"
    ]
    assert redirected
    assert all("Authorization" not in headers for headers in redirected)


@pytest.mark.parametrize("status", [401, 403, 429, 500, 503])
def test_artifact_list_auth_rate_limit_and_server_errors_fail_closed(
    release_system, status
):
    fake, registry, artifacts = release_system
    original = fake.__call__

    def error(method, url, headers, body, timeout, limit):
        if "/actions/runs/" in url:
            from scripts.release_image_promotion import HttpResult

            return HttpResult(
                status,
                b'{"message":"ambiguous"}',
                {"Content-Type": "application/vnd.github+json"},
            )
        return original(method, url, headers, body, timeout, limit)

    artifacts.transport = error
    with pytest.raises(PromotionError, match=f"HTTP {status}"):
        load_receipt(artifacts, registry, service="console")


@pytest.mark.parametrize(
    "location",
    [
        "http://omega-results.blob.core.windows.net/1",
        "https://omega-results.blob.core.windows.net.evil.example/1",
        "https://user@omega-results.blob.core.windows.net/1",
        "https://omega-results.blob.core.windows.net/1#fragment",
    ],
)
def test_artifact_archive_redirect_allowlist_is_exact(release_system, location):
    fake, registry, artifacts = release_system
    receipt = verify_candidate(
        registry, service="console", digest=fake.candidate_digest("console")
    )
    fake.add_artifact(
        candidate_artifact_name("console"),
        candidate_receipt_filename("console"),
        _json_bytes(receipt),
    )
    original = fake.__call__

    def redirect(method, url, headers, body, timeout, limit):
        result = original(method, url, headers, body, timeout, limit)
        if "/actions/artifacts/" in url:
            return type(result)(302, b"", {"Location": location})
        return result

    artifacts.transport = redirect
    with pytest.raises(PromotionError, match="redirect target"):
        load_receipt(artifacts, registry, service="console")


@pytest.mark.parametrize("attack", ["cross-run", "expired", "tampered", "duplicate"])
def test_receipt_artifact_attacks_fail_closed(release_system, attack):
    fake, registry, artifacts = release_system
    receipt = verify_candidate(
        registry, service="console", digest=fake.candidate_digest("console")
    )
    name = candidate_artifact_name("console")
    fake.add_artifact(name, candidate_receipt_filename("console"), _json_bytes(receipt))
    if attack == "cross-run":
        fake.artifacts[name]["workflow_run"] = {
            "id": registry.identity.run_id + 1,
            "head_sha": registry.identity.source_sha,
        }
    elif attack == "expired":
        fake.artifacts[name]["expired"] = True
    elif attack == "tampered":
        fake.artifacts[name]["digest"] = f"sha256:{'f' * 64}"
    elif attack == "duplicate":
        original = fake.__call__

        def duplicate(method, url, headers, body, timeout, limit):
            result = original(method, url, headers, body, timeout, limit)
            if "/actions/runs/" in url:
                payload = json.loads(result.body)
                if payload["artifacts"]:
                    payload["artifacts"].append(copy.deepcopy(payload["artifacts"][0]))
                    payload["total_count"] = 2
                    return type(result)(result.status, _json(payload), result.headers)
            return result

        artifacts.transport = duplicate

    with pytest.raises(PromotionError):
        load_receipt(artifacts, registry, service="console")


def test_noncanonical_receipt_json_is_rejected_even_when_archive_digest_matches(
    release_system,
):
    fake, registry, artifacts = release_system
    receipt = verify_candidate(
        registry, service="console", digest=fake.candidate_digest("console")
    )
    noncanonical = (json.dumps(receipt, indent=2, sort_keys=True) + "\n").encode()
    fake.add_artifact(
        candidate_artifact_name("console"),
        candidate_receipt_filename("console"),
        noncanonical,
    )

    with pytest.raises(PromotionError, match="not canonical JSON"):
        load_receipt(artifacts, registry, service="console")


def test_duplicate_json_keys_cannot_change_canonical_receipt_meaning(release_system):
    fake, registry, artifacts = release_system
    receipt = verify_candidate(
        registry, service="console", digest=fake.candidate_digest("console")
    )
    canonical = _json_bytes(receipt)
    duplicate = canonical.replace(
        b'{"attestation_digest":',
        b'{"schema_version":999,"attestation_digest":',
        1,
    )
    fake.add_artifact(
        candidate_artifact_name("console"),
        candidate_receipt_filename("console"),
        duplicate,
    )

    with pytest.raises(PromotionError, match="duplicate JSON key"):
        load_receipt(artifacts, registry, service="console")


def test_prepare_blocks_any_final_tag_without_a_sealed_intent(
    release_system, tmp_path
):
    fake, registry, artifacts = release_system
    fake.add_receipts(registry)
    service = "console"
    digest = fake.candidate_digest(service)
    fake.manifests[(service, registry.identity.release_tag)] = fake.manifests[
        (service, digest)
    ]

    with pytest.raises(PromotionError, match="without sealed promotion intent"):
        prepare_intent(
            artifacts,
            registry,
            intent_path=tmp_path / INTENT_FILENAME,
            fragments_dir=tmp_path / "fragments",
        )


def test_partial_promotion_resumes_from_same_intent_without_overwrite(
    release_system, tmp_path
):
    fake, registry, artifacts = release_system
    fake.add_receipts(registry)
    intent_path = tmp_path / INTENT_FILENAME
    action = prepare_intent(
        artifacts,
        registry,
        intent_path=intent_path,
        fragments_dir=tmp_path / "fragments",
    )
    assert action == "upload"
    intent_bytes = intent_path.read_bytes()
    fake.add_artifact(INTENT_ARTIFACT, INTENT_FILENAME, intent_bytes)
    intent = json.loads(intent_bytes)
    assert confirm_intent(
        artifacts, registry, local_intent=intent, retries=1, retry_delay=0
    ) == intent

    with pytest.raises(PromotionError, match="injected failure"):
        promote(registry, intent=intent, failure_after_put=7)
    assert len(fake.put_calls) == 7

    resumed_puts = promote(registry, intent=intent)
    assert resumed_puts == 23
    assert len(fake.put_calls) == 30
    assert fake.overwrite_calls == []
    verify_final(registry, intent=intent)


def test_rerun_reuses_exact_receipts_and_exact_intent(release_system, tmp_path):
    fake, registry, artifacts = release_system
    fake.add_receipts(registry)
    intent_path = tmp_path / INTENT_FILENAME
    assert (
        prepare_intent(
            artifacts,
            registry,
            intent_path=intent_path,
            fragments_dir=tmp_path / "fragments-first",
        )
        == "upload"
    )
    fake.add_artifact(INTENT_ARTIFACT, INTENT_FILENAME, intent_path.read_bytes())

    assert (
        prepare_intent(
            artifacts,
            registry,
            intent_path=tmp_path / "rerun-intent.json",
            fragments_dir=tmp_path / "fragments-rerun",
        )
        == "reuse"
    )


def test_tampered_or_cross_run_intent_cannot_authorize_promotion(
    release_system, tmp_path
):
    fake, registry, artifacts = release_system
    fake.add_receipts(registry)
    intent_path = tmp_path / INTENT_FILENAME
    prepare_intent(
        artifacts,
        registry,
        intent_path=intent_path,
        fragments_dir=tmp_path / "fragments",
    )
    intent = json.loads(intent_path.read_bytes())
    tampered = copy.deepcopy(intent)
    tampered["run_id"] += 1
    fake.add_artifact(INTENT_ARTIFACT, INTENT_FILENAME, _json_bytes(tampered))

    with pytest.raises(PromotionError, match="current intent"):
        confirm_intent(
            artifacts, registry, local_intent=intent, retries=1, retry_delay=0
        )


def test_conflicting_final_digest_blocks_without_overwrite(release_system, tmp_path):
    fake, registry, artifacts = release_system
    fake.add_receipts(registry)
    intent_path = tmp_path / INTENT_FILENAME
    prepare_intent(
        artifacts,
        registry,
        intent_path=intent_path,
        fragments_dir=tmp_path / "fragments",
    )
    intent = json.loads(intent_path.read_bytes())
    fake.add_artifact(INTENT_ARTIFACT, INTENT_FILENAME, intent_path.read_bytes())
    wrong_body = _json(
        {"schemaVersion": 2, "mediaType": OCI_INDEX, "manifests": []}
    )
    fake.manifests[("console", registry.identity.release_tag)] = (
        wrong_body,
        OCI_INDEX,
    )

    with pytest.raises(PromotionError, match="conflicts"):
        promote(registry, intent=intent)
    assert fake.overwrite_calls == []


def test_garbage_collected_untagged_candidate_does_not_expand_authority(
    release_system, tmp_path
):
    fake, registry, artifacts = release_system
    fake.add_receipts(registry)
    intent_path = tmp_path / INTENT_FILENAME
    prepare_intent(
        artifacts,
        registry,
        intent_path=intent_path,
        fragments_dir=tmp_path / "fragments",
    )
    intent = json.loads(intent_path.read_bytes())
    digest = intent["images"][0]["digest"]
    del fake.manifests[("console", digest)]

    with pytest.raises(PromotionError, match="HTTP 404"):
        promote(registry, intent=intent)
