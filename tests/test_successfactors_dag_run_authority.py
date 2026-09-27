from __future__ import annotations

import asyncio
import importlib.util
import sys
import types
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest


ROOT = Path(__file__).resolve().parents[1]
DAGS = ROOT / "cartridges" / "sap_successfactors" / "dags"
KEY = "successfactors-run-authority-key-with-entropy-000001"
TENANT = "11111111-1111-4111-8111-111111111111"
WORKSPACE = "22222222-2222-4222-8222-222222222222"
OTHER = "33333333-3333-4333-8333-333333333333"


class _Fail(Exception):
    pass


def _stub_airflow(monkeypatch, tasks: dict):
    airflow = types.ModuleType("airflow")
    decorators = types.ModuleType("airflow.decorators")
    exceptions = types.ModuleType("airflow.exceptions")

    def dag(*_args, **_kwargs):
        def decorate(fn):
            def build(*args, **kwargs):
                fn(*args, **kwargs)
                return {"dag_id": fn.__name__}

            return build

        return decorate

    def task(fn):
        tasks[fn.__name__] = fn
        return lambda *_a, **_k: {"task": fn.__name__}

    decorators.dag = dag
    decorators.task = task
    exceptions.AirflowFailException = _Fail
    monkeypatch.setitem(sys.modules, "airflow", airflow)
    monkeypatch.setitem(sys.modules, "airflow.decorators", decorators)
    monkeypatch.setitem(sys.modules, "airflow.exceptions", exceptions)


def _load(monkeypatch, name: str):
    monkeypatch.setenv("SECURITY_CONTEXT_SIGNING_KEY", KEY)
    monkeypatch.setenv("INTERNAL_API_KEY_AIRFLOW_TO_MCP_INFRA", "airflow-to-mcp-transport-key")
    monkeypatch.delenv("MINIO_BUCKET", raising=False)
    monkeypatch.delenv("S3_BUCKET_NAME", raising=False)
    monkeypatch.syspath_prepend(str(ROOT / "airflow" / "dags"))
    tasks: dict = {}
    _stub_airflow(monkeypatch, tasks)
    module_name = f"_sf_run_authority_{name}"
    spec = importlib.util.spec_from_file_location(module_name, DAGS / f"{name}.py")
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    monkeypatch.setitem(sys.modules, module_name, module)
    spec.loader.exec_module(module)
    return module, tasks


def _real_request_context():
    path = ROOT / "cartridges" / "sap_successfactors" / "app" / "core" / "request_context.py"
    spec = importlib.util.spec_from_file_location("_sf_real_request_context", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class _Recorder:
    def __init__(self):
        self.saves: list[dict] = []
        self.entities: list[dict] = []
        self.verified: list[dict] = []
        self.plans: list[dict] = []


def _runtime(recorder: _Recorder):
    real = _real_request_context()

    def set_security_context(ctx):
        token = real.set_security_context(ctx)
        recorder.verified.append(real.get_security_context())
        return token

    def run_entity(config, **_kwargs):
        verified = real.verify_security_context(config["security_context"])
        recorder.entities.append({"config": config, "verified": verified})
        return {"status": "success", "record_count": 3, "storage_uri": "s3://lakehouse/raw/x"}

    async def silver(entity, ctx):
        return {"status": "success"}

    async def gold(target, ctx):
        return {"status": "success", "results": []}

    def plan(*, conn_id, security_context, target):
        real.verify_security_context(security_context)
        recorder.plans.append(security_context)
        return [{"entity": "PerPerson", "conn_id": "default"}], []

    return SimpleNamespace(
        get_entity_config=lambda entity: {"entity": entity, "conn_id": "default"},
        get_extract_all_plan=plan,
        run_entity=run_entity,
        is_metadata_skip_result=lambda payload: False,
        trigger_silver_refresh=silver,
        trigger_successfactors_gold_refresh=gold,
        classify_successful_extraction=lambda result: {**result, "status": "extracted"},
        classify_extraction_exception=lambda entity, exc: {"entity": entity, "status": "failed", "code": "X"},
        public_failure_message=lambda classified: "failed",
        summarize_extraction_results=lambda results: {
            key: 0
            for key in ("auth_blocked", "permission_blocked", "failed_open", "blocked", "skipped_explicit", "partial")
        },
        hard_failure_code=lambda *args, **kwargs: None,
        require_storage_access=lambda: None,
        set_security_context=set_security_context,
        reset_security_context=real.reset_security_context,
    )


def _wire(monkeypatch, module, recorder: _Recorder):
    monkeypatch.setattr(module, "_RUNTIME", _runtime(recorder))
    monkeypatch.setattr(module, "_run_async", lambda coro: asyncio.run(coro))

    class _Client:
        def __init__(self, *args, **kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def post(self, url, headers, json):
            recorder.saves.append(json)
            return SimpleNamespace(status_code=200, text="ok", raise_for_status=lambda: None)

    monkeypatch.setattr(module, "httpx", SimpleNamespace(Client=_Client))


def _console(**overrides) -> dict:
    from runtime_security_context import sign_runtime_context

    payload = {
        "trusted": True,
        "source": "console",
        "user_id": "user-9",
        "role": "admin",
        "tenant_id": TENANT,
        "workspace_id": WORKSPACE,
        "permissions": ["pipelines.run"],
        "allowed_cartridges": ["sap_successfactors"],
    }
    payload.update(overrides)
    return sign_runtime_context(payload)


def _dag_run(conf: dict, *, dag_id: str, run_id: str = "manual__1"):
    return SimpleNamespace(
        conf=conf,
        dag_id=dag_id,
        run_id=run_id,
        queued_at=datetime.now(timezone.utc),
        start_date=None,
    )


def _context(dag_run) -> dict:
    return {"dag_run": dag_run, "run_id": dag_run.run_id, "logical_date": datetime.now(timezone.utc)}


def test_single_entity_run_executes_under_a_minted_context_the_runtime_accepts(monkeypatch):
    module, tasks = _load(monkeypatch, "sap_successfactors_extract")
    recorder = _Recorder()
    _wire(monkeypatch, module, recorder)
    conf = {"entity": "PerPerson", "conn_id": "default", "security_context": _console(allowed_cartridges=["*"])}

    tasks["trigger_extract"](**_context(_dag_run(conf, dag_id="sap_successfactors_extract")))

    verified = recorder.verified[0]
    assert verified["source"] == "airflow" and verified["on_behalf_of"] == "user-9"
    assert verified["allowed_cartridges"] == ["sap_successfactors"]
    scope = f"tenant_id={TENANT}/workspace_id={WORKSPACE}/"
    assert verified["allowed_prefixes"] == [
        "raw/sap_successfactors/",
        f"silver/sap_successfactors/{scope}",
        f"gold/sap_successfactors/{scope}",
    ]
    assert recorder.entities[0]["verified"]["tenant_id"] == TENANT
    assert conf["security_context"]["_signature"] != verified["_signature"]
    assert [save["args"]["tenant_id"] for save in recorder.saves] == [TENANT]
    assert recorder.saves[0]["args"]["workspace_id"] == WORKSPACE


@pytest.mark.parametrize(
    "conf",
    [
        {"entity": "PerPerson", "conn_id": "default", "tenant_id": TENANT, "workspace_id": WORKSPACE},
        {
            "entity": "PerPerson",
            "conn_id": "default",
            "security_context": {
                "trusted": True,
                "source": "console",
                "role": "admin",
                "tenant_id": TENANT,
                "workspace_id": WORKSPACE,
                "permissions": ["cartridges.execute", "vault.secrets.reveal"],
                "allowed_cartridges": ["sap_successfactors"],
            },
        },
    ],
    ids=["bare-conf", "unsigned-context"],
)
def test_single_entity_run_without_verified_authority_does_nothing(monkeypatch, conf):
    module, tasks = _load(monkeypatch, "sap_successfactors_extract")
    recorder = _Recorder()
    _wire(monkeypatch, module, recorder)

    with pytest.raises(_Fail, match="run authority rejected"):
        tasks["trigger_extract"](**_context(_dag_run(conf, dag_id="sap_successfactors_extract")))

    assert recorder.entities == [] and recorder.verified == []
    assert recorder.saves == [], "a rejected run must not write telemetry under a claimed scope"


def test_single_entity_run_rejects_a_conf_scope_that_contradicts_the_context(monkeypatch):
    module, tasks = _load(monkeypatch, "sap_successfactors_extract")
    recorder = _Recorder()
    _wire(monkeypatch, module, recorder)
    conf = {"entity": "PerPerson", "conn_id": "default", "workspace_id": OTHER, "security_context": _console()}

    with pytest.raises(_Fail):
        tasks["trigger_extract"](**_context(_dag_run(conf, dag_id="sap_successfactors_extract")))
    assert recorder.saves == [] and recorder.entities == []


def test_scheduled_cycle_is_admitted_from_the_scheduler_signature(monkeypatch):
    module, tasks = _load(monkeypatch, "sap_successfactors_extract_all")
    recorder = _Recorder()
    _wire(monkeypatch, module, recorder)
    from entity_scheduler_trigger import scheduled_run_conf

    run_id = "sched_sap_successfactors___foundation_cycle___20260926T100000_0000"
    conf = scheduled_run_conf(
        {
            "cartridge_id": "sap_successfactors",
            "entity": "__foundation_cycle__",
            "dag_id": "sap_successfactors_extract_all",
            "mode": "incremental",
            "tenant_id": TENANT,
            "workspace_id": WORKSPACE,
            "conn_id": "default",
            "dag_params": {"target": "foundation", "tenant_id": OTHER, "security_context": {"trusted": True}},
        },
        run_id=run_id,
    )
    dag_run = _dag_run(conf, dag_id="sap_successfactors_extract_all", run_id=run_id)

    admission = tasks["authorize_refresh_chain"](**_context(dag_run))
    assert admission["conf"]["tenant_id"] == TENANT
    assert admission["conf"]["workspace_id"] == WORKSPACE
    assert admission["conf"]["security_context"]["tenant_id"] == TENANT

    payload = tasks["trigger_extract_all"](admission, **_context(dag_run))
    assert payload["target"] == "foundation"
    assert recorder.plans[0]["tenant_id"] == TENANT
    minted = recorder.entities[0]["verified"]
    assert minted["source"] == "airflow" and minted["on_behalf_of"] == "airflow:entity_scheduler"
    assert {save["args"]["tenant_id"] for save in recorder.saves} == {TENANT}


def test_scheduler_signature_cannot_be_replayed_onto_another_run(monkeypatch):
    module, tasks = _load(monkeypatch, "sap_successfactors_extract_all")
    recorder = _Recorder()
    _wire(monkeypatch, module, recorder)
    from runtime_security_context import build_scheduled_run_context

    ctx = build_scheduled_run_context(
        cartridge_id="sap_successfactors",
        tenant_id=TENANT,
        workspace_id=WORKSPACE,
        dag_id="sap_successfactors_extract_all",
        dag_run_id="sched_original",
    )
    replay = _dag_run({"security_context": ctx}, dag_id="sap_successfactors_extract_all", run_id="manual__replay")
    with pytest.raises(_Fail):
        tasks["authorize_refresh_chain"](**_context(replay))
    with pytest.raises(_Fail):
        tasks["trigger_extract_all"]({"conf": {}}, **_context(replay))
    assert recorder.saves == [] and recorder.entities == []


def test_bare_scheduled_conf_no_longer_self_mints_authority(monkeypatch):
    module, tasks = _load(monkeypatch, "sap_successfactors_extract_all")
    recorder = _Recorder()
    _wire(monkeypatch, module, recorder)
    bare = _dag_run(
        {"tenant_id": TENANT, "workspace_id": WORKSPACE, "target": "foundation", "triggered_by": "entity_scheduler"},
        dag_id="sap_successfactors_extract_all",
    )
    with pytest.raises(_Fail, match="run authority rejected"):
        tasks["authorize_refresh_chain"](**_context(bare))
    assert recorder.saves == []
