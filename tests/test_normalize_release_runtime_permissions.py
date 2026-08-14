from __future__ import annotations

import os
import stat
from pathlib import Path

import pytest

from scripts.normalize_release_runtime_permissions import (
    RuntimePermissionError,
    main,
    normalize_runtime_permissions,
)


def _mode(path: Path) -> int:
    return stat.S_IMODE(path.lstat().st_mode)


def test_normalizer_repairs_private_playwright_files_and_preserves_execute_only(
    tmp_path: Path,
) -> None:
    runtime = tmp_path / "browsers"
    chrome = runtime / "chromium-1223" / "chrome-linux64"
    chrome.mkdir(parents=True)
    dependency_manifest = chrome / "deb.deps"
    dependency_manifest.write_text("libnss3\n", encoding="utf-8")
    dependency_manifest.chmod(0o600)
    executable = chrome / "chrome"
    executable.write_bytes(b"ELF")
    executable.chmod(0o710)
    non_executable = chrome / "resources.pak"
    non_executable.write_bytes(b"resources")
    non_executable.chmod(0o666)
    internal_link = chrome / "chrome-link"
    internal_link.symlink_to("chrome")
    runtime.chmod(0o700)
    chrome.parent.chmod(0o777)
    chrome.chmod(0o750)

    assert normalize_runtime_permissions(runtime) == (3, 3, 1)

    assert _mode(runtime) == 0o555
    assert _mode(chrome.parent) == 0o555
    assert _mode(chrome) == 0o555
    assert _mode(dependency_manifest) == 0o444
    assert _mode(executable) == 0o555
    assert _mode(non_executable) == 0o444
    assert internal_link.is_symlink()
    assert os.readlink(internal_link) == "chrome"
    assert _mode(dependency_manifest) & stat.S_IROTH
    assert main([str(runtime)]) == 0


def test_normalizer_rejects_escaping_symlink_before_changing_any_mode(
    tmp_path: Path,
) -> None:
    runtime = tmp_path / "browsers"
    runtime.mkdir(mode=0o700)
    reviewed = runtime / "reviewed"
    reviewed.write_bytes(b"reviewed")
    reviewed.chmod(0o600)
    outside = tmp_path / "outside"
    outside.write_bytes(b"outside")
    outside.chmod(0o600)
    (runtime / "escape").symlink_to(outside)

    with pytest.raises(RuntimePermissionError, match="escapes"):
        normalize_runtime_permissions(runtime)

    assert _mode(runtime) == 0o700
    assert _mode(reviewed) == 0o600
    assert _mode(outside) == 0o600


def test_normalizer_rejects_special_file_before_changing_any_mode(
    tmp_path: Path,
) -> None:
    runtime = tmp_path / "browsers"
    runtime.mkdir(mode=0o700)
    reviewed = runtime / "reviewed"
    reviewed.write_bytes(b"reviewed")
    reviewed.chmod(0o600)
    os.mkfifo(runtime / "unexpected.fifo", 0o600)

    with pytest.raises(RuntimePermissionError, match="special file"):
        normalize_runtime_permissions(runtime)

    assert _mode(runtime) == 0o700
    assert _mode(reviewed) == 0o600


def test_normalizer_requires_an_absolute_real_directory(tmp_path: Path) -> None:
    with pytest.raises(RuntimePermissionError, match="absolute"):
        normalize_runtime_permissions(Path("relative-runtime"))
    target = tmp_path / "target"
    target.mkdir()
    alias = tmp_path / "alias"
    alias.symlink_to(target, target_is_directory=True)
    with pytest.raises(RuntimePermissionError, match="real directory"):
        normalize_runtime_permissions(alias)
