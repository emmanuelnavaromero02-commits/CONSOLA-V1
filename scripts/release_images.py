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
import stat
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

    @classmethod
    def from_private_auth_file(cls, path: Path) -> RegistryClient:
        """Load one Docker GHCR credential from a confined private file.

        GCP writes this file only below its root-owned tmpfs authentication
        directory.  Opening it directly with ``O_NOFOLLOW`` avoids a pathname
        check/use race, and the credential remains process-memory-only.
        """

        if not path.is_absolute() or path.name != "config.json":
            raise ReleaseImageError("GHCR auth file must be one absolute config.json")
        parent = path.parent
        try:
            parent_info = parent.lstat()
        except OSError as exc:
            raise ReleaseImageError("GHCR auth directory is unavailable") from exc
        if (
            not stat.S_ISDIR(parent_info.st_mode)
            or parent_info.st_uid != os.geteuid()
            or stat.S_IMODE(parent_info.st_mode) != 0o700
            or parent.resolve(strict=True) != parent
        ):
            raise ReleaseImageError("GHCR auth directory is not privately confined")
        flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
        try:
            descriptor = os.open(path, flags)
        except OSError as exc:
            raise ReleaseImageError("GHCR auth file is unavailable or linked") from exc
        try:
            info = os.fstat(descriptor)
            if (
                not stat.S_ISREG(info.st_mode)
                or info.st_uid != os.geteuid()
                or stat.S_IMODE(info.st_mode) != 0o600
                or info.st_nlink != 1
                or not 2 <= info.st_size <= 65536
            ):
                raise ReleaseImageError("GHCR auth file ownership or mode is unsafe")
            raw = b""
            while len(raw) <= 65536:
                chunk = os.read(descriptor, 65536 - len(raw) + 1)
                if not chunk:
                    break
                raw += chunk
            if len(raw) != info.st_size or len(raw) > 65536:
                raise ReleaseImageError("GHCR auth file changed while reading")
        finally:
            os.close(descriptor)
        try:
            document = json.loads(raw)
        except (UnicodeDecodeError, ValueError) as exc:
            raise ReleaseImageError("GHCR auth file is invalid JSON") from exc
        if not isinstance(document, dict) or set(document) != {"auths"}:
            raise ReleaseImageError("GHCR auth file has an unexpected schema")
        auths = document.get("auths")
        if not isinstance(auths, dict) or set(auths) != {REGISTRY}:
            raise ReleaseImageError("GHCR auth file is not registry-exclusive")
        record = auths.get(REGISTRY)
        if not isinstance(record, dict) or set(record) != {"auth"}:
            raise ReleaseImageError("GHCR auth record has an unexpected schema")
        encoded = record.get("auth")
        if (
            not isinstance(encoded, str)
            or re.fullmatch(r"[A-Za-z0-9+/]+={0,2}", encoded) is None
        ):
            raise ReleaseImageError("GHCR auth record is not exact base64")
        try:
            decoded = base64.b64decode(encoded, validate=True).decode("utf-8")
        except (ValueError, UnicodeDecodeError) as exc:
            raise ReleaseImageError("GHCR auth record is invalid") from exc
        actor, separator, token = decoded.partition(":")
        if (
            not separator
            or ":" in token
            or re.fullmatch(r"[A-Za-z0-9](?:[A-Za-z0-9-]{0,37}[A-Za-z0-9])?", actor)
            is None
            or not 1 <= len(token) <= 4096
            or any(character.isspace() or ord(character) < 0x21 for character in token)
        ):
            raise ReleaseImageError("GHCR auth record credentials are invalid")
        return cls(actor=actor, token=token)

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
    *,
    source_sha: str,
    release_tag: str,
    version: str,
    records: Iterable[ImageRecord],
    github_run_id: str,
    github_run_attempt: str,
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
        "github_run_id": _require_workflow_run_value(
            github_run_id, field="github_run_id"
        ),
        "github_run_attempt": _require_workflow_run_value(
            github_run_attempt, field="github_run_attempt"
        ),
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
    records: tuple[ImageRecord, ...],
    digest_manifests: Mapping[str, RegistryResponse],
    github_run_id: str,
    github_run_attempt: str,
) -> str:
    expected_services = tuple(image.service for image in IMAGES)
    if tuple(record.service for record in records) != expected_services or set(
        digest_manifests
    ) != set(expected_services):
        raise ReleaseImageError("seal input is not the exact receipt-bound inventory")
    for record in records:
        response = digest_manifests[record.service]
        _manifest_json(response, expected_digest=record.digest)
    document = candidate_manifest(
        source_sha=source_sha,
        release_tag=release_tag,
        version=version,
        records=records,
        github_run_id=github_run_id,
        github_run_attempt=github_run_attempt,
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
    if current is not None:
        raise ReleaseImageError(
            "candidate namespace already exists; sealed manifest collision"
        )
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
        github_run_id=str(sealed.get("github_run_id", "")),
        github_run_attempt=str(sealed.get("github_run_attempt", "")),
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


def _records_from_bound_payload(
    payload: bytes,
    *,
    source_sha: str,
    release_tag: str,
    version: str,
    bound_manifest_digest: str,
) -> tuple[dict[str, Any], tuple[ImageRecord, ...]]:
    bound_manifest_digest = _require_digest(bound_manifest_digest)
    if _sha256(payload) != bound_manifest_digest:
        raise ReleaseImageError("sealed candidate payload differs from its binding")
    try:
        document = json.loads(payload)
    except (UnicodeDecodeError, ValueError) as exc:
        raise ReleaseImageError("sealed candidate payload is invalid JSON") from exc
    if not isinstance(document, dict) or payload != _canonical_json(document):
        raise ReleaseImageError("sealed candidate payload is not canonical JSON")
    exact_keys = {
        "candidate_tag",
        "images",
        "registry",
        "release_tag",
        "repository",
        "schema_version",
        "source",
        "source_sha",
        "version",
        "github_run_id",
        "github_run_attempt",
    }
    if (
        set(document) != exact_keys
        or document.get("schema_version") != MANIFEST_SCHEMA
        or document.get("candidate_tag") != candidate_tag(source_sha)
        or document.get("registry") != REGISTRY
        or document.get("release_tag") != _require_release_tag(release_tag)
        or document.get("repository") != CANONICAL_REPOSITORY
        or document.get("source") != CANONICAL_SOURCE
        or document.get("source_sha") != _require_sha(source_sha)
        or document.get("version") != version
        or re.fullmatch(r"[1-9][0-9]{0,19}", str(document.get("github_run_id", "")))
        is None
        or re.fullmatch(
            r"[1-9][0-9]{0,19}", str(document.get("github_run_attempt", ""))
        )
        is None
        or version_for_tag(release_tag) != version
        or not isinstance(document.get("images"), list)
        or len(document["images"]) != len(IMAGES)
    ):
        raise ReleaseImageError("sealed candidate payload identity or shape differs")
    records: list[ImageRecord] = []
    for spec, row in zip(IMAGES, document["images"], strict=True):
        if not isinstance(row, dict) or set(row) != {
            "service",
            "digest",
            "reference",
            "visibility",
        }:
            raise ReleaseImageError("sealed candidate image row shape differs")
        digest = row.get("digest")
        visibility = row.get("visibility")
        expected_reference = f"{REGISTRY}/{image_repository(spec.service)}@{digest}"
        if (
            row.get("service") != spec.service
            or not isinstance(digest, str)
            or DIGEST_RE.fullmatch(digest) is None
            or row.get("reference") != expected_reference
            or (
                visibility != "private"
                if spec.service in PROTECTED_PRIVATE_PACKAGES
                else visibility not in {"public", "private"}
            )
        ):
            raise ReleaseImageError(
                f"sealed candidate image identity differs: {spec.service}"
            )
        assert isinstance(visibility, str)
        records.append(
            ImageRecord(spec.service, digest, expected_reference, visibility)
        )
    return document, tuple(records)


def _records_from_runtime_images(
    path: Path, *, source_sha: str, version: str
) -> tuple[tuple[ImageRecord, ...], dict[str, str]]:
    try:
        raw = path.read_bytes()
        document = json.loads(raw)
    except (OSError, UnicodeDecodeError, ValueError) as exc:
        raise ReleaseImageError("rollback runtime image authority is invalid") from exc
    if (
        not isinstance(document, dict)
        or set(document)
        != {
            "schema_version",
            "source_release",
            "images",
            "secret_values_included",
        }
        or document.get("schema_version") != 2
        or document.get("source_release")
        != {"deploy_ref": _require_sha(source_sha), "version": version}
        or document.get("secret_values_included") is not False
        or not isinstance(document.get("images"), dict)
        or set(document["images"]) != set(IMAGE_BY_SERVICE)
    ):
        raise ReleaseImageError("rollback runtime image authority shape differs")
    records = []
    image_ids: dict[str, str] = {}
    for spec in IMAGES:
        row = document["images"][spec.service]
        repository = f"{REGISTRY}/{image_repository(spec.service)}"
        if not isinstance(row, dict) or set(row) != {
            "configured_ref",
            "repo_digest",
            "image_id",
        }:
            raise ReleaseImageError(
                f"rollback runtime image row shape differs: {spec.service}"
            )
        repo_digest = row.get("repo_digest", "")
        expected_prefix = f"{repository}@"
        digest = repo_digest.removeprefix(expected_prefix)
        if (
            not isinstance(repo_digest, str)
            or not repo_digest.startswith(expected_prefix)
            or DIGEST_RE.fullmatch(digest) is None
            or re.fullmatch(r"sha256:[0-9a-f]{64}", str(row.get("image_id", "")))
            is None
            or str(row.get("configured_ref", ""))
            not in {
                f"{repository}:v{version}",
                f"{repository}:v{version}@{digest}",
            }
        ):
            raise ReleaseImageError(
                f"rollback runtime image identity differs: {spec.service}"
            )
        records.append(
            ImageRecord(
                spec.service,
                digest,
                repo_digest,
                "private" if spec.service in PROTECTED_PRIVATE_PACKAGES else "unknown",
            )
        )
        image_ids[spec.service] = str(row["image_id"])
    return tuple(records), image_ids


def verify_bound_sealed_lock(
    registry: RegistryProtocol,
    *,
    authority_mode: str,
    source_sha: str,
    release_tag: str,
    image_tag: str,
    version: str,
    output: Path,
    bound_manifest_digest: str | None = None,
    tag_object_sha: str | None = None,
    github_run_id: str | None = None,
    github_run_attempt: str | None = None,
    controller_attestation_sha256: str | None = None,
    runtime_images: Path | None = None,
    legacy_tag_commit: str | None = None,
) -> str:
    """Build deterministic GCP image authority from immutable evidence."""

    source_sha = _require_sha(source_sha)
    release_tag = _require_release_tag(release_tag)
    if version_for_tag(release_tag) != version:
        raise ReleaseImageError("GCP image authority version differs from release tag")
    workflow: dict[str, str] | None = None
    legacy_image_ids: dict[str, str] | None = None
    if authority_mode == "published":
        if (
            image_tag != release_tag
            or bound_manifest_digest is None
            or tag_object_sha is None
            or SHA_RE.fullmatch(tag_object_sha) is None
            or any(
                value is not None
                for value in (
                    github_run_id,
                    github_run_attempt,
                    controller_attestation_sha256,
                    runtime_images,
                    legacy_tag_commit,
                )
            )
        ):
            raise ReleaseImageError("published image authority inputs are ambiguous")
    elif authority_mode == "candidate":
        if (
            image_tag != candidate_tag(source_sha)
            or bound_manifest_digest is None
            or github_run_id is None
            or github_run_attempt is None
            or controller_attestation_sha256 is None
            or re.fullmatch(r"[0-9a-f]{64}", controller_attestation_sha256) is None
            or controller_attestation_sha256
            != _require_digest(bound_manifest_digest).removeprefix("sha256:")
            or tag_object_sha is not None
            or runtime_images is not None
            or legacy_tag_commit is not None
        ):
            raise ReleaseImageError("candidate image authority inputs are incomplete")
        workflow = {
            "run_id": _require_workflow_run_value(github_run_id, field="github_run_id"),
            "run_attempt": _require_workflow_run_value(
                github_run_attempt, field="github_run_attempt"
            ),
            "head_sha": source_sha,
            "controller_attestation_sha256": controller_attestation_sha256,
        }
    elif authority_mode == "legacy-rollback":
        if (
            image_tag != release_tag
            or runtime_images is None
            or any(
                value is not None
                for value in (
                    bound_manifest_digest,
                    github_run_id,
                    github_run_attempt,
                    controller_attestation_sha256,
                    tag_object_sha,
                )
            )
            or legacy_tag_commit is None
            or SHA_RE.fullmatch(legacy_tag_commit) is None
        ):
            raise ReleaseImageError("rollback image authority inputs are incomplete")
        records, legacy_image_ids = _records_from_runtime_images(
            runtime_images, source_sha=source_sha, version=version
        )
    else:
        raise ReleaseImageError("GCP image authority mode is invalid")

    if authority_mode != "legacy-rollback":
        assert bound_manifest_digest is not None
        if registry.package_visibility(MANIFEST_PACKAGE) != "private":
            raise ReleaseImageError(
                "release candidate manifest package must remain private"
            )
        payload = registry.get_blob(
            f"{CANONICAL_OWNER}/{MANIFEST_PACKAGE}",
            _require_digest(bound_manifest_digest),
        )
        sealed_document, records = _records_from_bound_payload(
            payload,
            source_sha=source_sha,
            release_tag=release_tag,
            version=version,
            bound_manifest_digest=bound_manifest_digest,
        )
        if authority_mode == "candidate" and (
            sealed_document.get("github_run_id") != github_run_id
            or sealed_document.get("github_run_attempt") != github_run_attempt
        ):
            raise ReleaseImageError(
                "sealed payload was not produced by the controller-attested run"
            )
        if authority_mode == "published":
            workflow = {
                "run_id": str(sealed_document["github_run_id"]),
                "run_attempt": str(sealed_document["github_run_attempt"]),
                "head_sha": source_sha,
            }

    for package in PROTECTED_PRIVATE_PACKAGES:
        if registry.package_visibility(package) != "private":
            raise ReleaseImageError(
                f"protected GHCR package must remain private: {package}"
            )
    for record in records:
        repository = image_repository(record.service)
        immutable = registry.get_manifest(repository, record.digest)
        drift_tag = (
            None
            if authority_mode == "legacy-rollback"
            else registry.get_manifest(repository, image_tag)
        )
        if immutable is None or (
            authority_mode != "legacy-rollback" and drift_tag is None
        ):
            raise ReleaseImageError(
                f"GCP image authority is not pullable: {record.service}"
            )
        _manifest_json(immutable, expected_digest=record.digest)
        if drift_tag is not None:
            _manifest_json(drift_tag, expected_digest=record.digest)
            if immutable.body != drift_tag.body:
                raise ReleaseImageError(
                    f"mutable image tag drifted from authority: {record.service}"
                )

    receipt = {
        "schema_version": 1,
        "authority_mode": authority_mode,
        "source_sha": source_sha,
        "release_tag": release_tag,
        "image_tag": image_tag,
        "version": version,
        "manifest_digest": bound_manifest_digest,
        "tag_object_sha": tag_object_sha,
        "candidate_workflow": workflow,
        "legacy_image_ids": legacy_image_ids,
        "legacy_tag_commit": legacy_tag_commit,
        "images": [record.as_json() for record in records],
        "labels_authoritative": False,
        "secrets_included": False,
    }
    output.write_bytes(_canonical_json(receipt))
    return _sha256(_canonical_json(receipt))


def candidate_state(
    registry: RegistryProtocol,
    *,
    service: str,
    source_sha: str,
    release_tag: str,
) -> tuple[str, str]:
    """Require a pristine candidate namespace for this workflow run.

    OCI labels and tag content are writable by any principal with package
    mutation authority, so they cannot prove that a candidate was produced by
    this workflow run.  Digest-only build outputs may be content-addressed and
    shared by the registry, but candidate tags and the sealed manifest must be
    absent when the run starts.
    """
    version = version_for_tag(release_tag)
    record = inspect_candidate(
        registry,
        service=service,
        source_sha=source_sha,
        version=version,
        allow_missing=True,
    )
    sealed_response = registry.get_manifest(
        f"{CANONICAL_OWNER}/{MANIFEST_PACKAGE}", candidate_tag(source_sha)
    )
    if record is not None or sealed_response is not None:
        raise ReleaseImageError(
            "candidate namespace already exists; cross-run reuse is forbidden"
        )
    return "missing", ""


def _require_workflow_run_value(value: str, *, field: str) -> str:
    if re.fullmatch(r"[1-9][0-9]{0,19}", value) is None:
        raise ReleaseImageError(f"{field} must be an explicit positive integer")
    return value


def write_digest_receipt(
    path: Path,
    record: ImageRecord,
    *,
    github_run_id: str,
    github_run_attempt: str,
) -> None:
    document = {
        **record.as_json(),
        "github_run_id": _require_workflow_run_value(
            github_run_id, field="github_run_id"
        ),
        "github_run_attempt": _require_workflow_run_value(
            github_run_attempt, field="github_run_attempt"
        ),
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(_canonical_json(document))


def verify_built_digest(
    registry: RegistryProtocol,
    *,
    service: str,
    source_sha: str,
    release_tag: str,
    digest: str,
    receipt: Path,
    github_run_id: str,
    github_run_attempt: str,
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
    write_digest_receipt(
        receipt,
        record,
        github_run_id=github_run_id,
        github_run_attempt=github_run_attempt,
    )
    return record


def _load_digest_receipts(
    receipts_dir: Path, *, github_run_id: str, github_run_attempt: str
) -> tuple[ImageRecord, ...]:
    github_run_id = _require_workflow_run_value(github_run_id, field="github_run_id")
    github_run_attempt = _require_workflow_run_value(
        github_run_attempt, field="github_run_attempt"
    )
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
        expected_keys = {
            "service",
            "digest",
            "reference",
            "visibility",
            "github_run_id",
            "github_run_attempt",
        }
        if set(document) != expected_keys or document.get("service") != image.service:
            raise ReleaseImageError(
                f"candidate digest receipt has invalid fields: {image.service}"
            )
        if (
            document.get("github_run_id") != github_run_id
            or document.get("github_run_attempt") != github_run_attempt
        ):
            raise ReleaseImageError(
                "candidate digest receipts mix workflow runs or attempts"
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
    github_run_id: str,
    github_run_attempt: str,
) -> str:
    """Aggregate this run's digest-only builds, tag all 15, and seal once.

    A pre-existing service tag or sealed-manifest tag is a collision even when
    its bytes are identical.  Registry labels/content are not proof of workflow
    provenance, so this command never reuses cross-run candidate state.
    """

    version = version_for_tag(release_tag)
    records = _load_digest_receipts(
        receipts_dir,
        github_run_id=github_run_id,
        github_run_attempt=github_run_attempt,
    )
    by_service = {record.service: record for record in records}
    manifests: dict[str, RegistryResponse] = {}
    manifest_repository = f"{CANONICAL_OWNER}/{MANIFEST_PACKAGE}"
    if (
        registry.get_manifest(manifest_repository, candidate_tag(source_sha))
        is not None
    ):
        raise ReleaseImageError(
            "candidate namespace already exists; sealed manifest collision"
        )
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
        if existing is not None:
            raise ReleaseImageError(
                "candidate namespace already exists; service tag collision: "
                f"{image.service}"
            )

    # Every tag below is created from a receipt produced in this exact run.
    for service in (image.service for image in IMAGES):
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
        records=records,
        digest_manifests=manifests,
        github_run_id=github_run_id,
        github_run_attempt=github_run_attempt,
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
    sealed_digest_manifest = registry.get_manifest(repository, expected.digest)
    if sealed_digest_manifest is None:
        raise ReleaseImageError(
            f"sealed candidate digest disappeared before promotion: {service}"
        )
    _manifest_json(sealed_digest_manifest, expected_digest=expected.digest)
    # The mutable convenience tag is never the source of promotion bytes.  It
    # is still checked so concurrent package mutation fails visibly.
    candidate_tag_manifest = registry.get_manifest(
        repository, candidate_tag(source_sha)
    )
    if candidate_tag_manifest is None:
        raise ReleaseImageError(
            f"candidate tag disappeared before promotion: {service}"
        )
    _manifest_json(candidate_tag_manifest, expected_digest=expected.digest)
    if candidate_tag_manifest.body != sealed_digest_manifest.body:
        raise ReleaseImageError(f"candidate tag drifted before promotion: {service}")
    existing = registry.get_manifest(repository, release_tag)
    if existing is not None:
        try:
            _manifest_json(existing, expected_digest=expected.digest)
        except ReleaseImageError as exc:
            raise ReleaseImageError(
                f"existing release tag differs from candidate: {service}"
            ) from exc
        if existing.body != sealed_digest_manifest.body:
            raise ReleaseImageError(
                f"existing release tag differs from candidate: {service}"
            )
    else:
        media_type = sealed_digest_manifest.headers.get("content-type", "").split(
            ";", 1
        )[0]
        if media_type not in OCI_INDEX_MEDIA_TYPES | IMAGE_MANIFEST_MEDIA_TYPES:
            raise ReleaseImageError(
                f"candidate manifest media type is unsafe to promote: {service}"
            )
        promoted = registry.put_manifest(
            repository, release_tag, sealed_digest_manifest.body, media_type
        )
        if promoted != expected.digest:
            raise ReleaseImageError(
                f"promoted digest differs from sealed candidate: {service}"
            )
    readback = registry.get_manifest(repository, release_tag)
    if readback is None:
        raise ReleaseImageError(f"release tag read-back failed: {service}")
    _manifest_json(readback, expected_digest=expected.digest)
    if readback.body != sealed_digest_manifest.body:
        raise ReleaseImageError(
            f"release tag read-back differs from sealed digest: {service}"
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
        sealed_digest_manifest = registry.get_manifest(repository, record.digest)
        candidate = registry.get_manifest(repository, candidate_tag(source_sha))
        released = registry.get_manifest(repository, release_tag)
        if sealed_digest_manifest is None or candidate is None or released is None:
            raise ReleaseImageError(
                f"release image is missing after promotion: {record.service}"
            )
        _manifest_json(sealed_digest_manifest, expected_digest=record.digest)
        _manifest_json(candidate, expected_digest=record.digest)
        _manifest_json(released, expected_digest=record.digest)
        if not (candidate.body == sealed_digest_manifest.body == released.body):
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

    built = commands.add_parser("verify-built-digest")
    _add_common_registry_args(built)
    built.add_argument("--service", required=True)
    built.add_argument("--digest", required=True)
    built.add_argument("--receipt", type=Path, required=True)
    built.add_argument("--github-run-id", required=True)
    built.add_argument("--github-run-attempt", required=True)

    assemble = commands.add_parser("assemble-candidate")
    _add_common_registry_args(assemble)
    assemble.add_argument("--receipts-dir", type=Path, required=True)
    assemble.add_argument("--output", type=Path, required=True)
    assemble.add_argument("--github-run-id", required=True)
    assemble.add_argument("--github-run-attempt", required=True)

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

    gcp_lock = commands.add_parser("verify-bound-sealed-lock")
    _add_common_registry_args(gcp_lock)
    gcp_lock.add_argument(
        "--authority-mode",
        choices=("candidate", "published", "legacy-rollback"),
        required=True,
    )
    gcp_lock.add_argument("--image-tag", required=True)
    gcp_lock.add_argument("--docker-auth-file", type=Path, required=True)
    gcp_lock.add_argument("--bound-manifest-digest")
    gcp_lock.add_argument("--tag-object-sha")
    gcp_lock.add_argument("--github-run-id")
    gcp_lock.add_argument("--github-run-attempt")
    gcp_lock.add_argument("--controller-attestation-sha256")
    gcp_lock.add_argument("--runtime-images", type=Path)
    gcp_lock.add_argument("--legacy-tag-commit")
    gcp_lock.add_argument("--output", type=Path, required=True)
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

        if args.command == "verify-bound-sealed-lock":
            registry = RegistryClient.from_private_auth_file(args.docker_auth_file)
            receipt_digest = verify_bound_sealed_lock(
                registry,
                authority_mode=args.authority_mode,
                source_sha=args.source_sha,
                release_tag=args.release_tag,
                image_tag=args.image_tag,
                version=version_for_tag(args.release_tag),
                output=args.output,
                bound_manifest_digest=args.bound_manifest_digest,
                tag_object_sha=args.tag_object_sha,
                github_run_id=args.github_run_id,
                github_run_attempt=args.github_run_attempt,
                controller_attestation_sha256=args.controller_attestation_sha256,
                runtime_images=args.runtime_images,
                legacy_tag_commit=args.legacy_tag_commit,
            )
            print(
                "GCP_RELEASE_IMAGE_AUTHORITY\tPASS\t15/15"
                f"\tmode={args.authority_mode}\treceipt={receipt_digest}"
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
                github_run_id=args.github_run_id,
                github_run_attempt=args.github_run_attempt,
            )
            print(f"CANDIDATE_DIGEST\t{args.service}\tPASS\t{record.digest}")
            return 0
        if args.command == "assemble-candidate":
            digest = assemble_candidate(
                registry,
                source_sha=args.source_sha,
                release_tag=args.release_tag,
                receipts_dir=args.receipts_dir,
                output=args.output,
                github_run_id=args.github_run_id,
                github_run_attempt=args.github_run_attempt,
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
