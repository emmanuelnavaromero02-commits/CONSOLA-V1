from __future__ import annotations

import time
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from airflow.dags import cartridge_run_admission as admission
from airflow.dags.runtime_security_context import (
    build_materialize_context,
    build_scheduled_run_context,
    sign_runtime_context,
    verify_runtime_signature,
)


KEY = "cartridge-run-admission-test-key-with-entropy-0001"
TENANT = "11111111-1111-4111-8111-111111111111"
WORKSPACE = "22222222-2222-4222-8222-222222222222"
OTHER = "33333333-3333-4333-8333-333333333333"


@pytest.fixture(autouse=True)
def _signing_key(monkeypatch):
    monkeypatch.setenv("SECURITY_CONTEXT_SIGNING_KEY", KEY)
    monkeypatch.delenv("MINIO_BUCKET", raising=False)
    monkeypatch.delenv("S3_BUCKET_NAME", raising=False)


def _console(now: int | None = None, **overrides) -> dict:
    payload = {
        "trusted": True,
        "source": "console",
        "user_id": "user-7",
        "role": "admin",
        "tenant_id": TENANT,
        "workspace_id": WORKSPACE,
        "permissions": ["pipelines.run", "datasets.read"],
        "allowed_cartridges": ["hubspot"],
    }
    payload.update(overrides)
    return sign_runtime_context(payload, now=now)


def _run(queued_at: int | None = None, *, dag_id: str = "hubspot_extract", run_id: str = "manual__1"):
    moment = datetime.fromtimestamp(queued_at, tz=timezone.utc) if queued_at else None
    return SimpleNamespace(queued_at=moment, start_date=None, dag_id=dag_id, run_id=run_id)


def test_signed_console_context_is_admitted_with_its_scope():
    admitted = admission.admit_run({"security_context": _console()}, cartridge_id="hubspot", dag_run=_run())
    assert (admitted.cartridge_id, admitted.tenant_id, admitted.workspace_id) == ("hubspot", TENANT, WORKSPACE)
    assert admitted.principal == "user-7" and admitted.source == "console"


@pytest.mark.parametrize(
    "conf",
    [
        None,
        {},
        {"tenant_id": TENANT, "workspace_id": WORKSPACE},
        {"tenant_id": TENANT, "workspace_id": WORKSPACE, "cartridge_id": "hubspot", "conn_id": "default"},
        {"security_context": "not-a-mapping"},
    ],
)
def test_bare_conf_is_never_run_authority(conf):
    with pytest.raises(admission.RunAdmissionError):
        admission.admit_run(conf, cartridge_id="hubspot", dag_run=_run())


def test_unsigned_or_forged_contexts_are_rejected():
    signed = _console()
    unsigned = {key: value for key, value in signed.items() if not key.startswith("_")}
    forged = dict(signed, workspace_id=OTHER)
    wrong_version = dict(signed, _signature_version="hmac-sha256-v2")
    untrusted = dict(signed, trusted="true")
    for ctx in (unsigned, forged, wrong_version, untrusted):
        with pytest.raises(admission.RunAdmissionError):
            admission.admit_run({"security_context": ctx}, cartridge_id="hubspot", dag_run=_run())


def test_context_is_judged_at_queued_at_not_at_execution(monkeypatch):
    signed_at = int(time.time()) - 3600
    ctx = _console(now=signed_at)
    later = admission.admit_run(
        {"security_context": ctx}, cartridge_id="hubspot", dag_run=_run(queued_at=signed_at + 10)
    )
    assert later.tenant_id == TENANT
    with pytest.raises(admission.RunAdmissionError, match="expired"):
        admission.admit_run({"security_context": ctx}, cartridge_id="hubspot", dag_run=_run())


def test_context_already_expired_when_queued_is_rejected():
    signed_at = int(time.time()) - 7200
    ctx = _console(now=signed_at)
    with pytest.raises(admission.RunAdmissionError, match="expired"):
        admission.admit_run(
            {"security_context": ctx}, cartridge_id="hubspot", dag_run=_run(queued_at=signed_at + 301)
        )


def test_cleared_run_with_reset_queued_at_fails_admission():
    signed_at = int(time.time()) - 7200
    ctx = _console(now=signed_at)
    cleared = _run(queued_at=int(time.time()))
    with pytest.raises(admission.RunAdmissionError, match="expired"):
        admission.admit_run({"security_context": ctx}, cartridge_id="hubspot", dag_run=cleared)


@pytest.mark.parametrize("key,value", [("tenant_id", OTHER), ("workspace_id", OTHER)])
def test_conf_scope_that_disagrees_with_the_context_is_rejected(key, value):
    with pytest.raises(admission.RunAdmissionError, match="does not match"):
        admission.admit_run(
            {"security_context": _console(), key: value}, cartridge_id="hubspot", dag_run=_run()
        )


def test_conf_scope_equal_to_the_context_is_accepted_case_insensitively():
    admitted = admission.admit_run(
        {"security_context": _console(), "tenant_id": TENANT.upper(), "workspace_id": WORKSPACE},
        cartridge_id="hubspot",
        dag_run=_run(),
    )
    assert admitted.tenant_id == TENANT


def test_wrong_cartridge_is_rejected_and_wildcard_is_scoped():
    with pytest.raises(admission.RunAdmissionError, match="not allowed"):
        admission.admit_run({"security_context": _console()}, cartridge_id="salesforce", dag_run=_run())
    wildcard = _console(allowed_cartridges=["*"])
    admitted = admission.admit_run({"security_context": wildcard}, cartridge_id="salesforce", dag_run=_run())
    assert admitted.cartridge_id == "salesforce" and admitted.tenant_id == TENANT


def test_context_without_a_run_permission_is_rejected():
    ctx = _console(permissions=["datasets.read", "vault.secrets.reveal"])
    with pytest.raises(admission.RunAdmissionError, match="run permission"):
        admission.admit_run({"security_context": ctx}, cartridge_id="hubspot", dag_run=_run())
    executes = _console(permissions=["cartridges.execute"])
    assert admission.admit_run({"security_context": executes}, cartridge_id="hubspot", dag_run=_run())


def test_unscoped_admin_context_is_rejected():
    ctx = _console(tenant_id=None, workspace_id=None, allowed_cartridges=["*"], role="super_admin")
    with pytest.raises(admission.RunAdmissionError, match="UUIDs"):
        admission.admit_run({"security_context": ctx}, cartridge_id="hubspot", dag_run=_run())
    legacy = _console(tenant_id="default", workspace_id="main")
    with pytest.raises(admission.RunAdmissionError, match="UUIDs"):
        admission.admit_run({"security_context": legacy}, cartridge_id="hubspot", dag_run=_run())


@pytest.mark.parametrize("source", ["refinement", "agent_runner", "cartridge-hubspot", "airflow:file_ingest", ""])
def test_issuers_outside_the_run_allowlist_are_rejected(source):
    ctx = _console(source=source)
    with pytest.raises(admission.RunAdmissionError, match="issuer"):
        admission.admit_run({"security_context": ctx}, cartridge_id="hubspot", dag_run=_run())


def test_purpose_bound_tokens_are_not_run_authority():
    materialize = build_materialize_context(
        tenant_id=TENANT,
        workspace_id=WORKSPACE,
        cartridge_id="hubspot",
        dataset_name="hubspot_deals",
        run_id="run-1",
    )
    with pytest.raises(admission.RunAdmissionError):
        admission.admit_run({"security_context": materialize}, cartridge_id="hubspot", dag_run=_run())
    other_purpose = _console(purpose="mcp.pipeline_run_save")
    with pytest.raises(admission.RunAdmissionError, match="purpose"):
        admission.admit_run({"security_context": other_purpose}, cartridge_id="hubspot", dag_run=_run())


def test_scheduler_context_is_bound_to_its_dag_run():
    ctx = build_scheduled_run_context(
        cartridge_id="hubspot",
        tenant_id=TENANT,
        workspace_id=WORKSPACE,
        dag_id="hubspot_extract",
        dag_run_id="sched_hubspot_deals_1",
    )
    bound = _run(dag_id="hubspot_extract", run_id="sched_hubspot_deals_1")
    admitted = admission.admit_run({"security_context": ctx}, cartridge_id="hubspot", dag_run=bound)
    assert admitted.principal == "airflow:entity_scheduler" and admitted.source == "airflow"
    for other in (
        _run(dag_id="hubspot_extract", run_id="manual__replayed"),
        _run(dag_id="hubspot_extract_all", run_id="sched_hubspot_deals_1"),
        None,
    ):
        with pytest.raises(admission.RunAdmissionError, match="another run"):
            admission.admit_run({"security_context": ctx}, cartridge_id="hubspot", dag_run=other)


def test_minted_context_is_fresh_narrow_and_drops_caller_privileges(monkeypatch):
    monkeypatch.setenv("S3_BUCKET_NAME", "omega-bucket")
    wide = _console(
        allowed_cartridges=["*"],
        permissions=["pipelines.run", "users.manage", "vault.connections.write"],
        allowed_prefixes=["raw/", "silver/", "gold/"],
        email="someone@example.test",
        project_id="p-1",
    )
    admitted = admission.admit_run({"security_context": wide}, cartridge_id="hubspot", dag_run=_run())
    first = admitted.context(user_id="airflow:hubspot_extract")
    verify_runtime_signature(first)
    assert first["source"] == "airflow" and first["user_id"] == "airflow:hubspot_extract"
    assert first["on_behalf_of"] == "user-7"
    assert first["allowed_cartridges"] == ["hubspot"]
    assert first["permissions"] == ["cartridges.execute", "vault.secrets.reveal", "pipelines.run"]
    assert first["allowed_buckets"] == ["lakehouse", "omega-bucket"]
    scope = f"tenant_id={TENANT}/workspace_id={WORKSPACE}/"
    assert first["allowed_prefixes"] == ["raw/hubspot/", f"silver/hubspot/{scope}", f"gold/hubspot/{scope}"]
    assert "email" not in first and "project_id" not in first
    custom = admitted.context(user_id="airflow:x", prefixes=[f"raw/hubspot/deals/{scope}"])
    assert custom["allowed_prefixes"] == [f"raw/hubspot/deals/{scope}"]
    with pytest.raises(admission.RunAdmissionError):
        admitted.context(user_id="")


def test_minted_context_is_signed_at_each_call(monkeypatch):
    admitted = admission.admit_run({"security_context": _console()}, cartridge_id="hubspot", dag_run=_run())
    clock = iter([1_000_000, 1_000_900])
    monkeypatch.setattr(time, "time", lambda: next(clock))
    first = admitted.context(user_id="airflow:a")
    second = admitted.context(user_id="airflow:a")
    assert first["_signed_at"] != second["_signed_at"]
    assert first["_signature"] != second["_signature"]


def test_service_run_reads_platform_state_scope_only():
    admitted = admission.service_run("sap_b1", TENANT.upper(), WORKSPACE, principal="airflow:sap_b1_refresh")
    assert (admitted.tenant_id, admitted.source, admitted.principal) == (TENANT, "airflow", "airflow:sap_b1_refresh")
    for tenant, workspace, principal in ((TENANT, "w", "p"), ("", WORKSPACE, "p"), (TENANT, WORKSPACE, "")):
        with pytest.raises(admission.RunAdmissionError):
            admission.service_run("sap_b1", tenant, workspace, principal=principal)
    with pytest.raises(admission.RunAdmissionError, match="cartridge"):
        admission.service_run("../sap_b1", TENANT, WORKSPACE, principal="p")


def test_strip_reserved_removes_backend_owned_keys():
    params = {
        "target": "foundation",
        "file_pattern": "*.csv",
        "security_context": {"trusted": True},
        "tenant_id": OTHER,
        "workspace_id": OTHER,
        "cartridge_id": "sap_b1",
        "conn_id": "other",
        "connection_id": "other",
        "triggered_by": "console",
        "_signature": "x",
        "__proto__": 1,
    }
    assert admission.strip_reserved(params) == {"target": "foundation", "file_pattern": "*.csv"}
    assert admission.strip_reserved(None) == {}
    assert admission.strip_reserved(["tenant_id"]) == {}


def test_admission_time_prefers_queued_at():
    queued = datetime(2026, 9, 1, tzinfo=timezone.utc)
    started = datetime(2026, 9, 2, tzinfo=timezone.utc)
    assert admission.admission_time(SimpleNamespace(queued_at=queued, start_date=started)) == int(queued.timestamp())
    assert admission.admission_time(SimpleNamespace(queued_at=None, start_date=started)) == int(started.timestamp())
    assert admission.admission_time(SimpleNamespace(queued_at=None, start_date=None)) is None
