from __future__ import annotations

from pathlib import Path


REPO = Path(__file__).resolve().parents[1]
GCP_TF = REPO / "infra" / "terraform-gcp"


def test_gcp_terraform_files_stay_modular():
    offenders = []
    for path in GCP_TF.rglob("*"):
        if any(part.startswith(".") for part in path.relative_to(GCP_TF).parts):
            continue
        if path.is_file() and path.suffix not in {".hcl"}:
            lines = path.read_text(encoding="utf-8").splitlines()
            if len(lines) > 300:
                offenders.append(f"{path.relative_to(REPO)}:{len(lines)}")
    assert offenders == []


def test_gcp_startup_uses_native_lakehouse_provider_not_gcsfuse():
    startup = (GCP_TF / "templates" / "startup.sh.tftpl").read_text(encoding="utf-8")
    compose = (GCP_TF / "templates" / "docker-compose.gcp.yml.tftpl").read_text(encoding="utf-8")
    combined = f"{startup}\n{compose}"

    assert "gcsfuse" not in combined
    assert "gcs_fuse" not in combined
    assert "LAKEHOUSE_PROVIDER gcs" in startup
    assert "LAKEHOUSE_PROVIDER: $${LAKEHOUSE_PROVIDER:-gcs}" in compose


def test_gcp_secret_manifest_includes_macro_cartridges():
    locals_tf = (GCP_TF / "locals.tf").read_text(encoding="utf-8")
    required = {
        "internal_api_key_banxico_to_console",
        "internal_api_key_inegi_to_console",
        "omega_cartridge_banxico_password",
        "omega_cartridge_inegi_password",
        "gcs_hmac_access_key_id",
        "gcs_hmac_secret_access_key",
    }
    missing = sorted(item for item in required if item not in locals_tf)
    assert missing == []
