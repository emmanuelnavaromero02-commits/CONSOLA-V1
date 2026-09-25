from __future__ import annotations

import ast
from pathlib import Path

import pytest

from airflow.dags import runtime_security_context as runtime
from airflow.dags.b1_runtime_context import sap_b1_security_context, security_context_from_conf


REPO_ROOT = Path(__file__).resolve().parents[1]
TENANT = "11111111-1111-4111-8111-111111111111"
WORKSPACE = "22222222-2222-4222-8222-222222222222"


@pytest.fixture(autouse=True)
def _signing_key(monkeypatch):
    monkeypatch.setenv("SECURITY_CONTEXT_SIGNING_KEY", "k" * 48)


def test_the_context_is_signed_scoped_and_limited_to_sap_b1():
    context = sap_b1_security_context(TENANT.upper(), WORKSPACE, user_id="airflow:test")
    runtime.verify_runtime_signature(context)
    assert (context["tenant_id"], context["workspace_id"]) == (TENANT, WORKSPACE)
    assert context["allowed_cartridges"] == ["sap_b1"]
    assert {"vault.secrets.reveal", "cartridges.execute", "pipelines.run"} <= set(context["permissions"])
    scope = f"tenant_id={TENANT}/workspace_id={WORKSPACE}/"
    assert context["allowed_prefixes"] == ["raw/sap_b1/", f"silver/sap_b1/{scope}", f"gold/sap_b1/{scope}"]


@pytest.mark.parametrize("conf", [{}, {"tenant_id": TENANT}, {"tenant_id": "t", "workspace_id": "w"}])
def test_a_run_without_a_workspace_scope_is_refused(conf):
    with pytest.raises(ValueError):
        security_context_from_conf(conf, user_id="airflow:test")


def test_a_supplied_context_must_be_signed_and_match_the_run():
    signed = sap_b1_security_context(TENANT, WORKSPACE, user_id="console")
    fresh = security_context_from_conf({"security_context": signed}, user_id="airflow:test")
    assert (fresh["tenant_id"], fresh["workspace_id"], fresh["user_id"]) == (TENANT, WORKSPACE, "airflow:test")
    other = "33333333-3333-4333-8333-333333333333"
    with pytest.raises(ValueError):
        security_context_from_conf({"tenant_id": TENANT, "workspace_id": other, "security_context": signed}, user_id="x")
    forged = dict(signed, workspace_id=other)
    with pytest.raises(ValueError):
        security_context_from_conf({"security_context": forged}, user_id="x")
    with pytest.raises(ValueError):
        security_context_from_conf({"security_context": dict(signed, allowed_cartridges=["hubspot"])}, user_id="x")


@pytest.mark.parametrize("dag", ["sap_b1_extract.py", "sap_b1_extract_all.py"])
def test_the_pull_dags_send_only_a_fresh_signed_context(dag):
    source = (REPO_ROOT / "cartridges" / "sap_b1" / "dags" / dag).read_text(encoding="utf-8")
    ast.parse(source)
    assert "from b1_runtime_context import security_context_from_conf" in source
    assert 'return {"security_context": security_context_from_conf(conf, ' in source
    assert "json=skill_body," in source
    assert '"tenant_id", "workspace_id", "security_context"' not in source
