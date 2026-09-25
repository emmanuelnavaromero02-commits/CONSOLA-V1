from pathlib import Path

import yaml


REPO = Path(__file__).resolve().parents[1]
CURRENT_ID = "CONTROL_ROOM_EVIDENCE_SIGNING_KEY_ID"
CURRENT_KEY = "CONTROL_ROOM_EVIDENCE_SIGNING_KEY"
PREVIOUS_KEYS = "CONTROL_ROOM_EVIDENCE_SIGNING_PREVIOUS_KEYS"


def _services(path: Path) -> dict:
    return yaml.safe_load(path.read_text(encoding="utf-8"))["services"]


def test_local_evidence_keys_mount_only_in_console():
    services = _services(REPO / "infra/docker-compose.yml")
    for name in (CURRENT_ID, CURRENT_KEY, PREVIOUS_KEYS):
        assert name in services["console"]["environment"]
        assert all(
            name not in (service.get("environment") or {})
            for service_name, service in services.items()
            if service_name != "console"
        )


def test_aws_evidence_env_mounts_only_in_console():
    services = _services(REPO / "infra/terraform/deploy/docker-compose.aws.yml")
    private_path = (
        "${MODECISSIONS_CONTROL_ROOM_EVIDENCE_ENV_FILE:-"
        "${AWS_ENV_FILE:-.env}.control-room-evidence}"
    )
    console_env_files = services["console"]["env_file"]
    assert [item["required"] for item in console_env_files] == [True]
    assert console_env_files[0]["path"] == private_path
    for name in (CURRENT_ID, CURRENT_KEY, PREVIOUS_KEYS):
        assert name not in services["console"]["environment"]
    for service_name, service in services.items():
        if service_name != "console":
            assert private_path not in {
                item["path"] for item in service.get("env_file", [])
            }


def test_gcp_overlay_mounts_dedicated_evidence_keys_only_in_console():
    template = REPO / "infra/terraform-gcp/templates/docker-compose.gcp.yml.tftpl"
    services = _services(template)
    for name in (CURRENT_ID, CURRENT_KEY, PREVIOUS_KEYS):
        assert name in services["console"]["environment"]
        assert (
            sum(
                name in (service.get("environment") or {})
                for service in services.values()
            )
            == 1
        )
