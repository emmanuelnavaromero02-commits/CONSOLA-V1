#!/usr/bin/env python3
"""Validate a secret-free GCP image-authority receipt and emit digest rows."""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path


SERVICES = (
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
DIGEST = re.compile(r"sha256:[0-9a-f]{64}")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--authority", type=Path, required=True)
    parser.add_argument("--digests", type=Path, required=True)
    parser.add_argument("--mode", required=True)
    parser.add_argument("--source-sha", required=True)
    parser.add_argument("--version", required=True)
    parser.add_argument("--image-tag", required=True)
    args = parser.parse_args()
    payload = json.loads(args.authority.read_bytes())
    if (
        set(payload)
        != {
            "schema_version",
            "authority_mode",
            "source_sha",
            "release_tag",
            "image_tag",
            "version",
            "manifest_digest",
            "tag_object_sha",
            "candidate_workflow",
            "legacy_image_ids",
            "legacy_tag_commit",
            "images",
            "labels_authoritative",
            "secrets_included",
        }
        or payload.get("schema_version") != 1
        or payload.get("authority_mode") != args.mode
        or payload.get("source_sha") != args.source_sha
        or payload.get("release_tag") != f"v{args.version}"
        or payload.get("image_tag") != args.image_tag
        or payload.get("version") != args.version
        or payload.get("labels_authoritative") is not False
        or payload.get("secrets_included") is not False
        or not isinstance(payload.get("images"), list)
        or [row.get("service") for row in payload["images"]] != list(SERVICES)
    ):
        raise SystemExit("release image authority identity differs")
    if args.mode in {"candidate", "published"}:
        if DIGEST.fullmatch(str(payload.get("manifest_digest", ""))) is None:
            raise SystemExit("sealed manifest digest is missing")
        if payload.get("legacy_image_ids") is not None:
            raise SystemExit("sealed image authority contains legacy ImageIDs")
        if payload.get("legacy_tag_commit") is not None:
            raise SystemExit("sealed image authority contains a legacy tag commit")
    else:
        image_ids = payload.get("legacy_image_ids")
        if (
            payload.get("manifest_digest") is not None
            or payload.get("tag_object_sha") is not None
            or not isinstance(image_ids, dict)
            or set(image_ids) != set(SERVICES)
            or re.fullmatch(r"[0-9a-f]{40}", str(payload.get("legacy_tag_commit", "")))
            is None
        ):
            raise SystemExit("legacy rollback authority is ambiguous")
    lines = []
    for row in payload["images"]:
        if set(row) != {"service", "digest", "reference", "visibility"}:
            raise SystemExit("release image authority row shape differs")
        digest = row.get("digest", "")
        if DIGEST.fullmatch(digest) is None:
            raise SystemExit("release image authority digest is invalid")
        image_id = "-"
        if args.mode == "legacy-rollback":
            image_id = payload["legacy_image_ids"].get(row["service"], "")
            if DIGEST.fullmatch(image_id) is None:
                raise SystemExit("legacy rollback ImageID is invalid")
        lines.append(f"{row['service']}\t{digest}\t{image_id}\n")
    args.digests.write_text("".join(lines), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
