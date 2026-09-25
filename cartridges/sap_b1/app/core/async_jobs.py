from __future__ import annotations

import asyncio
import hashlib
import json
import re
import time
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Awaitable, Callable, Iterable

from fastapi import HTTPException
from starlette.datastructures import Headers
from starlette.responses import JSONResponse

JOB_PATH = re.compile(r"/jobs/([0-9a-f]{32})")
IDEMPOTENCY_KEY = re.compile(r"[A-Za-z0-9][A-Za-z0-9:._@+=/-]{7,255}")
MAX_BODY_BYTES = 1 << 20
MAX_RESULT_BYTES = 1 << 20
TTL_SECONDS = 6 * 3600
MAX_JOBS = 512
_STRIPPED_HEADERS = frozenset({b"prefer", b"idempotency-key", b"content-length"})


class JobConflict(Exception):
    pass


class JobCapacityExceeded(Exception):
    pass


def _iso(value: float | None) -> str | None:
    if value is None:
        return None
    return datetime.fromtimestamp(value, tz=timezone.utc).isoformat()


@dataclass
class Job:
    job_id: str
    key: str
    fingerprint: str
    created_at: float
    status: str = "running"
    finished_at: float | None = None
    status_code: int | None = None
    result: Any = None

    def snapshot(self) -> dict[str, Any]:
        return {
            "job_id": self.job_id,
            "status": self.status,
            "status_url": f"/jobs/{self.job_id}",
            "created_at": _iso(self.created_at),
            "finished_at": _iso(self.finished_at),
            "status_code": self.status_code,
            "result": self.result,
        }


class JobRegistry:
    def __init__(
        self,
        *,
        ttl_seconds: float = TTL_SECONDS,
        max_jobs: int = MAX_JOBS,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self._ttl = ttl_seconds
        self._max = max_jobs
        self._clock = clock
        self._jobs: dict[str, Job] = {}
        self._keys: dict[str, str] = {}
        self._tasks: set[asyncio.Task] = set()

    def get(self, job_id: str) -> Job | None:
        self._evict()
        return self._jobs.get(job_id)

    def claim(self, key: str, fingerprint: str) -> tuple[Job, bool]:
        self._evict()
        current = self._jobs.get(self._keys.get(key, ""))
        if current is not None:
            if current.fingerprint != fingerprint:
                raise JobConflict(key)
            if current.status != "failed":
                return current, False
        if len(self._jobs) >= self._max:
            raise JobCapacityExceeded(key)
        job = Job(uuid.uuid4().hex, key, fingerprint, self._clock())
        self._jobs[job.job_id] = job
        self._keys[key] = job.job_id
        return job, True

    def start(self, job: Job, work: Awaitable[tuple[int, Any]]) -> None:
        task = asyncio.create_task(self._run(job, work))
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    async def _run(self, job: Job, work: Awaitable[tuple[int, Any]]) -> None:
        try:
            status_code, payload = await work
        except Exception as exc:  # noqa: BLE001
            status_code, payload = 500, {"detail": type(exc).__name__}
        job.status_code = status_code
        job.result = payload
        job.status = "succeeded" if 200 <= status_code < 300 else "failed"
        job.finished_at = self._clock()

    def _evict(self) -> None:
        now = self._clock()
        finished = sorted(
            (job for job in self._jobs.values() if job.finished_at is not None),
            key=lambda job: job.finished_at or 0.0,
        )
        expired = [job for job in finished if now - (job.finished_at or now) > self._ttl]
        remaining = [job for job in finished if job not in expired]
        overflow = max(0, len(self._jobs) - len(expired) - self._max + 1)
        for job in expired + remaining[:overflow]:
            self._jobs.pop(job.job_id, None)
            if self._keys.get(job.key) == job.job_id:
                self._keys.pop(job.key, None)


def wants_async(headers: Headers) -> bool:
    return any(
        token.strip().lower() == "respond-async"
        for token in headers.get("prefer", "").split(",")
    )


def header_authorizer(verify: Callable[..., Any]) -> Callable[[Headers], None]:
    def authorize(headers: Headers) -> None:
        verify(
            x_internal_api_key=headers.get("x-internal-api-key"),
            x_api_key=headers.get("x-api-key"),
            x_internal_service=headers.get("x-internal-service"),
        )

    return authorize


def _decode(body: bytes) -> Any:
    try:
        return json.loads(body) if body else None
    except ValueError:
        return {"text": body[:4096].decode("utf-8", "replace")}


class AsyncJobMiddleware:
    def __init__(
        self,
        app: Any,
        *,
        authorize: Callable[[Headers], None],
        paths: Iterable[str],
        registry: JobRegistry | None = None,
    ) -> None:
        self.app = app
        self.authorize = authorize
        self.paths = tuple(re.compile(path) for path in paths)
        self.registry = registry or JobRegistry()

    async def __call__(self, scope: dict, receive: Callable, send: Callable) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        method, path = scope["method"], scope["path"]
        headers = Headers(scope=scope)
        match = JOB_PATH.fullmatch(path)
        if method == "GET" and match:
            await self._status(match.group(1), headers, scope, receive, send)
            return
        if method == "POST" and wants_async(headers) and any(p.fullmatch(path) for p in self.paths):
            await self._submit(headers, scope, receive, send)
            return
        await self.app(scope, receive, send)

    def _denied(self, headers: Headers) -> JSONResponse | None:
        try:
            self.authorize(headers)
        except HTTPException as exc:
            return JSONResponse({"detail": exc.detail}, status_code=exc.status_code)
        return None

    async def _status(self, job_id: str, headers: Headers, scope: dict, receive: Callable, send: Callable) -> None:
        response = self._denied(headers)
        if response is None:
            job = self.registry.get(job_id)
            if job is None:
                response = JSONResponse({"detail": "job not found"}, status_code=404)
            else:
                response = JSONResponse(job.snapshot())
        await response(scope, receive, send)

    async def _submit(self, headers: Headers, scope: dict, receive: Callable, send: Callable) -> None:
        response = self._denied(headers)
        key = headers.get("idempotency-key", "")
        if response is None and not IDEMPOTENCY_KEY.fullmatch(key):
            response = JSONResponse({"detail": "invalid Idempotency-Key"}, status_code=400)
        body = b""
        if response is None:
            body, complete = await _read_body(receive)
            if not complete:
                response = JSONResponse({"detail": "request body too large"}, status_code=413)
        if response is None:
            fingerprint = hashlib.sha256(
                b"\0".join((scope["path"].encode(), scope.get("query_string", b""), body))
            ).hexdigest()
            try:
                job, created = self.registry.claim(key, fingerprint)
            except JobConflict:
                response = JSONResponse({"detail": "Idempotency-Key reused with a different request"}, status_code=409)
            except JobCapacityExceeded:
                response = JSONResponse({"detail": "job capacity exhausted"}, status_code=503)
            else:
                if created:
                    self.registry.start(job, self._replay(scope, body))
                response = JSONResponse(
                    job.snapshot(),
                    status_code=202,
                    headers={"Location": f"/jobs/{job.job_id}"},
                )
        await response(scope, receive, send)

    async def _replay(self, scope: dict, body: bytes) -> tuple[int, Any]:
        inner = {
            **scope,
            "headers": [(k, v) for k, v in scope["headers"] if k.lower() not in _STRIPPED_HEADERS]
            + [(b"content-length", str(len(body)).encode())],
            "state": {},
        }
        delivered = False
        status_code = 500
        chunks: list[bytes] = []
        size = 0

        async def receive() -> dict:
            nonlocal delivered
            if not delivered:
                delivered = True
                return {"type": "http.request", "body": body, "more_body": False}
            await asyncio.Event().wait()
            return {"type": "http.disconnect"}

        async def send(message: dict) -> None:
            nonlocal status_code, size
            if message["type"] == "http.response.start":
                status_code = int(message["status"])
            elif message["type"] == "http.response.body":
                chunk = message.get("body", b"")
                size += len(chunk)
                if size <= MAX_RESULT_BYTES:
                    chunks.append(chunk)

        await self.app(inner, receive, send)
        if size > MAX_RESULT_BYTES:
            return status_code, {"truncated": True, "bytes": size}
        return status_code, _decode(b"".join(chunks))


async def _read_body(receive: Callable) -> tuple[bytes, bool]:
    parts: list[bytes] = []
    size = 0
    while True:
        message = await receive()
        if message["type"] != "http.request":
            return b"".join(parts), False
        chunk = message.get("body", b"")
        size += len(chunk)
        if size > MAX_BODY_BYTES:
            return b"", False
        parts.append(chunk)
        if not message.get("more_body", False):
            return b"".join(parts), True
