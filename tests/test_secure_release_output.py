from __future__ import annotations

import hashlib
import os
from pathlib import Path

import pytest

from scripts.secure_release_output import SecureOutputError, create_exclusive_output


def _private_dir(path: Path) -> Path:
    path.mkdir()
    path.chmod(0o700)
    return path


def test_secure_release_output_is_exclusive_and_inode_distinct(tmp_path: Path) -> None:
    root = _private_dir(tmp_path / "final")
    baseline = tmp_path / "baseline"
    baseline.write_bytes(b"canonical\n")
    body = b"canonical\n"
    output = root / "compose.json"

    observed = create_exclusive_output(
        output,
        body,
        expected_sha256=hashlib.sha256(body).hexdigest(),
        baseline=baseline,
    )

    assert observed == hashlib.sha256(body).hexdigest()
    assert output.read_bytes() == body
    assert output.stat().st_ino != baseline.stat().st_ino
    assert output.stat().st_mode & 0o777 == 0o600


@pytest.mark.parametrize("attack", ["symlink", "hardlink", "regular"])
def test_secure_release_output_rejects_every_preexisting_destination(
    tmp_path: Path, attack: str
) -> None:
    root = _private_dir(tmp_path / "final")
    baseline = tmp_path / "baseline"
    baseline.write_bytes(b"authority\n")
    victim = tmp_path / "victim"
    victim.write_bytes(b"do-not-touch\n")
    output = root / "compose.json"
    if attack == "symlink":
        output.symlink_to(victim)
    elif attack == "hardlink":
        os.link(victim, output)
    else:
        output.write_bytes(b"preexisting\n")

    with pytest.raises(SecureOutputError, match="already exists"):
        create_exclusive_output(
            output,
            b"authority\n",
            expected_sha256=hashlib.sha256(b"authority\n").hexdigest(),
            baseline=baseline,
        )

    assert victim.read_bytes() == b"do-not-touch\n"


def test_secure_release_output_rejects_non_private_parent(tmp_path: Path) -> None:
    root = tmp_path / "final"
    root.mkdir(mode=0o755)
    root.chmod(0o755)
    baseline = tmp_path / "baseline"
    baseline.write_bytes(b"authority\n")

    with pytest.raises(SecureOutputError, match="parent is not private"):
        create_exclusive_output(
            root / "compose.json",
            b"authority\n",
            expected_sha256=hashlib.sha256(b"authority\n").hexdigest(),
            baseline=baseline,
        )
