#!/usr/bin/env python3

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
import time
from collections.abc import Mapping
from pathlib import Path
from typing import Any
from urllib.parse import quote

REPO = Path(__file__).resolve().parents[1]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from scripts.release_image_promotion import (
    CANONICAL_SERVICES,
    DOCKER_MANIFEST,
    DOCKER_MANIFEST_LIST,
    GITHUB_API,
    GITHUB_API_VERSION,
    MAX_ARTIFACT_BYTES,
    OCI_CONFIG,
    OCI_INDEX,
    OCI_MANIFEST,
    ArtifactClient,
    PromotionError,
    RegistryClient,
    ReleaseIdentity,
    _header,
    _json_bytes,
    _manifest_object,
    _parse_json_object,
    _require_descriptor_bytes,
    _require_exact_annotations,
    _sha256,
    _validate_descriptor,
    expected_config_labels,
    http_request,
)

RECEIPT_KEYS = {
    "schema_version",
    "kind",
    "repository",
    "build_run_id",
    "source_sha",
    "release_tag",
    "service",
    "image",
    "digest",
    "manifest_media_type",
    "manifest_size",
    "config_digest",
    "layer_digests",
}
MANIFEST_KEYS = {
    "schema_version",
    "kind",
    "repository",
    "release_tag",
    "source_sha",
    "build_run_id",
    "images",
}
IMAGE_KEYS = {
    "schema_version",
    "service",
    "image",
    "release_tag",
    "source_sha",
    "build_run_id",
    "digest",
    "digest_reference",
    "manifest_media_type",
    "manifest_size",
}
DIGEST_RE = re.compile(r"^sha256:[0-9a-f]{64}$")
MAX_PACKAGE_PAGES = 20
PACKAGE_SETTLE_ATTEMPTS = 6
PACKAGE_SETTLE_SECONDS = 2.0
RUNNABLE_MANIFEST_KEYS = {"schemaVersion", "mediaType", "config", "layers", "annotations"}
DESCRIPTOR_KEYS = {"mediaType", "digest", "size"}
LAYER_MEDIA_TYPES = {
    "application/vnd.oci.image.layer.v1.tar",
    "application/vnd.oci.image.layer.v1.tar+gzip",
    "application/vnd.oci.image.layer.v1.tar+zstd",
}
RECOVERY_MANIFEST_MEDIA_TYPES = frozenset(
    {DOCKER_MANIFEST, DOCKER_MANIFEST_LIST, OCI_INDEX, OCI_MANIFEST}
)


def artifact_name(service: str) -> str:
    if service not in CANONICAL_SERVICES:
        raise PromotionError("release service is not canonical")
    return f"release-image-candidate-{service}"


def receipt_filename(service: str) -> str:
    if service not in CANONICAL_SERVICES:
        raise PromotionError("release service is not canonical")
    return f"{service}.release-candidate.json"


def release_annotations(identity: ReleaseIdentity, service: str) -> dict[str, str]:
    return {
        **identity.annotations(service),
        "io.omega.release.run-id": str(identity.run_id),
    }


def release_config_labels(
    identity: ReleaseIdentity, service: str
) -> dict[str, str]:
    return {
        **expected_config_labels(identity, service),
        "io.omega.release.run-id": str(identity.run_id),
    }


def verify_candidate(
    registry: RegistryClient, *, service: str, digest: str
) -> dict[str, Any]:

    if service not in CANONICAL_SERVICES:
        raise PromotionError("release service is not canonical")
    if DIGEST_RE.fullmatch(digest) is None:
        raise PromotionError("candidate digest is invalid")
    result = registry.get_manifest(service, digest)
    if result is None:  # pragma: no cover - allow_absent is false
        raise PromotionError("candidate digest is absent")
    manifest = _manifest_object(
        result,
        expected_digest=digest,
        expected_media_type=OCI_MANIFEST,
        label="candidate runnable manifest",
    )
    if set(manifest) != RUNNABLE_MANIFEST_KEYS:
        raise PromotionError("candidate runnable manifest fields are not canonical")
    expected_annotations = release_annotations(registry.identity, service)
    _require_exact_annotations(
        manifest.get("annotations"),
        expected_annotations,
        label="candidate runnable manifest",
    )
    config = _validate_descriptor(
        manifest.get("config"), label="candidate config", media_type=OCI_CONFIG
    )
    if set(config) != DESCRIPTOR_KEYS or config["size"] <= 0:
        raise PromotionError("candidate config descriptor is not canonical")
    layers = manifest.get("layers")
    if not isinstance(layers, list) or not layers:
        raise PromotionError("candidate runnable manifest has no layers")
    checked_layers: list[dict[str, Any]] = []
    seen_layer_digests: set[str] = set()
    for raw_layer in layers:
        layer = _validate_descriptor(raw_layer, label="candidate layer")
        media_type = layer.get("mediaType")
        if (
            set(layer) != DESCRIPTOR_KEYS
            or layer["size"] <= 0
            or media_type not in LAYER_MEDIA_TYPES
        ):
            raise PromotionError("candidate layer media type is invalid")
        if layer["digest"] in seen_layer_digests:
            raise PromotionError("candidate layer digest is duplicated")
        seen_layer_digests.add(layer["digest"])
        registry.head_blob(service, layer)
        checked_layers.append(layer)
    config_bytes = registry.get_blob(service, config)
    config_object = _parse_json_object(config_bytes, label="candidate config")
    if (
        config_object.get("architecture") != "amd64"
        or config_object.get("os") != "linux"
    ):
        raise PromotionError("candidate config platform is invalid")
    config_section = config_object.get("config")
    labels = config_section.get("Labels") if isinstance(config_section, dict) else None
    if labels != release_config_labels(registry.identity, service):
        raise PromotionError("candidate config labels are not exact release identity")
    return {
        "schema_version": 2,
        "kind": "omega-release-image-candidate",
        "repository": registry.identity.repository,
        "build_run_id": registry.identity.run_id,
        "source_sha": registry.identity.source_sha,
        "release_tag": registry.identity.release_tag,
        "service": service,
        "image": registry.identity.image(service),
        "digest": digest,
        "manifest_media_type": OCI_MANIFEST,
        "manifest_size": len(result.body),
        "config_digest": config["digest"],
        "layer_digests": [layer["digest"] for layer in checked_layers],
    }


def validate_receipt(
    value: object, *, identity: ReleaseIdentity, service: str
) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) != RECEIPT_KEYS:
        raise PromotionError("candidate receipt schema is invalid")
    expected = {
        "schema_version": 2,
        "kind": "omega-release-image-candidate",
        "repository": identity.repository,
        "build_run_id": identity.run_id,
        "source_sha": identity.source_sha,
        "release_tag": identity.release_tag,
        "service": service,
        "image": identity.image(service),
        "manifest_media_type": OCI_MANIFEST,
    }
    for key, expected_value in expected.items():
        if value.get(key) != expected_value:
            raise PromotionError(f"candidate receipt {key} is not bound to this run")
    for key in ("digest", "config_digest"):
        if not isinstance(value.get(key), str) or DIGEST_RE.fullmatch(value[key]) is None:
            raise PromotionError(f"candidate receipt {key} is invalid")
    layers = value.get("layer_digests")
    if (
        not isinstance(layers, list)
        or not layers
        or len(layers) != len(set(layers))
        or not all(isinstance(item, str) and DIGEST_RE.fullmatch(item) for item in layers)
    ):
        raise PromotionError("candidate receipt layer inventory is invalid")
    size = value.get("manifest_size")
    if not isinstance(size, int) or isinstance(size, bool) or size <= 0:
        raise PromotionError("candidate receipt manifest_size is invalid")
    return value


def load_receipt(
    artifacts: ArtifactClient,
    registry: RegistryClient,
    *,
    service: str,
) -> dict[str, Any] | None:
    value = artifacts.load(name=artifact_name(service), filename=receipt_filename(service))
    if value is None:
        return None
    receipt = validate_receipt(value, identity=registry.identity, service=service)
    actual = verify_candidate(registry, service=service, digest=receipt["digest"])
    if actual != receipt:
        raise PromotionError("candidate receipt does not match current OCI evidence")
    return receipt


class PackageVersionClient:

    def __init__(
        self,
        *,
        identity: ReleaseIdentity,
        github_token: str,
        transport=http_request,
        timeout: float = 20.0,
    ) -> None:
        if not github_token.strip():
            raise PromotionError("GitHub token is missing")
        self.identity = identity
        self.token = github_token.strip()
        self.transport = transport
        self.timeout = timeout

    @property
    def headers(self) -> dict[str, str]:
        return {
            "Accept": "application/vnd.github+json",
            "Authorization": f"Bearer {self.token}",
            "X-GitHub-Api-Version": GITHUB_API_VERSION,
            "User-Agent": "omega-release-digest-chain/2",
        }

    def _list_page(self, service: str, page: int) -> list[dict[str, Any]]:
        owner = quote(self.identity.repository.split("/", 1)[0], safe="")
        package = quote(service, safe="")
        url = (
            f"{GITHUB_API}/users/{owner}/packages/container/{package}/versions"
            f"?per_page=100&page={page}"
        )
        result = self.transport(
            "GET", url, self.headers, None, self.timeout, MAX_ARTIFACT_BYTES
        )
        if result.status != 200:
            raise PromotionError(
                f"GitHub Packages versions API returned HTTP {result.status}"
            )
        content_type = _header(result.headers, "Content-Type").split(";", 1)[0]
        if content_type.strip().lower() not in {
            "application/json",
            "application/vnd.github+json",
        }:
            raise PromotionError("GitHub Packages versions response content type is invalid")
        try:
            value = json.loads(result.body.decode("utf-8"))
        except (UnicodeError, json.JSONDecodeError) as exc:
            raise PromotionError("GitHub Packages versions response is invalid JSON") from exc
        if not isinstance(value, list) or not all(isinstance(item, dict) for item in value):
            raise PromotionError("GitHub Packages versions response schema is invalid")
        return value

    def recover(
        self, registry: RegistryClient, *, service: str
    ) -> dict[str, Any] | None:
        matches: dict[str, dict[str, Any]] = {}
        exhausted = False
        for page in range(1, MAX_PACKAGE_PAGES + 1):
            versions = self._list_page(service, page)
            for version in versions:
                digest = version.get("name")
                if (
                    not isinstance(digest, str)
                    or DIGEST_RE.fullmatch(digest) is None
                ):
                    continue
                result = registry.get_manifest(
                    service,
                    digest,
                    allow_absent=True,
                    accepted_media_types=RECOVERY_MANIFEST_MEDIA_TYPES,
                )
                if result is None:
                    continue
                content_type = _header(result.headers, "Content-Type").split(";", 1)[0]
                content_type = content_type.strip().lower()
                if content_type != OCI_MANIFEST:
                    continue
                candidate = _parse_json_object(
                    result.body, label="recoverable candidate manifest"
                )
                if candidate.get("annotations") != release_annotations(
                    self.identity, service
                ):
                    continue
                matches[digest] = verify_candidate(
                    registry, service=service, digest=digest
                )
            if len(versions) < 100:
                exhausted = True
                break
        if not exhausted:
            raise PromotionError("GitHub Packages recovery pagination limit exceeded")
        if len(matches) > 1:
            raise PromotionError("multiple recoverable candidates exist for this run")
        return next(iter(matches.values()), None)


def settle_built_candidate(
    packages: PackageVersionClient,
    registry: RegistryClient,
    *,
    service: str,
    digest: str,
) -> dict[str, Any]:
    for attempt in range(PACKAGE_SETTLE_ATTEMPTS):
        observed = packages.recover(registry, service=service)
        if observed is not None:
            if observed.get("digest") != digest:
                raise PromotionError(
                    "built candidate is not the unique exact package version"
                )
            return observed
        if attempt + 1 < PACKAGE_SETTLE_ATTEMPTS:
            time.sleep(PACKAGE_SETTLE_SECONDS)
    raise PromotionError("built candidate did not settle in GitHub Packages")


def _image_entry(identity: ReleaseIdentity, receipt: Mapping[str, Any]) -> dict[str, Any]:
    service = receipt["service"]
    image = identity.image(service)
    digest = receipt["digest"]
    return {
        "schema_version": 2,
        "service": service,
        "image": image,
        "release_tag": identity.release_tag,
        "source_sha": identity.source_sha,
        "build_run_id": identity.run_id,
        "digest": digest,
        "digest_reference": f"{image}@{digest}",
        "manifest_media_type": receipt["manifest_media_type"],
        "manifest_size": receipt["manifest_size"],
    }


def write_manifest(
    artifacts: ArtifactClient,
    registry: RegistryClient,
    *,
    output_dir: Path,
) -> tuple[Path, Path]:
    receipts: list[dict[str, Any]] = []
    for service in CANONICAL_SERVICES:
        receipt = load_receipt(artifacts, registry, service=service)
        if receipt is None:
            raise PromotionError(f"candidate receipt is absent: {service}")
        receipts.append(receipt)
    identity = registry.identity
    manifest = {
        "schema_version": 2,
        "kind": "omega-release-manifest",
        "repository": identity.repository,
        "release_tag": identity.release_tag,
        "source_sha": identity.source_sha,
        "build_run_id": identity.run_id,
        "images": [_image_entry(identity, receipt) for receipt in receipts],
    }
    if set(manifest) != MANIFEST_KEYS or any(
        set(image) != IMAGE_KEYS for image in manifest["images"]
    ):
        raise PromotionError("release manifest v2 schema is not exact")
    output_dir.mkdir(parents=True, exist_ok=False)
    manifest_path = output_dir / f"omega-release-manifest-{identity.release_tag}.json"
    body = (json.dumps(manifest, indent=2, sort_keys=True) + "\n").encode("utf-8")
    manifest_path.write_bytes(body)
    checksum_path = manifest_path.with_suffix(".json.sha256")
    checksum_path.write_text(
        f"{hashlib.sha256(body).hexdigest()}  {manifest_path.name}\n",
        encoding="utf-8",
    )
    return manifest_path, checksum_path


def _identity(args: argparse.Namespace) -> ReleaseIdentity:
    return ReleaseIdentity.create(
        repository=args.repository,
        owner=args.owner,
        source_sha=args.source_sha,
        release_tag=args.release_tag,
        run_id=args.run_id,
    )


def _add_identity(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--repository", required=True)
    parser.add_argument("--owner", required=True)
    parser.add_argument("--source-sha", required=True)
    parser.add_argument("--release-tag", required=True)
    parser.add_argument("--run-id", required=True, type=int)
    parser.add_argument("--username", required=True)


def _clients(args: argparse.Namespace):
    identity = _identity(args)
    token = os.environ.get("GITHUB_TOKEN", "")
    registry = RegistryClient(
        identity=identity,
        username=args.username,
        github_token=token,
    )
    artifacts = ArtifactClient(identity=identity, github_token=token)
    packages = PackageVersionClient(identity=identity, github_token=token)
    return artifacts, registry, packages


def _output(path: str | None, **values: object) -> None:
    if not path:
        return
    with Path(path).open("a", encoding="utf-8") as handle:
        for key, value in values.items():
            handle.write(f"{key}={value}\n")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    subs = parser.add_subparsers(dest="command", required=True)
    candidate = subs.add_parser("candidate-state")
    _add_identity(candidate)
    candidate.add_argument("--service", required=True)
    candidate.add_argument("--receipt", required=True, type=Path)
    candidate.add_argument("--github-output")
    receipt = subs.add_parser("write-receipt")
    _add_identity(receipt)
    receipt.add_argument("--service", required=True)
    receipt.add_argument("--digest", required=True)
    receipt.add_argument("--receipt", required=True, type=Path)
    manifest = subs.add_parser("write-manifest")
    _add_identity(manifest)
    manifest.add_argument("--output-dir", required=True, type=Path)
    manifest.add_argument("--github-output")
    args = parser.parse_args(argv)
    try:
        artifacts, registry, packages = _clients(args)
        if args.command == "candidate-state":
            value = load_receipt(artifacts, registry, service=args.service)
            if value is not None:
                action = "reuse"
                args.receipt.write_bytes(_json_bytes(value))
            else:
                recovered = packages.recover(registry, service=args.service)
                if recovered is None:
                    action = "build"
                else:
                    raise PromotionError(
                        "orphan candidate exists without an authoritative run artifact"
                    )
            _output(args.github_output, action=action)
            print(f"CANDIDATE {args.service}: {action.upper()}")
        elif args.command == "write-receipt":
            value = verify_candidate(
                registry, service=args.service, digest=args.digest
            )
            settle_built_candidate(
                packages,
                registry,
                service=args.service,
                digest=args.digest,
            )
            args.receipt.write_bytes(_json_bytes(value))
            print(f"CANDIDATE {args.service}: VERIFIED")
        elif args.command == "write-manifest":
            manifest_path, checksum_path = write_manifest(
                artifacts, registry, output_dir=args.output_dir
            )
            _output(
                args.github_output,
                manifest_path=manifest_path,
                checksum_path=checksum_path,
            )
            print("DIGEST RELEASE MANIFEST: 16/16 VERIFIED")
        else:  # pragma: no cover
            raise PromotionError("unknown command")
    except PromotionError as exc:
        print(f"RELEASE DIGEST CHAIN BLOCKED: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
