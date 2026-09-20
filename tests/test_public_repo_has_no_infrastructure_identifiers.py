"""CONSOLA-V1 is a public repository.

Infrastructure identifiers are not secrets in the cryptographic sense, but
published together they hand a reader the map: which instance to target, which
account it lives in, and which address answers. They belong in the operator's
local notes, not in the tree. This test is the guard that keeps them out.
"""

from __future__ import annotations

import re
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]

SKIP_DIRS = {
    ".git",
    ".next",
    ".pytest_cache",
    ".ruff_cache",
    ".terraform",
    ".venv",
    "__pycache__",
    "build",
    "dist",
    "node_modules",
    "venv",
}

TEXT_SUFFIXES = {
    ".cfg",
    ".csv",
    ".example",
    ".ini",
    ".js",
    ".json",
    ".jsx",
    ".md",
    ".py",
    ".sh",
    ".sql",
    ".toml",
    ".tf",
    ".tfvars",
    ".ts",
    ".tsx",
    ".txt",
    ".yaml",
    ".yml",
}

MAX_BYTES = 2_000_000

# Documented, obviously-fake stand-ins that examples are allowed to use.
PLACEHOLDER_ACCOUNTS = {"123456789012", "000000000000", "111111111111"}

EC2_INSTANCE_ID = re.compile(r"\bi-[0-9a-f]{8,17}\b")
ARN_ACCOUNT = re.compile(r"arn:aws[a-z-]*:[a-z0-9-]*:[a-z0-9-]*:(\d{12}):")


def _candidate_files() -> list[Path]:
    found: list[Path] = []
    stack = [ROOT]
    while stack:
        current = stack.pop()
        for child in current.iterdir():
            if child.is_symlink():
                continue
            if child.is_dir():
                if child.name not in SKIP_DIRS:
                    stack.append(child)
                continue
            if child.suffix.lower() not in TEXT_SUFFIXES:
                continue
            if child.stat().st_size > MAX_BYTES:
                continue
            found.append(child)
    return found


def _read(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except (UnicodeDecodeError, OSError):
        return ""


def test_no_ec2_instance_ids_are_committed():
    offenders: list[str] = []
    for path in _candidate_files():
        if path == Path(__file__):
            continue
        for match in EC2_INSTANCE_ID.findall(_read(path)):
            offenders.append(f"{path.relative_to(ROOT)}: {match}")
    assert not offenders, (
        "EC2 instance ids must not be committed to a public repository. "
        "Use a placeholder such as <aws-instance-id> in documents, and read "
        "the real value from the environment in scripts. Found: "
        + "; ".join(sorted(offenders)[:10])
    )


def test_no_real_aws_account_numbers_are_committed():
    offenders: list[str] = []
    for path in _candidate_files():
        if path == Path(__file__):
            continue
        for account in ARN_ACCOUNT.findall(_read(path)):
            if account not in PLACEHOLDER_ACCOUNTS:
                offenders.append(f"{path.relative_to(ROOT)}: {account}")
    assert not offenders, (
        "AWS account numbers must not be committed to a public repository. "
        "Examples use a documented placeholder account instead. Found: "
        + "; ".join(sorted(offenders)[:10])
    )
