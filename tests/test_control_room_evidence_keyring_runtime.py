from __future__ import annotations

import re
import os
import subprocess
from pathlib import Path


REPO = Path(__file__).resolve().parents[1]
GCP_STARTUP = REPO / "infra/terraform-gcp/templates/startup.sh.tftpl"
AWS_ENTRYPOINT = REPO / "scripts/aws-entrypoint.sh"
AWS_DEPLOY = REPO / "infra/terraform/deploy"
BOOTSTRAP_KEYS = REPO / "infra/bootstrap-keys.sh"

KEYRING = {
    "control_room_evidence_signing_key_id": "CONTROL_ROOM_EVIDENCE_SIGNING_KEY_ID",
    "control_room_evidence_signing_key": "CONTROL_ROOM_EVIDENCE_SIGNING_KEY",
    "control_room_evidence_signing_previous_keys": (
        "CONTROL_ROOM_EVIDENCE_SIGNING_PREVIOUS_KEYS"
    ),
}


def _array_body(source: str, name: str) -> str:
    match = re.search(rf"{name}=\((?P<body>[\s\S]*?)\n\)", source)
    assert match, f"missing shell array: {name}"
    return match.group("body")


def test_gcp_loads_complete_evidence_keyring_before_local_bootstrap():
    source = GCP_STARTUP.read_text(encoding="utf-8")
    declared = (REPO / "infra/terraform-gcp/locals.tf").read_text(encoding="utf-8")
    bootstrap_at = source.index("bash infra/bootstrap-keys.sh infra/.env")

    for secret_name, env_name in KEYRING.items():
        assert f'"{secret_name}"' in declared
        load = f"load_required_secret {secret_name} {env_name}"
        assert load in source
        assert source.index(load) < bootstrap_at

    helper = re.search(r"load_required_secret\(\) \{(?P<body>[\s\S]*?)\n\}", source)
    assert helper
    body = helper.group("body")
    assert "secret_value" in body
    assert "exit 1" in body
    assert "export" in body

    for env_name in KEYRING.values():
        persisted = f'set_env {env_name} "$${{{env_name}}}"'
        assert persisted in source
        assert source.index(persisted) < bootstrap_at

    bootstrap = (REPO / "infra/bootstrap.sh").read_text(encoding="utf-8")
    assert "${CONTROL_ROOM_EVIDENCE_SIGNING_KEY_ID:-evidence-" in bootstrap
    assert "${CONTROL_ROOM_EVIDENCE_SIGNING_KEY:-$(openssl rand -hex 32)}" in bootstrap
    assert "${CONTROL_ROOM_EVIDENCE_SIGNING_PREVIOUS_KEYS:-{}}" in bootstrap


def test_aws_previous_keyring_is_fatal_only_when_reference_is_configured():
    source = AWS_ENTRYPOINT.read_text(encoding="utf-8")
    previous = "CONTROL_ROOM_EVIDENCE_SIGNING_PREVIOUS_KEYS"

    required = _array_body(source, "required_evidence_secrets")
    configured = _array_body(source, "configured_evidence_secrets")
    optional = _array_body(source, "optional_secrets")
    assert "CONTROL_ROOM_EVIDENCE_SIGNING_KEY_ID" in required
    assert "CONTROL_ROOM_EVIDENCE_SIGNING_KEY" in required
    assert previous in configured
    assert previous not in optional

    loop = re.search(
        r'for secret_name in "\$\{configured_evidence_secrets\[@\]\}"; do'
        r"(?P<body>[\s\S]*?)\ndone",
        source,
    )
    assert loop
    body = loop.group("body")
    assert 'if [[ -z "$arn" ]]' in body
    assert 'write_evidence_env "$secret_name" ""' in body
    assert 'fetch_secret "$secret_name" "$arn"' in body
    assert "exit 1" in body


def test_aws_launchers_do_not_repopulate_evidence_keys_in_shared_env():
    for name in ("start.sh", "update.sh"):
        source = (AWS_DEPLOY / name).read_text(encoding="utf-8")
        assert "MODECISSIONS_BOOTSTRAP_CONTROL_ROOM_EVIDENCE=false" in source
        assert "bootstrap-keys.sh .env" in source


def test_aws_bootstrap_still_generates_every_non_evidence_key(tmp_path):
    source = BOOTSTRAP_KEYS.read_text(encoding="utf-8")
    declared = set(
        re.findall(
            r'"([A-Z][A-Z0-9_]+)"',
            _array_body(source, "KEYS") + _array_body(source, "DB_KEYS"),
        )
    )
    declared.remove("CONTROL_ROOM_EVIDENCE_SIGNING_KEY")
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    fake_openssl = bin_dir / "openssl"
    fake_openssl.write_text(
        "#!/usr/bin/env bash\nprintf 'generated-secret'\n", encoding="utf-8"
    )
    fake_openssl.chmod(0o755)
    env = os.environ.copy()
    env.update(
        {
            "PATH": f"{bin_dir}:{env['PATH']}",
            "MODECISSIONS_BOOTSTRAP_CONTROL_ROOM_EVIDENCE": "false",
        }
    )
    output = tmp_path / "runtime.env"

    result = subprocess.run(
        ["bash", str(BOOTSTRAP_KEYS), str(output)],
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 0, result.stdout + result.stderr
    generated = {
        line.split("=", 1)[0]
        for line in output.read_text(encoding="utf-8").splitlines()
    }
    assert generated == declared
    assert not generated.intersection(KEYRING.values())


def test_aws_configured_previous_keyring_fetch_failure_stops_entrypoint(tmp_path):
    source = AWS_ENTRYPOINT.read_text(encoding="utf-8")
    required = (
        _array_body(source, "required_secrets").split()
        + _array_body(source, "required_evidence_secrets").split()
    )
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    fake_aws = bin_dir / "aws"
    fake_aws.write_text(
        """#!/usr/bin/env bash
set -eu
for ((i=1; i<=$#; i++)); do
  if [[ "${!i}" == "--secret-id" ]]; then
    next=$((i + 1))
    secret_id="${!next}"
    break
  fi
done
if [[ "${secret_id:-}" == "arn:test:previous" ]]; then
  exit 41
fi
printf 'test-secret-value'
""",
        encoding="utf-8",
    )
    fake_aws.chmod(0o755)
    fake_sleep = bin_dir / "sleep"
    fake_sleep.write_text("#!/usr/bin/env bash\nexit 0\n", encoding="utf-8")
    fake_sleep.chmod(0o755)

    env = os.environ.copy()
    env.update(
        {
            "PATH": f"{bin_dir}:{env['PATH']}",
            "APP_ENV": "development",
            "S3_BUCKET_NAME": "test-bucket",
            "CONSOLE_URL": "http://console.test",
            "WORKSPACE_PUBLIC_URL": "http://workspace.test",
            "MODECISSIONS_ENV_FILE": str(tmp_path / "runtime.env"),
            "MODECISSIONS_AWS_ENTRYPOINT_LOG": str(tmp_path / "entrypoint.log"),
            "MODECISSIONS_SECRET_CONTROL_ROOM_EVIDENCE_SIGNING_PREVIOUS_KEYS_ARN": (
                "arn:test:previous"
            ),
        }
    )
    for secret_name in required:
        env[f"MODECISSIONS_SECRET_{secret_name}_ARN"] = f"arn:test:{secret_name}"

    result = subprocess.run(
        ["bash", str(AWS_ENTRYPOINT)],
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )

    combined = f"{result.stdout}\n{result.stderr}"
    assert result.returncode == 1
    assert "unable to fetch configured secret" in combined
    assert "optional secret unavailable" not in combined
    assert not (tmp_path / "runtime.env").exists()
    assert not (tmp_path / "runtime.env.control-room-evidence").exists()
