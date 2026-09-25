#!/usr/bin/env python3
"""Build evidence and recoverably promote the 16 release images.

The build matrix publishes only untagged, content-addressed candidates.  This
module verifies their OCI graph and BuildKit attestation, seals one immutable
receipt per service to the current Actions run, and promotes the exact top
manifest bytes only after all receipts and a canonical promotion intent exist.
Candidates deliberately remain untagged in the already-private canonical GHCR
package.  Registry retention may garbage-collect them after their useful life;
a missing digest blocks recovery and never authorizes a rebuild or substitute.

Manifest/tag lookups never follow redirects.  The only redirects accepted are
GHCR blob downloads to ``pkg-containers.githubusercontent.com`` and GitHub
artifact archives to ``*.blob.core.windows.net``; credentials are stripped on
both hops.
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import io
import json
import os
import re
import sys
import time
import zipfile
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlencode, urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener

GHCR_ORIGIN = "https://ghcr.io"
GITHUB_API = "https://api.github.com"
GITHUB_API_VERSION = "2022-11-28"
MAX_JSON_BYTES = 8 * 1024 * 1024
MAX_BLOB_BYTES = 64 * 1024 * 1024
MAX_ARTIFACT_BYTES = 2 * 1024 * 1024
OCI_INDEX = "application/vnd.oci.image.index.v1+json"
OCI_MANIFEST = "application/vnd.oci.image.manifest.v1+json"
DOCKER_MANIFEST = "application/vnd.docker.distribution.manifest.v2+json"
DOCKER_MANIFEST_LIST = "application/vnd.docker.distribution.manifest.list.v2+json"
OCI_CONFIG = "application/vnd.oci.image.config.v1+json"
OCI_EMPTY = "application/vnd.oci.empty.v1+json"
OCI_ATTESTATION = "application/vnd.docker.attestation.manifest.v1+json"
IN_TOTO = "application/vnd.in-toto+json"
OCI_ACCEPT = f"{OCI_INDEX}, {OCI_MANIFEST}"
CANONICAL_SERVICES = (
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
    "sap_b1",
)
# The Airflow Dockerfile inherits these reviewed labels from its version-selected
# ``apache/airflow:2.10.5`` base.  Every other release image currently inherits
# no labels from ``python:3.12-slim``.  Managed release identity keys below
# overwrite the base source/revision/version values and are checked separately.
# F2 binds and tests the resulting artifact digest; it does not claim a
# bit-for-bit rebuild. Base digest pinning, SBOM and provenance belong to F17.
EXPECTED_INHERITED_LABELS: dict[str, dict[str, str]] = {
    "airflow": {
        "org.apache.airflow.component": "airflow",
        "org.apache.airflow.distro": "debian",
        "org.apache.airflow.image": "airflow",
        "org.apache.airflow.main-image.build-id": "",
        "org.apache.airflow.main-image.commit-sha": (
            "223b0a4b61a44a83895371b2c9a3a5cafa5df8ea"
        ),
        "org.apache.airflow.module": "airflow",
        "org.apache.airflow.uid": "50000",
        "org.apache.airflow.version": "2.10.5",
        "org.opencontainers.image.authors": "dev@airflow.apache.org",
        "org.opencontainers.image.created": "",
        "org.opencontainers.image.description": (
            "Reference, production-ready Apache Airflow image"
        ),
        "org.opencontainers.image.documentation": (
            "https://airflow.apache.org/docs/docker-stack/index.html"
        ),
        "org.opencontainers.image.licenses": "Apache-2.0",
        "org.opencontainers.image.ref.name": "airflow",
        "org.opencontainers.image.title": "Production Airflow Image",
        "org.opencontainers.image.url": "https://airflow.apache.org",
        "org.opencontainers.image.vendor": "Apache Software Foundation",
    }
}
STRICT_TAG = re.compile(
    r"^v(?:0|[1-9][0-9]*)\."
    r"(?:0|[1-9][0-9]*)\."
    r"(?:0|[1-9][0-9]*)"
    r"(?:-(?:0|[1-9][0-9]*|[0-9A-Za-z-]*[A-Za-z-][0-9A-Za-z-]*)"
    r"(?:\.(?:0|[1-9][0-9]*|[0-9A-Za-z-]*[A-Za-z-][0-9A-Za-z-]*))*)?$"
)
DIGEST_RE = re.compile(r"^sha256:[0-9a-f]{64}$")
REPOSITORY_RE = re.compile(
    r"^[A-Za-z0-9](?:[A-Za-z0-9-]{0,38})/[A-Za-z0-9_.-]{1,100}$"
)


class PromotionError(RuntimeError):
    """Evidence is absent, ambiguous, malformed, or not bound to this run."""


@dataclass(frozen=True)
class HttpResult:
    status: int
    body: bytes
    headers: Mapping[str, str]


Transport = Callable[
    [str, str, Mapping[str, str], bytes | None, float, int], HttpResult
]


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(
        self,
        req: Request,
        fp: Any,
        code: int,
        msg: str,
        headers: Any,
        newurl: str,
    ) -> None:
        return None


def _header(headers: Mapping[str, str], name: str) -> str:
    for key, value in headers.items():
        if key.lower() == name.lower():
            return value
    return ""


def _read_bounded(response: Any, limit: int) -> bytes:
    try:
        body = response.read(limit + 1)
    except OSError as exc:
        raise PromotionError("HTTP response body could not be read") from exc
    if len(body) > limit:
        raise PromotionError("HTTP response is oversized")
    return body


def http_request(
    method: str,
    url: str,
    headers: Mapping[str, str],
    body: bytes | None,
    timeout: float,
    limit: int,
) -> HttpResult:
    request = Request(url, data=body, headers=dict(headers), method=method)
    opener = build_opener(_NoRedirect)
    try:
        with opener.open(request, timeout=timeout) as response:
            return HttpResult(
                status=int(response.status),
                body=_read_bounded(response, limit),
                headers=dict(response.headers.items()),
            )
    except HTTPError as exc:
        return HttpResult(
            status=int(exc.code),
            body=_read_bounded(exc, limit),
            headers=dict(exc.headers.items()),
        )
    except (URLError, TimeoutError, OSError) as exc:
        raise PromotionError("HTTP transport failed") from exc


def _json_bytes(value: object) -> bytes:
    return (json.dumps(value, sort_keys=True, separators=(",", ":")) + "\n").encode(
        "utf-8"
    )


def _parse_json_object(
    body: bytes,
    *,
    label: str,
    require_canonical: bool = False,
) -> dict[str, Any]:
    def unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        value: dict[str, Any] = {}
        for key, item in pairs:
            if key in value:
                raise PromotionError(f"{label} contains a duplicate JSON key")
            value[key] = item
        return value

    def reject_constant(value: str) -> None:
        raise PromotionError(f"{label} contains non-finite JSON: {value}")

    try:
        value = json.loads(
            body.decode("utf-8"),
            object_pairs_hook=unique_object,
            parse_constant=reject_constant,
        )
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise PromotionError(f"{label} is not valid JSON") from exc
    if not isinstance(value, dict):
        raise PromotionError(f"{label} must be a JSON object")
    if require_canonical and body != _json_bytes(value):
        raise PromotionError(f"{label} is not canonical JSON")
    return value


def _require_json_content_type(result: HttpResult, *, label: str) -> None:
    content_type = _header(result.headers, "Content-Type").split(";", 1)[0]
    if content_type.strip().lower() not in {
        "application/json",
        "application/vnd.github+json",
    }:
        raise PromotionError(f"{label} has an invalid content type")


def _sha256(body: bytes) -> str:
    return f"sha256:{hashlib.sha256(body).hexdigest()}"


def _validate_token(value: object, *, label: str) -> str:
    if not isinstance(value, str):
        raise PromotionError(f"{label} is missing or invalid")
    value = value.strip()
    if (
        not value
        or len(value) > 16_384
        or any(ord(character) < 33 or ord(character) == 127 for character in value)
    ):
        raise PromotionError(f"{label} is missing or invalid")
    return value


@dataclass(frozen=True)
class ReleaseIdentity:
    repository: str
    owner: str
    source_sha: str
    release_tag: str
    run_id: int

    @classmethod
    def create(
        cls,
        *,
        repository: str,
        owner: str,
        source_sha: str,
        release_tag: str,
        run_id: int,
    ) -> ReleaseIdentity:
        if REPOSITORY_RE.fullmatch(repository) is None:
            raise PromotionError("source repository is invalid")
        repository_owner = repository.split("/", 1)[0]
        if repository_owner.lower() != owner.lower():
            raise PromotionError("GHCR owner does not match source repository")
        if (
            re.fullmatch(
                r"(?=.{1,39}\Z)[A-Za-z0-9](?:[A-Za-z0-9-]*[A-Za-z0-9])?",
                owner,
            )
            is None
            or "--" in owner
        ):
            raise PromotionError("GHCR owner is invalid")
        if re.fullmatch(r"[0-9a-f]{40}", source_sha) is None:
            raise PromotionError("source SHA is invalid")
        if STRICT_TAG.fullmatch(release_tag) is None:
            raise PromotionError("release tag is not strict SemVer")
        if not isinstance(run_id, int) or isinstance(run_id, bool) or run_id <= 0:
            raise PromotionError("GitHub run_id is invalid")
        return cls(repository, owner.lower(), source_sha, release_tag, run_id)

    @property
    def source_url(self) -> str:
        return f"https://github.com/{self.repository}"

    def image(self, service: str) -> str:
        _validate_service(service)
        return f"ghcr.io/{self.owner}/{service}"

    def annotations(self, service: str) -> dict[str, str]:
        _validate_service(service)
        return {
            "io.omega.release.service": service,
            "org.opencontainers.image.revision": self.source_sha,
            "org.opencontainers.image.source": self.source_url,
            "org.opencontainers.image.version": self.release_tag,
        }


def _validate_service(service: str) -> str:
    if service not in CANONICAL_SERVICES:
        raise PromotionError("release service is not canonical")
    return service


def expected_config_labels(
    identity: ReleaseIdentity, service: str
) -> dict[str, str]:
    """Return the exact reviewed config-label allowlist for one service."""

    _validate_service(service)
    return {
        **EXPECTED_INHERITED_LABELS.get(service, {}),
        **identity.annotations(service),
    }


def _validate_digest(value: object, *, label: str) -> str:
    if not isinstance(value, str) or DIGEST_RE.fullmatch(value) is None:
        raise PromotionError(f"{label} is not a SHA-256 digest")
    return value


def _validate_descriptor(
    value: object,
    *,
    label: str,
    media_type: str | None = None,
) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise PromotionError(f"{label} is not an OCI descriptor")
    _validate_digest(value.get("digest"), label=f"{label} digest")
    size = value.get("size")
    if not isinstance(size, int) or isinstance(size, bool) or size < 0:
        raise PromotionError(f"{label} size is invalid")
    if media_type is not None and value.get("mediaType") != media_type:
        raise PromotionError(f"{label} media type is invalid")
    return value


class RegistryClient:
    def __init__(
        self,
        *,
        identity: ReleaseIdentity,
        username: str,
        github_token: str,
        transport: Transport = http_request,
        timeout: float = 20.0,
    ) -> None:
        self.identity = identity
        self.username = _validate_token(username, label="GHCR username")
        if ":" in self.username:
            raise PromotionError("GHCR username is invalid")
        self.github_token = _validate_token(github_token, label="GitHub token")
        self.transport = transport
        self.timeout = timeout
        self._tokens: dict[tuple[str, str], str] = {}

    def _token(self, service: str, actions: str) -> str:
        _validate_service(service)
        key = (service, actions)
        if key in self._tokens:
            return self._tokens[key]
        credentials = base64.b64encode(
            f"{self.username}:{self.github_token}".encode()
        ).decode("ascii")
        scope = f"repository:{self.identity.owner}/{service}:{actions}"
        url = f"{GHCR_ORIGIN}/token?{urlencode({'service': 'ghcr.io', 'scope': scope})}"
        result = self.transport(
            "GET",
            url,
            {
                "Accept": "application/json",
                "Authorization": f"Basic {credentials}",
                "User-Agent": "omega-release-promotion/1",
            },
            None,
            self.timeout,
            MAX_JSON_BYTES,
        )
        if result.status != 200:
            raise PromotionError(
                f"GHCR token exchange returned HTTP {result.status}"
            )
        _require_json_content_type(result, label="GHCR token response")
        payload = _parse_json_object(result.body, label="GHCR token response")
        token = _validate_token(
            payload.get("token", ""), label="GHCR registry token"
        )
        self._tokens[key] = token
        return token

    def _manifest_url(self, service: str, reference: str) -> str:
        if DIGEST_RE.fullmatch(reference) is None and (
            STRICT_TAG.fullmatch(reference) is None
            and re.fullmatch(r"sha-[0-9a-f]{40}", reference) is None
        ):
            raise PromotionError("OCI manifest reference is invalid")
        repository = (
            f"{quote(self.identity.owner, safe='')}/{quote(service, safe='')}"
        )
        return (
            f"{GHCR_ORIGIN}/v2/{repository}/manifests/"
            f"{quote(reference, safe=':')}"
        )

    def get_manifest(
        self,
        service: str,
        reference: str,
        *,
        allow_absent: bool = False,
        accepted_media_types: frozenset[str] | None = None,
    ) -> HttpResult | None:
        _validate_service(service)
        media_types = (
            frozenset({OCI_INDEX, OCI_MANIFEST})
            if accepted_media_types is None
            else accepted_media_types
        )
        if not media_types or not media_types <= {
            OCI_INDEX,
            OCI_MANIFEST,
            DOCKER_MANIFEST,
            DOCKER_MANIFEST_LIST,
        }:
            raise PromotionError("GHCR accepted manifest media types are invalid")
        result = self.transport(
            "GET",
            self._manifest_url(service, reference),
            {
                "Accept": ", ".join(sorted(media_types)),
                "Authorization": f"Bearer {self._token(service, 'pull')}",
                "User-Agent": "omega-release-promotion/1",
            },
            None,
            self.timeout,
            MAX_JSON_BYTES,
        )
        if result.status == 404 and allow_absent:
            _require_json_content_type(result, label="GHCR manifest error")
            payload = _parse_json_object(
                result.body, label="GHCR manifest error"
            )
            errors = payload.get("errors")
            if (
                not isinstance(errors, list)
                or len(errors) != 1
                or not isinstance(errors[0], dict)
                or errors[0].get("code") != "MANIFEST_UNKNOWN"
            ):
                raise PromotionError(
                    "GHCR 404 is not structured MANIFEST_UNKNOWN absence"
                )
            return None
        if result.status != 200:
            raise PromotionError(
                f"GHCR manifest lookup returned HTTP {result.status}"
            )
        content_type = _header(result.headers, "Content-Type").split(";", 1)[0]
        if content_type.strip().lower() not in media_types:
            raise PromotionError("GHCR manifest has an invalid content type")
        digest = _validate_digest(
            _header(result.headers, "Docker-Content-Digest"),
            label="GHCR manifest response digest",
        )
        if _sha256(result.body) != digest:
            raise PromotionError("GHCR manifest response digest does not match bytes")
        if DIGEST_RE.fullmatch(reference) is not None and digest != reference:
            raise PromotionError("GHCR digest reference resolved to different bytes")
        return result

    def get_blob(self, service: str, descriptor: Mapping[str, Any]) -> bytes:
        descriptor = _validate_descriptor(descriptor, label="OCI blob descriptor")
        digest = descriptor["digest"]
        repository = (
            f"{quote(self.identity.owner, safe='')}/{quote(service, safe='')}"
        )
        url = f"{GHCR_ORIGIN}/v2/{repository}/blobs/{quote(digest, safe=':')}"
        result = self.transport(
            "GET",
            url,
            {
                "Accept": "application/octet-stream",
                "Authorization": f"Bearer {self._token(service, 'pull')}",
                "User-Agent": "omega-release-promotion/1",
            },
            None,
            self.timeout,
            MAX_BLOB_BYTES,
        )
        if result.status in {301, 302, 303, 307, 308}:
            location = _header(result.headers, "Location")
            parts = urlsplit(location)
            if (
                result.status not in {302, 307}
                or parts.scheme != "https"
                or parts.hostname != "pkg-containers.githubusercontent.com"
                or parts.username is not None
                or parts.password is not None
                or parts.fragment
            ):
                raise PromotionError("GHCR blob redirect target is not trusted")
            result = self.transport(
                "GET",
                location,
                {
                    "Accept": "application/octet-stream",
                    "User-Agent": "omega-release-promotion/1",
                },
                None,
                self.timeout,
                MAX_BLOB_BYTES,
            )
        if result.status != 200:
            raise PromotionError(f"GHCR blob lookup returned HTTP {result.status}")
        body = result.body
        if len(body) != descriptor["size"] or _sha256(body) != digest:
            raise PromotionError("GHCR blob bytes do not match OCI descriptor")
        return body

    def head_blob(self, service: str, descriptor: Mapping[str, Any]) -> None:
        """Prove that one layer blob is present without loading it into memory."""

        descriptor = _validate_descriptor(descriptor, label="OCI layer descriptor")
        digest = descriptor["digest"]
        repository = (
            f"{quote(self.identity.owner, safe='')}/{quote(service, safe='')}"
        )
        url = f"{GHCR_ORIGIN}/v2/{repository}/blobs/{quote(digest, safe=':')}"
        result = self.transport(
            "HEAD",
            url,
            {
                "Accept": "application/octet-stream",
                "Authorization": f"Bearer {self._token(service, 'pull')}",
                "User-Agent": "omega-release-promotion/2",
            },
            None,
            self.timeout,
            0,
        )
        authoritative_digest = _header(result.headers, "Docker-Content-Digest")
        if result.status in {302, 307}:
            location = _header(result.headers, "Location")
            parts = urlsplit(location)
            if (
                parts.scheme != "https"
                or parts.hostname != "pkg-containers.githubusercontent.com"
                or parts.username is not None
                or parts.password is not None
                or parts.fragment
            ):
                raise PromotionError("GHCR layer redirect target is not trusted")
            result = self.transport(
                "HEAD",
                location,
                {
                    "Accept": "application/octet-stream",
                    "User-Agent": "omega-release-promotion/2",
                },
                None,
                self.timeout,
                0,
            )
            redirected_digest = _header(result.headers, "Docker-Content-Digest")
            if redirected_digest:
                authoritative_digest = redirected_digest
        if result.status != 200:
            raise PromotionError(f"GHCR layer HEAD returned HTTP {result.status}")
        raw_length = _header(result.headers, "Content-Length")
        try:
            length = int(raw_length)
        except (TypeError, ValueError) as exc:
            raise PromotionError("GHCR layer HEAD has invalid Content-Length") from exc
        if length != descriptor["size"]:
            raise PromotionError("GHCR layer HEAD size does not match descriptor")
        if authoritative_digest != digest:
            raise PromotionError("GHCR layer HEAD digest does not match descriptor")

    def put_manifest(
        self,
        service: str,
        reference: str,
        *,
        body: bytes,
        media_type: str,
        expected_digest: str,
    ) -> None:
        if media_type not in {OCI_INDEX, OCI_MANIFEST}:
            raise PromotionError("promotion manifest media type is invalid")
        if _sha256(body) != expected_digest:
            raise PromotionError("promotion bytes do not match intended digest")
        result = self.transport(
            "PUT",
            self._manifest_url(service, reference),
            {
                "Accept": OCI_ACCEPT,
                "Authorization": f"Bearer {self._token(service, 'pull,push')}",
                "Content-Type": media_type,
                "If-None-Match": "*",
                "User-Agent": "omega-release-promotion/1",
            },
            body,
            self.timeout,
            MAX_JSON_BYTES,
        )
        if result.status != 201:
            raise PromotionError(f"GHCR manifest PUT returned HTTP {result.status}")
        response_digest = _validate_digest(
            _header(result.headers, "Docker-Content-Digest"),
            label="GHCR promotion response digest",
        )
        if response_digest != expected_digest:
            raise PromotionError("GHCR promotion response digest mismatch")


def _manifest_object(
    result: HttpResult,
    *,
    expected_digest: str,
    expected_media_type: str,
    label: str,
) -> dict[str, Any]:
    if _sha256(result.body) != expected_digest:
        raise PromotionError(f"{label} digest does not match bytes")
    content_type = _header(result.headers, "Content-Type").split(";", 1)[0]
    if content_type.strip().lower() != expected_media_type:
        raise PromotionError(f"{label} response media type is invalid")
    value = _parse_json_object(result.body, label=label)
    if value.get("schemaVersion") != 2 or value.get("mediaType") != expected_media_type:
        raise PromotionError(f"{label} OCI identity is invalid")
    return value


def _require_exact_annotations(
    value: object, expected: Mapping[str, str], *, label: str
) -> None:
    if value != dict(expected):
        raise PromotionError(f"{label} annotations are not exact release identity")


def _require_descriptor_bytes(
    result: HttpResult, descriptor: Mapping[str, Any], *, label: str
) -> None:
    if len(result.body) != descriptor["size"]:
        raise PromotionError(f"{label} size does not match descriptor")
    if _sha256(result.body) != descriptor["digest"]:
        raise PromotionError(f"{label} digest does not match descriptor")


def verify_candidate(
    registry: RegistryClient,
    *,
    service: str,
    digest: str,
) -> dict[str, object]:
    """Validate the complete candidate graph and return its sealed receipt data."""

    _validate_service(service)
    digest = _validate_digest(digest, label="candidate digest")
    identity = registry.identity
    expected = identity.annotations(service)
    top_result = registry.get_manifest(service, digest)
    if top_result is None:  # pragma: no cover - allow_absent is false
        raise PromotionError("candidate digest is absent")
    top = _manifest_object(
        top_result,
        expected_digest=digest,
        expected_media_type=OCI_INDEX,
        label="candidate OCI index",
    )
    _require_exact_annotations(
        top.get("annotations"), expected, label="candidate OCI index"
    )
    manifests = top.get("manifests")
    if not isinstance(manifests, list) or len(manifests) != 2:
        raise PromotionError(
            "candidate OCI index must contain one image and one attestation"
        )
    runnable: dict[str, Any] | None = None
    attestation: dict[str, Any] | None = None
    for raw_descriptor in manifests:
        descriptor = _validate_descriptor(raw_descriptor, label="index manifest")
        platform = descriptor.get("platform")
        if platform == {"architecture": "amd64", "os": "linux"}:
            if runnable is not None:
                raise PromotionError("candidate has duplicate runnable manifests")
            if descriptor.get("mediaType") != OCI_MANIFEST:
                raise PromotionError("runnable descriptor media type is invalid")
            _require_exact_annotations(
                descriptor.get("annotations"),
                expected,
                label="runnable descriptor",
            )
            runnable = descriptor
        elif platform == {"architecture": "unknown", "os": "unknown"}:
            if attestation is not None:
                raise PromotionError("candidate has duplicate attestations")
            if descriptor.get("mediaType") != OCI_MANIFEST:
                raise PromotionError("attestation descriptor media type is invalid")
            attestation = descriptor
        else:
            raise PromotionError("candidate OCI index has an unexpected platform")
    if runnable is None or attestation is None:
        raise PromotionError("candidate image or attestation is missing")

    runnable_result = registry.get_manifest(service, runnable["digest"])
    if runnable_result is None:  # pragma: no cover
        raise PromotionError("runnable manifest is absent")
    _require_descriptor_bytes(
        runnable_result, runnable, label="runnable manifest"
    )
    runnable_manifest = _manifest_object(
        runnable_result,
        expected_digest=runnable["digest"],
        expected_media_type=OCI_MANIFEST,
        label="runnable manifest",
    )
    _require_exact_annotations(
        runnable_manifest.get("annotations"),
        expected,
        label="runnable manifest",
    )
    config = _validate_descriptor(
        runnable_manifest.get("config"),
        label="runnable config",
        media_type=OCI_CONFIG,
    )
    layers = runnable_manifest.get("layers")
    if not isinstance(layers, list) or not layers:
        raise PromotionError("runnable manifest has no layers")
    for layer in layers:
        _validate_descriptor(layer, label="runnable layer")
    config_bytes = registry.get_blob(service, config)
    config_object = _parse_json_object(config_bytes, label="runnable config")
    if config_object.get("architecture") != "amd64" or config_object.get("os") != "linux":
        raise PromotionError("runnable config platform is invalid")
    config_section = config_object.get("config")
    labels = config_section.get("Labels") if isinstance(config_section, dict) else None
    if labels != expected_config_labels(identity, service):
        raise PromotionError("runnable config labels are not exact release identity")

    attestation_expected = {
        "vnd.docker.reference.digest": runnable["digest"],
        "vnd.docker.reference.type": "attestation-manifest",
    }
    _require_exact_annotations(
        attestation.get("annotations"),
        attestation_expected,
        label="attestation descriptor",
    )
    attestation_result = registry.get_manifest(service, attestation["digest"])
    if attestation_result is None:  # pragma: no cover
        raise PromotionError("attestation manifest is absent")
    _require_descriptor_bytes(
        attestation_result, attestation, label="attestation manifest"
    )
    attestation_manifest = _manifest_object(
        attestation_result,
        expected_digest=attestation["digest"],
        expected_media_type=OCI_MANIFEST,
        label="attestation manifest",
    )
    if attestation_manifest.get("artifactType") != OCI_ATTESTATION:
        raise PromotionError("attestation artifact type is invalid")
    subject = attestation_manifest.get("subject")
    subject = _validate_descriptor(subject, label="attestation subject")
    if (
        subject.get("digest") != runnable["digest"]
        or subject.get("mediaType") != OCI_MANIFEST
        or subject.get("size") != runnable["size"]
    ):
        raise PromotionError("attestation subject does not bind runnable manifest")
    empty_config = _validate_descriptor(
        attestation_manifest.get("config"),
        label="attestation config",
        media_type=OCI_EMPTY,
    )
    if registry.get_blob(service, empty_config) != b"{}":
        raise PromotionError("attestation config is not the OCI empty object")
    attestation_layers = attestation_manifest.get("layers")
    if not isinstance(attestation_layers, list) or len(attestation_layers) != 1:
        raise PromotionError("candidate must contain exactly one provenance statement")
    statement_descriptor = _validate_descriptor(
        attestation_layers[0], label="provenance statement", media_type=IN_TOTO
    )
    statement = _parse_json_object(
        registry.get_blob(service, statement_descriptor),
        label="provenance statement",
    )
    _verify_provenance(
        statement,
        identity=identity,
        runnable_digest=runnable["digest"],
    )
    return {
        "schema_version": 1,
        "kind": "omega-release-image-candidate",
        "repository": identity.repository,
        "run_id": identity.run_id,
        "source_sha": identity.source_sha,
        "release_tag": identity.release_tag,
        "service": service,
        "image": identity.image(service),
        "digest": digest,
        "manifest_media_type": OCI_INDEX,
        "manifest_size": len(top_result.body),
        "runnable_digest": runnable["digest"],
        "attestation_digest": attestation["digest"],
    }


def _verify_provenance(
    statement: Mapping[str, Any],
    *,
    identity: ReleaseIdentity,
    runnable_digest: str,
) -> None:
    if statement.get("_type") != "https://in-toto.io/Statement/v0.1":
        raise PromotionError("provenance statement type is invalid")
    if statement.get("predicateType") != "https://slsa.dev/provenance/v1":
        raise PromotionError("provenance predicate type is invalid")
    subjects = statement.get("subject")
    expected_hash = runnable_digest.removeprefix("sha256:")
    if (
        not isinstance(subjects, list)
        or len(subjects) != 1
        or not isinstance(subjects[0], dict)
        or subjects[0].get("digest") != {"sha256": expected_hash}
    ):
        raise PromotionError("provenance subject does not bind runnable manifest")
    predicate = statement.get("predicate")
    build_definition = (
        predicate.get("buildDefinition") if isinstance(predicate, dict) else None
    )
    external = (
        build_definition.get("externalParameters")
        if isinstance(build_definition, dict)
        else None
    )
    root = external.get("root") if isinstance(external, dict) else None
    request = root.get("request") if isinstance(root, dict) else None
    args = request.get("args") if isinstance(request, dict) else None
    if not isinstance(args, list) or not all(isinstance(value, str) for value in args):
        raise PromotionError("provenance build arguments are invalid")
    if f"vcs:revision={identity.source_sha}" not in args:
        raise PromotionError("provenance does not bind source SHA")
    if f"vcs:source={identity.source_url}" not in args:
        raise PromotionError("provenance does not bind source repository")
    run_details = predicate.get("runDetails") if isinstance(predicate, dict) else None
    builder = run_details.get("builder") if isinstance(run_details, dict) else None
    builder_id = builder.get("id") if isinstance(builder, dict) else None
    expected_builder = re.compile(
        rf"^{re.escape(identity.source_url)}/actions/runs/"
        rf"{identity.run_id}/attempts/[1-9][0-9]*$"
    )
    if not isinstance(builder_id, str) or expected_builder.fullmatch(builder_id) is None:
        raise PromotionError("provenance builder does not bind GitHub run_id")


RECEIPT_KEYS = {
    "schema_version",
    "kind",
    "repository",
    "run_id",
    "source_sha",
    "release_tag",
    "service",
    "image",
    "digest",
    "manifest_media_type",
    "manifest_size",
    "runnable_digest",
    "attestation_digest",
}


def validate_receipt(
    receipt: object,
    *,
    identity: ReleaseIdentity,
    service: str,
) -> dict[str, Any]:
    _validate_service(service)
    if not isinstance(receipt, dict) or set(receipt) != RECEIPT_KEYS:
        raise PromotionError("candidate receipt schema is invalid")
    expected = {
        "schema_version": 1,
        "kind": "omega-release-image-candidate",
        "repository": identity.repository,
        "run_id": identity.run_id,
        "source_sha": identity.source_sha,
        "release_tag": identity.release_tag,
        "service": service,
        "image": identity.image(service),
        "manifest_media_type": OCI_INDEX,
    }
    for key, value in expected.items():
        if receipt.get(key) != value:
            raise PromotionError(f"candidate receipt {key} is not bound to this run")
    for key in ("digest", "runnable_digest", "attestation_digest"):
        _validate_digest(receipt.get(key), label=f"candidate receipt {key}")
    size = receipt.get("manifest_size")
    if not isinstance(size, int) or isinstance(size, bool) or size <= 0:
        raise PromotionError("candidate receipt manifest_size is invalid")
    return receipt


class ArtifactClient:
    def __init__(
        self,
        *,
        identity: ReleaseIdentity,
        github_token: str,
        transport: Transport = http_request,
        timeout: float = 20.0,
    ) -> None:
        self.identity = identity
        self.github_token = _validate_token(github_token, label="GitHub token")
        self.transport = transport
        self.timeout = timeout

    @property
    def _headers(self) -> dict[str, str]:
        return {
            "Accept": "application/vnd.github+json",
            "Authorization": f"Bearer {self.github_token}",
            "X-GitHub-Api-Version": GITHUB_API_VERSION,
            "User-Agent": "omega-release-promotion/1",
        }

    def _api(self, path: str) -> HttpResult:
        result = self.transport(
            "GET",
            f"{GITHUB_API}{path}",
            self._headers,
            None,
            self.timeout,
            MAX_ARTIFACT_BYTES,
        )
        if result.status != 200:
            raise PromotionError(
                f"GitHub Actions artifact API returned HTTP {result.status}"
            )
        _require_json_content_type(result, label="GitHub artifact API response")
        return result

    def find(self, name: str) -> dict[str, Any] | None:
        if re.fullmatch(r"[A-Za-z0-9_.-]{1,200}", name) is None:
            raise PromotionError("artifact name is invalid")
        owner, repository = self.identity.repository.split("/", 1)
        path = (
            f"/repos/{quote(owner, safe='')}/{quote(repository, safe='')}"
            f"/actions/runs/{self.identity.run_id}/artifacts?"
            f"{urlencode({'name': name, 'per_page': 100})}"
        )
        payload = _parse_json_object(
            self._api(path).body, label="GitHub artifact list"
        )
        artifacts = payload.get("artifacts")
        total = payload.get("total_count")
        if (
            not isinstance(artifacts, list)
            or not isinstance(total, int)
            or isinstance(total, bool)
            or total != len(artifacts)
        ):
            raise PromotionError("GitHub artifact list schema is invalid")
        if total > 1:
            raise PromotionError(f"duplicate immutable artifact: {name}")
        if total == 0:
            return None
        artifact = artifacts[0]
        if not isinstance(artifact, dict) or artifact.get("name") != name:
            raise PromotionError("GitHub artifact identity is invalid")
        if artifact.get("expired") is not False:
            raise PromotionError("GitHub artifact is expired")
        artifact_id = artifact.get("id")
        size = artifact.get("size_in_bytes")
        digest = artifact.get("digest")
        workflow_run = artifact.get("workflow_run")
        if (
            not isinstance(artifact_id, int)
            or isinstance(artifact_id, bool)
            or artifact_id <= 0
            or not isinstance(size, int)
            or isinstance(size, bool)
            or size <= 0
            or size > MAX_ARTIFACT_BYTES
            or not isinstance(workflow_run, dict)
            or workflow_run.get("id") != self.identity.run_id
            or workflow_run.get("head_sha") != self.identity.source_sha
        ):
            raise PromotionError("GitHub artifact is not bound to this workflow run")
        _validate_digest(digest, label="GitHub artifact archive digest")
        return artifact

    def download_canonical(
        self,
        artifact: Mapping[str, Any],
        *,
        filename: str,
    ) -> tuple[bytes, dict[str, Any]]:
        artifact_id = artifact["id"]
        owner, repository = self.identity.repository.split("/", 1)
        path = (
            f"/repos/{quote(owner, safe='')}/{quote(repository, safe='')}"
            f"/actions/artifacts/{artifact_id}/zip"
        )
        initial = self.transport(
            "GET",
            f"{GITHUB_API}{path}",
            self._headers,
            None,
            self.timeout,
            MAX_ARTIFACT_BYTES,
        )
        if initial.status != 302:
            raise PromotionError(
                f"GitHub artifact archive returned HTTP {initial.status}"
            )
        location = _header(initial.headers, "Location")
        parts = urlsplit(location)
        if (
            parts.scheme != "https"
            or parts.hostname is None
            or not parts.hostname.endswith(".blob.core.windows.net")
            or parts.hostname == ".blob.core.windows.net"
            or parts.username is not None
            or parts.password is not None
            or parts.fragment
        ):
            raise PromotionError("GitHub artifact redirect target is not trusted")
        archive_result = self.transport(
            "GET",
            location,
            {
                "Accept": "application/zip",
                "User-Agent": "omega-release-promotion/1",
            },
            None,
            self.timeout,
            MAX_ARTIFACT_BYTES,
        )
        if archive_result.status != 200:
            raise PromotionError(
                f"GitHub artifact archive download returned HTTP {archive_result.status}"
            )
        archive = archive_result.body
        if len(archive) != artifact["size_in_bytes"]:
            raise PromotionError("GitHub artifact archive size mismatch")
        if _sha256(archive) != artifact["digest"]:
            raise PromotionError("GitHub artifact archive digest mismatch")
        try:
            with zipfile.ZipFile(io.BytesIO(archive), "r") as bundle:
                members = bundle.infolist()
                if len(members) != 1:
                    raise PromotionError("artifact ZIP must contain exactly one file")
                member = members[0]
                path = PurePosixPath(member.filename)
                mode = member.external_attr >> 16
                if (
                    member.filename != filename
                    or path.is_absolute()
                    or ".." in path.parts
                    or member.is_dir()
                    or mode & 0o170000 == 0o120000
                    or member.flag_bits & 0x1
                    or member.file_size > MAX_JSON_BYTES
                ):
                    raise PromotionError("artifact ZIP member is unsafe or unexpected")
                content = bundle.read(member)
        except (zipfile.BadZipFile, RuntimeError, OSError) as exc:
            raise PromotionError("GitHub artifact archive is invalid") from exc
        payload = _parse_json_object(
            content, label="artifact payload", require_canonical=True
        )
        return content, payload

    def load(self, *, name: str, filename: str) -> dict[str, Any] | None:
        artifact = self.find(name)
        if artifact is None:
            return None
        _content, payload = self.download_canonical(artifact, filename=filename)
        return payload


def candidate_artifact_name(service: str) -> str:
    return f"release-image-candidate-{_validate_service(service)}"


def candidate_receipt_filename(service: str) -> str:
    return f"{_validate_service(service)}.release-candidate.json"


INTENT_ARTIFACT = "omega-release-promotion-intent"
INTENT_FILENAME = "omega-release-promotion-intent.json"


def load_receipt(
    artifacts: ArtifactClient,
    registry: RegistryClient,
    *,
    service: str,
) -> dict[str, Any] | None:
    receipt = artifacts.load(
        name=candidate_artifact_name(service),
        filename=candidate_receipt_filename(service),
    )
    if receipt is None:
        return None
    receipt = validate_receipt(
        receipt, identity=registry.identity, service=service
    )
    actual = verify_candidate(registry, service=service, digest=receipt["digest"])
    if receipt != actual:
        raise PromotionError("candidate receipt does not match current OCI evidence")
    return receipt


def _intent(
    identity: ReleaseIdentity, receipts: Sequence[Mapping[str, Any]]
) -> dict[str, Any]:
    if (
        not all(isinstance(receipt, Mapping) for receipt in receipts)
        or [receipt.get("service") for receipt in receipts]
        != list(CANONICAL_SERVICES)
    ):
        raise PromotionError("promotion receipts are not canonical 16/16 inventory")
    return {
        "schema_version": 1,
        "kind": "omega-release-promotion-intent",
        "repository": identity.repository,
        "run_id": identity.run_id,
        "source_sha": identity.source_sha,
        "release_tag": identity.release_tag,
        "images": list(receipts),
    }


def validate_intent(
    value: object,
    *,
    identity: ReleaseIdentity,
    expected: Mapping[str, Any],
) -> dict[str, Any]:
    if value != expected:
        raise PromotionError("promotion intent is not byte-equivalent current intent")
    if not isinstance(value, dict):  # narrowed by equality, retained for typing
        raise PromotionError("promotion intent schema is invalid")
    if value.get("run_id") != identity.run_id:
        raise PromotionError("promotion intent is cross-run")
    return value


def _fragment(identity: ReleaseIdentity, receipt: Mapping[str, Any]) -> dict[str, Any]:
    service = receipt["service"]
    image = identity.image(service)
    digest = receipt["digest"]
    return {
        "schema_version": 1,
        "service": service,
        "image": image,
        "release_tag": identity.release_tag,
        "source_sha": identity.source_sha,
        "digest": digest,
        "release_reference": f"{image}:{identity.release_tag}@{digest}",
        "sha_reference": f"{image}:sha-{identity.source_sha}@{digest}",
    }


def load_all_receipts(
    artifacts: ArtifactClient, registry: RegistryClient
) -> list[dict[str, Any]]:
    receipts: list[dict[str, Any]] = []
    for service in CANONICAL_SERVICES:
        receipt = load_receipt(artifacts, registry, service=service)
        if receipt is None:
            raise PromotionError(f"candidate receipt is absent: {service}")
        receipts.append(receipt)
    return receipts


def _final_references(identity: ReleaseIdentity, service: str) -> tuple[str, str]:
    return identity.release_tag, f"sha-{identity.source_sha}"


def prepare_intent(
    artifacts: ArtifactClient,
    registry: RegistryClient,
    *,
    intent_path: Path,
    fragments_dir: Path,
) -> str:
    receipts = load_all_receipts(artifacts, registry)
    expected = _intent(registry.identity, receipts)
    existing = artifacts.load(name=INTENT_ARTIFACT, filename=INTENT_FILENAME)
    if existing is None:
        for receipt in receipts:
            for reference in _final_references(
                registry.identity, receipt["service"]
            ):
                if (
                    registry.get_manifest(
                        receipt["service"], reference, allow_absent=True
                    )
                    is not None
                ):
                    raise PromotionError(
                        "final image reference exists without sealed promotion intent"
                    )
        action = "upload"
    else:
        validate_intent(existing, identity=registry.identity, expected=expected)
        action = "reuse"
    intent_path.write_bytes(_json_bytes(expected))
    fragments_dir.mkdir(parents=True, exist_ok=False)
    for receipt in receipts:
        service = receipt["service"]
        (fragments_dir / f"{service}.release-digest.json").write_text(
            json.dumps(_fragment(registry.identity, receipt), indent=2, sort_keys=True)
            + "\n",
            encoding="utf-8",
        )
    return action


def confirm_intent(
    artifacts: ArtifactClient,
    registry: RegistryClient,
    *,
    local_intent: Mapping[str, Any],
    retries: int = 4,
    retry_delay: float = 2.0,
) -> dict[str, Any]:
    last_absent = False
    for attempt in range(retries):
        current = artifacts.load(name=INTENT_ARTIFACT, filename=INTENT_FILENAME)
        if current is not None:
            return validate_intent(
                current,
                identity=registry.identity,
                expected=local_intent,
            )
        last_absent = True
        if attempt + 1 < retries:
            time.sleep(retry_delay)
    if last_absent:
        raise PromotionError("sealed promotion intent artifact is absent")
    raise PromotionError("sealed promotion intent could not be confirmed")


def promote(
    registry: RegistryClient,
    *,
    intent: Mapping[str, Any],
    failure_after_put: int | None = None,
) -> int:
    identity = registry.identity
    images = intent.get("images")
    if not isinstance(images, list) or not all(
        isinstance(image, dict) for image in images
    ):
        raise PromotionError("promotion intent images are invalid")
    expected = _intent(identity, [dict(image) for image in images])
    validate_intent(intent, identity=identity, expected=expected)
    put_count = 0
    for receipt in images:
        if not isinstance(receipt, dict):
            raise PromotionError("promotion intent contains an invalid receipt")
        receipt = validate_receipt(
            receipt, identity=identity, service=receipt.get("service", "")
        )
        candidate_result = registry.get_manifest(
            receipt["service"], receipt["digest"]
        )
        if candidate_result is None:  # pragma: no cover
            raise PromotionError("candidate digest was garbage-collected")
        if (
            len(candidate_result.body) != receipt["manifest_size"]
            or _sha256(candidate_result.body) != receipt["digest"]
        ):
            raise PromotionError("candidate manifest no longer matches receipt")
        for reference in _final_references(identity, receipt["service"]):
            current = registry.get_manifest(
                receipt["service"], reference, allow_absent=True
            )
            if current is not None:
                if _sha256(current.body) != receipt["digest"]:
                    raise PromotionError(
                        "final reference conflicts with sealed promotion intent"
                    )
                continue
            # This GET is intentionally adjacent to the mutation.  GHCR does
            # not expose a portable cross-repository transaction primitive.
            registry.put_manifest(
                receipt["service"],
                reference,
                body=candidate_result.body,
                media_type=receipt["manifest_media_type"],
                expected_digest=receipt["digest"],
            )
            put_count += 1
            verified = registry.get_manifest(receipt["service"], reference)
            if verified is None or _sha256(verified.body) != receipt["digest"]:
                raise PromotionError("promoted reference failed immediate verification")
            if failure_after_put is not None and put_count == failure_after_put:
                raise PromotionError("injected failure after registry PUT")
    verify_final(registry, intent=intent)
    return put_count


def verify_final(
    registry: RegistryClient, *, intent: Mapping[str, Any]
) -> None:
    images = intent.get("images")
    if not isinstance(images, list) or len(images) != len(CANONICAL_SERVICES):
        raise PromotionError("final verification has no canonical intent")
    verified = 0
    for receipt in images:
        service = receipt.get("service", "") if isinstance(receipt, dict) else ""
        receipt = validate_receipt(
            receipt, identity=registry.identity, service=service
        )
        verify_candidate(registry, service=service, digest=receipt["digest"])
        for reference in _final_references(registry.identity, service):
            result = registry.get_manifest(service, reference)
            if result is None or _sha256(result.body) != receipt["digest"]:
                raise PromotionError("final image reference does not match intent")
            verified += 1
    expected = 2 * len(CANONICAL_SERVICES)
    if verified != expected:
        raise PromotionError(
            f"final image verification is incomplete: {verified}/{expected}"
        )


def _identity_from_args(args: argparse.Namespace) -> ReleaseIdentity:
    return ReleaseIdentity.create(
        repository=args.repository,
        owner=args.owner,
        source_sha=args.source_sha,
        release_tag=args.release_tag,
        run_id=args.run_id,
    )


def _clients(
    args: argparse.Namespace,
) -> tuple[ArtifactClient, RegistryClient]:
    identity = _identity_from_args(args)
    token = os.environ.get("GITHUB_TOKEN", "")
    registry = RegistryClient(
        identity=identity,
        username=args.username,
        github_token=token,
    )
    artifacts = ArtifactClient(identity=identity, github_token=token)
    return artifacts, registry


def _write_output(path: str | None, **values: object) -> None:
    if not path:
        return
    with Path(path).open("a", encoding="utf-8") as output:
        for key, value in values.items():
            output.write(f"{key}={value}\n")


def _read_canonical(path: Path, *, label: str) -> dict[str, Any]:
    try:
        body = path.read_bytes()
    except OSError as exc:
        raise PromotionError(f"{label} could not be read") from exc
    return _parse_json_object(body, label=label, require_canonical=True)


def _add_identity(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--repository", required=True)
    parser.add_argument("--owner", required=True)
    parser.add_argument("--source-sha", required=True)
    parser.add_argument("--release-tag", required=True)
    parser.add_argument("--run-id", required=True, type=int)
    parser.add_argument("--username", required=True)


def main(argv: list[str] | None = None) -> int:
    del argv
    print(
        "LEGACY RELEASE IMAGE PROMOTION DISABLED: use the tagless "
        "scripts/release_digest_chain.py authority",
        file=sys.stderr,
    )
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
