from __future__ import annotations

import asyncio
import importlib.util
import re
import sys
import threading
from pathlib import Path

import httpx
import pytest
from fastapi import Body, FastAPI, Header, HTTPException
from fastapi.responses import JSONResponse

ROOT = Path(__file__).resolve().parents[1]
MODULE = ROOT / "cartridges" / "banxico" / "app" / "core" / "async_jobs.py"
CARTRIDGES = ("banxico", "inegi", "sec_edgar", "hubspot", "salesforce", "sap_hcm", "sap_s4hana", "sap_b1")
EXTRACT_DAGS = (
    "cartridges/banxico/dags/banxico_extract.py",
    "cartridges/inegi/dags/inegi_extract.py",
    "cartridges/sec_edgar/dags/sec_edgar_extract.py",
    "cartridges/hubspot/dags/hubspot_extract.py",
    "cartridges/hubspot/dags/hubspot_extract_all.py",
    "cartridges/salesforce/dags/salesforce_extract.py",
    "cartridges/salesforce/dags/salesforce_extract_all.py",
    "cartridges/sap_hcm/dags/sap_hcm_extract.py",
    "cartridges/sap_hcm/dags/sap_hcm_extract_all.py",
    "cartridges/sap_s4hana/dags/sap_s4hana_extract.py",
    "cartridges/sap_s4hana/dags/sap_s4hana_extract_all.py",
    "cartridges/sap_b1/dags/sap_b1_extract.py",
    "cartridges/sap_b1/dags/sap_b1_extract_all.py",
)
KEY = "airflow:" + "a" * 64


def _load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


jobs = _load("_test_async_jobs", MODULE)
client_module = _load("_test_service_job_client", ROOT / "airflow" / "dags" / "service_job_client.py")


def _verify(x_internal_api_key=None, x_api_key=None, x_internal_service=None):
    if (x_internal_api_key or x_api_key) != "secret" or x_internal_service != "airflow":
        raise HTTPException(status_code=401, detail="Missing or invalid X-Internal-Api-Key")


class _Service:
    def __init__(self) -> None:
        self.calls: list[dict] = []
        self.release = threading.Event()
        self.release.set()
        app = FastAPI()

        @app.post("/entities/{entity}/extract")
        def extract(entity: str, mode: str = "incremental", body: dict | None = Body(None)):
            self.calls.append({"entity": entity, "mode": mode, "body": body})
            self.release.wait(5)
            if entity == "broken":
                return JSONResponse({"status": "degraded", "error": "source down"}, status_code=503)
            if entity == "crash":
                raise RuntimeError("boom")
            return {"entity": entity, "mode": mode, "rows": 3}

        @app.post("/other")
        def other(x_api_key: str | None = Header(None)):
            return {"sync": True}

        app.add_middleware(
            jobs.AsyncJobMiddleware,
            authorize=jobs.header_authorizer(_verify),
            paths=(r"/entities/[A-Za-z0-9_.-]+/extract",),
        )
        self.app = app


HEADERS = {"X-Api-Key": "secret", "X-Internal-Service": "airflow"}
ASYNC = {**HEADERS, "Prefer": "respond-async", "Idempotency-Key": KEY}


async def _settle(client: httpx.AsyncClient, job: dict) -> dict:
    for _ in range(200):
        if job["status"] != "running":
            return job
        await asyncio.sleep(0.01)
        job = (await client.get(job["status_url"], headers=HEADERS)).json()
    raise AssertionError("job never settled")


def _run(coro):
    return asyncio.run(coro)


def _client(service: _Service) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=service.app), base_url="http://svc")


def test_async_request_returns_job_and_result_is_polled():
    service = _Service()

    async def scenario():
        async with _client(service) as client:
            response = await client.post("/entities/Items/extract", params={"mode": "full"}, json={"a": 1}, headers=ASYNC)
            assert response.status_code == 202
            assert response.headers["location"] == response.json()["status_url"]
            return await _settle(client, response.json())

    job = _run(scenario())
    assert job["status"] == "succeeded"
    assert job["status_code"] == 200
    assert job["result"] == {"entity": "Items", "mode": "full", "rows": 3}
    assert service.calls == [{"entity": "Items", "mode": "full", "body": {"a": 1}}]


def test_same_key_attaches_to_the_running_job_instead_of_running_twice():
    service = _Service()
    service.release.clear()

    async def scenario():
        async with _client(service) as client:
            first = (await client.post("/entities/Items/extract", json={}, headers=ASYNC)).json()
            second = (await client.post("/entities/Items/extract", json={}, headers=ASYNC)).json()
            assert second["job_id"] == first["job_id"]
            service.release.set()
            settled = await _settle(client, first)
            third = (await client.post("/entities/Items/extract", json={}, headers=ASYNC)).json()
            return settled, third, first

    settled, third, first = _run(scenario())
    assert settled["status"] == "succeeded"
    assert third["job_id"] == first["job_id"] and third["status"] == "succeeded"
    assert len(service.calls) == 1


def test_same_key_with_a_different_request_is_a_conflict():
    service = _Service()

    async def scenario():
        async with _client(service) as client:
            await client.post("/entities/Items/extract", json={"a": 1}, headers=ASYNC)
            return await client.post("/entities/Items/extract", json={"a": 2}, headers=ASYNC)

    assert _run(scenario()).status_code == 409


def test_failed_job_is_retried_with_a_fresh_attempt():
    service = _Service()

    async def scenario():
        async with _client(service) as client:
            first = await _settle(client, (await client.post("/entities/broken/extract", json={}, headers=ASYNC)).json())
            second = (await client.post("/entities/broken/extract", json={}, headers=ASYNC)).json()
            await _settle(client, second)
            return first, second

    first, second = _run(scenario())
    assert first["status"] == "failed"
    assert first["status_code"] == 503
    assert first["result"] == {"status": "degraded", "error": "source down"}
    assert second["job_id"] != first["job_id"]
    assert len(service.calls) == 2


def test_unhandled_exception_becomes_a_failed_job_without_details():
    service = _Service()

    async def scenario():
        async with _client(service) as client:
            return await _settle(client, (await client.post("/entities/crash/extract", json={}, headers=ASYNC)).json())

    job = _run(scenario())
    assert job["status"] == "failed"
    assert job["status_code"] == 500
    assert job["result"] == {"detail": "RuntimeError"}


@pytest.mark.parametrize(
    "headers,status",
    [
        ({**ASYNC, "X-Api-Key": "wrong"}, 401),
        ({k: v for k, v in ASYNC.items() if k != "Idempotency-Key"}, 400),
        ({**ASYNC, "Idempotency-Key": "short"}, 400),
        ({**ASYNC, "Idempotency-Key": "bad key with spaces"}, 400),
    ],
)
def test_submission_is_authenticated_and_keyed(headers, status):
    service = _Service()

    async def scenario():
        async with _client(service) as client:
            return await client.post("/entities/Items/extract", json={}, headers=headers)

    assert _run(scenario()).status_code == status
    assert service.calls == []


def test_status_requires_authentication_and_unknown_jobs_are_404():
    service = _Service()

    async def scenario():
        async with _client(service) as client:
            job = (await client.post("/entities/Items/extract", json={}, headers=ASYNC)).json()
            denied = await client.get(job["status_url"], headers={**HEADERS, "X-Api-Key": "wrong"})
            missing = await client.get("/jobs/" + "0" * 32, headers=HEADERS)
            return denied.status_code, missing.status_code

    assert _run(scenario()) == (401, 404)


def test_requests_without_prefer_and_other_paths_stay_synchronous():
    service = _Service()

    async def scenario():
        async with _client(service) as client:
            direct = await client.post("/entities/Items/extract", json={}, headers=HEADERS)
            other = await client.post("/other", json={}, headers=ASYNC)
            return direct, other

    direct, other = _run(scenario())
    assert direct.status_code == 200 and direct.json()["rows"] == 3
    assert other.status_code == 200 and other.json() == {"sync": True}


def test_oversized_body_is_rejected():
    service = _Service()

    async def scenario():
        async with _client(service) as client:
            return await client.post(
                "/entities/Items/extract",
                content=b"x" * (jobs.MAX_BODY_BYTES + 1),
                headers={**ASYNC, "Content-Type": "application/json"},
            )

    assert _run(scenario()).status_code == 413
    assert service.calls == []


def test_registry_evicts_expired_then_oldest_finished_and_bounds_running_jobs():
    now = [1000.0]
    registry = jobs.JobRegistry(ttl_seconds=60, max_jobs=2, clock=lambda: now[0])
    first, created = registry.claim("key-one-1", "f1")
    assert created
    first.status, first.finished_at = "succeeded", now[0]
    now[0] += 61
    assert registry.get(first.job_id) is None
    second, _ = registry.claim("key-two-2", "f2")
    second.status, second.finished_at = "succeeded", now[0]
    third, _ = registry.claim("key-three", "f3")
    fourth, created = registry.claim("key-four-4", "f4")
    assert created
    assert registry.get(second.job_id) is None
    assert registry.get(third.job_id) is third and registry.get(fourth.job_id) is fourth
    with pytest.raises(jobs.JobCapacityExceeded):
        registry.claim("key-five-5", "f5")


def test_airflow_client_polls_until_success_and_reports_progress():
    service = _Service()

    async def scenario():
        async with _client(service) as client:
            return await client_module.run_service_job_async(
                client,
                "http://svc/entities/Items/extract",
                headers=HEADERS,
                key=KEY,
                json=lambda: {"a": 1},
                params={"mode": "incremental"},
                deadline_seconds=30,
                poll_seconds=0.01,
            )

    assert _run(scenario()) == {"entity": "Items", "mode": "incremental", "rows": 3}
    assert len(service.calls) == 1


def test_airflow_client_raises_with_the_service_status_on_failure():
    service = _Service()

    async def scenario():
        async with _client(service) as client:
            return await client_module.run_service_job_async(
                client,
                "http://svc/entities/broken/extract",
                headers=HEADERS,
                key=KEY,
                json={},
                deadline_seconds=30,
                poll_seconds=0.01,
            )

    with pytest.raises(client_module.ServiceJobError) as raised:
        _run(scenario())
    assert raised.value.status_code == 503
    assert "source down" in str(raised.value)


def _scripted_client(responses: list, seen: list):
    def handler(request: httpx.Request) -> httpx.Response:
        seen.append((request.method, request.url.path, dict(request.headers)))
        item = responses.pop(0)
        if isinstance(item, Exception):
            raise item
        return item

    return httpx.Client(transport=httpx.MockTransport(handler), base_url="http://svc")


def _job(status: str, **extra) -> dict:
    return {"job_id": "b" * 32, "status": status, "status_url": "/jobs/" + "b" * 32, **extra}


def test_sync_client_resubmits_when_the_service_lost_the_job_and_tolerates_restarts():
    seen: list = []
    responses = [
        httpx.Response(202, json=_job("running")),
        httpx.ConnectError("restarting"),
        httpx.Response(502, json={}),
        httpx.Response(404, json={"detail": "job not found"}),
        httpx.Response(202, json=_job("running")),
        httpx.Response(200, json=_job("succeeded", status_code=200, result={"rows": 1})),
    ]
    polled: list[int] = []
    bodies = iter([{"attempt": 1}, {"attempt": 2}])
    with _scripted_client(responses, seen) as client:
        result = client_module.run_service_job(
            client,
            "http://svc/extract-all",
            headers=HEADERS,
            key=KEY,
            json=lambda: next(bodies),
            deadline_seconds=60,
            on_poll=lambda: polled.append(1),
            sleep=lambda _s: None,
        )
    assert result == {"rows": 1}
    posts = [item for item in seen if item[0] == "POST"]
    assert len(posts) == 2
    assert all(item[2]["idempotency-key"] == KEY and item[2]["prefer"] == "respond-async" for item in posts)
    assert all("prefer" not in item[2] for item in seen if item[0] == "GET")
    assert len(polled) == 4


def test_sync_client_gives_up_at_the_deadline():
    clock = iter(range(0, 1000, 50))
    responses = [httpx.Response(202, json=_job("running"))] + [
        httpx.Response(200, json=_job("running")) for _ in range(10)
    ]
    with _scripted_client(responses, []) as client:
        with pytest.raises(client_module.ServiceJobError, match="still running"):
            client_module.run_service_job(
                client,
                "http://svc/extract-all",
                headers=HEADERS,
                key=KEY,
                deadline_seconds=120,
                sleep=lambda _s: None,
                clock=lambda: next(clock),
            )


def test_sync_client_accepts_a_synchronous_answer_from_an_older_service():
    with _scripted_client([httpx.Response(200, json={"rows": 2})], []) as client:
        assert client_module.run_service_job(
            client, "http://svc/extract-all", headers=HEADERS, key=KEY, deadline_seconds=60
        ) == {"rows": 2}
    with _scripted_client([httpx.Response(403, json={"detail": "no"})], []) as client:
        with pytest.raises(httpx.HTTPStatusError):
            client_module.run_service_job(
                client, "http://svc/extract-all", headers=HEADERS, key=KEY, deadline_seconds=60
            )


def test_idempotency_keys_are_stable_per_task_and_valid_for_the_services():
    class DagRun:
        dag_id, run_id = "sap_b1_extract", "manual__2026-09-25T10:00:00+00:00"

    class Ti:
        task_id, map_index = "trigger_extract", -1

    context = {"dag_run": DagRun(), "ti": Ti()}
    key = client_module.idempotency_key(context, "Items")
    assert key == client_module.idempotency_key(context, "Items")
    assert key != client_module.idempotency_key(context, "Orders")
    assert jobs.IDEMPOTENCY_KEY.fullmatch(key)


def test_ambient_key_uses_the_airflow_task_environment(monkeypatch):
    monkeypatch.setenv("AIRFLOW_CTX_DAG_ID", "sap_successfactors_extract_all")
    monkeypatch.setenv("AIRFLOW_CTX_DAG_RUN_ID", "scheduled__2026-09-25")
    monkeypatch.setenv("AIRFLOW_CTX_TASK_ID", "trigger_extract_all")
    key = client_module.ambient_idempotency_key("gold", "dataset_a")
    assert key == client_module.ambient_idempotency_key("gold", "dataset_a")
    assert key != client_module.ambient_idempotency_key("curated-silver", "dataset_a")
    monkeypatch.delenv("AIRFLOW_CTX_DAG_RUN_ID")
    assert client_module.ambient_idempotency_key("gold", "a") != client_module.ambient_idempotency_key("gold", "a")
    assert jobs.IDEMPOTENCY_KEY.fullmatch(key)


def test_every_service_runs_the_same_job_module():
    reference = MODULE.read_bytes()
    copies = [ROOT / "cartridges" / name / "app" / "core" / "async_jobs.py" for name in CARTRIDGES]
    copies.append(ROOT / "refinement" / "app" / "async_jobs.py")
    for path in copies:
        assert path.read_bytes() == reference, path


@pytest.mark.parametrize("cartridge", CARTRIDGES)
def test_every_polled_cartridge_registers_the_job_middleware(cartridge):
    source = (ROOT / "cartridges" / cartridge / "app" / "main.py").read_text(encoding="utf-8")
    assert "AsyncJobMiddleware" in source
    assert "authorize=header_authorizer(verify_api_key)" in source


def test_refinement_registers_the_job_middleware_for_mcp_invoke():
    source = (ROOT / "refinement" / "app" / "main.py").read_text(encoding="utf-8")
    assert "AsyncJobMiddleware" in source
    assert 'paths=(r"/mcp/invoke",)' in source


@pytest.mark.parametrize("path", EXTRACT_DAGS)
def test_extract_dags_poll_jobs_instead_of_holding_long_requests(path):
    source = (ROOT / path).read_text(encoding="utf-8")
    assert "run_service_job(" in source
    assert "idempotency_key(context" in source
    assert "REQUEST_TIMEOUT_SECONDS = 60" in source
    assert "client.post(" not in source
    assert not re.search(r"httpx\.Client\(timeout=\d{3,}", source)


def test_refresh_materialization_polls_and_keeps_its_lease():
    source = (ROOT / "airflow" / "dags" / "dataset_refresh_materialize.py").read_text(encoding="utf-8")
    assert "run_service_job(" in source
    assert "heartbeat_materialization" in source
    assert "requests.post" not in source
    triggers = (ROOT / "cartridges/sap_successfactors/app/core/refinement_triggers.py").read_text(encoding="utf-8")
    assert "run_service_job_async(" in triggers
    assert "ambient_idempotency_key(" in triggers
