#!/usr/bin/env python3

from __future__ import annotations

import argparse
import os
import tempfile
from pathlib import Path


class RenderError(ValueError):
    pass


_REQUIRED_HARDENING = (
    'MINIO_ACCESS_KEY: ""',
    'MINIO_SECRET_KEY: ""',
    'AWS_ACCESS_KEY_ID: ""',
    'AWS_SECRET_ACCESS_KEY: ""',
    'AWS_SESSION_TOKEN: ""',
    'AIRFLOW_VAR_MINIO_ACCESS_KEY: ""',
    'AIRFLOW_VAR_MINIO_SECRET_KEY: ""',
)


def render_template(raw: str) -> str:

    if "\x00" in raw or "\r" in raw:
        raise RenderError("template contains unsupported bytes")
    terraform_probe = raw.replace("$${", "")
    if "${" in terraform_probe or "%{" in raw:
        raise RenderError("template contains unsupported Terraform syntax")
    rendered = raw.replace("$${", "${")
    if "$${" in rendered or "%{" in rendered:
        raise RenderError("rendered overlay retains Terraform syntax")
    if not rendered.endswith("\n"):
        raise RenderError("template must end with one newline")
    for marker in _REQUIRED_HARDENING:
        if marker not in rendered:
            raise RenderError("template is missing required GCP credential isolation")
    return rendered


def render_file(source: Path, target: Path) -> None:
    if source.is_symlink() or not source.is_file():
        raise RenderError("template is missing or is not a regular file")
    if target.exists() and (target.is_symlink() or not target.is_file()):
        raise RenderError("target exists but is not a regular file")
    raw = source.read_bytes().decode("utf-8")
    rendered = render_template(raw)
    payload = rendered.encode("utf-8")
    target.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary_name = tempfile.mkstemp(
        prefix=".docker-compose.gcp.", suffix=".tmp", dir=target.parent
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(temporary, 0o600)
        os.replace(temporary, target)
        if target.read_bytes() != payload:
            raise RenderError("rendered overlay failed post-write verification")
        directory_fd = os.open(target.parent, os.O_RDONLY)
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
    finally:
        temporary.unlink(missing_ok=True)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("source", type=Path)
    parser.add_argument("target", type=Path)
    args = parser.parse_args()
    try:
        render_file(args.source, args.target)
    except (OSError, UnicodeError, RenderError) as exc:
        parser.error(str(exc))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
