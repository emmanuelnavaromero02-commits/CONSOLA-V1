from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

import scripts.verify_release_source_checkout as source_gate


def _run(repo: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", *args], cwd=repo, text=True, capture_output=True, check=True
    )
    return result.stdout.strip()


@pytest.fixture
def source_repo(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> tuple[Path, Path, str]:
    repo = tmp_path / "repo"
    (repo / "cartridges").mkdir(parents=True)
    (repo / "cartridges" / "worker.py").write_text("VALUE = 1\n", encoding="utf-8")
    (repo / ".gitignore").write_text("*.pyc\n*.so\n", encoding="utf-8")
    _run(repo, "init")
    _run(repo, "config", "user.email", "release@example.com")
    _run(repo, "config", "user.name", "Release Test")
    _run(repo, "add", ".")
    _run(repo, "commit", "-m", "baseline")
    sha = _run(repo, "rev-parse", "HEAD")
    allowlist = repo / "mounts.json"
    allowlist.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "mounts": [
                    {
                        "service": "console",
                        "type": "bind",
                        "source": "cartridges",
                        "target": "/registry/cartridges",
                        "read_only": True,
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    compose = repo / "compose.json"
    compose.write_text(
        json.dumps(
            {
                "services": {
                    "console": {
                        "volumes": [
                            {
                                "type": "bind",
                                "source": str(repo / "cartridges"),
                                "target": "/registry/cartridges",
                                "read_only": True,
                            }
                        ]
                    }
                }
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(source_gate, "REPO", repo)
    monkeypatch.setattr(source_gate, "ALLOWLIST", allowlist)
    monkeypatch.setattr(source_gate, "SOURCE_ROOTS", ("cartridges",))
    return repo, compose, sha


def test_exact_clean_source_and_mount_inventory_passes(
    source_repo: tuple[Path, Path, str],
) -> None:
    _repo, compose, sha = source_repo
    source_gate.verify(source_sha=sha, compose_path=compose)


def test_wrong_head_and_tracked_staged_or_unstaged_changes_block(
    source_repo: tuple[Path, Path, str],
) -> None:
    repo, compose, sha = source_repo
    with pytest.raises(source_gate.SourceCheckoutError, match="HEAD differs"):
        source_gate.verify(source_sha="0" * 40, compose_path=compose)
    worker = repo / "cartridges" / "worker.py"
    worker.write_text("VALUE = 2\n", encoding="utf-8")
    with pytest.raises(source_gate.SourceCheckoutError, match="differs from HEAD"):
        source_gate.verify(source_sha=sha, compose_path=compose)
    _run(repo, "add", str(worker))
    with pytest.raises(source_gate.SourceCheckoutError, match="differs from HEAD"):
        source_gate.verify(source_sha=sha, compose_path=compose)


@pytest.mark.parametrize(
    "relative",
    ["new.py", "cache.pyc", "native.so", ".airflowignore", "payload.html"],
)
def test_untracked_and_ignored_bind_content_blocks(
    source_repo: tuple[Path, Path, str], relative: str
) -> None:
    repo, compose, sha = source_repo
    (repo / "cartridges" / relative).write_bytes(b"unreviewed")

    with pytest.raises(source_gate.SourceCheckoutError, match="untracked"):
        source_gate.verify(source_sha=sha, compose_path=compose)


@pytest.mark.parametrize(
    "attack", ["outside", "symlink", "missing", "extra", "read-only"]
)
def test_mount_symlink_outside_extra_missing_and_mode_drift_block(
    source_repo: tuple[Path, Path, str], attack: str, tmp_path: Path
) -> None:
    _repo, compose, sha = source_repo
    value = json.loads(compose.read_text(encoding="utf-8"))
    mount = value["services"]["console"]["volumes"][0]
    if attack in {"outside", "symlink"}:
        outside = tmp_path / "outside"
        outside.mkdir()
        if attack == "symlink":
            link = tmp_path / "bind-link"
            link.symlink_to(outside, target_is_directory=True)
            mount["source"] = str(link)
        else:
            mount["source"] = str(outside)
    elif attack == "missing":
        value["services"]["console"]["volumes"] = []
    elif attack == "extra":
        value["services"]["console"]["volumes"].append(dict(mount))
    else:
        mount["read_only"] = False
    compose.write_text(json.dumps(value), encoding="utf-8")

    with pytest.raises(source_gate.SourceCheckoutError, match="mount inventory"):
        source_gate.verify(source_sha=sha, compose_path=compose)


def test_post_gate_rerender_detects_mount_mutation(
    source_repo: tuple[Path, Path, str],
) -> None:
    _repo, compose, sha = source_repo
    source_gate.verify(source_sha=sha, compose_path=compose)
    value = json.loads(compose.read_text(encoding="utf-8"))
    value["services"]["console"]["volumes"][0]["target"] = "/tmp/attacker"
    compose.write_text(json.dumps(value), encoding="utf-8")

    with pytest.raises(source_gate.SourceCheckoutError, match="mount inventory"):
        source_gate.verify(source_sha=sha, compose_path=compose)
