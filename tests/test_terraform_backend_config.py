from __future__ import annotations

import subprocess
from pathlib import Path


REPO = Path(__file__).resolve().parents[1]
TF = REPO / "infra/terraform/infra"


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def test_backend_tf_uses_s3_with_encryption():
    src = _read(TF / "backend.tf")
    assert 'backend "s3"' in src
    assert 'bucket         = "modecissions-tfstate-us-east-1"' in src
    assert 'key            = "infra/terraform.tfstate"' in src
    assert "encrypt        = true" in src


def test_backend_tf_uses_dynamodb_lock():
    src = _read(TF / "backend.tf")
    assert 'dynamodb_table = "modecissions-tfstate-lock"' in src


def test_gitignore_blocks_tfstate_files():
    src = _read(REPO / ".gitignore")
    for pattern in ("terraform.tfstate", "terraform.tfstate.backup", ".terraform/", "*.tfvars"):
        assert pattern in src
    assert "!*.tfvars.example" in src


def test_bootstrap_script_is_idempotent_check():
    src = _read(REPO / "scripts/bootstrap_tf_backend.sh")
    assert "aws s3api head-bucket" in src
    assert "aws s3api create-bucket" in src
    assert "aws dynamodb describe-table" in src
    assert "aws dynamodb create-table" in src
    assert "--billing-mode PAY_PER_REQUEST" in src


def test_no_tfstate_currently_in_git_history():
    out = subprocess.check_output(
        ["git", "log", "--all", "--pretty=format:", "--name-only"],
        cwd=REPO,
        text=True,
    )
    tracked_tfstate = [
        line for line in out.splitlines()
        if line and ("terraform.tfstate" in line or line.endswith(".tfstate"))
    ]
    assert tracked_tfstate == []
