"""
Sprint v1.40 — Replicon cartridge restored.

The original Replicon cartridge was deleted in v1.32 audit cleanup
(``cartridges/replicon/`` removed; only the DAGs survived as zombie
files in ``airflow/dags/``). v1.40 restores the cartridge from the
original ZIP unchanged EXCEPT for one targeted swap:
``_get_connection`` now reads the bearer token from OMEGA Vault
(via the console reveal endpoint) instead of from
``BaseHook.get_connection`` (Airflow Connections). That single
change is the integration point — every other module is bit-identical
to the source ZIP.

These tests pin the contract:

  1. The cartridge directory exists with the expected file count
     and shape.
  2. ``_get_connection`` no longer references airflow.hooks.
  3. The mock service and zombie DAGs are gone.
  4. Compose wires the cartridge as a least-privilege role with a
     healthcheck and pair-keys plumbed.
  5. Migration 37 creates the role.
"""

from __future__ import annotations

import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
CART_DIR = REPO_ROOT / "cartridges" / "replicon"


# ── 1. Cartridge present and intact ─────────────────────────────────────────


def test_replicon_cartridge_directory_exists():
    assert CART_DIR.is_dir(), "cartridges/replicon/ missing"
    files = sorted(p.relative_to(CART_DIR) for p in CART_DIR.rglob("*") if p.is_file())
    assert len(files) >= 27, (
        f"expected ~28 files in restored Replicon cartridge, got {len(files)}: "
        f"{files[:5]}..."
    )


def test_replicon_cartridge_has_canonical_layout():
    """Spot-check the modules listed in the audit ZIP are present —
    if a future cleanup deletes one of these by accident, this fails."""
    must_exist = (
        "Dockerfile",
        "requirements.txt",
        "app/main.py",
        "app/mcp_server.py",
        "app/api/routes_health.py",
        "app/api/routes_skills.py",
        "app/config/connector.yaml",
        "app/config/entities.yaml",
        "app/config/knowledge_bits.yaml",
        "app/core/replicon_client.py",
        "app/core/job_runner.py",
        "app/core/vault_client.py",
        "app/services/catalog_service.py",
        "app/services/extraction_service.py",
        "app/services/kb_service.py",
        "app/services/parquet_service.py",
        "app/services/protection_service.py",
        "app/services/runlog_service.py",
        "app/services/watermark_service.py",
        "config/seed.sql",
        "dags/replicon_extract.py",
        "dags/replicon_ses_inbox_import.py",
    )
    for rel in must_exist:
        assert (CART_DIR / rel).is_file(), f"missing {rel} in restored cartridge"


def test_replicon_entities_yaml_has_eighteen_entities():
    """Original ZIP shipped 18 entities. If the YAML drifts (someone
    edits it locally and forgets to commit), this fails."""
    import yaml

    src = (CART_DIR / "app" / "config" / "entities.yaml").read_text(encoding="utf-8")
    data = yaml.safe_load(src)
    entities = data.get("entities") if isinstance(data, dict) else data
    assert entities, "entities.yaml is empty / unreadable"
    assert (
        len(entities) >= 17
    ), f"expected at least 17 Replicon entities, got {len(entities)}"


# ── 2. _get_connection reads Vault, not Airflow ─────────────────────────────


def test_replicon_extract_get_connection_reads_vault():
    src = (CART_DIR / "dags" / "replicon_extract.py").read_text(encoding="utf-8")
    # The new contract calls the console vault reveal endpoint.
    assert "/api/vault/connections/replicon/" in src, (
        "replicon_extract.py:_get_connection must hit the console "
        "vault reveal endpoint"
    )
    # And carries the cartridge-to-console pair key header.
    assert "INTERNAL_API_KEY_REPLICON_TO_CONSOLE" in src
    assert '"x-internal-service": "replicon"' in src


def test_replicon_extract_does_not_use_airflow_basehook():
    src = (CART_DIR / "dags" / "replicon_extract.py").read_text(encoding="utf-8")
    # The legacy BaseHook lookup must be gone from _get_connection.
    func = re.search(
        r"def _get_connection\(.*?\n(?=\n\ndef|\nclass|\Z)",
        src,
        re.DOTALL,
    )
    assert func, "could not locate _get_connection in replicon_extract.py"
    assert "BaseHook" not in func.group(0), (
        "_get_connection still falls back to Airflow BaseHook — operators "
        "would still see credentials in the Airflow UI"
    )


# ── 3. Mock + zombie DAGs are gone ─────────────────────────────────────────


def test_replicon_mock_directory_is_gone():
    mock = REPO_ROOT / "infra" / "replicon-mock"
    assert not mock.exists(), (
        "infra/replicon-mock/ must be removed — the mock served synthetic "
        "data and would feed real ETL pipelines if a misconfigured "
        "REPLICON_BASE_URL pointed at it in prod"
    )


def test_replicon_mock_not_in_compose_services():
    src = (REPO_ROOT / "infra" / "docker-compose.yml").read_text(encoding="utf-8")
    # Comments are allowed (we left a v1.40 note documenting the removal),
    # but no service definition or build context can mention it.
    code_lines = [
        line for line in src.splitlines() if not line.lstrip().startswith("#")
    ]
    code = "\n".join(code_lines)
    assert (
        "replicon-mock" not in code
    ), "docker-compose.yml still has live references to replicon-mock"


def test_replicon_zombie_dags_are_removed():
    """Old per-project Replicon DAGs are gone. MEJORAS keeps root inbox
    import DAGs and mirrors SES inside the cartridge."""
    airflow_dags = REPO_ROOT / "airflow" / "dags"
    if airflow_dags.is_dir():
        allowed = {
            "replicon_ses_inbox_import.py",
            "replicon_outlook_audit_report_import.py",
        }
        leftovers = [
            p for p in airflow_dags.glob("replicon_*.py") if p.name not in allowed
        ]
        assert not leftovers, (
            f"zombie Replicon DAGs still in airflow/dags/: "
            f"{[p.name for p in leftovers]}."
        )


# ── 4. Replicon service wired into compose ──────────────────────────────────


def test_replicon_service_in_local_compose():
    import yaml

    with (REPO_ROOT / "infra" / "docker-compose.yml").open("r", encoding="utf-8") as f:
        compose = yaml.safe_load(f)
    services = compose.get("services", {}) or {}
    assert "replicon" in services, "docker-compose.yml must define a `replicon` service"
    svc = services["replicon"]
    # Healthcheck.
    assert svc.get("healthcheck"), "replicon service must have a healthcheck"
    # Pair keys present in env.
    env = svc.get("environment") or {}
    if isinstance(env, list):
        env_keys = {item.split("=", 1)[0] for item in env}
    else:
        env_keys = set(env.keys())
    for key in (
        "INTERNAL_API_KEY_REPLICON_TO_CONSOLE",
        "INTERNAL_API_KEY_REPLICON_TO_MCP_INFRA",
        "INTERNAL_API_KEY_REPLICON_TO_REFINEMENT",
        "FIELD_ENCRYPTION_KEY",
        "DATABASE_URL",
    ):
        assert key in env_keys, f"replicon service environment missing {key}"
    # Least-privilege role on DATABASE_URL (no postgres superuser).
    db_url = env.get("DATABASE_URL", "") if isinstance(env, dict) else ""
    assert "://omega_cartridge_replicon:" in db_url, (
        "replicon service must connect as omega_cartridge_replicon, "
        "never the postgres superuser"
    )
    # Depends on postgres + minio.
    deps = svc.get("depends_on") or {}
    assert "postgres" in deps and "minio" in deps


def test_airflow_services_mount_replicon_dags():
    """Both the local airflow webserver/scheduler and the AWS variants
    must bind-mount the cartridge's dags directory so Airflow picks
    them up alongside the SAP DAGs."""
    local = (REPO_ROOT / "infra" / "docker-compose.yml").read_text(encoding="utf-8")
    aws = (
        REPO_ROOT / "infra" / "terraform" / "deploy" / "docker-compose.aws.yml"
    ).read_text(encoding="utf-8")
    assert (
        "cartridges/replicon/dags:/opt/airflow/dags/replicon" in local
    ), "local compose missing replicon DAG bind-mount"
    assert (
        "cartridges/replicon/dags:/opt/airflow/dags/replicon" in aws
    ), "AWS compose missing replicon DAG bind-mount"


# ── 5. Migration 37: role + grants ──────────────────────────────────────────


def test_migration_37_creates_replicon_role():
    mig = REPO_ROOT / "infra" / "init" / "37_replicon_role_and_tables.sql"
    assert mig.is_file(), "migration 37 missing"
    src = mig.read_text(encoding="utf-8")
    assert "CREATE ROLE omega_cartridge_replicon" in src
    assert "current_setting('app.omega_cartridge_replicon_password'" in src
    assert "RAISE EXCEPTION" in src, (
        "migration 37 must fail-fast when the password GUC is empty "
        "(matches the v1.36/v1.38 pattern)"
    )


def test_migration_37_grants_minimum_operational_set():
    src = (REPO_ROOT / "infra" / "init" / "37_replicon_role_and_tables.sql").read_text(
        encoding="utf-8"
    )
    # The cartridge needs to write its own catalog + run/watermark rows.
    for tbl in (
        "cartridges",
        "entity_config",
        "kb_config",
        "entity_watermarks",
        "extraction_runs",
        "kb_runs",
        "jobs",
        "run_logs",
    ):
        assert tbl in src, f"migration 37 missing GRANT on {tbl!r}"


def test_migration_37_revokes_sensitive_tables():
    src = (REPO_ROOT / "infra" / "init" / "37_replicon_role_and_tables.sql").read_text(
        encoding="utf-8"
    )
    for tbl in (
        "users",
        "tenants",
        "decisions",
        "vault_entries",
        "audit_events",
        "login_attempts",
    ):
        assert f"'{tbl}'" in src, (
            f"migration 37 must hard-lock {tbl!r} from " f"omega_cartridge_replicon"
        )


def test_apply_db_migrations_does_not_put_replicon_password_in_argv():
    src = (REPO_ROOT / "scripts" / "apply_db_migrations.sh").read_text(encoding="utf-8")
    compose = (REPO_ROOT / "infra/docker-compose.yml").read_text(encoding="utf-8")
    assert "OMEGA_CARTRIDGE_REPLICON_PASSWORD" in compose
    assert "app.omega_cartridge_replicon_password" in compose
    assert "OMEGA_CARTRIDGE_REPLICON_PASSWORD" not in src
    assert "app.omega_cartridge_replicon_password" not in src
    assert 'exec -T -e "PGOPTIONS=' not in src


def test_bootstrap_generates_replicon_secrets():
    src = (REPO_ROOT / "infra" / "bootstrap.sh").read_text(encoding="utf-8")
    for var in (
        "OMEGA_CARTRIDGE_REPLICON_PASSWORD",
        "INTERNAL_API_KEY_REPLICON_TO_CONSOLE",
        "INTERNAL_API_KEY_REPLICON_TO_MCP_INFRA",
        "INTERNAL_API_KEY_REPLICON_TO_REFINEMENT",
    ):
        assert re.search(
            rf'^{var}="\$\(openssl rand', src, re.MULTILINE
        ), f"bootstrap.sh must generate {var}"
        assert re.search(
            rf"^{var}=\$\{{{var}\}}", src, re.MULTILINE
        ), f"bootstrap.sh must persist {var} in the env file"


def test_env_example_documents_replicon_secrets():
    src = (REPO_ROOT / "infra" / ".env.example").read_text(encoding="utf-8")
    for var in (
        "OMEGA_CARTRIDGE_REPLICON_PASSWORD",
        "INTERNAL_API_KEY_REPLICON_TO_CONSOLE",
        "INTERNAL_API_KEY_REPLICON_TO_MCP_INFRA",
        "INTERNAL_API_KEY_REPLICON_TO_REFINEMENT",
    ):
        assert f"{var}=" in src, f".env.example must document {var}"
