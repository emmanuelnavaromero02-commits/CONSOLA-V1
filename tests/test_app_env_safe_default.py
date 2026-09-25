"""Sprint v1.43.2 (P1-2) — APP_ENV defaults to ``production``.

Pre-v1.43.2, an unset ``APP_ENV`` silently put the stack in
development mode: cookies were Secure=False, vault production-only
pair-key checks were skipped, and mcp-infra exposed dangerous
write tools (airflow_create_dag, superset_create_*, vault_set).

The fix flips every default to ``production`` so misconfiguration
fails closed. Local dev must opt in explicitly with
``APP_ENV=development`` outside the committed compose default.

This test pins the invariant in both code and infrastructure.
"""
from __future__ import annotations

import importlib
import os
import re
import sys
from pathlib import Path

import pytest

from tests.console_route_source import console_route_source
import yaml


REPO = Path(__file__).resolve().parents[1]
COMPOSE_LOCAL = REPO / "infra" / "docker-compose.yml"
COMPOSE_AWS   = REPO / "infra" / "terraform" / "deploy" / "docker-compose.aws.yml"
COMPOSE_AWS_CARTRIDGES = REPO / "infra" / "terraform" / "deploy" / "docker-compose.cartridges.yml"


# ── Helpers ────────────────────────────────────────────────────────────────

def _isolated_import(subdir: str, module_path: str):
    """Import ``module_path`` from ``subdir`` of the repo, with sys.path
    scrubbed so app/ collisions across services don't bleed through."""
    sys.path[:] = [
        p for p in sys.path
        if not any(s in p for s in ("/cartridges/", "/console", "/vault",
                                     "/workspace", "/mcp-infra",
                                     "/refinement"))
    ]
    sys.path.insert(0, str(REPO / subdir))
    for name in list(sys.modules):
        if name == "app" or name.startswith("app."):
            del sys.modules[name]
    return importlib.import_module(module_path)


def _services(path: Path) -> dict:
    return yaml.safe_load(path.read_text(encoding="utf-8")).get("services", {}) or {}


# ── Code-level: every _is_production / _is_development defaults safe ──────

def test_console_security_is_development_defaults_production(monkeypatch):
    """Once APP_ENV is unset, the console must consider itself in
    production — never dev. The pre-v1.43.2 bug was the opposite."""
    monkeypatch.delenv("APP_ENV", raising=False)
    monkeypatch.delenv("ENV", raising=False)
    monkeypatch.delenv("MODE", raising=False)
    sec = _isolated_import("console", "app.security")
    assert sec._runtime_env() == "production"


def test_console_rate_limiter_is_production_defaults_true(monkeypatch):
    monkeypatch.delenv("APP_ENV", raising=False)
    rl = _isolated_import("console", "app.services.rate_limiter")
    assert rl._is_production() is True


def test_console_auth_is_production_defaults_true(monkeypatch):
    # auth.py runs _require_pair_keys_in_production() at import time;
    # under the new default it would block unless we satisfy the
    # required pair keys first. That's the intended hardening — for
    # this test we only care about the helper's polarity.
    for env in (
        "INTERNAL_API_KEY_CARTRIDGE_TO_CONSOLE",
        "INTERNAL_API_KEY_REPLICON_TO_CONSOLE",
        "INTERNAL_API_KEY_WORKSPACE_TO_CONSOLE",
        "INTERNAL_API_KEY_REFINEMENT_TO_CONSOLE",
        "INTERNAL_API_KEY_VAULT_TO_CONSOLE",
        "INTERNAL_API_KEY_MCP_INFRA_TO_CONSOLE",
        "INTERNAL_API_KEY_AIRFLOW_TO_CONSOLE",
        "INTERNAL_API_KEY_HUBSPOT_TO_CONSOLE",
        "INTERNAL_API_KEY_SAP_HCM_TO_CONSOLE",
        "INTERNAL_API_KEY_SAP_S4HANA_TO_CONSOLE",
        "INTERNAL_API_KEY_SAP_SUCCESSFACTORS_TO_CONSOLE",
        "INTERNAL_API_KEY_SAP_B1_TO_CONSOLE",
        "INTERNAL_API_KEY_SALESFORCE_TO_CONSOLE",
    ):
        monkeypatch.setenv(env, "x" * 32)
    monkeypatch.delenv("APP_ENV", raising=False)
    auth = _isolated_import("console", "app.services.auth")
    assert auth._is_production() is True


def test_console_dependencies_default_to_production(monkeypatch):
    for env in (
        "INTERNAL_API_KEY_AIRFLOW_TO_CONSOLE",
        "INTERNAL_API_KEY_CARTRIDGE_TO_CONSOLE",
        "INTERNAL_API_KEY_HUBSPOT_TO_CONSOLE",
        "INTERNAL_API_KEY_MCP_INFRA_TO_CONSOLE",
        "INTERNAL_API_KEY_REPLICON_TO_CONSOLE",
        "INTERNAL_API_KEY_SAP_HCM_TO_CONSOLE",
        "INTERNAL_API_KEY_SAP_S4HANA_TO_CONSOLE",
        "INTERNAL_API_KEY_SAP_SUCCESSFACTORS_TO_CONSOLE",
        "INTERNAL_API_KEY_SAP_B1_TO_CONSOLE",
        "INTERNAL_API_KEY_SALESFORCE_TO_CONSOLE",
        "INTERNAL_API_KEY_WORKSPACE_TO_CONSOLE",
    ):
        monkeypatch.setenv(env, "x" * 32)
    monkeypatch.delenv("APP_ENV", raising=False)
    monkeypatch.delenv("ENVIRONMENT", raising=False)
    deps = _isolated_import("console", "app.dependencies")
    assert deps._is_production_env() is True


@pytest.mark.asyncio
async def test_workspace_cartridge_dataset_fallback_denied_when_env_unset(monkeypatch):
    for env in (
        "INTERNAL_API_KEY_AIRFLOW_TO_CONSOLE",
        "INTERNAL_API_KEY_CARTRIDGE_TO_CONSOLE",
        "INTERNAL_API_KEY_HUBSPOT_TO_CONSOLE",
        "INTERNAL_API_KEY_MCP_INFRA_TO_CONSOLE",
        "INTERNAL_API_KEY_REPLICON_TO_CONSOLE",
        "INTERNAL_API_KEY_SAP_HCM_TO_CONSOLE",
        "INTERNAL_API_KEY_SAP_S4HANA_TO_CONSOLE",
        "INTERNAL_API_KEY_SAP_SUCCESSFACTORS_TO_CONSOLE",
        "INTERNAL_API_KEY_SAP_B1_TO_CONSOLE",
        "INTERNAL_API_KEY_SALESFORCE_TO_CONSOLE",
        "INTERNAL_API_KEY_WORKSPACE_TO_CONSOLE",
    ):
        monkeypatch.setenv(env, "x" * 32)
    monkeypatch.delenv("APP_ENV", raising=False)
    monkeypatch.delenv("ENVIRONMENT", raising=False)
    deps = _isolated_import("console", "app.dependencies")

    class Pool:
        async def fetchval(self, *_args, **_kwargs):
            return None

        async def fetch(self, *_args, **_kwargs):
            raise AssertionError("datasets fallback must stay disabled by default")

    async def pool():
        return Pool()

    monkeypatch.setattr(deps._auth, "pool", pool)
    assert await deps._workspace_cartridges("workspace-1") == []


def test_vault_is_production_defaults_true(monkeypatch):
    # Same story as auth.py: vault/app/main.py invokes
    # _require_pair_keys_in_production() at import. Satisfy keys first.
    for env in (
        "INTERNAL_API_KEY_CONSOLE_TO_VAULT",
        "INTERNAL_API_KEY_MCP_INFRA_TO_VAULT",
        "INTERNAL_API_KEY_WORKSPACE_TO_VAULT",
        "INTERNAL_API_KEY_REFINEMENT_TO_VAULT",
        "INTERNAL_API_KEY_AIRFLOW_TO_VAULT",
        "INTERNAL_API_KEY_CARTRIDGE_TO_VAULT",
    ):
        monkeypatch.setenv(env, "x" * 32)
    # Satisfy INTERNAL_API_KEY too — vault halts at import otherwise.
    monkeypatch.setenv("INTERNAL_API_KEY", "x" * 64)
    monkeypatch.delenv("APP_ENV", raising=False)
    vault_main = _isolated_import("vault", "app.main")
    assert vault_main._is_production() is True


def _seed_mcp_infra_settings(monkeypatch):
    """mcp-infra's pydantic Settings demands airflow/superset/pg creds
    at import time. Provide stubs so we can reach _is_development()."""
    for env in (
        "AIRFLOW_USER", "AIRFLOW_PASSWORD",
        "SUPERSET_USER", "SUPERSET_PASSWORD",
        "PG_PASSWORD",
    ):
        monkeypatch.setenv(env, "x")


@pytest.mark.parametrize("module_path", [
    "app.tools.airflow",
    "app.tools.superset",
    "app.tools.vault",
])
def test_mcp_infra_tools_is_development_defaults_false(monkeypatch, module_path):
    """The dangerous-tool gate must DENY when APP_ENV is unset."""
    _seed_mcp_infra_settings(monkeypatch)
    monkeypatch.delenv("APP_ENV", raising=False)
    mod = _isolated_import("mcp-infra", module_path)
    assert mod._is_development() is False


# ── Behaviour: the dangerous tool actually refuses ─────────────────────────

@pytest.mark.asyncio
async def test_airflow_create_dag_refuses_when_app_env_unset(monkeypatch):
    """End-to-end: airflow_create_dag must raise PermissionError when
    APP_ENV is unset (default-secure). Pre-fix this would have run."""
    _seed_mcp_infra_settings(monkeypatch)
    monkeypatch.delenv("APP_ENV", raising=False)
    airflow = _isolated_import("mcp-infra", "app.tools.airflow")
    with pytest.raises(PermissionError):
        await airflow.airflow_create_dag(dag_id="x", code="pass")


# ── Infra: compose files set APP_ENV explicitly on every app service ──────

# Services that run application code and therefore MUST set APP_ENV in
# the local compose so the production guardrail is explicit. Pure-infra
# services (postgres, redis, minio, airflow-init, mailhog,
# superset-init) don't read APP_ENV.
_LOCAL_APP_SERVICES = [
    "console", "workspace", "refinement", "vault", "mcp-infra",
    "replicon", "hubspot", "salesforce",
    "sap-successfactors", "sap-hcm", "sap-s4hana",
]


@pytest.mark.parametrize("svc", _LOCAL_APP_SERVICES)
def test_local_compose_app_services_set_app_env(svc):
    services = _services(COMPOSE_LOCAL)
    assert svc in services, f"{svc} missing from local compose"
    env = (services[svc] or {}).get("environment") or {}
    # Compose ``environment`` may be a dict or a list; we wrote dicts.
    assert isinstance(env, dict), f"{svc} environment must be a mapping"
    assert "APP_ENV" in env, (
        f"{svc} must declare APP_ENV explicitly in local compose. "
        "Without it, the post-v1.43.2 code defaults to ``production``."
    )
    val = str(env["APP_ENV"])
    assert "production" in val, (
        f"{svc} APP_ENV should default to production guardrails locally, got {val!r}"
    )


def test_console_system_info_exposes_dev_mode_flag():
    """v1.43.2 (Frontend R1 hardening): the UI gates dev-only CTAs
    (Studio Deploy DAG button) on this flag. Pre-R1 the button was
    rendered unconditionally and clicking it in production surfaced
    a confusing PermissionError from airflow_create_dag."""
    src = console_route_source()
    # The endpoint exists and returns dev_mode based on APP_ENV.
    assert '"dev_mode"' in src
    assert '"app_env"' in src
    # The flag flips on the documented dev-mode env values.
    assert "{\"development\", \"dev\", \"local\", \"test\"}" in src


def test_pipeline_js_hides_deploy_button_outside_dev_mode():
    """v1.43.2 (Frontend R1 hardening): pipeline.js must consult
    /api/system/info and disable the Deploy DAG button when
    dev_mode is false."""
    js = (REPO / "console" / "app" / "static" / "js" / "viewers"
          / "pipeline.js").read_text(encoding="utf-8")
    assert "/api/system/info" in js
    assert "dev_mode" in js
    assert "btn-deploy" in js


def test_studio_airflow_button_stays_visible_and_external():
    """The Studio Airflow button must remain visible and open the external
    Airflow UI deep link for the selected DAG, matching the legacy Studio UX."""
    legacy_js = (REPO / "console" / "app" / "static" / "js" / "studio"
                 / "legacy.js").read_text(encoding="utf-8")
    pipeline_html = (REPO / "console" / "app" / "static" / "viewers"
                     / "pipeline.html").read_text(encoding="utf-8")
    pipeline_js = (REPO / "console" / "app" / "static" / "js" / "viewers"
                   / "pipeline.js").read_text(encoding="utf-8")

    airflow_url_fn = re.search(
        r"function airflowDagUrl\(dagId\)\s*\{(.*?)\n    \}",
        legacy_js,
        re.DOTALL,
    )
    assert airflow_url_fn, "Studio legacy.js must define airflowDagUrl"
    assert "airflowBaseUrl()" in airflow_url_fn.group(1)
    assert "/dags/${encodeURIComponent(dagId)}/grid" in airflow_url_fn.group(1)
    assert "/viewer?type=jobs" not in legacy_js
    assert "AIRFLOW_PUBLIC_URL" in legacy_js
    assert ":8082" in legacy_js

    assert 'id="dag-airflow-link"' in legacy_js
    assert 'href="#"' in legacy_js
    assert 'target="_blank"' in legacy_js
    assert 'title="Ver en Airflow UI"' in legacy_js
    assert ">◈ Airflow</a>" in legacy_js
    assert "Airflow en consola" not in legacy_js

    assert 'id="dag-airflow-link"' in pipeline_html
    assert 'href="#"' in pipeline_html
    assert 'target="_blank"' in pipeline_html
    assert 'title="Ver en Airflow UI"' in pipeline_html
    assert ">◈ Airflow</a>" in pipeline_html
    assert "Airflow en consola" not in pipeline_html
    assert "/dags/${encodeURIComponent(dagId)}/grid" in pipeline_js
    assert "document.getElementById('dag-airflow-link').href = '#';" in pipeline_js


def test_legacy_js_gates_every_dev_only_action():
    """v1.0: destructive legacy Studio actions must never be silent no-ops.

    Rename/delete/schedule still short-circuit in the browser because they
    have no useful production path. Deploy is different: the backend owns the
    RCE gate and returns a structured 403, so the click must still issue
    /api/studio/dag-deploy for E2E and auditability."""
    js = (REPO / "console" / "app" / "static" / "js" / "studio"
          / "legacy.js").read_text(encoding="utf-8")
    # The cache helper exists.
    assert "_devModeCache" in js
    assert "/api/system/info" in js
    for func_name in ("renameDag", "deleteDag", "_setEntitySchedule"):
        # Match the function source up to the next ``export async``
        # or end of file. Ensure the gate appears within that span.
        m = re.search(
            rf"export async function {func_name}\([^)]*\)\s*\{{(.*?)"
            r"(?=\n    export async function |\Z)",
            js, re.DOTALL,
        )
        assert m, f"function {func_name} not found in legacy.js"
        body = m.group(1)
        assert ("_gateDevOnlyAction" in body or "_isDevMode" in body), (
            f"{func_name} must short-circuit on _isDevMode / "
            "_gateDevOnlyAction — otherwise it surfaces a raw "
            "PermissionError in production."
        )
    deploy = re.search(
        r"export async function deployDag\([^)]*\)\s*\{(.*?)"
        r"(?=\n    export async function |\Z)",
        js, re.DOTALL,
    )
    assert deploy, "function deployDag not found in legacy.js"
    deploy_body = deploy.group(1)

    # Case A: packaged cartridge DAG cannot be deployed from Studio.
    # It must short-circuit and never perform an API call.
    m_packaged = re.search(
        r"if\s*\(_isCartridgeManagedDag\(dagId\)\)\s*\{[^\n]*?\n.*?return;",
        deploy_body,
        re.DOTALL,
    )
    assert m_packaged, (
        "deployDag must early-return for cartridge-managed DAGs "
        "(no backend mutation call)."
    )
    assert "DAG empaquetado por el cartucho, ya activo en Airflow" in m_packaged.group(0)

    # Case B: user-authored DAG keeps the backend path alive (for RCE
    # ownership + auditing in /api/studio/dag-deploy).
    assert re.search(
        r"const hasProductionGate = !\(await _isDagDeployEnabled\(\)\);",
        deploy_body,
    )
    assert re.search(
        r"fetch\('/api/studio/dag-deploy'",
        deploy_body,
    )
    assert "backend owns the production RCE gate" in deploy_body

    packaged_cut = m_packaged.end()
    first_backend_call = deploy_body.find("fetch('/api/studio/dag-deploy'")
    assert first_backend_call != -1 and first_backend_call > packaged_cut

    # Explicitly ensure this is UI-hardening only; server-side gate remains.
    assert "_gateDevOnlyAction" not in deploy_body


def test_aws_compose_app_env_defaults_production():
    """The AWS compose must keep APP_ENV pointing at production by
    default. A drift here would silently flip a prod node into dev."""
    services = _services(COMPOSE_AWS)
    found = 0
    for name, svc in services.items():
        env = (svc or {}).get("environment") or {}
        if isinstance(env, dict) and "APP_ENV" in env:
            val = str(env["APP_ENV"])
            assert "production" in val, (
                f"AWS service {name} declares APP_ENV={val!r} — must "
                "default to production in the AWS compose."
            )
            found += 1
    assert found >= 1, "AWS compose declares APP_ENV on no service"


def test_aws_cartridge_overlay_app_env_defaults_production():
    services = _services(COMPOSE_AWS_CARTRIDGES)
    for name, svc in services.items():
        env = (svc or {}).get("environment") or {}
        if name in {"replicon", "sap-hcm", "sap-s4hana", "sap-successfactors"}:
            assert isinstance(env, dict)
            assert "APP_ENV" in env
            assert "production" in str(env["APP_ENV"])


def test_agent_runner_does_not_require_internal_keys_at_parse_time():
    """Airflow imports DAG modules before runtime secrets are always
    available. The runner must read pair keys inside task execution, not
    assign them at module import time."""
    src = (REPO / "airflow" / "dags" / "agent_runner.py").read_text(encoding="utf-8")
    assert "MCP_INFRA_KEY =" not in src
    assert "CONSOLE_INTERNAL_KEY =" not in src
    assert '_internal_key("INTERNAL_API_KEY_AIRFLOW_TO_MCP_INFRA")' in src
    assert '_internal_key("INTERNAL_API_KEY_AIRFLOW_TO_CONSOLE")' in src
