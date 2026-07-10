#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import tempfile
from dataclasses import dataclass
from typing import Iterable


DEFAULT_PREFIXES = (
    "raw/sap_successfactors/",
    "silver/sap_successfactors/",
    "gold/sap_successfactors/",
)


def _bool_env(name: str, default: bool) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _clean_prefixes(values: Iterable[str]) -> list[str]:
    prefixes: list[str] = []
    for raw in values:
        for part in str(raw or "").split(","):
            prefix = part.strip().lstrip("/")
            if not prefix:
                continue
            if not prefix.endswith("/"):
                prefix += "/"
            if prefix not in prefixes:
                prefixes.append(prefix)
    return prefixes


@dataclass(frozen=True)
class StoreConfig:
    endpoint: str
    bucket: str
    access_key: str
    secret_key: str
    secure: bool


def _source_config() -> StoreConfig:
    return StoreConfig(
        endpoint=os.environ.get("SOURCE_MINIO_ENDPOINT", "minio:9000"),
        bucket=os.environ.get("SOURCE_MINIO_BUCKET", os.environ.get("MINIO_BUCKET", "lakehouse")),
        access_key=os.environ.get("SOURCE_MINIO_ACCESS_KEY", os.environ.get("MINIO_ACCESS_KEY", "minio")),
        secret_key=os.environ.get("SOURCE_MINIO_SECRET_KEY", os.environ.get("MINIO_SECRET_KEY", "miniosecret")),
        secure=_bool_env("SOURCE_MINIO_SECURE", False),
    )


def _dest_config() -> StoreConfig:
    return StoreConfig(
        endpoint=os.environ.get("GCS_ENDPOINT", os.environ.get("DEST_MINIO_ENDPOINT", "storage.googleapis.com")),
        bucket=os.environ.get("GCS_BUCKET", os.environ.get("DEST_MINIO_BUCKET", os.environ.get("MINIO_BUCKET", ""))),
        access_key=(
            os.environ.get("GCS_ACCESS_KEY_ID")
            or os.environ.get("DEST_MINIO_ACCESS_KEY")
            or os.environ.get("AWS_ACCESS_KEY_ID")
            or os.environ.get("MINIO_ACCESS_KEY")
            or ""
        ),
        secret_key=(
            os.environ.get("GCS_SECRET_ACCESS_KEY")
            or os.environ.get("DEST_MINIO_SECRET_KEY")
            or os.environ.get("AWS_SECRET_ACCESS_KEY")
            or os.environ.get("MINIO_SECRET_KEY")
            or ""
        ),
        secure=_bool_env("GCS_SECURE", True),
    )


def _client(config: StoreConfig):
    from minio import Minio

    if not config.bucket:
        raise ValueError("bucket is required")
    return Minio(
        config.endpoint,
        access_key=config.access_key,
        secret_key=config.secret_key,
        secure=config.secure,
    )


def _dest_exists_same_size(client, bucket: str, key: str, size: int) -> bool:
    try:
        stat = client.stat_object(bucket, key)
        return int(getattr(stat, "size", -1)) == int(size)
    except Exception:
        return False


def run_backfill(prefixes: list[str], dry_run: bool, overwrite: bool) -> dict:
    source = _source_config()
    dest = _dest_config()
    if not dry_run and (not dest.bucket or not dest.access_key or not dest.secret_key):
        raise ValueError(
            "GCS_BUCKET, GCS_ACCESS_KEY_ID and GCS_SECRET_ACCESS_KEY are required when DRY_RUN=false"
        )

    source_client = _client(source)
    dest_client = _client(dest) if dest.bucket and dest.access_key and dest.secret_key else None

    summary = {
        "dry_run": dry_run,
        "source_bucket": source.bucket,
        "destination_bucket": dest.bucket,
        "prefixes": prefixes,
        "copied": 0,
        "skipped_existing": 0,
        "planned": 0,
        "bytes": 0,
        "destination_check": "ok" if dest_client else "skipped_missing_credentials",
    }

    for prefix in prefixes:
        for obj in source_client.list_objects(source.bucket, prefix=prefix, recursive=True):
            key = str(obj.object_name or "")
            if not key or key.endswith("/"):
                continue
            size = int(getattr(obj, "size", 0) or 0)
            if dest_client and not overwrite and _dest_exists_same_size(dest_client, dest.bucket, key, size):
                summary["skipped_existing"] += 1
                continue
            summary["planned"] += 1
            summary["bytes"] += size
            if dry_run:
                continue
            with tempfile.NamedTemporaryFile(prefix="omega-gcs-backfill-", delete=True) as tmp:
                source_client.fget_object(source.bucket, key, tmp.name)
                dest_client.fput_object(dest.bucket, key, tmp.name, content_type="application/octet-stream")
            summary["copied"] += 1
    return summary


def main() -> int:
    parser = argparse.ArgumentParser(description="Backfill active SAP lakehouse prefixes into GCS.")
    parser.add_argument("--prefix", action="append", default=[], help="Prefix to copy. Can be repeated.")
    parser.add_argument("--execute", action="store_true", help="Actually copy objects. Default is dry-run.")
    parser.add_argument("--overwrite", action="store_true", help="Overwrite destination objects even if size matches.")
    args = parser.parse_args()

    env_prefixes = os.environ.get("PREFIXES", "")
    prefixes = _clean_prefixes(args.prefix or (env_prefixes.split(",") if env_prefixes else DEFAULT_PREFIXES))
    dry_run = not args.execute and _bool_env("DRY_RUN", True)
    summary = run_backfill(prefixes=prefixes, dry_run=dry_run, overwrite=bool(args.overwrite))
    print(json.dumps(summary, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
