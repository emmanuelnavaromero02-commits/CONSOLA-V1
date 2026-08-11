#!/usr/bin/env python3
"""Fail-closed GHCR candidate sealing and digest-only release promotion.

The release-candidate workflow builds service images by digest, assigns
``candidate-<commit>`` only after all 15 digest receipts validate, and seals
the inventory in a deterministic OCI artifact.  The tag workflow later
promotes those exact registry manifests to the release tag without rebuilding.

Authentication is intentionally limited to the ephemeral ``GITHUB_TOKEN``.
Neither registry bearer tokens nor response headers are ever printed.
"""

from __future__ import annotations

import argparse
import base64
import dataclasses
import hashlib
import json
import os
import re
import subprocess
import sys
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Iterable, Mapping
from pathlib import Path
from typing import Any, Protocol

CANONICAL_REPOSITORY = "emmanuelnavaromero02-commits/CONSOLA-V1"
CANONICAL_OWNER = "emmanuelnavaromero02-commits"
CANONICAL_SOURCE = f"https://github.com/{CANONICAL_REPOSITORY}"
CANONICAL_ORIGIN_URLS = {
    f"https://github.com/{CANONICAL_REPOSITORY}",
    f"https://github.com/{CANONICAL_REPOSITORY}.git",
    f"git@github.com:{CANONICAL_REPOSITORY}.git",
    f"ssh://git@github.com/{CANONICAL_REPOSITORY}.git",
}
REGISTRY = "ghcr.io"
MANIFEST_PACKAGE = "release-candidate-manifests"
PROTECTED_PRIVATE_PACKAGES = frozenset({"banxico", "inegi", "sec_edgar"})
MANIFEST_SCHEMA = 1
MANIFEST_LAYER_MEDIA_TYPE = "application/vnd.omega.release-candidate.v1+json"
OCI_MANIFEST_MEDIA_TYPE = "application/vnd.oci.image.manifest.v1+json"
OCI_INDEX_MEDIA_TYPES = {
    "application/vnd.oci.image.index.v1+json",
    "application/vnd.docker.distribution.manifest.list.v2+json",
}
IMAGE_MANIFEST_MEDIA_TYPES = {
    OCI_MANIFEST_MEDIA_TYPE,
    "application/vnd.docker.distribution.manifest.v2+json",
}
MANIFEST_ACCEPT = (
    "application/vnd.oci.image.index.v1+json, "
    "application/vnd.docker.distribution.manifest.list.v2+json, "
    f"{OCI_MANIFEST_MEDIA_TYPE}, "
    "application/vnd.docker.distribution.manifest.v2+json"
)
SHA_RE = re.compile(r"^[0-9a-f]{40}$")
DIGEST_RE = re.compile(r"^sha256:[0-9a-f]{64}$")
TAG_RE = re.compile(r"^v[0-9]+\.[0-9]+\.[0-9]+(?:-[0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*)?$")
TAG_BINDING_PREFIX = "OMEGA-Release-Candidate-Manifest-SHA256: "


class ReleaseImageError(RuntimeError):
    """A release invariant was not satisfied."""


@dataclasses.dataclass(frozen=True)
class ImageSpec:
    service: str
    context: str
    dockerfile: str


IMAGES = (
    ImageSpec("console", ".", "./console/Dockerfile"),
    ImageSpec("workspace", "./workspace", "./workspace/Dockerfile"),
    ImageSpec("refinement", ".", "./refinement/Dockerfile"),
    ImageSpec("vault", "./vault", "./vault/Dockerfile"),
    ImageSpec("mcp-infra", ".", "./mcp-infra/Dockerfile"),
    ImageSpec("airflow", "./infra/airflow", "./infra/airflow/Dockerfile"),
    ImageSpec("replicon", "./cartridges/replicon", "./cartridges/replicon/Dockerfile"),
    ImageSpec("hubspot", "./cartridges/hubspot", "./cartridges/hubspot/Dockerfile"),
    ImageSpec("banxico", ".", "./cartridges/banxico/Dockerfile"),
    ImageSpec("inegi", ".", "./cartridges/inegi/Dockerfile"),
    ImageSpec("sec_edgar", ".", "./cartridges/sec_edgar/Dockerfile"),
    ImageSpec("sap_hcm", "./cartridges/sap_hcm", "./cartridges/sap_hcm/Dockerfile"),
    ImageSpec(
        "sap_s4hana", "./cartridges/sap_s4hana", "./cartridges/sap_s4hana/Dockerfile"
    ),
    ImageSpec(
        "sap_successfactors",
        "./cartridges/sap_successfactors",
        "./cartridges/sap_successfactors/Dockerfile",
    ),
    ImageSpec(
        "salesforce", "./cartridges/salesforce", "./cartridges/salesforce/Dockerfile"
    ),
)
IMAGE_BY_SERVICE = {item.service: item for item in IMAGES}

if len(IMAGES) != 15 or len(IMAGE_BY_SERVICE) != 15:  # pragma: no cover
    raise RuntimeError("release inventory must contain exactly 15 unique images")


def _canonical_json(value: Any) -> bytes:
    return (json.dumps(value, sort_keys=True, separators=(",", ":")) + "\n").encode()


def _sha256(payload: bytes) -> str:
    return f"sha256:{hashlib.sha256(payload).hexdigest()}"


def _require_sha(value: str) -> str:
    if not SHA_RE.fullmatch(value):
        raise ReleaseImageError(
            "source SHA must be exactly 40 lowercase hexadecimal characters"
        )
    return value


def _require_digest(value: str) -> str:
    if not DIGEST_RE.fullmatch(value):
        raise ReleaseImageError("manifest binding must be an exact sha256 digest")
    return value


def _require_release_tag(value: str) -> str:
    if not TAG_RE.fullmatch(value):
        raise ReleaseImageError(
            "release tag must be an exact vMAJOR.MINOR.PATCH[-PRERELEASE] tag"
        )
    return value


def version_for_tag(tag: str) -> str:
    return _require_release_tag(tag)[1:]


def candidate_tag(source_sha: str) -> str:
    return f"candidate-{_require_sha(source_sha)}"


def image_repository(service: str) -> str:
    if service not in IMAGE_BY_SERVICE:
        raise ReleaseImageError(f"unknown release image service: {service}")
    return f"{CANONICAL_OWNER}/{service}"


def _run_git(
    args: Iterable[str], *, cwd: Path, check: bool = True
) -> subprocess.CompletedProcess[str]:
    result = subprocess.run(
        ["git", *args],
        cwd=cwd,
        check=False,
        text=True,
        capture_output=True,
    )
    if check and result.returncode:
        raise ReleaseImageError(f"git {' '.join(args)} failed")
    return result


def _git_output(args: Iterable[str], *, cwd: Path) -> str:
    return _run_git(args, cwd=cwd).stdout.strip()


def _normalized_origin(origin: str) -> str:
    return origin.rstrip("/")


def _validate_repository_identity(repo_root: Path, repository: str) -> None:
    if repository != CANONICAL_REPOSITORY:
        raise ReleaseImageError(
            "workflow is not running in the canonical GitHub repository"
        )
    origin = _normalized_origin(
        _git_output(["remote", "get-url", "origin"], cwd=repo_root)
    )
    if origin not in CANONICAL_ORIGIN_URLS:
        raise ReleaseImageError("origin is not the canonical repository")


def _read_version(repo_root: Path) -> str:
    raw = (repo_root / "VERSION").read_bytes()
    if not raw or b"\x00" in raw:
        raise ReleaseImageError("VERSION is empty or invalid")
    try:
        value = raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ReleaseImageError("VERSION must be UTF-8") from exc
    if value not in {value.strip(), f"{value.strip()}\n"} or not value.strip():
        raise ReleaseImageError("VERSION must contain exactly one normalized value")
    return value.strip()


def verify_candidate_request(
    *, repo_root: Path, expected_sha: str, release_tag: str, repository: str
) -> str:
    expected_sha = _require_sha(expected_sha)
    release_tag = _require_release_tag(release_tag)
    _validate_repository_identity(repo_root, repository)
    if _git_output(["rev-parse", "HEAD"], cwd=repo_root) != expected_sha:
        raise ReleaseImageError(
            "checked-out commit does not equal the authorized source SHA"
        )
    if _git_output(["cat-file", "-t", expected_sha], cwd=repo_root) != "commit":
        raise ReleaseImageError("authorized source SHA is not a commit")
    main_ref = _git_output(["rev-parse", "refs/remotes/origin/main"], cwd=repo_root)
    if main_ref != expected_sha:
        raise ReleaseImageError(
            "authorized source SHA is not exact current origin/main"
        )
    remote_tag = _run_git(
        [
            "ls-remote",
            "--exit-code",
            "--tags",
            "origin",
            f"refs/tags/{release_tag}",
            f"refs/tags/{release_tag}^{{}}",
        ],
        cwd=repo_root,
        check=False,
    )
    if remote_tag.returncode == 0 or remote_tag.stdout.strip():
        raise ReleaseImageError("release tag already exists; candidate build is closed")
    if remote_tag.returncode != 2:
        raise ReleaseImageError("could not prove that the release tag is absent")
    version = version_for_tag(release_tag)
    if _read_version(repo_root) != version:
        raise ReleaseImageError(
            "VERSION does not exactly match the requested release tag"
        )
    return version


def verify_release_tag(
    *,
    repo_root: Path,
    source_sha: str,
    release_tag: str,
    repository: str,
    expected_tag_object_sha: str | None = None,
) -> tuple[str, str, str]:
    source_sha = _require_sha(source_sha)
    release_tag = _require_release_tag(release_tag)
    _validate_repository_identity(repo_root, repository)
    if _git_output(["rev-parse", "HEAD"], cwd=repo_root) != source_sha:
        raise ReleaseImageError("release checkout does not equal the tag event SHA")
    if (
        _git_output(["cat-file", "-t", f"refs/tags/{release_tag}"], cwd=repo_root)
        != "tag"
    ):
        raise ReleaseImageError(
            "release tag must be annotated; lightweight tags are forbidden"
        )
    tag_object = _git_output(["rev-parse", f"refs/tags/{release_tag}"], cwd=repo_root)
    _require_sha(tag_object)
    if expected_tag_object_sha is not None and tag_object != _require_sha(
        expected_tag_object_sha
    ):
        raise ReleaseImageError("annotated release tag object changed after preflight")
    tag_commit = _git_output(
        ["rev-parse", f"refs/tags/{release_tag}^{{commit}}"], cwd=repo_root
    )
    if tag_commit != source_sha:
        raise ReleaseImageError("release tag does not resolve to the event SHA")
    remote_lines = _git_output(
        [
            "ls-remote",
            "--tags",
            "origin",
            f"refs/tags/{release_tag}",
            f"refs/tags/{release_tag}^{{}}",
        ],
        cwd=repo_root,
    ).splitlines()
    remote_refs = {
        line.split()[1]: line.split()[0]
        for line in remote_lines
        if len(line.split()) == 2
    }
    if remote_refs.get(f"refs/tags/{release_tag}") != tag_object:
        raise ReleaseImageError(
            "remote annotated tag object differs from the checked-out tag"
        )
    if remote_refs.get(f"refs/tags/{release_tag}^{{}}") != source_sha:
        raise ReleaseImageError("remote release tag does not peel to the event SHA")
    main_ref = _git_output(["rev-parse", "refs/remotes/origin/main"], cwd=repo_root)
    if main_ref != source_sha:
        raise ReleaseImageError(
            "release tag no longer points to exact current origin/main"
        )
    version = version_for_tag(release_tag)
    if _read_version(repo_root) != version:
        raise ReleaseImageError("VERSION does not exactly match the release tag")
    raw_tag = _git_output(
        ["cat-file", "tag", f"refs/tags/{release_tag}"], cwd=repo_root
    )
    raw_headers, separator, message = raw_tag.partition("\n\n")
    if not separator:
        raise ReleaseImageError("annotated release tag has no binding message")
    tag_headers: dict[str, str] = {}
    for line in raw_headers.splitlines():
        key, split, value = line.partition(" ")
        if split and key in {"object", "type", "tag"}:
            tag_headers[key] = value
    if tag_headers != {"object": source_sha, "type": "commit", "tag": release_tag}:
        raise ReleaseImageError(
            "annotated release tag is not directly bound to the exact commit"
        )
    bindings = [
        line.removeprefix(TAG_BINDING_PREFIX)
        for line in message.splitlines()
        if line.startswith(TAG_BINDING_PREFIX)
    ]
    if len(bindings) != 1 or not DIGEST_RE.fullmatch(bindings[0]):
        raise ReleaseImageError(
            "annotated release tag must contain one exact candidate manifest binding"
        )
    return version, bindings[0], tag_object


@dataclasses.dataclass(frozen=True)
class RegistryResponse:
    status: int
    headers: Mapping[str, str]
    body: bytes


class _SafeRedirectHandler(urllib.request.HTTPRedirectHandler):
    """Follow HTTPS blob redirects without forwarding registry credentials."""

    def redirect_request(self, request, fp, code, msg, headers, newurl):
        redirected = super().redirect_request(request, fp, code, msg, headers, newurl)
        if redirected is None:
            return None
        old = urllib.parse.urlsplit(request.full_url)
        new = urllib.parse.urlsplit(redirected.full_url)
        try:
            old_port = old.port
            new_port = new.port
        except ValueError as exc:
            raise ReleaseImageError("refusing an unsafe registry redirect") from exc
        if (
            new.scheme != "https"
            or new.username is not None
            or new.password is not None
        ):
            raise ReleaseImageError("refusing an unsafe registry redirect")
        if (old.scheme, old.hostname, old_port) != (new.scheme, new.hostname, new_port):
            authorization = request.get_header("Authorization") or ""
            if authorization.startswith("Basic ") or old.hostname == "api.github.com":
                raise ReleaseImageError(
                    "refusing to redirect a GitHub credential to another host"
                )
            redirected.remove_header("Authorization")
            redirected.unredirected_hdrs.pop("Authorization", None)
            redirected.unredirected_hdrs.pop("authorization", None)
        return redirected


class RegistryProtocol(Protocol):
    def get_manifest(
        self, repository: str, reference: str
    ) -> RegistryResponse | None: ...
    def get_blob(self, repository: str, digest: str) -> bytes: ...
    def put_blob(self, repository: str, payload: bytes) -> str: ...
    def put_manifest(
        self, repository: str, reference: str, payload: bytes, media_type: str
    ) -> str: ...
    def package_visibility(
        self, package: str, *, allow_missing: bool = False
    ) -> str | None: ...


def _parse_bearer_challenge(value: str) -> tuple[str, dict[str, str]]:
    if not value.lower().startswith("bearer "):
        raise ReleaseImageError("GHCR returned an unsupported authentication challenge")
    params: dict[str, str] = {}
    for match in re.finditer(r'(\w+)="([^"]*)"', value[7:]):
        params[match.group(1)] = match.group(2)
    realm = params.pop("realm", "")
    parsed = urllib.parse.urlsplit(realm)
    try:
        parsed_port = parsed.port
    except ValueError as exc:
        raise ReleaseImageError(
            "GHCR authentication realm is not the exact trusted endpoint"
        ) from exc
    if (
        parsed.scheme != "https"
        or parsed.hostname != REGISTRY
        or parsed.netloc != REGISTRY
        or parsed_port is not None
        or parsed.username is not None
        or parsed.password is not None
        or parsed.path != "/token"
        or parsed.query
        or parsed.fragment
    ):
        raise ReleaseImageError(
            "GHCR authentication realm is not the exact trusted endpoint"
        )
    return realm, params


class RegistryClient:
    """Small authenticated OCI Distribution client for GHCR only."""

    def __init__(self, *, actor: str, token: str, timeout: int = 45) -> None:
        if not actor or not token:
            raise ReleaseImageError("GITHUB_ACTOR and GITHUB_TOKEN are required")
        self._actor = actor
        self._token = token
        self._timeout = timeout
        self._opener = urllib.request.build_opener(_SafeRedirectHandler())

    @classmethod
    def from_env(cls) -> RegistryClient:
        return cls(
            actor=os.environ.get("GITHUB_ACTOR", ""),
            token=os.environ.get("GITHUB_TOKEN", ""),
        )

    def _basic(self) -> str:
        value = base64.b64encode(f"{self._actor}:{self._token}".encode()).decode()
        return f"Basic {value}"

    def _open(
        self,
        method: str,
        url: str,
        *,
        body: bytes | None = None,
        headers: Mapping[str, str] | None = None,
        repository: str | None = None,
        allow_missing: bool = False,
    ) -> RegistryResponse | None:
        if not url.startswith("https://"):
            raise ReleaseImageError("refusing a non-HTTPS registry request")
        request_headers = {"User-Agent": "omega-release-images/1"}
        request_headers.update(headers or {})
        request_headers["Authorization"] = self._basic()

        for attempt in range(2):
            request = urllib.request.Request(
                url, data=body, headers=request_headers, method=method
            )
            try:
                with self._opener.open(request, timeout=self._timeout) as response:
                    return RegistryResponse(
                        status=response.status,
                        headers={
                            key.lower(): value
                            for key, value in response.headers.items()
                        },
                        body=response.read(),
                    )
            except urllib.error.HTTPError as exc:
                if exc.code == 404 and allow_missing:
                    return None
                challenge = exc.headers.get("WWW-Authenticate", "")
                if exc.code != 401 or attempt or not repository or not challenge:
                    raise ReleaseImageError(
                        f"GHCR request failed with HTTP {exc.code} ({method})"
                    ) from None
                realm, params = _parse_bearer_challenge(challenge)
                query = urllib.parse.urlencode(params)
                token_url = f"{realm}?{query}" if query else realm
                token_request = urllib.request.Request(
                    token_url,
                    headers={
                        "Accept": "application/json",
                        "Authorization": self._basic(),
                        "User-Agent": "omega-release-images/1",
                    },
                )
                try:
                    with self._opener.open(
                        token_request, timeout=self._timeout
                    ) as response:
                        token_doc = json.load(response)
                except (urllib.error.HTTPError, ValueError) as token_exc:
                    raise ReleaseImageError(
                        "GHCR bearer-token exchange failed"
                    ) from token_exc
                bearer = token_doc.get("token") or token_doc.get("access_token")
                if not isinstance(bearer, str) or not bearer:
                    raise ReleaseImageError("GHCR bearer-token response was invalid")
                request_headers["Authorization"] = f"Bearer {bearer}"
            except urllib.error.URLError as exc:
                raise ReleaseImageError(
                    f"GHCR request transport failed ({method})"
                ) from exc
        raise AssertionError("unreachable")

    def get_manifest(self, repository: str, reference: str) -> RegistryResponse | None:
        quoted = urllib.parse.quote(reference, safe=":")
        return self._open(
            "GET",
            f"https://{REGISTRY}/v2/{repository}/manifests/{quoted}",
            headers={"Accept": MANIFEST_ACCEPT},
            repository=repository,
            allow_missing=True,
        )

    def get_blob(self, repository: str, digest: str) -> bytes:
        if not DIGEST_RE.fullmatch(digest):
            raise ReleaseImageError("invalid OCI blob digest")
        response = self._open(
            "GET",
            f"https://{REGISTRY}/v2/{repository}/blobs/{digest}",
            repository=repository,
        )
        assert response is not None
        if _sha256(response.body) != digest:
            raise ReleaseImageError("OCI blob content does not match its digest")
        return response.body

    def put_blob(self, repository: str, payload: bytes) -> str:
        digest = _sha256(payload)
        present = self._open(
            "HEAD",
            f"https://{REGISTRY}/v2/{repository}/blobs/{digest}",
            repository=repository,
            allow_missing=True,
        )
        if present is not None:
            return digest
        upload = self._open(
            "POST",
            f"https://{REGISTRY}/v2/{repository}/blobs/uploads/",
            body=b"",
            repository=repository,
        )
        assert upload is not None
        location = upload.headers.get("location", "")
        if not location:
            raise ReleaseImageError("GHCR blob upload did not return a location")
        location = urllib.parse.urljoin(f"https://{REGISTRY}", location)
        parsed = urllib.parse.urlsplit(location)
        if (
            parsed.scheme != "https"
            or parsed.netloc != REGISTRY
            or parsed.hostname != REGISTRY
            or parsed.username is not None
            or parsed.password is not None
            or parsed.port is not None
            or not parsed.path.startswith(f"/v2/{repository}/blobs/uploads/")
            or parsed.fragment
        ):
            raise ReleaseImageError("GHCR blob upload location changed registry host")
        query = [
            (key, value)
            for key, value in urllib.parse.parse_qsl(
                parsed.query, keep_blank_values=True
            )
            if key != "digest"
        ]
        query.append(("digest", digest))
        complete_url = urllib.parse.urlunsplit(
            (
                parsed.scheme,
                parsed.netloc,
                parsed.path,
                urllib.parse.urlencode(query),
                parsed.fragment,
            )
        )
        result = self._open(
            "PUT",
            complete_url,
            body=payload,
            headers={"Content-Type": "application/octet-stream"},
            repository=repository,
        )
        assert result is not None
        returned = result.headers.get("docker-content-digest", digest)
        if returned != digest:
            raise ReleaseImageError(
                "GHCR stored blob digest differs from local content"
            )
        return digest

    def put_manifest(
        self, repository: str, reference: str, payload: bytes, media_type: str
    ) -> str:
        response = self._open(
            "PUT",
            f"https://{REGISTRY}/v2/{repository}/manifests/{urllib.parse.quote(reference, safe=':')}",
            body=payload,
            headers={"Content-Type": media_type},
            repository=repository,
        )
        assert response is not None
        expected = _sha256(payload)
        returned = response.headers.get("docker-content-digest", expected)
        if returned != expected:
            raise ReleaseImageError(
                "GHCR manifest digest differs from promoted content"
            )
        return expected

    def package_visibility(
        self, package: str, *, allow_missing: bool = False
    ) -> str | None:
        quoted = urllib.parse.quote(package, safe="")
        last_error: Exception | None = None
        # The canonical owner is a user account, not an organization.  Keeping
        # the endpoint exact also makes an absent-package result unambiguous.
        for namespace in ("users",):
            request = urllib.request.Request(
                f"https://api.github.com/{namespace}/{CANONICAL_OWNER}/packages/container/{quoted}",
                headers={
                    "Accept": "application/vnd.github+json",
                    "Authorization": f"Bearer {self._token}",
                    "X-GitHub-Api-Version": "2022-11-28",
                    "User-Agent": "omega-release-images/1",
                },
            )
            try:
                with self._opener.open(request, timeout=self._timeout) as response:
                    value = json.load(response).get("visibility")
                    return value if isinstance(value, str) else ""
            except urllib.error.HTTPError as exc:
                last_error = exc
                if exc.code == 404:
                    if allow_missing:
                        return None
                    continue
                raise ReleaseImageError(
                    f"GitHub package visibility request failed with HTTP {exc.code}"
                ) from None
            except (urllib.error.URLError, ValueError) as exc:
                last_error = exc
                break
        raise ReleaseImageError(
            "could not verify GitHub package visibility"
        ) from last_error


def _manifest_json(
    response: RegistryResponse, *, expected_digest: str | None = None
) -> dict[str, Any]:
    digest = response.headers.get("docker-content-digest", _sha256(response.body))
    if not DIGEST_RE.fullmatch(digest) or digest != _sha256(response.body):
        raise ReleaseImageError("registry manifest digest is absent or inconsistent")
    if expected_digest is not None and digest != expected_digest:
        raise ReleaseImageError("registry manifest changed from its sealed digest")
    try:
        document = json.loads(response.body)
    except ValueError as exc:
        raise ReleaseImageError("registry returned invalid manifest JSON") from exc
    if not isinstance(document, dict) or document.get("schemaVersion") != 2:
        raise ReleaseImageError("registry returned an unsupported manifest schema")
    return document


def _image_config_descriptor(
    registry: RegistryProtocol, repository: str, response: RegistryResponse
) -> Mapping[str, Any]:
    document = _manifest_json(response)
    media_type = (
        document.get("mediaType")
        or response.headers.get("content-type", "").split(";", 1)[0]
    )
    if media_type in OCI_INDEX_MEDIA_TYPES:
        descriptors = document.get("manifests")
        if not isinstance(descriptors, list):
            raise ReleaseImageError("OCI image index has no manifest inventory")
        runnable = []
        for descriptor in descriptors:
            if not isinstance(descriptor, dict):
                continue
            platform = descriptor.get("platform") or {}
            if platform.get("os") not in {None, "unknown"} and platform.get(
                "architecture"
            ) not in {
                None,
                "unknown",
            }:
                runnable.append(descriptor)
        if len(runnable) != 1:
            raise ReleaseImageError(
                "candidate image must contain exactly one runnable platform"
            )
        child_digest = runnable[0].get("digest", "")
        if not DIGEST_RE.fullmatch(child_digest):
            raise ReleaseImageError(
                "candidate platform descriptor has an invalid digest"
            )
        child = registry.get_manifest(repository, child_digest)
        if child is None:
            raise ReleaseImageError("candidate platform manifest is missing")
        document = _manifest_json(child, expected_digest=child_digest)
        media_type = (
            document.get("mediaType")
            or child.headers.get("content-type", "").split(";", 1)[0]
        )
    if media_type not in IMAGE_MANIFEST_MEDIA_TYPES:
        raise ReleaseImageError("candidate reference is not a runnable OCI image")
    config = document.get("config")
    if not isinstance(config, dict):
        raise ReleaseImageError("candidate image has no config descriptor")
    return config


@dataclasses.dataclass(frozen=True)
class ImageRecord:
    service: str
    digest: str
    reference: str
    visibility: str

    def as_json(self) -> dict[str, str]:
        return dataclasses.asdict(self)


def inspect_image_reference(
    registry: RegistryProtocol,
    *,
    service: str,
    reference: str,
    source_sha: str,
    version: str,
    allow_missing: bool = False,
) -> ImageRecord | None:
    _require_sha(source_sha)
    if service not in IMAGE_BY_SERVICE:
        raise ReleaseImageError(f"unknown release image service: {service}")
    repository = image_repository(service)
    response = registry.get_manifest(repository, reference)
    if response is None:
        if allow_missing:
            return None
        raise ReleaseImageError(f"candidate image is missing: {service}")
    document = _manifest_json(response)
    del document
    digest = response.headers.get("docker-content-digest", _sha256(response.body))
    descriptor = _image_config_descriptor(registry, repository, response)
    config_digest = descriptor.get("digest", "")
    if not DIGEST_RE.fullmatch(config_digest):
        raise ReleaseImageError(f"candidate image config digest is invalid: {service}")
    try:
        config = json.loads(registry.get_blob(repository, config_digest))
    except ValueError as exc:
        raise ReleaseImageError(
            f"candidate image config is invalid JSON: {service}"
        ) from exc
    labels = (config.get("config") or {}).get("Labels") or {}
    expected_labels = {
        "org.opencontainers.image.revision": source_sha,
        "org.opencontainers.image.version": version,
        "org.opencontainers.image.source": CANONICAL_SOURCE,
    }
    if any(labels.get(key) != value for key, value in expected_labels.items()):
        raise ReleaseImageError(
            f"candidate image has incorrect OCI provenance labels: {service}"
        )
    visibility = registry.package_visibility(service)
    if service in PROTECTED_PRIVATE_PACKAGES:
        if visibility != "private":
            raise ReleaseImageError(
                f"protected GHCR package must remain private: {service}"
            )
    elif visibility not in {"public", "private"}:
        raise ReleaseImageError(
            f"GHCR package visibility could not be verified: {service}"
        )
    assert isinstance(visibility, str)
    return ImageRecord(
        service=service,
        digest=digest,
        reference=f"{REGISTRY}/{repository}@{digest}",
        visibility=visibility,
    )


def inspect_candidate(
    registry: RegistryProtocol,
    *,
    service: str,
    source_sha: str,
    version: str,
    allow_missing: bool = False,
) -> ImageRecord | None:
    return inspect_image_reference(
        registry,
        service=service,
        reference=candidate_tag(source_sha),
        source_sha=source_sha,
        version=version,
        allow_missing=allow_missing,
    )


def collect_candidate_records(
    registry: RegistryProtocol, *, source_sha: str, version: str
) -> tuple[ImageRecord, ...]:
    records = tuple(
        inspect_candidate(
            registry,
            service=image.service,
            source_sha=source_sha,
            version=version,
        )
        for image in IMAGES
    )
    if len(records) != 15 or any(
        record is None for record in records
    ):  # pragma: no cover
        raise ReleaseImageError("candidate release inventory is not 15/15")
    return tuple(record for record in records if record is not None)


def candidate_manifest(
    *, source_sha: str, release_tag: str, version: str, records: Iterable[ImageRecord]
) -> dict[str, Any]:
    expected_version = version_for_tag(release_tag)
    if version != expected_version:
        raise ReleaseImageError("candidate manifest version does not match release tag")
    rows = [record.as_json() for record in records]
    if [row["service"] for row in rows] != [image.service for image in IMAGES]:
        raise ReleaseImageError(
            "candidate manifest image order or inventory is not canonical"
        )
    return {
        "candidate_tag": candidate_tag(source_sha),
        "images": rows,
        "registry": REGISTRY,
        "release_tag": release_tag,
        "repository": CANONICAL_REPOSITORY,
        "schema_version": MANIFEST_SCHEMA,
        "source": CANONICAL_SOURCE,
        "source_sha": source_sha,
        "version": version,
    }


def _artifact_config(payload_doc: Mapping[str, Any]) -> bytes:
    labels = {
        "org.opencontainers.image.revision": payload_doc["source_sha"],
        "org.opencontainers.image.source": CANONICAL_SOURCE,
        "org.opencontainers.image.version": payload_doc["version"],
    }
    return _canonical_json(
        {
            "architecture": "unknown",
            "config": {"Labels": labels},
            "os": "unknown",
            "rootfs": {
                "diff_ids": [_sha256(_canonical_json(payload_doc))],
                "type": "layers",
            },
        }
    )


def _artifact_manifest(
    payload: bytes, payload_doc: Mapping[str, Any]
) -> tuple[bytes, bytes]:
    config = _artifact_config(payload_doc)
    annotations = {
        "org.opencontainers.image.revision": str(payload_doc["source_sha"]),
        "org.opencontainers.image.source": CANONICAL_SOURCE,
        "org.opencontainers.image.title": "OMEGA sealed release-candidate manifest",
        "org.opencontainers.image.version": str(payload_doc["version"]),
    }
    manifest = _canonical_json(
        {
            "annotations": annotations,
            "artifactType": MANIFEST_LAYER_MEDIA_TYPE,
            "config": {
                "digest": _sha256(config),
                "mediaType": "application/vnd.oci.image.config.v1+json",
                "size": len(config),
            },
            "layers": [
                {
                    "annotations": {
                        "org.opencontainers.image.title": "release-candidate.json"
                    },
                    "digest": _sha256(payload),
                    "mediaType": MANIFEST_LAYER_MEDIA_TYPE,
                    "size": len(payload),
                }
            ],
            "mediaType": OCI_MANIFEST_MEDIA_TYPE,
            "schemaVersion": 2,
        }
    )
    return config, manifest


def seal_candidate(
    registry: RegistryProtocol,
    *,
    source_sha: str,
    release_tag: str,
    version: str,
    output: Path,
) -> str:
    records = collect_candidate_records(
        registry, source_sha=source_sha, version=version
    )
    document = candidate_manifest(
        source_sha=source_sha,
        release_tag=release_tag,
        version=version,
        records=records,
    )
    payload = _canonical_json(document)
    config, oci_manifest = _artifact_manifest(payload, document)
    repository = f"{CANONICAL_OWNER}/{MANIFEST_PACKAGE}"
    tag = candidate_tag(source_sha)
    current = registry.get_manifest(repository, tag)
    # GHCR creates a new container package private by default.  Before the
    # first raw OCI write we require the package to be either absent or already
    # private, then re-read GitHub package metadata immediately after the write.
    # No sealed manifest is returned to a workflow until that read-back proves
    # exact private visibility.
    prior_visibility = registry.package_visibility(MANIFEST_PACKAGE, allow_missing=True)
    if prior_visibility not in {None, "private"}:
        raise ReleaseImageError(
            "release candidate manifest package must remain private"
        )
    if current is not None and prior_visibility is None:
        raise ReleaseImageError(
            "sealed candidate artifact exists without verifiable package metadata"
        )
    if current is not None:
        _manifest_json(current, expected_digest=_sha256(oci_manifest))
        if current.body != oci_manifest:
            raise ReleaseImageError(
                "sealed candidate manifest tag already exists with different content"
            )
    else:
        if registry.put_blob(repository, config) != _sha256(config):
            raise ReleaseImageError(
                "sealed candidate config upload was not content-addressed"
            )
        if registry.put_blob(repository, payload) != _sha256(payload):
            raise ReleaseImageError(
                "sealed candidate payload upload was not content-addressed"
            )
        if registry.put_manifest(
            repository, tag, oci_manifest, OCI_MANIFEST_MEDIA_TYPE
        ) != _sha256(oci_manifest):
            raise ReleaseImageError("sealed candidate manifest upload digest mismatch")
    verified = registry.get_manifest(repository, tag)
    if verified is None or verified.body != oci_manifest:
        raise ReleaseImageError("sealed candidate manifest read-back mismatch")
    _manifest_json(verified, expected_digest=_sha256(oci_manifest))
    if registry.package_visibility(MANIFEST_PACKAGE) != "private":
        raise ReleaseImageError(
            "release candidate manifest package must remain private"
        )
    output.write_bytes(payload)
    return _sha256(payload)


def load_sealed_candidate(
    registry: RegistryProtocol, *, source_sha: str
) -> tuple[dict[str, Any], bytes]:
    repository = f"{CANONICAL_OWNER}/{MANIFEST_PACKAGE}"
    response = registry.get_manifest(repository, candidate_tag(source_sha))
    if response is None:
        raise ReleaseImageError("sealed candidate manifest is missing")
    root = _manifest_json(response)
    if root.get("mediaType") != OCI_MANIFEST_MEDIA_TYPE:
        raise ReleaseImageError(
            "sealed candidate artifact has an unexpected media type"
        )
    layers = root.get("layers")
    if not isinstance(layers, list) or len(layers) != 1:
        raise ReleaseImageError("sealed candidate artifact must have exactly one layer")
    layer = layers[0]
    if (
        not isinstance(layer, dict)
        or layer.get("mediaType") != MANIFEST_LAYER_MEDIA_TYPE
    ):
        raise ReleaseImageError("sealed candidate payload media type is invalid")
    digest = layer.get("digest", "")
    payload = registry.get_blob(repository, digest)
    if layer.get("size") != len(payload):
        raise ReleaseImageError("sealed candidate payload size is invalid")
    try:
        document = json.loads(payload)
    except ValueError as exc:
        raise ReleaseImageError("sealed candidate payload is invalid JSON") from exc
    if not isinstance(document, dict):
        raise ReleaseImageError("sealed candidate payload must be a JSON object")
    config, expected_root = _artifact_manifest(payload, document)
    config_descriptor = root.get("config")
    if not isinstance(config_descriptor, dict):
        raise ReleaseImageError("sealed candidate config descriptor is invalid")
    config_digest = config_descriptor.get("digest", "")
    if config_digest != _sha256(config) or config_descriptor.get("size") != len(config):
        raise ReleaseImageError(
            "sealed candidate config descriptor differs from canonical content"
        )
    if registry.get_blob(repository, config_digest) != config:
        raise ReleaseImageError(
            "sealed candidate config blob differs from canonical content"
        )
    if response.body != expected_root:
        raise ReleaseImageError("sealed candidate OCI artifact is not canonical")
    if registry.package_visibility(MANIFEST_PACKAGE) != "private":
        raise ReleaseImageError(
            "release candidate manifest package must remain private"
        )
    return document, payload


def verify_sealed_candidate(
    registry: RegistryProtocol,
    *,
    source_sha: str,
    release_tag: str,
    version: str,
    bound_manifest_digest: str | None = None,
) -> tuple[dict[str, Any], tuple[ImageRecord, ...]]:
    sealed, sealed_payload = load_sealed_candidate(registry, source_sha=source_sha)
    records = collect_candidate_records(
        registry, source_sha=source_sha, version=version
    )
    expected = candidate_manifest(
        source_sha=source_sha,
        release_tag=release_tag,
        version=version,
        records=records,
    )
    if sealed_payload != _canonical_json(expected):
        raise ReleaseImageError(
            "current candidate images differ from the immutable sealed manifest"
        )
    if bound_manifest_digest is not None and _sha256(sealed_payload) != _require_digest(
        bound_manifest_digest
    ):
        raise ReleaseImageError(
            "annotated tag binding differs from the recomputed candidate manifest"
        )
    return sealed, records


def candidate_state(
    registry: RegistryProtocol,
    *,
    service: str,
    source_sha: str,
    release_tag: str,
    receipt: Path | None = None,
) -> tuple[str, str]:
    version = version_for_tag(release_tag)
    record = inspect_candidate(
        registry,
        service=service,
        source_sha=source_sha,
        version=version,
        allow_missing=True,
    )
    if record is None:
        sealed = registry.get_manifest(
            f"{CANONICAL_OWNER}/{MANIFEST_PACKAGE}", candidate_tag(source_sha)
        )
        if sealed is not None:
            raise ReleaseImageError(
                "sealed candidate exists but one of its images is missing"
            )
        return "missing", ""
    sealed_response = registry.get_manifest(
        f"{CANONICAL_OWNER}/{MANIFEST_PACKAGE}", candidate_tag(source_sha)
    )
    if sealed_response is not None:
        sealed, _ = load_sealed_candidate(registry, source_sha=source_sha)
        if sealed.get("release_tag") != release_tag:
            raise ReleaseImageError(
                "sealed candidate was created for a different release tag"
            )
        by_service = {
            row.get("service"): row
            for row in sealed.get("images", [])
            if isinstance(row, dict)
        }
        if by_service.get(service, {}).get("digest") != record.digest:
            raise ReleaseImageError("candidate image digest changed after sealing")
    if receipt is not None:
        write_digest_receipt(receipt, record)
    return "reusable", record.digest


def write_digest_receipt(path: Path, record: ImageRecord) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(_canonical_json(record.as_json()))


def verify_built_digest(
    registry: RegistryProtocol,
    *,
    service: str,
    source_sha: str,
    release_tag: str,
    digest: str,
    receipt: Path,
) -> ImageRecord:
    if not DIGEST_RE.fullmatch(digest):
        raise ReleaseImageError("build action did not return an exact sha256 digest")
    record = inspect_image_reference(
        registry,
        service=service,
        reference=digest,
        source_sha=source_sha,
        version=version_for_tag(release_tag),
    )
    assert record is not None
    if record.digest != digest:
        raise ReleaseImageError(
            "pushed-by-digest image differs from the build output digest"
        )
    write_digest_receipt(receipt, record)
    return record


def _load_digest_receipts(receipts_dir: Path) -> tuple[ImageRecord, ...]:
    expected_paths = {f"{image.service}.json" for image in IMAGES}
    if not receipts_dir.is_dir():
        raise ReleaseImageError("candidate digest receipt directory is missing")
    actual_paths = {
        path.name
        for path in receipts_dir.iterdir()
        if path.is_file() and not path.name.startswith(".")
    }
    if actual_paths != expected_paths:
        raise ReleaseImageError(
            "candidate digest receipts are not the exact 15-image inventory"
        )
    records = []
    for image in IMAGES:
        path = receipts_dir / f"{image.service}.json"
        try:
            raw = path.read_bytes()
            document = json.loads(raw)
        except (OSError, ValueError) as exc:
            raise ReleaseImageError(
                f"invalid candidate digest receipt: {image.service}"
            ) from exc
        if raw != _canonical_json(document) or not isinstance(document, dict):
            raise ReleaseImageError(
                f"candidate digest receipt is not canonical: {image.service}"
            )
        expected_keys = {"service", "digest", "reference", "visibility"}
        if set(document) != expected_keys or document.get("service") != image.service:
            raise ReleaseImageError(
                f"candidate digest receipt has invalid fields: {image.service}"
            )
        digest = document.get("digest", "")
        if not isinstance(digest, str) or not DIGEST_RE.fullmatch(digest):
            raise ReleaseImageError(
                f"candidate digest receipt has invalid digest: {image.service}"
            )
        expected_reference = f"{REGISTRY}/{image_repository(image.service)}@{digest}"
        if document.get("reference") != expected_reference:
            raise ReleaseImageError(
                f"candidate digest receipt has invalid reference: {image.service}"
            )
        visibility = document.get("visibility")
        if image.service in PROTECTED_PRIVATE_PACKAGES:
            if visibility != "private":
                raise ReleaseImageError(
                    f"protected package receipt is not private: {image.service}"
                )
        elif visibility not in {"public", "private"}:
            raise ReleaseImageError(
                f"candidate digest receipt has invalid visibility: {image.service}"
            )
        assert isinstance(visibility, str)
        records.append(
            ImageRecord(image.service, digest, expected_reference, visibility)
        )
    return tuple(records)


def assemble_candidate(
    registry: RegistryProtocol,
    *,
    source_sha: str,
    release_tag: str,
    receipts_dir: Path,
    output: Path,
) -> str:
    """Aggregate digest-only builds, then create candidate tags and seal once.

    All 15 receipts and all pre-existing candidate tags are validated before
    the first tag write.  Therefore a matrix failure cannot overwrite a partial
    candidate.  A retry accepts only an already-identical digest.
    """

    version = version_for_tag(release_tag)
    records = _load_digest_receipts(receipts_dir)
    by_service = {record.service: record for record in records}
    manifests: dict[str, RegistryResponse] = {}
    missing: list[str] = []
    for image in IMAGES:
        record = by_service[image.service]
        digest_response = registry.get_manifest(
            image_repository(image.service), record.digest
        )
        if digest_response is None:
            raise ReleaseImageError(
                f"digest-only candidate build is missing: {image.service}"
            )
        _manifest_json(digest_response, expected_digest=record.digest)
        inspected = inspect_image_reference(
            registry,
            service=image.service,
            reference=record.digest,
            source_sha=source_sha,
            version=version,
        )
        assert inspected is not None
        if inspected != record:
            raise ReleaseImageError(
                f"digest receipt differs from registry: {image.service}"
            )
        manifests[image.service] = digest_response
        existing = registry.get_manifest(
            image_repository(image.service), candidate_tag(source_sha)
        )
        if existing is None:
            missing.append(image.service)
            continue
        try:
            _manifest_json(existing, expected_digest=record.digest)
        except ReleaseImageError as exc:
            raise ReleaseImageError(
                f"existing candidate tag differs from aggregate digest: {image.service}"
            ) from exc
        if existing.body != digest_response.body:
            raise ReleaseImageError(
                f"existing candidate tag differs from aggregate digest: {image.service}"
            )

    # Only absent tags are created; an existing candidate tag is never moved.
    for service in missing:
        record = by_service[service]
        response = manifests[service]
        media_type = response.headers.get("content-type", "").split(";", 1)[0]
        if media_type not in OCI_INDEX_MEDIA_TYPES | IMAGE_MANIFEST_MEDIA_TYPES:
            raise ReleaseImageError(
                f"candidate manifest media type is unsafe: {service}"
            )
        if (
            registry.put_manifest(
                image_repository(service),
                candidate_tag(source_sha),
                response.body,
                media_type,
            )
            != record.digest
        ):
            raise ReleaseImageError(f"candidate tag digest mismatch: {service}")

    # Read back every tag before sealing the digest inventory.
    for image in IMAGES:
        record = by_service[image.service]
        tagged = registry.get_manifest(
            image_repository(image.service), candidate_tag(source_sha)
        )
        if tagged is None:
            raise ReleaseImageError(
                f"candidate tag is missing after aggregation: {image.service}"
            )
        _manifest_json(tagged, expected_digest=record.digest)
        if tagged.body != manifests[image.service].body:
            raise ReleaseImageError(f"candidate tag read-back differs: {image.service}")
    return seal_candidate(
        registry,
        source_sha=source_sha,
        release_tag=release_tag,
        version=version,
        output=output,
    )


def promotion_preflight(
    registry: RegistryProtocol,
    *,
    source_sha: str,
    release_tag: str,
    version: str,
    bound_manifest_digest: str,
    output: Path,
) -> str:
    sealed, records = verify_sealed_candidate(
        registry,
        source_sha=source_sha,
        release_tag=release_tag,
        version=version,
        bound_manifest_digest=bound_manifest_digest,
    )
    for record in records:
        existing = registry.get_manifest(image_repository(record.service), release_tag)
        if existing is None:
            continue
        try:
            _manifest_json(existing, expected_digest=record.digest)
        except ReleaseImageError as exc:
            raise ReleaseImageError(
                f"existing release tag differs from candidate manifest: {record.service}"
            ) from exc
        candidate = registry.get_manifest(
            image_repository(record.service), candidate_tag(source_sha)
        )
        if candidate is None or existing.body != candidate.body:
            raise ReleaseImageError(
                f"existing release tag differs from candidate manifest: {record.service}"
            )
    payload = _canonical_json(sealed)
    output.write_bytes(payload)
    return _sha256(payload)


def promote_one(
    registry: RegistryProtocol,
    *,
    service: str,
    source_sha: str,
    release_tag: str,
    version: str,
    bound_manifest_digest: str,
) -> str:
    _, records = verify_sealed_candidate(
        registry,
        source_sha=source_sha,
        release_tag=release_tag,
        version=version,
        bound_manifest_digest=bound_manifest_digest,
    )
    expected = next((record for record in records if record.service == service), None)
    if expected is None:
        raise ReleaseImageError(f"service is absent from sealed candidate: {service}")
    repository = image_repository(service)
    candidate = registry.get_manifest(repository, candidate_tag(source_sha))
    if candidate is None:
        raise ReleaseImageError(
            f"candidate image disappeared before promotion: {service}"
        )
    _manifest_json(candidate, expected_digest=expected.digest)
    existing = registry.get_manifest(repository, release_tag)
    if existing is not None:
        try:
            _manifest_json(existing, expected_digest=expected.digest)
        except ReleaseImageError as exc:
            raise ReleaseImageError(
                f"existing release tag differs from candidate: {service}"
            ) from exc
        if existing.body != candidate.body:
            raise ReleaseImageError(
                f"existing release tag differs from candidate: {service}"
            )
    else:
        media_type = candidate.headers.get("content-type", "").split(";", 1)[0]
        if media_type not in OCI_INDEX_MEDIA_TYPES | IMAGE_MANIFEST_MEDIA_TYPES:
            raise ReleaseImageError(
                f"candidate manifest media type is unsafe to promote: {service}"
            )
        promoted = registry.put_manifest(
            repository, release_tag, candidate.body, media_type
        )
        if promoted != expected.digest:
            raise ReleaseImageError(
                f"promoted digest differs from sealed candidate: {service}"
            )
    readback = registry.get_manifest(repository, release_tag)
    if readback is None:
        raise ReleaseImageError(f"release tag read-back failed: {service}")
    _manifest_json(readback, expected_digest=expected.digest)
    if readback.body != candidate.body:
        raise ReleaseImageError(
            f"release tag read-back differs from candidate: {service}"
        )
    return expected.digest


def verify_release_images(
    registry: RegistryProtocol,
    *,
    source_sha: str,
    release_tag: str,
    version: str,
    bound_manifest_digest: str,
    output: Path,
) -> str:
    sealed, records = verify_sealed_candidate(
        registry,
        source_sha=source_sha,
        release_tag=release_tag,
        version=version,
        bound_manifest_digest=bound_manifest_digest,
    )
    for record in records:
        repository = image_repository(record.service)
        candidate = registry.get_manifest(repository, candidate_tag(source_sha))
        released = registry.get_manifest(repository, release_tag)
        if candidate is None or released is None:
            raise ReleaseImageError(
                f"release image is missing after promotion: {record.service}"
            )
        _manifest_json(candidate, expected_digest=record.digest)
        _manifest_json(released, expected_digest=record.digest)
        if candidate.body != released.body:
            raise ReleaseImageError(
                f"release image was not an exact digest promotion: {record.service}"
            )
    document = dict(sealed)
    document["promotion"] = "exact-candidate-digests"
    payload = _canonical_json(document)
    output.write_bytes(payload)
    return _sha256(payload)


def _write_github_output(values: Mapping[str, str]) -> None:
    path = os.environ.get("GITHUB_OUTPUT")
    if not path:
        for key, value in values.items():
            print(f"{key}={value}")
        return
    with Path(path).open("a", encoding="utf-8") as handle:
        for key, value in values.items():
            if "\n" in value or "\r" in value:
                raise ReleaseImageError("refusing multiline GitHub output")
            handle.write(f"{key}={value}\n")


def _add_common_registry_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--source-sha", required=True)
    parser.add_argument("--release-tag", required=True)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)

    matrix = commands.add_parser(
        "matrix", help="emit the canonical 15-image build matrix"
    )
    matrix.add_argument("--github-output", action="store_true")

    verify_candidate = commands.add_parser("verify-candidate-request")
    verify_candidate.add_argument("--repo-root", type=Path, default=Path.cwd())
    verify_candidate.add_argument("--expected-sha", required=True)
    verify_candidate.add_argument("--release-tag", required=True)
    verify_candidate.add_argument("--repository", required=True)

    check = commands.add_parser("check-candidate")
    _add_common_registry_args(check)
    check.add_argument("--service", required=True)
    check.add_argument("--receipt", type=Path)

    built = commands.add_parser("verify-built-digest")
    _add_common_registry_args(built)
    built.add_argument("--service", required=True)
    built.add_argument("--digest", required=True)
    built.add_argument("--receipt", type=Path, required=True)

    seal = commands.add_parser("seal-candidate")
    _add_common_registry_args(seal)
    seal.add_argument("--output", type=Path, required=True)

    assemble = commands.add_parser("assemble-candidate")
    _add_common_registry_args(assemble)
    assemble.add_argument("--receipts-dir", type=Path, required=True)
    assemble.add_argument("--output", type=Path, required=True)

    verify_tag = commands.add_parser("verify-release-tag")
    verify_tag.add_argument("--repo-root", type=Path, default=Path.cwd())
    verify_tag.add_argument("--source-sha", required=True)
    verify_tag.add_argument("--release-tag", required=True)
    verify_tag.add_argument("--repository", required=True)
    verify_tag.add_argument("--expected-tag-object-sha")

    preflight = commands.add_parser("promotion-preflight")
    _add_common_registry_args(preflight)
    preflight.add_argument("--bound-manifest-digest", required=True)
    preflight.add_argument("--output", type=Path, required=True)

    promote = commands.add_parser("promote-one")
    _add_common_registry_args(promote)
    promote.add_argument("--bound-manifest-digest", required=True)
    promote.add_argument("--service", required=True)

    verify = commands.add_parser("verify-release")
    _add_common_registry_args(verify)
    verify.add_argument("--bound-manifest-digest", required=True)
    verify.add_argument("--output", type=Path, required=True)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        if args.command == "matrix":
            value = json.dumps(
                [dataclasses.asdict(image) for image in IMAGES], separators=(",", ":")
            )
            if args.github_output:
                _write_github_output({"matrix": value})
            else:
                print(value)
            return 0

        if args.command == "verify-candidate-request":
            version = verify_candidate_request(
                repo_root=args.repo_root.resolve(),
                expected_sha=args.expected_sha,
                release_tag=args.release_tag,
                repository=args.repository,
            )
            _write_github_output({"version": version})
            print("RELEASE_CANDIDATE_REQUEST\tPASS\texact-current-main\ttag-absent")
            return 0

        if args.command == "verify-release-tag":
            version, manifest_digest, tag_object_sha = verify_release_tag(
                repo_root=args.repo_root.resolve(),
                source_sha=args.source_sha,
                release_tag=args.release_tag,
                repository=args.repository,
                expected_tag_object_sha=args.expected_tag_object_sha,
            )
            _write_github_output(
                {
                    "version": version,
                    "bound_manifest_digest": manifest_digest,
                    "tag_object_sha": tag_object_sha,
                }
            )
            print(
                "RELEASE_TAG\tPASS\texact-current-main\tVERSION-exact\tmanifest-bound"
            )
            return 0

        registry = RegistryClient.from_env()
        version = version_for_tag(args.release_tag)
        if args.command == "check-candidate":
            state, digest = candidate_state(
                registry,
                service=args.service,
                source_sha=args.source_sha,
                release_tag=args.release_tag,
                receipt=args.receipt,
            )
            _write_github_output({"state": state, "digest": digest})
            print(f"CANDIDATE_IMAGE\t{args.service}\t{state.upper()}")
            return 0
        if args.command == "verify-built-digest":
            record = verify_built_digest(
                registry,
                service=args.service,
                source_sha=args.source_sha,
                release_tag=args.release_tag,
                digest=args.digest,
                receipt=args.receipt,
            )
            print(f"CANDIDATE_DIGEST\t{args.service}\tPASS\t{record.digest}")
            return 0
        if args.command == "seal-candidate":
            digest = seal_candidate(
                registry,
                source_sha=args.source_sha,
                release_tag=args.release_tag,
                version=version,
                output=args.output,
            )
            _write_github_output({"manifest_digest": digest})
            print(
                "RELEASE_CANDIDATE_IMAGES\tPASS\t15/15\tvisibility=15/15"
                "\tprotected-private=3/3"
                f"\tmanifest-package=private\tmanifest={digest}"
            )
            return 0
        if args.command == "assemble-candidate":
            digest = assemble_candidate(
                registry,
                source_sha=args.source_sha,
                release_tag=args.release_tag,
                receipts_dir=args.receipts_dir,
                output=args.output,
            )
            _write_github_output({"manifest_digest": digest})
            print(
                "RELEASE_CANDIDATE_IMAGES\tPASS\t15/15\tvisibility=15/15"
                "\tprotected-private=3/3"
                f"\tmanifest-package=private\tmanifest={digest}"
            )
            return 0
        if args.command == "promotion-preflight":
            digest = promotion_preflight(
                registry,
                source_sha=args.source_sha,
                release_tag=args.release_tag,
                version=version,
                bound_manifest_digest=args.bound_manifest_digest,
                output=args.output,
            )
            _write_github_output({"manifest_digest": digest})
            print(f"RELEASE_PROMOTION_PREFLIGHT\tPASS\t15/15\tmanifest={digest}")
            return 0
        if args.command == "promote-one":
            digest = promote_one(
                registry,
                service=args.service,
                source_sha=args.source_sha,
                release_tag=args.release_tag,
                version=version,
                bound_manifest_digest=args.bound_manifest_digest,
            )
            print(f"RELEASE_IMAGE\t{args.service}\tPASS\t{digest}")
            return 0
        if args.command == "verify-release":
            digest = verify_release_images(
                registry,
                source_sha=args.source_sha,
                release_tag=args.release_tag,
                version=version,
                bound_manifest_digest=args.bound_manifest_digest,
                output=args.output,
            )
            _write_github_output({"manifest_digest": digest})
            print(f"RELEASE_IMAGES\tPASS\t15/15\tmanifest={digest}")
            return 0
        raise AssertionError("unknown command")
    except ReleaseImageError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
