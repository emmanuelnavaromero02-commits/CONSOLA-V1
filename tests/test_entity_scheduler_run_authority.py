from __future__ import annotations

from types import SimpleNamespace

import pytest

from airflow.dags import cartridge_run_admission as admission
from airflow.dags.entity_scheduler_trigger import (
    ScopeMissing,
    require_all_succeeded,
    scheduled_run_conf,
)
from airflow.dags.runtime_security_context import verify_runtime_signature
from tests.airflow_dag_test_loader import load_dag


KEY = "entity-scheduler-run-authority-key-with-entropy-01"
TENANT = "11111111-1111-4111-8111-111111111111"
WORKSPACE = "22222222-2222-4222-8222-222222222222"
OTHER = "33333333-3333-4333-8333-333333333333"


@pytest.fixture(autouse=True)
def _signing_key(monkeypatch):
    monkeypatch.setenv("SECURITY_CONTEXT_SIGNING_KEY", KEY)


def _row(**overrides) -> dict:
    row = {
        "cartridge_id": "banxico",
        "entity": "series_observations",
        "dag_id": "banxico_extract",
        "mode": "incremental",
        "tenant_id": TENANT,
        "workspace_id": WORKSPACE,
        "conn_id": "default",
        "fire_time": "2026-09-26T10:00:00+00:00",
        "dag_params": {},
    }
    row.update(overrides)
    return row


def test_scoped_cartridge_row_gets_a_run_bound_signed_context():
    conf = scheduled_run_conf(_row(), run_id="sched_banxico_1")

    ctx = conf["security_context"]
    verify_runtime_signature(ctx)
    assert (ctx["tenant_id"], ctx["workspace_id"]) == (TENANT, WORKSPACE)
    assert ctx["allowed_cartridges"] == ["banxico"] and ctx["permissions"] == ["pipelines.run"]
    assert (ctx["dag_id"], ctx["dag_run_id"]) == ("banxico_extract", "sched_banxico_1")
    run = SimpleNamespace(dag_id="banxico_extract", run_id="sched_banxico_1", queued_at=None, start_date=None)
    admitted = admission.admit_run(conf, cartridge_id="banxico", dag_run=run)
    assert admitted.tenant_id == TENANT and admitted.principal == "airflow:entity_scheduler"


def test_stored_dag_params_cannot_override_backend_keys_or_inject_authority():
    forged = {"trusted": True, "source": "console", "tenant_id": OTHER, "workspace_id": OTHER}
    conf = scheduled_run_conf(
        _row(
            conn_id=None,
            dag_params={
                "series_ids": ["SF43718"],
                "security_context": forged,
                "tenant_id": OTHER,
                "workspace_id": OTHER,
                "cartridge_id": "sap_b1",
                "conn_id": "someone-elses",
                "connection_id": "someone-elses",
                "triggered_by": "console",
                "entity": "other_entity",
                "mode": "full",
                "_internal": True,
            },
        ),
        run_id="sched_banxico_2",
    )

    assert conf["series_ids"] == ["SF43718"]
    assert conf["tenant_id"] == TENANT and conf["workspace_id"] == WORKSPACE
    assert conf["cartridge_id"] == "banxico" and conf["triggered_by"] == "entity_scheduler"
    assert conf["entity"] == "series_observations" and conf["mode"] == "incremental"
    assert "conn_id" not in conf and "connection_id" not in conf and "_internal" not in conf
    assert conf["security_context"]["tenant_id"] == TENANT
    assert conf["security_context"] is not forged


def test_platform_rows_carry_no_run_authority():
    conf = scheduled_run_conf(
        _row(cartridge_id="platform", dag_id="file_ingest", tenant_id=None, workspace_id=None,
             dag_params={"file_pattern": "*.csv", "security_context": {"trusted": True}}),
        run_id="sched_platform_1",
    )
    assert "security_context" not in conf and "tenant_id" not in conf
    assert conf["file_pattern"] == "*.csv"


@pytest.mark.parametrize("missing", ["tenant_id", "workspace_id"])
def test_unscoped_cartridge_rows_are_not_triggered(missing):
    with pytest.raises(ScopeMissing):
        scheduled_run_conf(_row(**{missing: None}), run_id="sched_banxico_3")


def test_scope_missing_is_recorded_but_not_a_trigger_failure():
    require_all_succeeded([{"ok": True}, {"ok": False, "status": "scope_missing"}])
    with pytest.raises(RuntimeError, match="1 of 3"):
        require_all_succeeded(
            [{"ok": True}, {"ok": False, "status": "scope_missing"}, {"ok": False, "status": 502}]
        )


class _Cursor:
    def __init__(self, updates: list):
        self.updates = updates

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def execute(self, sql, params):
        self.updates.append(params)


class _Conn:
    def __init__(self, updates: list):
        self.updates = updates

    def cursor(self):
        return _Cursor(self.updates)

    def commit(self):
        return None

    def close(self):
        return None


def test_trigger_each_signs_scoped_rows_and_skips_unscoped_ones(monkeypatch):
    module = load_dag(monkeypatch, "entity_scheduler")
    posted: list[dict] = []
    updates: list = []

    def fake_trigger(**kwargs):
        posted.append(kwargs)
        return 200

    monkeypatch.setattr(module, "trigger_dag_run", fake_trigger)
    monkeypatch.setattr(module, "_pg", lambda: _Conn(updates))
    due = [
        _row(dag_params={"tenant_id": OTHER}),
        _row(cartridge_id="sap_successfactors", entity="__foundation_cycle__",
             dag_id="sap_successfactors_extract_all", tenant_id=None, workspace_id=None),
        _row(cartridge_id="platform", entity="Uploads", dag_id="file_ingest", tenant_id=None, workspace_id=None),
    ]
    ti = SimpleNamespace(xcom_pull=lambda task_ids, key: due)

    outcome = module.trigger_each(ti=ti)

    assert [call["dag_id"] for call in posted] == ["banxico_extract", "file_ingest"]
    scheduled = posted[0]
    assert scheduled["conf"]["tenant_id"] == TENANT
    assert scheduled["conf"]["security_context"]["dag_run_id"] == scheduled["run_id"]
    assert "security_context" not in posted[1]["conf"]
    statuses = {item["dag_id"]: item["status"] for item in outcome["results"]}
    assert statuses["sap_successfactors_extract_all"] == "scope_missing"
    assert outcome["triggered"] == 2
    assert [params[1:] for params in updates] == [
        ("banxico", "series_observations"),
        ("platform", "Uploads"),
    ]
