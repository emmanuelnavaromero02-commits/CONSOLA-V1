"""
Sprint v1.38 — audit B5 + B6 (P0.5).

Pre-v1.38 the three SAP cartridges, the Airflow webserver / scheduler
plus the Airflow DAG variable, and the Superset webserver all
connected to Postgres as the ``postgres`` superuser. Any one of them
being compromised handed the attacker read/write on every public
table (including ``users``, ``decisions``, ``vault_entries``),
``CREATE ROLE``, and bypass of every row-level filter we add later.

v1.38 introduces six least-privilege roles and rewires the runtime
services to use them. Bootstrap (the init containers) still use the
superuser by design — they have to ``CREATE DATABASE`` and run
``airflow db migrate`` / ``superset db upgrade`` which create the
tables we want to grant on; after that they transfer ownership via
explicit GRANT statements before exiting.

These tests are AST-style guards that run in milliseconds without a
live Postgres. The live smoke test (scripts/smoke_test.sh, checks
19-23) covers the "GRANTs actually held in pg_catalog" path.
"""
from __future__ import annotations

import re
from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parents[1]
LOCAL_COMPOSE = REPO_ROOT / "infra" / "docker-compose.yml"
AWS_COMPOSE = REPO_ROOT / "infra" / "terraform" / "deploy" / "docker-compose.aws.yml"
MIGRATION_36 = REPO_ROOT / "infra" / "init" / "36_cartridge_and_meta_roles.sql"
BOOTSTRAP_SH = REPO_ROOT / "infra" / "bootstrap.sh"
ENV_EXAMPLE = REPO_ROOT / "infra" / ".env.example"
SMOKE_SCRIPT = REPO_ROOT / "scripts" / "smoke_test.sh"


NEW_ROLES = (
    "omega_cartridge_sap_hcm",
    "omega_cartridge_sap_s4",
    "omega_cartridge_sap_sf",
    "omega_airflow_dag",
    "omega_airflow_meta",
    "omega_superset_meta",
)

# Tables the migration must EXPLICITLY REVOKE on the operational
# roles (SAP cartridges + omega_airflow_dag).
HARD_LOCKED_TABLES = (
    "users", "user_sessions", "user_tokens", "refresh_tokens",
    "tenants", "workspaces", "roles", "user_workspace_roles",
    "decisions", "decision_actions",
    "audit_events", "login_attempts", "vault_access_log",
    "vault_entries",
)


# ── Migration 36 ────────────────────────────────────────────────────────────

def test_migration_36_creates_all_six_roles():
    src = MIGRATION_36.read_text(encoding="utf-8")
    for role in NEW_ROLES:
        # The migration creates roles inside DO blocks; verify the
        # ``CREATE ROLE <name>`` text is emitted for each.
        assert re.search(
            rf"CREATE ROLE {role}\b", src
        ), f"migration must CREATE ROLE {role!r}"


def test_migration_36_requires_password_from_guc():
    src = MIGRATION_36.read_text(encoding="utf-8")
    # Every role must read its password from current_setting() and
    # refuse to create the role if the GUC is empty (same v1.19
    # pattern that omega_console / omega_vault use).
    for role in NEW_ROLES:
        guc = f"app.{role}_password"
        assert (
            f"current_setting('{guc}', true)" in src
        ), f"migration must read password for {role!r} from GUC {guc!r}"
    # And the empty-password guard must fire for every role.
    assert src.count("refusing to create role with empty password") >= len(NEW_ROLES)


def test_migration_36_hard_locks_sensitive_tables():
    src = MIGRATION_36.read_text(encoding="utf-8")
    # The REVOKE block iterates over an ARRAY[...] — verify every
    # sensitive table name appears in that list so it can't slip past.
    for tbl in HARD_LOCKED_TABLES:
        assert (
            f"'{tbl}'" in src
        ), f"migration must REVOKE on {tbl!r}"
    # And the four roles that must be REVOKED.
    for role in (
        "omega_cartridge_sap_hcm",
        "omega_cartridge_sap_s4",
        "omega_cartridge_sap_sf",
        "omega_airflow_dag",
    ):
        assert f"'{role}'" in src, (
            f"migration must REVOKE sensitive tables from {role!r}"
        )


def test_migration_36_grants_operational_to_sap_cartridges():
    src = MIGRATION_36.read_text(encoding="utf-8")
    # The cartridges need at least these for the SAP runtime to work.
    must_grant = (
        "cartridges",
        "entity_config",
        "kb_config",
        "entity_watermarks",
        "extraction_runs",
        "jobs",
        "run_logs",
    )
    for tbl in must_grant:
        assert tbl in src, f"migration must grant SAP cartridges on {tbl!r}"


def test_migration_36_grants_airflow_dag_runtime_observability_tables():
    src = MIGRATION_36.read_text(encoding="utf-8")
    grant = re.search(
        r"GRANT SELECT, INSERT, UPDATE ON(?P<body>[^;]*?)TO omega_airflow_dag;",
        src,
        re.DOTALL,
    )
    assert grant, "migration must grant operational tables to omega_airflow_dag"
    body = grant.group("body")
    for tbl in ("entity_config", "entity_watermarks", "extraction_runs", "pipeline_runs"):
        assert tbl in body, f"omega_airflow_dag needs {tbl!r} for direct DAG runtime"


# ── docker-compose.yml ──────────────────────────────────────────────────────

def test_local_compose_no_postgres_superuser_in_runtime_services():
    """The five runtime services (3 SAP cartridges, airflow webserver,
    airflow scheduler, superset) must NOT use ``postgres:`` in their
    connection strings. The two init containers (airflow-init,
    superset-init) are allowed — they bootstrap the DBs."""
    with LOCAL_COMPOSE.open("r", encoding="utf-8") as f:
        compose = yaml.safe_load(f)
    services = compose.get("services", {}) or {}

    runtime_services = (
        "sap-hcm", "sap-s4hana", "sap-successfactors",
        "superset", "airflow", "airflow-scheduler",
    )
    for name in runtime_services:
        svc = services.get(name)
        assert svc is not None, f"service {name!r} missing from compose"
        env = svc.get("environment") or {}
        # environment may be a dict or a list — normalize.
        if isinstance(env, list):
            env_str = "\n".join(env)
        else:
            env_str = "\n".join(f"{k}={v}" for k, v in env.items())
        assert "://postgres:" not in env_str, (
            f"runtime service {name!r} still connects as postgres "
            f"superuser; switch to its omega_* role"
        )


def test_local_compose_sap_cartridges_use_dedicated_roles():
    with LOCAL_COMPOSE.open("r", encoding="utf-8") as f:
        compose = yaml.safe_load(f)
    services = compose["services"]

    expected = {
        "sap-hcm": "omega_cartridge_sap_hcm",
        "sap-s4hana": "omega_cartridge_sap_s4",
        "sap-successfactors": "omega_cartridge_sap_sf",
    }
    for service_name, role in expected.items():
        env = services[service_name]["environment"]
        db_url = env.get("DATABASE_URL") if isinstance(env, dict) else ""
        assert f"://{role}:" in db_url, (
            f"{service_name} must connect as {role!r}, got {db_url!r}"
        )


def test_local_compose_airflow_runtime_uses_dedicated_roles():
    with LOCAL_COMPOSE.open("r", encoding="utf-8") as f:
        compose = yaml.safe_load(f)
    services = compose["services"]

    for svc_name in ("airflow", "airflow-scheduler"):
        env = services[svc_name]["environment"]
        # Metastore connection.
        meta = env.get("AIRFLOW__DATABASE__SQL_ALCHEMY_CONN", "")
        assert "://omega_airflow_meta:" in meta, (
            f"{svc_name} metastore must use omega_airflow_meta"
        )
        # DAG-side variable.
        dag = env.get("AIRFLOW_VAR_POSTGRES_CONN", "")
        assert "://omega_airflow_dag:" in dag, (
            f"{svc_name} AIRFLOW_VAR_POSTGRES_CONN must use omega_airflow_dag"
        )


def test_local_compose_superset_runtime_uses_dedicated_role():
    with LOCAL_COMPOSE.open("r", encoding="utf-8") as f:
        compose = yaml.safe_load(f)
    env = compose["services"]["superset"]["environment"]
    uri = env.get("SQLALCHEMY_DATABASE_URI", "")
    assert "://omega_superset_meta:" in uri, (
        "superset runtime must use omega_superset_meta"
    )


def test_local_console_can_register_superset_gold_database():
    with LOCAL_COMPOSE.open("r", encoding="utf-8") as f:
        compose = yaml.safe_load(f)
    env = compose["services"]["console"]["environment"]
    uri = env.get("SUPERSET_GOLD_SQLALCHEMY_URI", "")
    assert "://omega_refinement_gold:" in uri
    assert "@postgres_gold:5433/modecissions_gold" in uri


def test_superset_config_honors_runtime_sqlalchemy_uri_env():
    config = (
        Path(__file__).resolve().parents[1]
        / "infra/terraform/deploy/superset_config/superset_config.py"
    ).read_text(encoding="utf-8")
    assert 'os.environ.get("SQLALCHEMY_DATABASE_URI")' in config
    assert "refusing superuser fallback" in config


def test_local_compose_pgoptions_carries_six_new_passwords():
    """The postgres container must forward all six new GUC passwords
    so the migration can read them via ``current_setting``."""
    with LOCAL_COMPOSE.open("r", encoding="utf-8") as f:
        compose = yaml.safe_load(f)
    pgoptions = compose["services"]["postgres"]["environment"]["PGOPTIONS"]
    for role in NEW_ROLES:
        guc_flag = f"app.{role}_password="
        assert guc_flag in pgoptions, (
            f"postgres PGOPTIONS must forward {guc_flag!r}"
        )


# ── docker-compose.aws.yml ──────────────────────────────────────────────────

def test_aws_compose_airflow_runtime_uses_dedicated_roles():
    with AWS_COMPOSE.open("r", encoding="utf-8") as f:
        compose = yaml.safe_load(f)
    services = compose["services"]
    for svc_name in ("airflow", "airflow-scheduler"):
        env = services[svc_name]["environment"]
        meta = env.get("AIRFLOW__DATABASE__SQL_ALCHEMY_CONN", "")
        assert "://omega_airflow_meta:" in meta, (
            f"AWS {svc_name} must use omega_airflow_meta"
        )
        dag = env.get("AIRFLOW_VAR_POSTGRES_CONN", "")
        assert "://omega_airflow_dag:" in dag, (
            f"AWS {svc_name} AIRFLOW_VAR_POSTGRES_CONN must use omega_airflow_dag"
        )


def test_aws_console_can_register_superset_gold_database():
    with AWS_COMPOSE.open("r", encoding="utf-8") as f:
        compose = yaml.safe_load(f)
    env = compose["services"]["console"]["environment"]
    uri = env.get("SUPERSET_GOLD_SQLALCHEMY_URI", "")
    assert "://omega_refinement_gold:" in uri
    assert "@postgres_gold:5433/modecissions_gold" in uri


def test_aws_compose_airflow_init_keeps_superuser_for_bootstrap():
    """The init container must KEEP postgres superuser — it has to
    CREATE the metastore tables before any GRANT can fire."""
    with AWS_COMPOSE.open("r", encoding="utf-8") as f:
        compose = yaml.safe_load(f)
    env = compose["services"]["airflow-init"]["environment"]
    meta = env.get("AIRFLOW__DATABASE__SQL_ALCHEMY_CONN", "")
    assert "://postgres:" in meta, (
        "airflow-init must connect as postgres superuser to run db migrate"
    )


# ── bootstrap.sh / .env.example ─────────────────────────────────────────────

def test_bootstrap_generates_six_new_passwords():
    src = BOOTSTRAP_SH.read_text(encoding="utf-8")
    for var in (
        "OMEGA_CARTRIDGE_SAP_HCM_PASSWORD",
        "OMEGA_CARTRIDGE_SAP_S4_PASSWORD",
        "OMEGA_CARTRIDGE_SAP_SF_PASSWORD",
        "OMEGA_AIRFLOW_DAG_PASSWORD",
        "OMEGA_AIRFLOW_META_PASSWORD",
        "OMEGA_SUPERSET_META_PASSWORD",
    ):
        # bootstrap.sh must both generate AND write the variable.
        assert re.search(rf'^{var}="\$\(openssl rand', src, re.MULTILINE), (
            f"bootstrap.sh must generate {var!r}"
        )
        assert re.search(rf"^{var}=\$\{{{var}\}}", src, re.MULTILINE), (
            f"bootstrap.sh must write {var!r} to the env file"
        )


def test_env_example_documents_six_new_passwords():
    src = ENV_EXAMPLE.read_text(encoding="utf-8")
    for var in (
        "OMEGA_CARTRIDGE_SAP_HCM_PASSWORD",
        "OMEGA_CARTRIDGE_SAP_S4_PASSWORD",
        "OMEGA_CARTRIDGE_SAP_SF_PASSWORD",
        "OMEGA_AIRFLOW_DAG_PASSWORD",
        "OMEGA_AIRFLOW_META_PASSWORD",
        "OMEGA_SUPERSET_META_PASSWORD",
    ):
        assert f"{var}=" in src, f".env.example must document {var}"


# ── smoke_test.sh ───────────────────────────────────────────────────────────

def test_smoke_test_includes_lockdown_checks_for_new_roles():
    src = SMOKE_SCRIPT.read_text(encoding="utf-8")
    must_appear = (
        "omega_cartridge_sap_hcm",
        "omega_cartridge_sap_s4",
        "omega_cartridge_sap_sf",
        "omega_airflow_dag",
    )
    for role in must_appear:
        assert role in src, f"smoke must exercise {role!r}"
    # And the positive check (no over-revoke).
    assert "entity_config" in src, (
        "smoke must also verify SAP cartridges keep operational access"
    )


# ── Hotfix v1.38.1: live deployment evidence ────────────────────────────────
#
# Background: the original v1.38 commit got merged with a green test
# suite but failed in real life: `make migrate && make smoke` reported
# every v1.38 role as "got: ''" — the role didn't exist at all. The
# root cause was scripts/apply_db_migrations.sh: it forwards GUC
# passwords to psql via the PGOPTIONS env var, but the v1.38 commit
# only updated infra/docker-compose.yml's PGOPTIONS, not the
# migrate-script's separate PGOPTIONS_VALUE. The migration then
# raised "password not set", the script aborted the transaction for
# that file, the next migration ran successfully, and `make smoke`
# saw a partly-migrated DB.
#
# These two tests pin the contract so the hotfix can't silently
# regress.

def test_migration_36_fails_loud_when_password_missing():
    """Every CREATE-ROLE DO block in migration 36 must RAISE EXCEPTION
    (not RETURN) when its GUC password is empty.

    A silent RETURN would skip the role and let the migration record
    itself as successful in schema_migrations even though the role
    was never created — then the smoke test would later report
    has_table_privilege(<missing role>, …) = '' (NULL) and fail in
    confusing ways. The loud RAISE aborts the transaction so
    apply_db_migrations.sh stops at the failing file and the
    operator gets a clear error pointing at the missing env var.
    """
    src = MIGRATION_36.read_text(encoding="utf-8")
    # The migration has six "IF pw IS NULL OR pw = '' THEN" guards,
    # one per role. Each must be followed by RAISE EXCEPTION before
    # any other statement.
    pattern = re.compile(
        r"IF pw IS NULL OR pw = ''\s+THEN\s+(\S+)",
        re.IGNORECASE,
    )
    matches = pattern.findall(src)
    assert len(matches) == 6, (
        f"expected 6 password-missing guards (one per role), found "
        f"{len(matches)}"
    )
    for first_keyword in matches:
        assert first_keyword.upper() == "RAISE", (
            f"password-missing guard uses {first_keyword!r}, expected "
            f"RAISE EXCEPTION; a silent RETURN here would let "
            f"apply_db_migrations.sh report success while the role "
            f"was never created"
        )


def test_apply_db_migrations_script_passes_six_new_passwords():
    """scripts/apply_db_migrations.sh must forward the six new
    v1.38 GUC passwords to psql via PGOPTIONS so migration 36 can
    read them via current_setting()."""
    src = (REPO_ROOT / "scripts" / "apply_db_migrations.sh").read_text(
        encoding="utf-8"
    )
    must_forward = (
        ("OMEGA_CARTRIDGE_SAP_HCM_PASSWORD", "app.omega_cartridge_sap_hcm_password"),
        ("OMEGA_CARTRIDGE_SAP_S4_PASSWORD",  "app.omega_cartridge_sap_s4_password"),
        ("OMEGA_CARTRIDGE_SAP_SF_PASSWORD",  "app.omega_cartridge_sap_sf_password"),
        ("OMEGA_AIRFLOW_DAG_PASSWORD",       "app.omega_airflow_dag_password"),
        ("OMEGA_AIRFLOW_META_PASSWORD",      "app.omega_airflow_meta_password"),
        ("OMEGA_SUPERSET_META_PASSWORD",     "app.omega_superset_meta_password"),
    )
    for env_var, guc in must_forward:
        assert env_var in src, (
            f"apply_db_migrations.sh must read {env_var} from the env"
        )
        assert guc in src, (
            f"apply_db_migrations.sh must forward {guc} via PGOPTIONS"
        )
