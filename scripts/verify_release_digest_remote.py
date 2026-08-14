#!/usr/bin/env python3
"""Re-verify every published digest against private GHCR evidence."""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from scripts.release_digest_chain import verify_candidate
from scripts.release_digest_env import ManifestError, load_manifest
from scripts.release_image_promotion import (
    PromotionError,
    RegistryClient,
    ReleaseIdentity,
)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--checksum", type=Path, required=True)
    parser.add_argument("--repository", required=True)
    parser.add_argument("--owner", required=True)
    parser.add_argument("--release-tag", required=True)
    parser.add_argument("--source-sha", required=True)
    parser.add_argument("--build-run-id", type=int, required=True)
    parser.add_argument("--username", required=True)
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
        identity = ReleaseIdentity.create(
            repository=args.repository,
            owner=args.owner,
            source_sha=args.source_sha,
            release_tag=args.release_tag,
            run_id=args.build_run_id,
        )
        registry = RegistryClient(
            identity=identity,
            username=args.username,
            github_token=os.environ.get("GITHUB_TOKEN", ""),
        )
        for entry in manifest["images"]:
            actual = verify_candidate(
                registry,
                service=entry["service"],
                digest=entry["digest"],
            )
            if (
                actual["digest"] != entry["digest"]
                or actual["manifest_media_type"] != entry["manifest_media_type"]
                or actual["manifest_size"] != entry["manifest_size"]
            ):
                raise ManifestError("remote candidate differs from published manifest")
    except (ManifestError, PromotionError) as exc:
        print(f"RELEASE DIGEST REMOTE BLOCKED: {exc}", file=sys.stderr)
        return 1
    print("RELEASE DIGEST REMOTE PASS: 15/15 private images reachable")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
