from __future__ import annotations

import hashlib
import hmac
import json
from pathlib import Path
from types import SimpleNamespace

import pytest
import sqlglot

from airflow.dags.market_security_context import security_context_from_conf
from airflow.dags.runtime_security_context import build_scheduled_run_context


REPO = Path(__file__).resolve().parents[1]
TENANT_ID = "d5d95d5e-0326-4f36-b04f-2a3b77ed61d2"
WORKSPACE_ID = "4a6e9743-d54e-46ff-a023-111f06572c42"
SIGNING_KEY = "market-scheduler-test-signing-key-distinct-123456789"


def _canonical(context: dict) -> bytes:
    payload = {key: value for key, value in context.items() if key != "_signature"}
    return json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()


def _scheduled(cartridge: str, run_id: str = "sched_banxico_series_observations_1") -> dict:
    return build_scheduled_run_context(
        cartridge_id=cartridge,
        tenant_id=TENANT_ID,
        workspace_id=WORKSPACE_ID,
        dag_id=f"{cartridge}_extract",
        dag_run_id=run_id,
    )


def _dag_run(cartridge: str, run_id: str = "sched_banxico_series_observations_1"):
    return SimpleNamespace(
        dag_id=f"{cartridge}_extract", run_id=run_id, queued_at=None, start_date=None
    )


def test_market_context_is_fresh_signed_and_scoped(monkeypatch):
    monkeypatch.setenv("SECURITY_CONTEXT_SIGNING_KEY", SIGNING_KEY)
    monkeypatch.setenv("S3_BUCKET_NAME", "omega-production")

    context = security_context_from_conf(
        {
            "tenant_id": TENANT_ID,
            "workspace_id": WORKSPACE_ID,
            "security_context": _scheduled("banxico"),
        },
        "banxico",
        dag_run=_dag_run("banxico"),
    )

    expected = hmac.new(SIGNING_KEY.encode(), _canonical(context), hashlib.sha256).hexdigest()
    assert hmac.compare_digest(context["_signature"], expected)
    assert context["source"] == "airflow"
    assert context["allowed_cartridges"] == ["banxico"]
    assert context["tenant_id"] == TENANT_ID
    assert context["workspace_id"] == WORKSPACE_ID
    assert "omega-production" in context["allowed_buckets"]
    assert all(WORKSPACE_ID in prefix for prefix in context["allowed_prefixes"])
    assert "issuer" not in context and "dag_run_id" not in context


def test_bare_scheduled_conf_is_not_market_authority(monkeypatch):
    monkeypatch.setenv("SECURITY_CONTEXT_SIGNING_KEY", SIGNING_KEY)

    with pytest.raises(ValueError, match="signed run authority"):
        security_context_from_conf(
            {"tenant_id": TENANT_ID, "workspace_id": WORKSPACE_ID},
            "banxico",
            dag_run=_dag_run("banxico"),
        )


def test_supplied_context_cannot_change_backend_scope(monkeypatch):
    monkeypatch.setenv("SECURITY_CONTEXT_SIGNING_KEY", SIGNING_KEY)
    unsigned = {
        "trusted": True,
        "source": "console",
        "tenant_id": TENANT_ID,
        "workspace_id": WORKSPACE_ID,
        "permissions": ["pipelines.run"],
        "allowed_cartridges": ["banxico"],
    }
    with pytest.raises(ValueError):
        security_context_from_conf(
            {"security_context": unsigned}, "banxico", dag_run=_dag_run("banxico")
        )

    with pytest.raises(ValueError, match="tenant_id does not match"):
        security_context_from_conf(
            {
                "tenant_id": "33333333-3333-4333-8333-333333333333",
                "workspace_id": WORKSPACE_ID,
                "security_context": _scheduled("banxico"),
            },
            "banxico",
            dag_run=_dag_run("banxico"),
        )

    with pytest.raises(ValueError, match="not allowed"):
        security_context_from_conf(
            {"security_context": _scheduled("inegi")}, "banxico", dag_run=_dag_run("banxico")
        )


@pytest.mark.parametrize("cartridge", ["banxico", "inegi", "sec_edgar"])
def test_market_dags_build_context_at_task_execution(cartridge: str):
    source = (REPO / f"cartridges/{cartridge}/dags/{cartridge}_extract.py").read_text()
    assert "from market_security_context import security_context_from_conf" in source
    assert f'security_context_from_conf(conf, "{cartridge}", dag_run=dag_run)' in source
    assert 'headers["X-Security-Context"]' in source
    assert 'body["tenant_id"] = security_context["tenant_id"]' in source
    assert 'conf.get("tenant_id")' not in source


def test_repair_migration_targets_fc_america_and_resets_schedule():
    path = REPO / "infra/init/99zza_market_context_fc_america_scope.sql"
    source = path.read_text()
    statements = [statement for statement in sqlglot.parse(source, read="postgres") if statement]

    assert [type(statement).__name__ for statement in statements] == ["Update", "Update", "Insert"]
    assert TENANT_ID in source
    assert WORKSPACE_ID in source
    assert "connection_id = 'default'" in source
    assert source.count("last_scheduled_at = NULL") == 2
    assert "99zza_market_context_fc_america_scope.sql" in source
