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

HARD_LOCKED_TABLES = (
    "users", "user_sessions", "user_tokens", "refresh_tokens",
    "tenants", "workspaces", "roles", "user_workspace_roles",
    "decisions", "decision_actions",
    "audit_events", "login_attempts", "vault_access_log",
    "vault_entries",
)


def test_migration_36_creates_all_six_roles():
    src = MIGRATION_36.read_text(encoding="utf-8")
    for role in NEW_ROLES:
        assert re.search(
            rf"CREATE ROLE {role}\b", src
        ), f"migration must CREATE ROLE {role!r}"


def test_migration_36_requires_password_from_guc():
    src = MIGRATION_36.read_text(encoding="utf-8")
    for role in NEW_ROLES:
        guc = f"app.{role}_password"
        assert (
            f"current_setting('{guc}', true)" in src
        ), f"migration must read password for {role!r} from GUC {guc!r}"
    assert src.count("refusing to create role with empty password") >= len(NEW_ROLES)


def test_migration_36_hard_locks_sensitive_tables():
    src = MIGRATION_36.read_text(encoding="utf-8")
    for tbl in HARD_LOCKED_TABLES:
        assert (
            f"'{tbl}'" in src
        ), f"migration must REVOKE on {tbl!r}"
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


def test_local_compose_no_postgres_superuser_in_runtime_services():
    with LOCAL_COMPOSE.open("r", encoding="utf-8") as f:
        compose = yaml.safe_load(f)
    services = compose.get("services", {}) or {}

    runtime_services = (
        "sap-hcm", "sap-s4hana", "sap-successfactors", "sap-b1",
        "superset", "airflow", "airflow-scheduler",
    )
    for name in runtime_services:
        svc = services.get(name)
        assert svc is not None, f"service {name!r} missing from compose"
        env = svc.get("environment") or {}
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
        "sap-b1": "omega_cartridge_sap_b1",
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
        meta = env.get("AIRFLOW__DATABASE__SQL_ALCHEMY_CONN", "")
        assert "://omega_airflow_meta:" in meta, (
            f"{svc_name} metastore must use omega_airflow_meta"
        )
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
    with LOCAL_COMPOSE.open("r", encoding="utf-8") as f:
        compose = yaml.safe_load(f)
    pgoptions = compose["services"]["postgres"]["environment"]["PGOPTIONS"]
    for role in NEW_ROLES:
        guc_flag = f"app.{role}_password="
        assert guc_flag in pgoptions, (
            f"postgres PGOPTIONS must forward {guc_flag!r}"
        )


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
    with AWS_COMPOSE.open("r", encoding="utf-8") as f:
        compose = yaml.safe_load(f)
    env = compose["services"]["airflow-init"]["environment"]
    meta = env.get("AIRFLOW__DATABASE__SQL_ALCHEMY_CONN", "")
    assert "://postgres:" in meta, (
        "airflow-init must connect as postgres superuser to run db migrate"
    )


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
    assert "entity_config" in src, (
        "smoke must also verify SAP cartridges keep operational access"
    )


def test_migration_36_fails_loud_when_password_missing():
    src = MIGRATION_36.read_text(encoding="utf-8")
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
