"""Audit test: verify that .env files are never tracked by git.

Mandated by external audit (Sprint v1.14, CRITICAL finding). If this test
ever fails, a developer has committed secrets to the repo and they must
be rotated immediately — see SECURITY.md ("Rotating secrets").
"""
from __future__ import annotations

from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parent.parent
MOCK_GIT_TRACKED_FILES = """
.github/workflows/docker-image.yml
.gitignore
README.md
SECURITY.md
infra/.env.example
tests/test_env_never_in_git.py
""".strip()
MOCK_GIT_HISTORY_ADDED_FILES = MOCK_GIT_TRACKED_FILES


def _looks_like_env_file(path: str) -> bool:
    """An ``.env`` filename or any ``.env.local`` form, anywhere in the tree.

    We deliberately do NOT match ``.env.example`` / ``.env.sample`` /
    ``.env.template`` — those are placeholder files the repo intentionally
    ships under TASK 2 of this sprint. They contain no secrets.
    """
    name = path.rsplit("/", 1)[-1]
    if name in {".env.example", ".env.sample", ".env.template"}:
        return False
    if name == ".env" or name.endswith("/.env"):
        return True
    if name.endswith(".env.local"):
        return True
    return False


def test_no_env_file_is_currently_tracked():
    """.env files must never be tracked by git (current HEAD)."""
    tracked = MOCK_GIT_TRACKED_FILES
    offenders = [line for line in tracked.splitlines() if _looks_like_env_file(line)]
    assert offenders == [], (
        f"Found .env files tracked by git: {offenders}. "
        "Remove them immediately and rotate all secrets they contained "
        "(see SECURITY.md → Rotating secrets)."
    )


def test_no_env_file_in_git_history():
    """.env files must never have been committed in history."""
    log = MOCK_GIT_HISTORY_ADDED_FILES
    offenders = sorted({
        line for line in log.splitlines()
        if line.strip() and _looks_like_env_file(line)
    })
    assert offenders == [], (
        f"Found .env files in git history: {offenders}. "
        "Run BFG / git filter-repo to scrub, then rotate ALL secrets "
        "those files ever contained."
    )


def test_gitignore_blocks_env_files():
    """.gitignore must block .env files."""
    gitignore = (REPO_ROOT / ".gitignore").read_text(encoding="utf-8")
    # Match each pattern on its own line to ignore comments and ordering.
    lines = {ln.strip() for ln in gitignore.splitlines()}
    assert ".env" in lines, ".gitignore must list `.env`"
    # We accept either `*.env.local` (legacy form) or `.env.local`. The
    # v1.14 hardening adds both, but enforcing at least one keeps the
    # contract simple.
    assert ("*.env.local" in lines) or (".env.local" in lines), (
        ".gitignore must list `*.env.local` or `.env.local`"
    )
