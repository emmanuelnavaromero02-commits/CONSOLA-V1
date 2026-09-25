from __future__ import annotations

import asyncio
import hashlib
import os
import secrets
import time
from typing import Any, Awaitable, Callable, Mapping
from urllib.parse import urljoin

import httpx

POLL_SECONDS = 10.0
REQUEST_TIMEOUT_SECONDS = 60.0
MAX_RESUBMITS = 2


class ServiceJobError(RuntimeError):
    def __init__(self, message: str, *, status_code: int | None = None, result: Any = None) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.result = result


def _key(*parts: object) -> str:
    raw = "\0".join(str(item or "") for item in parts)
    return "airflow:" + hashlib.sha256(raw.encode("utf-8")).hexdigest()


def idempotency_key(context: Mapping[str, Any], *parts: object) -> str:
    dag_run = context.get("dag_run")
    task_instance = context.get("ti") or context.get("task_instance")
    return _key(
        getattr(dag_run, "dag_id", None) or context.get("dag_id"),
        getattr(dag_run, "run_id", None) or context.get("run_id"),
        getattr(task_instance, "task_id", None),
        getattr(task_instance, "map_index", None),
        *parts,
    )


def ambient_idempotency_key(*parts: object) -> str:
    run_id = os.environ.get("AIRFLOW_CTX_DAG_RUN_ID")
    if not run_id:
        return "adhoc:" + secrets.token_hex(32)
    return _key(
        os.environ.get("AIRFLOW_CTX_DAG_ID"),
        run_id,
        os.environ.get("AIRFLOW_CTX_TASK_ID"),
        os.environ.get("AIRFLOW_CTX_TRY_NUMBER"),
        *parts,
    )


def _error_message(job: Mapping[str, Any]) -> str:
    result = job.get("result")
    detail = (result.get("detail") or result.get("error") or result.get("status")) if isinstance(result, dict) else None
    return f"job {job.get('job_id')} failed with HTTP {job.get('status_code')}: {detail or 'no detail'}"


class _Tracker:
    def __init__(self, url: str, deadline_seconds: float, clock: Callable[[], float]) -> None:
        self.url = url
        self.deadline_seconds = deadline_seconds
        self.deadline = clock() + deadline_seconds
        self.clock = clock
        self.resubmits = 0

    def accept(self, response: httpx.Response) -> tuple[dict[str, Any] | None, Any]:
        if response.status_code != 202:
            response.raise_for_status()
            return None, response.json()
        return response.json(), None

    def settle(self, job: Mapping[str, Any]) -> tuple[bool, Any]:
        status = job.get("status")
        if status == "succeeded":
            return True, job.get("result")
        if status == "failed":
            raise ServiceJobError(_error_message(job), status_code=job.get("status_code"), result=job.get("result"))
        if self.clock() >= self.deadline:
            raise ServiceJobError(f"job {job.get('job_id')} still {status} after {self.deadline_seconds:.0f}s")
        return False, None

    def status_url(self, job: Mapping[str, Any]) -> str:
        return urljoin(self.url, str(job["status_url"]))

    def next_step(self, response: httpx.Response | None) -> str:
        if response is None or response.status_code >= 500:
            return "wait"
        if response.status_code == 404 and self.resubmits < MAX_RESUBMITS:
            self.resubmits += 1
            return "resubmit"
        response.raise_for_status()
        return "update"


def run_service_job(
    client: httpx.Client,
    url: str,
    *,
    headers: Mapping[str, str],
    key: str,
    deadline_seconds: float,
    json: Any = None,
    params: Mapping[str, Any] | None = None,
    on_poll: Callable[[], None] | None = None,
    poll_seconds: float = POLL_SECONDS,
    sleep: Callable[[float], None] = time.sleep,
    clock: Callable[[], float] = time.monotonic,
) -> Any:
    tracker = _Tracker(url, deadline_seconds, clock)
    submit_headers = {**headers, "Prefer": "respond-async", "Idempotency-Key": key}

    def submit() -> tuple[dict[str, Any] | None, Any]:
        body = json() if callable(json) else json
        return tracker.accept(client.post(url, params=params, json=body, headers=submit_headers))

    job, direct = submit()
    while job is not None:
        done, result = tracker.settle(job)
        if done:
            return result
        sleep(poll_seconds)
        if on_poll is not None:
            on_poll()
        try:
            response = client.get(tracker.status_url(job), headers=dict(headers))
        except httpx.TransportError:
            response = None
        step = tracker.next_step(response)
        if step == "resubmit":
            job, direct = submit()
        elif step == "update":
            job = response.json()
    return direct


async def run_service_job_async(
    client: httpx.AsyncClient,
    url: str,
    *,
    headers: Mapping[str, str],
    key: str,
    deadline_seconds: float,
    json: Any = None,
    params: Mapping[str, Any] | None = None,
    poll_seconds: float = POLL_SECONDS,
    sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    clock: Callable[[], float] = time.monotonic,
) -> Any:
    tracker = _Tracker(url, deadline_seconds, clock)
    submit_headers = {**headers, "Prefer": "respond-async", "Idempotency-Key": key}

    async def submit() -> tuple[dict[str, Any] | None, Any]:
        body = json() if callable(json) else json
        return tracker.accept(await client.post(url, params=params, json=body, headers=submit_headers))

    job, direct = await submit()
    while job is not None:
        done, result = tracker.settle(job)
        if done:
            return result
        await sleep(poll_seconds)
        try:
            response = await client.get(tracker.status_url(job), headers=dict(headers))
        except httpx.TransportError:
            response = None
        step = tracker.next_step(response)
        if step == "resubmit":
            job, direct = await submit()
        elif step == "update":
            job = response.json()
    return direct


__all__ = (
    "REQUEST_TIMEOUT_SECONDS",
    "ServiceJobError",
    "ambient_idempotency_key",
    "idempotency_key",
    "run_service_job",
    "run_service_job_async",
)
