#!/usr/bin/env python3
"""Validate a v2 release manifest and export its exact Compose digest lock."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from scripts.release_digest_chain import (
    IMAGE_KEYS,
    MANIFEST_KEYS,
    CANONICAL_SERVICES,
)
from scripts.release_image_promotion import OCI_MANIFEST

ENV_NAMES = {
    service: f"OMEGA_GCP_IMAGE_{service.upper().replace('-', '_')}"
    for service in CANONICAL_SERVICES
}
EXPECTED_COMPOSE = {
    "console": "console",
    "workspace": "workspace",
    "refinement": "refinement",
    "vault": "vault",
    "mcp-infra": "mcp-infra",
    "airflow-init": "airflow",
    "airflow": "airflow",
    "airflow-scheduler": "airflow",
    "replicon": "replicon",
    "hubspot": "hubspot",
    "banxico": "banxico",
    "inegi": "inegi",
    "sec-edgar": "sec_edgar",
    "sap-hcm": "sap_hcm",
    "sap-s4hana": "sap_s4hana",
    "sap-successfactors": "sap_successfactors",
    "salesforce": "salesforce",
    "sap-b1": "sap_b1",
}


# Infrastructure images this repository rebuilds from source and hosts in its
# own GHCR namespace because their publisher withdrew them (MinIO, see
# infra/images/minio and .github/workflows/mirror-minio.yml). They are not
# release images: they never appear in the release manifest and the stack
# runs them by tag. The names are exact on purpose, not a prefix, so any other
# repository in the namespace that a stray compose service consumes still
# blocks the release.
INFRASTRUCTURE_MIRRORS = frozenset({"omega-minio", "omega-mc"})


def _repository_name(image: str) -> str:
    """``ghcr.io/owner/name:tag@sha256:...`` -> ``name``."""
    reference = image.split("@", 1)[0]
    return reference.rsplit("/", 1)[-1].split(":", 1)[0]


class ManifestError(RuntimeError):
    pass


def _strict_json(raw: bytes) -> dict:
    def pairs(items: list[tuple[str, object]]) -> dict:
        value: dict[str, object] = {}
        for key, item in items:
            if key in value:
                raise ManifestError("release manifest contains a duplicate JSON key")
            value[key] = item
        return value

    def reject_constant(value: str) -> None:
        raise ManifestError(f"release manifest contains non-finite JSON: {value}")

    try:
        value = json.loads(
            raw.decode("utf-8"),
            object_pairs_hook=pairs,
            parse_constant=reject_constant,
        )
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise ManifestError("release manifest cannot be parsed") from exc
    if not isinstance(value, dict):
        raise ManifestError("release manifest must be an object")
    canonical = (json.dumps(value, indent=2, sort_keys=True) + "\n").encode("utf-8")
    if raw != canonical:
        raise ManifestError("release manifest bytes are not canonical")
    return value


def load_manifest(
    path: Path,
    *,
    checksum_path: Path,
    repository: str,
    release_tag: str,
    source_sha: str,
    build_run_id: int,
) -> dict:
    try:
        raw = path.read_bytes()
        checksum_raw = checksum_path.read_bytes()
    except OSError as exc:
        raise ManifestError("release manifest cannot be read") from exc
    value = _strict_json(raw)
    if path.name != f"omega-release-manifest-{release_tag}.json":
        raise ManifestError("release manifest filename is not exact")
    expected_checksum = (
        f"{hashlib.sha256(raw).hexdigest()}  {path.name}\n".encode("ascii")
    )
    if checksum_raw != expected_checksum or checksum_path.name != f"{path.name}.sha256":
        raise ManifestError("release manifest checksum asset is not exact")
    if not isinstance(value, dict) or set(value) != MANIFEST_KEYS:
        raise ManifestError("release manifest v2 schema is invalid")
    if value.get("schema_version") != 2 or value.get("kind") != "omega-release-manifest":
        raise ManifestError("release manifest v2 identity is invalid")
    run_id = value.get("build_run_id")
    if not isinstance(run_id, int) or isinstance(run_id, bool) or run_id <= 0:
        raise ManifestError("release manifest build_run_id is invalid")
    if (
        value.get("repository") != repository
        or value.get("release_tag") != release_tag
        or value.get("source_sha") != source_sha
        or run_id != build_run_id
    ):
        raise ManifestError("release manifest is not bound to the expected release")
    owner = repository.split("/", 1)[0].lower()
    images = value.get("images")
    if not isinstance(images, list) or len(images) != len(CANONICAL_SERVICES):
        raise ManifestError("release manifest image inventory is not 16/16")
    by_service = {}
    for expected_service, image in zip(CANONICAL_SERVICES, images, strict=True):
        if not isinstance(image, dict) or set(image) != IMAGE_KEYS:
            raise ManifestError("release manifest image schema is invalid")
        if image.get("schema_version") != 2 or image.get("service") != expected_service:
            raise ManifestError("release manifest image order is not canonical")
        if (
            image.get("release_tag") != value.get("release_tag")
            or image.get("source_sha") != value.get("source_sha")
            or image.get("build_run_id") != run_id
        ):
            raise ManifestError("release manifest image identity mismatch")
        expected_image = f"ghcr.io/{owner}/{expected_service}"
        size = image.get("manifest_size")
        if (
            image.get("image") != expected_image
            or image.get("manifest_media_type") != OCI_MANIFEST
            or not isinstance(size, int)
            or isinstance(size, bool)
            or size <= 0
        ):
            raise ManifestError("release manifest image descriptor is invalid")
        digest = image.get("digest")
        reference = image.get("digest_reference")
        if (
            not isinstance(digest, str)
            or re.fullmatch(r"sha256:[0-9a-f]{64}", digest) is None
            or reference != f"{image.get('image')}@{digest}"
        ):
            raise ManifestError("release manifest digest reference is invalid")
        by_service[expected_service] = reference
    return {**value, "by_service": by_service}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--checksum", type=Path, required=True)
    parser.add_argument("--repository", required=True)
    parser.add_argument("--release-tag", required=True)
    parser.add_argument("--source-sha", required=True)
    parser.add_argument("--build-run-id", type=int, required=True)
    parser.add_argument("--github-env", type=Path)
    parser.add_argument("--compose-config", type=Path)
    args = parser.parse_args(argv)
    try:
        manifest = load_manifest(
            args.manifest,
            checksum_path=args.checksum,
            repository=args.repository,
            release_tag=args.release_tag,
            source_sha=args.source_sha,
            build_run_id=args.build_run_id,
        )
        by_service = manifest["by_service"]
        lines = [f"{ENV_NAMES[service]}={by_service[service]}" for service in CANONICAL_SERVICES]
        if args.github_env:
            with args.github_env.open("a", encoding="utf-8") as handle:
                handle.write("\n".join(lines) + "\n")
        else:
            print("\n".join(lines))
        if args.compose_config:
            compose = json.loads(args.compose_config.read_text(encoding="utf-8"))
            services = compose.get("services") if isinstance(compose, dict) else None
            if not isinstance(services, dict):
                raise ManifestError("Compose config services are invalid")
            actual = {
                name: services.get(name, {}).get("image")
                for name in EXPECTED_COMPOSE
            }
            expected = {
                name: by_service[service]
                for name, service in EXPECTED_COMPOSE.items()
            }
            if actual != expected:
                raise ManifestError("Compose release images are not exact 18/16 digest lock")
            expected_references = set(by_service.values())
            for name in EXPECTED_COMPOSE:
                service = services[name]
                if "build" in service or service.get("pull_policy") != "never":
                    raise ManifestError("Compose release service can build or pull by tag")
            for name, service in services.items():
                if name in EXPECTED_COMPOSE:
                    continue
                image = service.get("image") if isinstance(service, dict) else None
                namespace = f"ghcr.io/{args.repository.split('/', 1)[0].lower()}/"
                if isinstance(image, str) and (
                    image in expected_references
                    or (
                        image.startswith(namespace)
                        and _repository_name(image) not in INFRASTRUCTURE_MIRRORS
                    )
                ):
                    raise ManifestError(
                        "unexpected Compose service consumes an OMEGA release image"
                    )
    except (ManifestError, OSError, UnicodeError, json.JSONDecodeError) as exc:
        print(f"RELEASE DIGEST ENV BLOCKED: {exc}", file=sys.stderr)
        return 1
    print("RELEASE DIGEST ENV PASS 18 services / 16 digests")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
