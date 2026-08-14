from __future__ import annotations

import subprocess
from pathlib import Path

from scripts.select_previous_release import SemVer, select_previous_release


def _git(repo: Path, *args: str) -> str:
    return subprocess.check_output(["git", *args], cwd=repo, text=True).strip()


def _commit(repo: Path, name: str) -> str:
    (repo / "history.txt").write_text(name, encoding="utf-8")
    subprocess.run(["git", "add", "history.txt"], cwd=repo, check=True)
    subprocess.run(
        ["git", "commit", "-m", name], cwd=repo, check=True, capture_output=True
    )
    return _git(repo, "rev-parse", "HEAD")


def test_semver_prerelease_precedence_is_strict() -> None:
    assert SemVer.parse("v1.2.3") > SemVer.parse("v1.2.3-rc.1")
    assert SemVer.parse("v1.2.3-rc.2") > SemVer.parse("v1.2.3-rc.1")
    assert SemVer.parse("v1.2") is None
    assert SemVer.parse("release-v1.2.3") is None
    assert SemVer.parse("v01.2.3") is None
    assert SemVer.parse("v1.2.3-rc.01") is None


def test_selector_ignores_auxiliary_and_non_ancestral_tags(tmp_path: Path) -> None:
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    subprocess.run(["git", "config", "user.email", "ci@example.com"], cwd=tmp_path, check=True)
    subprocess.run(["git", "config", "user.name", "CI"], cwd=tmp_path, check=True)
    first = _commit(tmp_path, "first")
    subprocess.run(["git", "tag", "v1.0.0"], cwd=tmp_path, check=True)
    subprocess.run(["git", "tag", "latest"], cwd=tmp_path, check=True)
    _commit(tmp_path, "second")
    subprocess.run(["git", "tag", "audit-2026"], cwd=tmp_path, check=True)
    head = _commit(tmp_path, "release")
    subprocess.run(["git", "tag", "v1.1.0-beta"], cwd=tmp_path, check=True)

    base, tag = select_previous_release(head, "v1.1.0-beta", repo=tmp_path)

    assert base == first
    assert tag == "v1.0.0"
