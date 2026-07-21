from pathlib import Path

import yaml


REPO = Path(__file__).resolve().parents[1]
CURRENT_ID = "CONTROL_ROOM_EVIDENCE_SIGNING_KEY_ID"
CURRENT_KEY = "CONTROL_ROOM_EVIDENCE_SIGNING_KEY"
PREVIOUS_KEYS = "CONTROL_ROOM_EVIDENCE_SIGNING_PREVIOUS_KEYS"


def _services(path: Path) -> dict:
    return yaml.safe_load(path.read_text(encoding="utf-8"))["services"]


def test_dedicated_evidence_keys_mount_only_in_console():
    for relative in (
        "infra/docker-compose.yml",
        "infra/terraform/deploy/docker-compose.aws.yml",
    ):
        services = _services(REPO / relative)
        assert CURRENT_ID in services["console"]["environment"]
        assert CURRENT_KEY in services["console"]["environment"]
        assert PREVIOUS_KEYS in services["console"]["environment"]
        for service_name, service in services.items():
            if service_name == "console":
                continue
            environment = service.get("environment") or {}
            assert CURRENT_ID not in environment
            assert CURRENT_KEY not in environment
            assert PREVIOUS_KEYS not in environment


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
