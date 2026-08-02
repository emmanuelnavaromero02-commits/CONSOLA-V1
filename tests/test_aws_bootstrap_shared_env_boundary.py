from __future__ import annotations

import os
import re
import stat
import subprocess
from pathlib import Path


REPO = Path(__file__).resolve().parents[1]
BOOTSTRAP = REPO / "infra/bootstrap-keys.sh"
AWS_PATHS = (
    REPO / ".github/workflows/deploy-aws.yml",
    REPO / "infra/terraform/deploy/start.sh",
    REPO / "infra/terraform/deploy/update.sh",
)
AWS_ENV_EXAMPLE = REPO / "infra/terraform/deploy/.env.example"
EVIDENCE_KEYS = {
    "CONTROL_ROOM_EVIDENCE_SIGNING_KEY_ID",
    "CONTROL_ROOM_EVIDENCE_SIGNING_KEY",
    "CONTROL_ROOM_EVIDENCE_SIGNING_PREVIOUS_KEYS",
}


def _array_keys(source: str, name: str) -> set[str]:
    match = re.search(rf"{name}=\((?P<body>[\s\S]*?)\n\)", source)
    assert match, f"missing shell array: {name}"
    return set(re.findall(r'"([A-Z][A-Z0-9_]+)"', match.group("body")))


def _run_bootstrap(tmp_path: Path, *, evidence: str | None = None) -> Path:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    fake_openssl = bin_dir / "openssl"
    fake_openssl.write_text(
        "#!/usr/bin/env bash\nprintf 'generated-secret'\n", encoding="utf-8"
    )
    fake_openssl.chmod(0o755)
    env = os.environ.copy()
    env.pop("MODECISSIONS_BOOTSTRAP_CONTROL_ROOM_EVIDENCE", None)
    env["PATH"] = f"{bin_dir}:{env['PATH']}"
    if evidence is not None:
        env["MODECISSIONS_BOOTSTRAP_CONTROL_ROOM_EVIDENCE"] = evidence
    output = tmp_path / "runtime.env"
    result = subprocess.run(
        ["bash", str(BOOTSTRAP), str(output)],
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    return output


def test_bootstrap_defaults_to_non_evidence_keys_without_collateral_loss(
    tmp_path: Path,
) -> None:
    source = BOOTSTRAP.read_text(encoding="utf-8")
    declared = (
        _array_keys(source, "KEYS")
        | _array_keys(source, "DB_KEYS")
        | _array_keys(source, "DERIVED_KEYS")
    )
    expected = declared - {"CONTROL_ROOM_EVIDENCE_SIGNING_KEY"}

    output = _run_bootstrap(tmp_path)

    generated = {
        line.split("=", 1)[0]
        for line in output.read_text(encoding="utf-8").splitlines()
    }
    assert generated == expected
    assert generated.isdisjoint(EVIDENCE_KEYS)
    assert stat.S_IMODE(output.stat().st_mode) == 0o600


def test_evidence_generation_requires_explicit_opt_in(tmp_path: Path) -> None:
    output = _run_bootstrap(tmp_path, evidence="true")
    generated = {
        line.split("=", 1)[0]
        for line in output.read_text(encoding="utf-8").splitlines()
    }
    assert EVIDENCE_KEYS <= generated


def test_every_aws_bootstrap_path_explicitly_disables_evidence() -> None:
    for path in AWS_PATHS:
        source = path.read_text(encoding="utf-8")
        matches = list(re.finditer(r"bootstrap-keys\.sh", source))
        assert matches, f"missing bootstrap invocation: {path}"
        for match in matches:
            prefix = source[max(0, match.start() - 200) : match.start()]
            assert "MODECISSIONS_BOOTSTRAP_CONTROL_ROOM_EVIDENCE=false" in prefix


def test_aws_shared_env_example_excludes_evidence_keyring() -> None:
    source = AWS_ENV_EXAMPLE.read_text(encoding="utf-8")
    for key in EVIDENCE_KEYS:
        assert not re.search(rf"(?m)^{key}=", source)
