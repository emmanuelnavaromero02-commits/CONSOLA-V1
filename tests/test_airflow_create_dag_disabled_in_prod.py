from __future__ import annotations

import importlib
import sys
from pathlib import Path

import pytest


SERVICE_PATH_MARKERS = (
    "/cartridges/",
    "/console",
    "/mcp-infra",
    "/refinement",
    "/vault",
    "/workspace",
)


def _purge_app_modules() -> None:
    for name in list(sys.modules):
        if name == "app" or name.startswith("app."):
            del sys.modules[name]


@pytest.fixture(autouse=True)
def clean_app_modules_after():
    yield
    _purge_app_modules()


def _load_airflow_tools(monkeypatch):
    _purge_app_modules()
    root = Path(__file__).resolve().parents[1]
    sys.path[:] = [
        p for p in sys.path
        if not any(marker in p for marker in SERVICE_PATH_MARKERS)
    ]
    sys.path.insert(0, str(root / "mcp-infra"))
    monkeypatch.setenv("AIRFLOW_USER", "airflow")
    monkeypatch.setenv("AIRFLOW_PASSWORD", "airflow")
    monkeypatch.setenv("PG_PASSWORD", "pg-password")
    monkeypatch.setenv("SUPERSET_USER", "admin")
    monkeypatch.setenv("SUPERSET_PASSWORD", "admin-password")
    return importlib.import_module("app.tools.airflow")


@pytest.mark.asyncio
async def test_airflow_create_dag_disabled_in_production(monkeypatch):
    airflow = _load_airflow_tools(monkeypatch)
    monkeypatch.setenv("APP_ENV", "production")

    with pytest.raises(PermissionError, match="disabled outside development"):
        await airflow.airflow_create_dag(
            dag_id="test_rce_blocked",
            code="print('this must not be written')\n",
        )


@pytest.mark.asyncio
async def test_airflow_delete_dag_disabled_in_production(monkeypatch):
    airflow = _load_airflow_tools(monkeypatch)
    monkeypatch.setenv("APP_ENV", "production")

    with pytest.raises(PermissionError, match="deleting DAGs is a destructive operation"):
        await airflow.airflow_delete_dag(dag_id="test_rce_blocked")


@pytest.mark.asyncio
async def test_airflow_set_variable_disabled_in_production(monkeypatch):
    airflow = _load_airflow_tools(monkeypatch)
    monkeypatch.setenv("APP_ENV", "production")

    with pytest.raises(PermissionError, match="Airflow Variables are persistent runtime configuration"):
        await airflow.airflow_set_variable(key="danger", value="blocked")


@pytest.mark.asyncio
async def test_airflow_set_variable_blocked_when_only_app_env_set(monkeypatch):
    airflow = _load_airflow_tools(monkeypatch)
    monkeypatch.setenv("APP_ENV", "development")
    monkeypatch.delenv("ALLOW_RCE_TOOLS", raising=False)
    monkeypatch.setattr(
        airflow,
        "_client",
        lambda: (_ for _ in ()).throw(AssertionError("_client must not be reached")),
    )

    with pytest.raises(PermissionError, match="ALLOW_RCE_TOOLS"):
        await airflow.airflow_set_variable(key="danger", value="blocked")


@pytest.mark.asyncio
@pytest.mark.parametrize("env_value", ["", "false", "no", "0", "off", "random"])
async def test_airflow_set_variable_blocked_when_allow_rce_false_values(monkeypatch, env_value):
    airflow = _load_airflow_tools(monkeypatch)
    monkeypatch.setenv("APP_ENV", "development")
    monkeypatch.setenv("ALLOW_RCE_TOOLS", env_value)

    with pytest.raises(PermissionError, match="ALLOW_RCE_TOOLS"):
        await airflow.airflow_set_variable(key="danger", value="blocked")


@pytest.mark.asyncio
async def test_airflow_set_variable_still_requires_development_when_allow_rce_true(monkeypatch):
    airflow = _load_airflow_tools(monkeypatch)
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.setenv("ALLOW_RCE_TOOLS", "true")

    with pytest.raises(PermissionError, match="disabled outside development"):
        await airflow.airflow_set_variable(key="danger", value="blocked")


@pytest.mark.asyncio
async def test_airflow_create_dag_still_available_in_development(monkeypatch, tmp_path):
    airflow = _load_airflow_tools(monkeypatch)
    monkeypatch.setenv("APP_ENV", "development")
    monkeypatch.setenv("ALLOW_RCE_TOOLS", "true")
    monkeypatch.setattr(airflow.settings, "airflow_dags_path", str(tmp_path))

    result = await airflow.airflow_create_dag(
        dag_id="test_dev_dag",
        code="print('dev only')\n",
    )

    assert result["dag_id"] == "test_dev_dag"
    assert (tmp_path / "test_dev_dag.py").read_text() == "print('dev only')\n"


@pytest.mark.asyncio
async def test_airflow_create_dag_blocked_when_only_app_env_set(monkeypatch):
    airflow = _load_airflow_tools(monkeypatch)
    monkeypatch.setenv("APP_ENV", "development")
    monkeypatch.delenv("ALLOW_RCE_TOOLS", raising=False)

    with pytest.raises(PermissionError) as exc:
        await airflow.airflow_create_dag(
            dag_id="x_should_fail",
            code="print('blocked')\n",
        )
    assert "ALLOW_RCE_TOOLS" in str(exc.value)


@pytest.mark.asyncio
async def test_airflow_delete_dag_blocked_when_only_app_env_set(monkeypatch):
    airflow = _load_airflow_tools(monkeypatch)
    monkeypatch.setenv("APP_ENV", "development")
    monkeypatch.delenv("ALLOW_RCE_TOOLS", raising=False)

    with pytest.raises(PermissionError) as exc:
        await airflow.airflow_delete_dag(dag_id="x_should_fail")
    assert "ALLOW_RCE_TOOLS" in str(exc.value)


@pytest.mark.asyncio
async def test_airflow_create_dag_blocked_when_only_allow_rce_tools_set(monkeypatch):
    airflow = _load_airflow_tools(monkeypatch)
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.setenv("ALLOW_RCE_TOOLS", "true")

    with pytest.raises(PermissionError) as exc:
        await airflow.airflow_create_dag(
            dag_id="x_should_fail",
            code="print('blocked')\n",
        )
    assert "outside development" in str(exc.value)


@pytest.mark.asyncio
@pytest.mark.parametrize("env_value", ["", "false", "no", "0", "off", "random"])
async def test_airflow_create_dag_allow_rce_truthy_values(monkeypatch, env_value):
    airflow = _load_airflow_tools(monkeypatch)
    monkeypatch.setenv("APP_ENV", "development")
    monkeypatch.setenv("ALLOW_RCE_TOOLS", env_value)

    with pytest.raises(PermissionError):
        await airflow.airflow_create_dag(
            dag_id="x_should_fail",
            code="print('blocked')\n",
        )


def test_mcp_infra_port_8010_not_exposed_in_aws_compose():
    import yaml
    aws = Path(__file__).resolve().parents[1] / "infra/terraform/deploy/docker-compose.aws.yml"
    with aws.open(encoding="utf-8") as f:
        data = yaml.safe_load(f)
    svc = data["services"]["mcp-infra"]
    ports = svc.get("ports", []) or []
    for entry in ports:
        s = str(entry)
        assert "8010" not in s.split(":")[0], (
            "infra/terraform/deploy/docker-compose.aws.yml still publishes "
            f"mcp-infra port 8010 to the host (entry: {entry!r}). "
            "Remove the ports: mapping (v1.43.4 H1)."
        )
