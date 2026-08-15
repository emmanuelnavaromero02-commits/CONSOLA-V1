#!/usr/bin/env python3
"""Select the nearest trusted ancestral strict-SemVer release.

Names and ancestry are only candidate filters.  Authority comes from a
checksum-valid canonical GitHub Release manifest bound to the tag/SHA, apart
from one exact current-tag-bound transition ledger entry.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from functools import total_ordering
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener


SEMVER_RE = re.compile(
    r"^v(?P<major>0|[1-9][0-9]*)\."
    r"(?P<minor>0|[1-9][0-9]*)\."
    r"(?P<patch>0|[1-9][0-9]*)"
    r"(?:-(?P<pre>[0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*))?$"
)

GITHUB_API_URL = "https://api.github.com"
GITHUB_API_VERSION = "2022-11-28"
MAX_EVIDENCE_BYTES = 2_097_152
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
)
# The release chain predates canonical manifests.  This sole recovery bridge
# skips the known failed .210 through .218 runs only while their exact annotated
# tag objects, peeled commits, direct-parent chain, and lack of canonical
# evidence all match. Every later base must carry a checksum-valid canonical
# manifest.
TRANSITION_RELEASES = {
    "v1.45.219-beta": (
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
            (
                "v1.45.218-beta",
                "51bf1b642f8c0312c5a9c7eaf29ce68f568ff5f8",
                "019e4d279dbc3c97db7b00a55df98cb3ba6740c4",
            ),
        ),
    ),
}
TRANSITION_AUTHORITY_ROOTS = {
    "v1.45.219-beta": "refs/omega-release-authority/v1.45.219-beta",
}

RawFetcher = Callable[[str, Mapping[str, str]], tuple[int, bytes, Mapping[str, str]]]
TrustVerifier = Callable[[str, str], bool]


class ReleaseTrustError(RuntimeError):
    """Previous-release evidence is unavailable or ambiguous."""


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


def _fetch_bytes(
    url: str, headers: Mapping[str, str]
) -> tuple[int, bytes, Mapping[str, str]]:
    request = Request(url, headers=dict(headers), method="GET")
    try:
        with build_opener(_NoRedirect).open(request, timeout=15.0) as response:  # noqa: S310
            status = int(response.status)
            raw = response.read(MAX_EVIDENCE_BYTES + 1)
    except HTTPError as exc:
        return int(exc.code), b"", dict(exc.headers.items())
    except (URLError, TimeoutError, OSError) as exc:
        raise ReleaseTrustError("GitHub release evidence transport failed") from exc
    if len(raw) > MAX_EVIDENCE_BYTES:
        raise ReleaseTrustError("GitHub release evidence is oversized")
    return status, raw, dict(response.headers.items())


def _strict_repository(repository: str) -> tuple[str, str]:
    if not re.fullmatch(
        r"[A-Za-z0-9](?:[A-Za-z0-9-]{0,38})/[A-Za-z0-9_.-]{1,100}",
        repository,
    ):
        raise ReleaseTrustError("GitHub repository identity is invalid")
    return tuple(repository.split("/", 1))  # type: ignore[return-value]


def _json_object(raw: bytes, *, label: str) -> dict[str, Any]:
    try:
        value = json.loads(raw.decode("utf-8"))
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise ReleaseTrustError(f"{label} is not valid JSON") from exc
    if not isinstance(value, dict):
        raise ReleaseTrustError(f"{label} must be a JSON object")
    return value


def _asset_bytes(
    *,
    repository: str,
    asset: Any,
    headers: Mapping[str, str],
    fetcher: RawFetcher,
) -> bytes | None:
    if not isinstance(asset, dict):
        return None
    asset_id = asset.get("id")
    if not isinstance(asset_id, int) or isinstance(asset_id, bool) or asset_id <= 0:
        return None
    owner, name = _strict_repository(repository)
    url = (
        f"{GITHUB_API_URL}/repos/{quote(owner, safe='')}/{quote(name, safe='')}"
        f"/releases/assets/{asset_id}"
    )
    download_headers = {**headers, "Accept": "application/octet-stream"}
    status, raw, response_headers = fetcher(url, download_headers)
    if status in {301, 302, 303, 307, 308}:
        location = response_headers.get("Location") or response_headers.get("location")
        parsed = urlsplit(location) if isinstance(location, str) else None
        if (
            parsed is None
            or parsed.scheme != "https"
            or parsed.hostname
            not in {
                "objects.githubusercontent.com",
                "release-assets.githubusercontent.com",
            }
            or parsed.username is not None
            or parsed.password is not None
            or parsed.fragment
        ):
            raise ReleaseTrustError("GitHub release asset redirect is unsafe")
        # GitHub's API may redirect an authenticated asset request to a signed
        # CDN URL.  Never forward the bearer token across that host boundary.
        status, raw, _response_headers = fetcher(
            location,
            {
                "Accept": "application/octet-stream",
                "User-Agent": "omega-previous-release-selector/1",
            },
        )
    if status == 404:
        return None
    if status != 200:
        raise ReleaseTrustError(f"GitHub release asset returned HTTP {status}")
    if len(raw) > MAX_EVIDENCE_BYTES:
        raise ReleaseTrustError("GitHub release asset is oversized")
    return raw


def _manifest_is_canonical(
    *, repository: str, tag: str, commit: str, raw: bytes
) -> bool:
    try:
        manifest = json.loads(raw.decode("utf-8"))
    except (UnicodeError, json.JSONDecodeError):
        return False
    if not isinstance(manifest, dict):
        return False
    if manifest.get("schema_version") == 2:
        return _manifest_v2_is_canonical(
            manifest=manifest, repository=repository, tag=tag, commit=commit
        )
    if set(manifest) != {
        "schema_version",
        "repository",
        "release_tag",
        "source_sha",
        "images",
    }:
        return False
    if (
        manifest["schema_version"] != 1
        or manifest["repository"] != repository
        or manifest["release_tag"] != tag
        or manifest["source_sha"] != commit
    ):
        return False
    images = manifest["images"]
    if not isinstance(images, list) or len(images) != len(CANONICAL_SERVICES):
        return False
    owner = repository.split("/", 1)[0]
    for service, image_entry in zip(CANONICAL_SERVICES, images, strict=True):
        if not isinstance(image_entry, dict) or set(image_entry) != {
            "schema_version",
            "service",
            "image",
            "release_tag",
            "source_sha",
            "digest",
            "release_reference",
            "sha_reference",
        }:
            return False
        image = f"ghcr.io/{owner}/{service}"
        digest = image_entry["digest"]
        if not isinstance(digest, str) or not re.fullmatch(
            r"sha256:[0-9a-f]{64}", digest
        ):
            return False
        if image_entry != {
            "schema_version": 1,
            "service": service,
            "image": image,
            "release_tag": tag,
            "source_sha": commit,
            "digest": digest,
            "release_reference": f"{image}:{tag}@{digest}",
            "sha_reference": f"{image}:sha-{commit}@{digest}",
        }:
            return False
    return True


def _manifest_v2_is_canonical(
    *, manifest: dict[str, Any], repository: str, tag: str, commit: str
) -> bool:
    if set(manifest) != {
        "schema_version",
        "kind",
        "repository",
        "release_tag",
        "source_sha",
        "build_run_id",
        "images",
    }:
        return False
    run_id = manifest.get("build_run_id")
    if (
        manifest.get("schema_version") != 2
        or manifest.get("kind") != "omega-release-manifest"
        or manifest.get("repository") != repository
        or manifest.get("release_tag") != tag
        or manifest.get("source_sha") != commit
        or not isinstance(run_id, int)
        or isinstance(run_id, bool)
        or run_id <= 0
    ):
        return False
    images = manifest.get("images")
    if not isinstance(images, list) or len(images) != len(CANONICAL_SERVICES):
        return False
    owner = repository.split("/", 1)[0]
    for service, entry in zip(CANONICAL_SERVICES, images, strict=True):
        if not isinstance(entry, dict) or set(entry) != {
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
        }:
            return False
        image = f"ghcr.io/{owner}/{service}"
        digest = entry.get("digest")
        size = entry.get("manifest_size")
        if (
            not isinstance(digest, str)
            or re.fullmatch(r"sha256:[0-9a-f]{64}", digest) is None
            or not isinstance(size, int)
            or isinstance(size, bool)
            or size <= 0
            or entry
            != {
                "schema_version": 2,
                "service": service,
                "image": image,
                "release_tag": tag,
                "source_sha": commit,
                "build_run_id": run_id,
                "digest": digest,
                "digest_reference": f"{image}@{digest}",
                "manifest_media_type": "application/vnd.oci.image.manifest.v1+json",
                "manifest_size": size,
            }
        ):
            return False
    return True


def verify_github_release_manifest(
    *,
    repository: str,
    tag: str,
    commit: str,
    token: str,
    fetcher: RawFetcher = _fetch_bytes,
) -> bool:
    """Return true only for a release with checksum-valid canonical evidence.

    A structural 404 or invalid/missing canonical asset marks only that tag as
    untrusted.  Authentication, transport, and other HTTP failures are
    ambiguous and abort selection instead of silently shortening the delta.
    """

    owner, name = _strict_repository(repository)
    if SemVer.parse(tag) is None or not re.fullmatch(r"[0-9a-f]{40}", commit):
        raise ReleaseTrustError("release evidence identity is invalid")
    token = token.strip()
    if not token:
        raise ReleaseTrustError("GitHub token is missing")
    headers = {
        "Accept": "application/vnd.github+json",
        "Authorization": f"Bearer {token}",
        "X-GitHub-Api-Version": GITHUB_API_VERSION,
        "User-Agent": "omega-previous-release-selector/1",
    }
    release_url = (
        f"{GITHUB_API_URL}/repos/{quote(owner, safe='')}/{quote(name, safe='')}"
        f"/releases/tags/{quote(tag, safe='')}"
    )
    status, raw, _response_headers = fetcher(release_url, headers)
    if status == 404:
        return False
    if status != 200:
        raise ReleaseTrustError(f"GitHub Release API returned HTTP {status}")
    release = _json_object(raw, label="GitHub Release response")
    if (
        release.get("tag_name") != tag
        or release.get("draft") is not False
        or release.get("immutable") is not True
    ):
        return False
    assets = release.get("assets")
    if not isinstance(assets, list):
        raise ReleaseTrustError("GitHub Release assets are invalid")
    manifest_name = f"omega-release-manifest-{tag}.json"
    checksum_name = f"{manifest_name}.sha256"
    by_name: dict[str, list[Any]] = {manifest_name: [], checksum_name: []}
    for asset in assets:
        if isinstance(asset, dict) and asset.get("name") in by_name:
            by_name[asset["name"]].append(asset)
    if any(len(matches) != 1 for matches in by_name.values()):
        return False
    manifest_raw = _asset_bytes(
        repository=repository,
        asset=by_name[manifest_name][0],
        headers=headers,
        fetcher=fetcher,
    )
    checksum_raw = _asset_bytes(
        repository=repository,
        asset=by_name[checksum_name][0],
        headers=headers,
        fetcher=fetcher,
    )
    if manifest_raw is None or checksum_raw is None:
        return False
    try:
        checksum_text = checksum_raw.decode("ascii")
    except UnicodeError:
        return False
    expected_checksum = hashlib.sha256(manifest_raw).hexdigest()
    if checksum_text != f"{expected_checksum}  {manifest_name}\n":
        return False
    return _manifest_is_canonical(
        repository=repository,
        tag=tag,
        commit=commit,
        raw=manifest_raw,
    )


def _git(
    repo: Path, *args: str, check: bool = True
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", *args],
        cwd=repo,
        check=check,
        text=True,
        capture_output=True,
    )


@total_ordering
@dataclass(frozen=True)
class SemVer:
    major: int
    minor: int
    patch: int
    prerelease: tuple[str, ...] | None

    @classmethod
    def parse(cls, tag: str) -> SemVer | None:
        match = SEMVER_RE.fullmatch(tag)
        if match is None:
            return None
        prerelease = match.group("pre")
        if prerelease is not None and any(
            identifier.isdigit() and len(identifier) > 1 and identifier.startswith("0")
            for identifier in prerelease.split(".")
        ):
            return None
        return cls(
            int(match.group("major")),
            int(match.group("minor")),
            int(match.group("patch")),
            tuple(prerelease.split(".")) if prerelease is not None else None,
        )

    def _compare_prerelease(self, other: SemVer) -> int:
        left = self.prerelease
        right = other.prerelease
        if left is None:
            return 0 if right is None else 1
        if right is None:
            return -1
        for a, b in zip(left, right, strict=False):
            if a == b:
                continue
            a_numeric = a.isdigit()
            b_numeric = b.isdigit()
            if a_numeric and b_numeric:
                return -1 if int(a) < int(b) else 1
            if a_numeric != b_numeric:
                return -1 if a_numeric else 1
            return -1 if a < b else 1
        return (len(left) > len(right)) - (len(left) < len(right))

    def __lt__(self, other: object) -> bool:
        if not isinstance(other, SemVer):
            return NotImplemented
        left_core = (self.major, self.minor, self.patch)
        right_core = (other.major, other.minor, other.patch)
        if left_core != right_core:
            return left_core < right_core
        return self._compare_prerelease(other) < 0


def select_previous_release(
    head: str,
    current_tag: str | None = None,
    *,
    repo: Path | None = None,
    trust_verifier: TrustVerifier | None = None,
) -> tuple[str, str]:
    repo = repo or Path.cwd()
    shallow = _git(repo, "rev-parse", "--is-shallow-repository", check=False)
    if shallow.returncode != 0 or shallow.stdout.strip() != "false":
        raise ReleaseTrustError(
            "previous-release selection requires a complete non-shallow history"
        )
    head_commit = _git(repo, "rev-parse", f"{head}^{{commit}}").stdout.strip()
    parents = _git(repo, "rev-list", "--parents", "-n", "1", head_commit).stdout.split()
    transition = TRANSITION_RELEASES.get(current_tag or "")
    if transition is not None:
        authority_root = TRANSITION_AUTHORITY_ROOTS.get(current_tag or "")
        if authority_root is None:
            raise ReleaseTrustError(
                "transition release has no remote authority namespace"
            )
        if len(parents) != 2:
            raise ReleaseTrustError(
                "transition release head must have exactly one parent"
            )
        if trust_verifier is None:
            raise ReleaseTrustError(
                "transition release requires failed-marker evidence verification"
            )
        (
            base_tag,
            base_tag_object,
            base_commit,
            failed_markers,
        ) = transition
        if len(failed_markers) != 9:
            raise ReleaseTrustError(
                "transition release must have exactly nine failed markers"
            )
        current_ref = f"{authority_root}/current"
        current_object = _git(
            repo,
            "rev-parse",
            "--verify",
            f"{current_ref}^{{object}}",
            check=False,
        )
        if current_object.returncode != 0:
            raise ReleaseTrustError("transition release current tag is missing")
        current_type = _git(
            repo,
            "cat-file",
            "-t",
            current_object.stdout.strip(),
            check=False,
        )
        if current_type.returncode != 0 or current_type.stdout.strip() != "tag":
            raise ReleaseTrustError(
                "transition release current marker is not an annotated tag"
            )
        current_payload = _git(
            repo,
            "cat-file",
            "-p",
            current_object.stdout.strip(),
            check=False,
        )
        expected_headers = [
            f"object {head_commit}",
            "type commit",
            f"tag {current_tag}",
        ]
        if (
            current_payload.returncode != 0
            or current_payload.stdout.splitlines()[:3] != expected_headers
        ):
            raise ReleaseTrustError(
                "transition release current annotated tag identity is invalid"
            )
        current = _git(
            repo,
            "rev-parse",
            "--verify",
            f"{current_ref}^{{commit}}",
            check=False,
        )
        if current.returncode != 0 or current.stdout.strip() != head_commit:
            raise ReleaseTrustError(
                "transition release current tag does not resolve to head"
            )

        resolved_failed: list[tuple[str, str]] = []
        for index, (failed_tag, failed_tag_object, failed_commit) in enumerate(
            failed_markers
        ):
            failed_ref = f"{authority_root}/failed-{index}"
            failed_object = _git(
                repo,
                "rev-parse",
                "--verify",
                f"{failed_ref}^{{object}}",
                check=False,
            )
            if failed_object.returncode != 0:
                raise ReleaseTrustError(
                    f"transition failed release marker is missing: {failed_tag}"
                )
            if failed_object.stdout.strip() != failed_tag_object:
                raise ReleaseTrustError(
                    "transition failed release tag object differs from the ledger: "
                    f"{failed_tag}"
                )
            failed_type = _git(repo, "cat-file", "-t", failed_tag_object, check=False)
            if failed_type.returncode != 0 or failed_type.stdout.strip() != "tag":
                raise ReleaseTrustError(
                    "transition failed release marker is not the annotated tag "
                    f"object: {failed_tag}"
                )
            failed_peeled = _git(
                repo,
                "rev-parse",
                "--verify",
                f"{failed_ref}^{{commit}}",
                check=False,
            )
            if (
                failed_peeled.returncode != 0
                or failed_peeled.stdout.strip() != failed_commit
            ):
                raise ReleaseTrustError(
                    "transition failed release peeled commit differs from the "
                    f"ledger: {failed_tag}"
                )
            resolved_failed.append((failed_tag, failed_commit))

        base_object = _git(
            repo,
            "rev-parse",
            "--verify",
            f"{authority_root}/base^{{object}}",
            check=False,
        )
        if base_object.returncode != 0:
            raise ReleaseTrustError("transition base tag marker is missing")
        if base_object.stdout.strip() != base_tag_object:
            raise ReleaseTrustError(
                "transition base tag object differs from the ledger"
            )
        base_type = _git(repo, "cat-file", "-t", base_tag_object, check=False)
        if base_type.returncode != 0 or base_type.stdout.strip() != "tag":
            raise ReleaseTrustError(
                "transition base marker is not the annotated tag object"
            )
        base_peeled = _git(
            repo,
            "rev-parse",
            "--verify",
            f"{authority_root}/base^{{commit}}",
            check=False,
        )
        if base_peeled.returncode != 0 or base_peeled.stdout.strip() != base_commit:
            raise ReleaseTrustError(
                "transition base tag peeled commit differs from the ledger"
            )

        if parents[1] != resolved_failed[-1][1]:
            raise ReleaseTrustError(
                "transition release head is not directly atop the latest failed "
                "release"
            )
        for (_older_tag, older_commit), (newer_tag, newer_commit) in zip(
            resolved_failed[:-1], resolved_failed[1:], strict=True
        ):
            newer_parents = _git(
                repo, "rev-list", "--parents", "-n", "1", newer_commit
            ).stdout.split()
            if len(newer_parents) != 2 or newer_parents[1] != older_commit:
                raise ReleaseTrustError(
                    f"transition failed release chain is not direct at {newer_tag}"
                )
        if _git(
            repo,
            "merge-base",
            "--is-ancestor",
            base_commit,
            resolved_failed[0][1],
            check=False,
        ).returncode:
            raise ReleaseTrustError(
                "transition base is not an ancestor of the failed release"
            )
        for failed_tag, failed_commit in resolved_failed:
            if trust_verifier(failed_tag, failed_commit):
                raise ReleaseTrustError(
                    "known failed release has contradictory canonical evidence: "
                    f"{failed_tag}"
                )
        return base_commit, base_tag
    if len(parents) == 1:
        return head_commit, "none"
    parent = parents[1]

    candidates: list[tuple[int, SemVer, str, str]] = []
    for tag in _git(repo, "tag", "--list").stdout.splitlines():
        version = SemVer.parse(tag)
        if version is None or tag == current_tag:
            continue
        resolved = _git(repo, "rev-parse", f"{tag}^{{commit}}", check=False)
        if resolved.returncode != 0:
            continue
        commit = resolved.stdout.strip()
        if _git(
            repo, "merge-base", "--is-ancestor", commit, parent, check=False
        ).returncode:
            continue
        distance_text = _git(
            repo, "rev-list", "--count", f"{commit}..{parent}"
        ).stdout.strip()
        candidates.append((int(distance_text), version, tag, commit))

    # Check nearest commits first, and the highest SemVer only as a deterministic
    # tie-breaker.  A newly injected SemVer tag has no authority by itself: it
    # is ignored unless it has canonical release evidence.  The sole pinned
    # transition was validated and returned above.
    for distance in sorted({candidate[0] for candidate in candidates}):
        at_distance = sorted(
            (candidate for candidate in candidates if candidate[0] == distance),
            key=lambda item: item[1],
            reverse=True,
        )
        for _distance, _version, tag, commit in at_distance:
            if trust_verifier is not None and trust_verifier(tag, commit):
                return commit, tag
    raise ReleaseTrustError("no trusted previous release manifest or ledger entry")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--head", required=True)
    parser.add_argument("--current-tag")
    parser.add_argument("--github-output", type=Path)
    parser.add_argument("--repository")
    parser.add_argument("--token-env", default="GITHUB_TOKEN")
    args = parser.parse_args(argv)

    repository = args.repository or os.environ.get("GITHUB_REPOSITORY", "")
    token = os.environ.get(args.token_env, "")

    def trust(tag: str, commit: str) -> bool:
        return verify_github_release_manifest(
            repository=repository,
            tag=tag,
            commit=commit,
            token=token,
        )

    try:
        commit, tag = select_previous_release(
            args.head,
            args.current_tag,
            trust_verifier=trust,
        )
    except (ReleaseTrustError, RuntimeError) as exc:
        parser.exit(2, f"PREVIOUS_RELEASE_SELECTION BLOCKED: {exc}\n")
    rendered = f"base={commit}\ntag={tag}\n"
    output_path = args.github_output
    if output_path is None and os.environ.get("GITHUB_OUTPUT"):
        output_path = Path(os.environ["GITHUB_OUTPUT"])
    if output_path is not None:
        with output_path.open("a", encoding="utf-8") as output:
            output.write(rendered)
    else:
        print(rendered, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
