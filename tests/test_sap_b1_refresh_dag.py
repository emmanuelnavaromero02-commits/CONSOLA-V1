from __future__ import annotations

import importlib.util
import sys
import types
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest


REPO_ROOT = Path(__file__).resolve().parents[1]
DAG_FILE = REPO_ROOT / "cartridges" / "sap_b1" / "dags" / "sap_b1_refresh.py"
TENANT = "11111111-1111-4111-8111-111111111111"
WORKSPACE = "22222222-2222-4222-8222-222222222222"
OTHER_WORKSPACE = "33333333-3333-4333-8333-333333333333"
NOW = datetime(2026, 9, 25, 12, 0, tzinfo=timezone.utc)


@pytest.fixture
def module(monkeypatch):
    captured: dict = {}
    airflow = types.ModuleType("airflow")
    decorators = types.ModuleType("airflow.decorators")
    models = types.ModuleType("airflow.models")

    def dag(**kwargs):
        captured.update(kwargs)
        return lambda function: function

    decorators.dag = dag
    decorators.task = lambda function: function
    models.Variable = types.SimpleNamespace(get=lambda *_args, **_kwargs: "[]")
    monkeypatch.setitem(sys.modules, "airflow", airflow)
    monkeypatch.setitem(sys.modules, "airflow.decorators", decorators)
    monkeypatch.setitem(sys.modules, "airflow.models", models)
    monkeypatch.syspath_prepend(str(REPO_ROOT / "airflow" / "dags"))
    monkeypatch.setenv("SECURITY_CONTEXT_SIGNING_KEY", "s" * 48)
    for name in ("runtime_security_context", "dataset_refresh_admission"):
        monkeypatch.delitem(sys.modules, name, raising=False)
    spec = importlib.util.spec_from_file_location("sap_b1_refresh_under_test", DAG_FILE)
    loaded = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(loaded)
    loaded.DAG_KWARGS = captured
    return loaded


def _marker(workspace: str, batch: str, when: datetime, day: str = "2026-09-25") -> dict:
    key = (
        f"raw/sap_b1/IntercompanyPartners/tenant_id={TENANT}/workspace_id={workspace}/"
        f"load_date={day}/batch_id={batch}/IntercompanyPartners.parquet"
    )
    return {"Key": key, "LastModified": when}


def test_dag_is_paused_on_creation_and_never_overlaps(module):
    assert module.DAG_KWARGS["dag_id"] == "sap_b1_refresh"
    assert module.DAG_KWARGS["schedule"] == "*/30 * * * *"
    assert module.DAG_KWARGS["is_paused_upon_creation"] is True
    assert module.DAG_KWARGS["max_active_runs"] == 1
    assert module.DAG_KWARGS["catchup"] is False


def test_scopes_are_validated_and_normalized(module):
    assert module.parse_scopes("") == []
    assert module.parse_scopes(
        f'[{{"tenant_id": "{TENANT.upper()}", "workspace_id": "{WORKSPACE}"}}]'
    ) == [(TENANT, WORKSPACE)]
    for raw in (
        "{",
        "{}",
        '[{"tenant_id": "x", "workspace_id": "y"}]',
        f'[{{"tenant_id": "{TENANT}"}}]',
        f'[{{"tenant_id": "{TENANT}", "workspace_id": "{WORKSPACE}", "extra": 1}}]',
        f'[{{"tenant_id": "{TENANT}", "workspace_id": "{WORKSPACE}/../x"}}]',
        f'[{{"tenant_id": "{TENANT}", "workspace_id": "{WORKSPACE}"}},'
        f' {{"tenant_id": "{TENANT}", "workspace_id": "{WORKSPACE}"}}]',
    ):
        with pytest.raises(ValueError):
            module.parse_scopes(raw)


def test_marker_prefixes_cover_today_and_yesterday(module):
    assert module.marker_prefixes(TENANT, WORKSPACE, NOW) == [
        f"raw/sap_b1/IntercompanyPartners/tenant_id={TENANT}/workspace_id={WORKSPACE}/load_date=2026-09-25/",
        f"raw/sap_b1/IntercompanyPartners/tenant_id={TENANT}/workspace_id={WORKSPACE}/load_date=2026-09-24/",
    ]


def test_latest_delivery_is_the_newest_marker(module):
    older = _marker(WORKSPACE, "run-a", NOW - timedelta(hours=3), day="2026-09-24")
    newer = _marker(WORKSPACE, "run-b", NOW - timedelta(hours=1))
    noise = {"Key": newer["Key"].replace("IntercompanyPartners.parquet", "other.parquet"), "LastModified": NOW}

    assert module.latest_delivery([older, newer, noise])["batch_id"] == "run-b"
    assert module.latest_delivery([noise]) is None


def test_refresh_trigger_is_signed_scoped_and_idempotent_per_batch(module):
    first = module.refresh_trigger(TENANT, WORKSPACE, "run-a")
    again = module.refresh_trigger(TENANT, WORKSPACE, "run-a")
    other = module.refresh_trigger(TENANT, WORKSPACE, "run-b")

    assert first["dag_run_id"] == again["dag_run_id"] != other["dag_run_id"]
    assert first["dag_run_id"].startswith("sap_b1_push__dataset_refresh__")
    conf = first["conf"]
    assert conf["seed_raw"] == "raw/sap_b1/*"
    assert conf["cartridge_id"] == "sap_b1"
    assert conf["skip_intelligence"] is True
    assert (conf["tenant_id"], conf["workspace_id"]) == (TENANT, WORKSPACE)
    admission = conf["security_context"]
    assert admission["seed_raw"] == "raw/sap_b1/*"
    assert admission["dag_run_id"] == first["dag_run_id"]
    assert (admission["tenant_id"], admission["workspace_id"]) == (TENANT, WORKSPACE)


def test_refresh_triggers_new_deliveries_and_skips_processed_ones(module):
    listings = {
        WORKSPACE: [_marker(WORKSPACE, "run-a", NOW - timedelta(minutes=20))],
        OTHER_WORKSPACE: [_marker(OTHER_WORKSPACE, "run-b", NOW - timedelta(hours=2))],
    }
    calls = []

    def list_objects(prefix):
        workspace = WORKSPACE if WORKSPACE in prefix else OTHER_WORKSPACE
        return [item for item in listings[workspace] if item["Key"].startswith(prefix)]

    def trigger(run_id, conf):
        calls.append((run_id, conf["workspace_id"]))
        return 409 if conf["workspace_id"] == OTHER_WORKSPACE else 200

    summary = module.refresh_scopes(
        [(TENANT, WORKSPACE), (TENANT, OTHER_WORKSPACE)],
        now=NOW,
        list_objects=list_objects,
        trigger=trigger,
    )

    assert summary["triggered"] == [f"{TENANT}/{WORKSPACE}"]
    assert summary["already_done"] == [f"{TENANT}/{OTHER_WORKSPACE}"]
    assert [workspace for _run, workspace in calls] == [WORKSPACE, OTHER_WORKSPACE]


def test_a_silent_agent_fails_the_run_after_every_scope_is_processed(module):
    calls = []

    def list_objects(prefix):
        if OTHER_WORKSPACE in prefix and "2026-09-25" in prefix:
            return [_marker(OTHER_WORKSPACE, "run-b", NOW - timedelta(minutes=5))]
        if WORKSPACE in prefix and "2026-09-24" in prefix:
            return [_marker(WORKSPACE, "run-a", NOW - timedelta(hours=7), day="2026-09-24")]
        return []

    with pytest.raises(RuntimeError, match="no sap_b1 delivery"):
        module.refresh_scopes(
            [(TENANT, WORKSPACE), (TENANT, OTHER_WORKSPACE)],
            now=NOW,
            list_objects=list_objects,
            trigger=lambda run_id, conf: calls.append(conf["workspace_id"]) or 201,
        )
    assert calls == [WORKSPACE, OTHER_WORKSPACE]


def test_a_scope_without_any_delivery_is_reported_silent(module):
    with pytest.raises(RuntimeError, match=WORKSPACE):
        module.refresh_scopes(
            [(TENANT, WORKSPACE)],
            now=NOW,
            list_objects=lambda prefix: [],
            trigger=lambda run_id, conf: pytest.fail("nothing to trigger"),
        )


def test_a_failing_scope_does_not_stop_the_others(module):
    calls = []

    def list_objects(prefix):
        if WORKSPACE in prefix:
            raise OSError("listing failed")
        return [_marker(OTHER_WORKSPACE, "run-b", NOW - timedelta(minutes=5))] if "2026-09-25" in prefix else []

    with pytest.raises(RuntimeError, match="failed for 1 scope"):
        module.refresh_scopes(
            [(TENANT, WORKSPACE), (TENANT, OTHER_WORKSPACE)],
            now=NOW,
            list_objects=list_objects,
            trigger=lambda run_id, conf: calls.append(conf["workspace_id"]) or 201,
        )
    assert calls == [OTHER_WORKSPACE]
