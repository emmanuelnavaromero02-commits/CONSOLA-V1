from __future__ import annotations

import asyncio
import base64
import hashlib
import hmac
import importlib
import json
import subprocess
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient


ROOT = Path(__file__).resolve().parents[1]
SIGNING_KEY = "pdf_capacity_signing_key_64_chars_aaaaaaaaaaaaaaaaaaaaaaaa"
PAIR_KEY = "pdf_capacity_console_pair_key_64_chars_bbbbbbbbbbbbbbbbbbbb"
SERVICE_MARKERS = (
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
def _restore_imports():
    saved = list(sys.path)
    yield
    sys.path[:] = saved
    _purge_app_modules()


def _load_main(monkeypatch):
    _purge_app_modules()
    sys.path[:] = [
        path
        for path in sys.path
        if not any(marker in path for marker in SERVICE_MARKERS)
    ]
    sys.path.insert(0, str(ROOT / "mcp-infra"))
    env = {
        "APP_ENV": "test",
        "INTERNAL_API_KEY": "pdf_capacity_legacy_key_64_chars_cccccccccccccccccc",
        "INTERNAL_API_KEY_CONSOLE_TO_MCP_INFRA": PAIR_KEY,
        "SECURITY_CONTEXT_SIGNING_KEY": SIGNING_KEY,
        "AIRFLOW_USER": "airflow",
        "AIRFLOW_PASSWORD": "airflow",
        "PG_PASSWORD": "postgres",
        "SUPERSET_USER": "admin",
        "SUPERSET_PASSWORD": "admin",
        "MINIO_SECRET_KEY": "miniosecret",
        "MCP_INFRA_PDF_MAX_WORKERS": "2",
    }
    for name, value in env.items():
        monkeypatch.setenv(name, value)
    return importlib.import_module("app.main")


def _signed_context() -> dict[str, Any]:
    context: dict[str, Any] = {
        "trusted": True,
        "source": "console",
        "role": "admin",
        "tenant_id": "11111111-1111-1111-1111-111111111111",
        "workspace_id": "22222222-2222-2222-2222-222222222222",
        "permissions": ["datasets.write"],
        "allowed_cartridges": ["*"],
        "_signed_at": int(time.time()),
        "_signature_version": "hmac-sha256-v1",
    }
    raw = json.dumps(
        context, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    ).encode()
    context["_signature"] = hmac.new(
        SIGNING_KEY.encode(), raw, hashlib.sha256
    ).hexdigest()
    return context


def _body() -> dict[str, Any]:
    return {
        "name": "capacity",
        "content": base64.b64encode(b"bounded-pdf").decode(),
        "mime_type": "application/pdf",
        "security_context": _signed_context(),
    }


def _headers() -> dict[str, str]:
    return {"x-api-key": PAIR_KEY, "x-internal-service": "console"}


def _success_result() -> subprocess.CompletedProcess[bytes]:
    return subprocess.CompletedProcess([], 0, b"OMEGA PDF", b"")


def test_capacity_never_exceeds_two_and_rejects_without_subprocess(
    monkeypatch,
) -> None:
    main = _load_main(monkeypatch)
    capacity = importlib.import_module("app.rag.pdf_capacity")
    ingest = importlib.import_module("app.rag.ingest")
    monkeypatch.setattr(capacity, "PDF_WORKER_CAPACITY", capacity.PdfWorkerCapacity(2))
    active = 0
    maximum = 0
    invocations = 0
    lock = threading.Lock()
    both_started = threading.Barrier(3)
    release = threading.Event()

    def fake_run(*args, **kwargs):
        nonlocal active, maximum, invocations
        with lock:
            invocations += 1
            active += 1
            maximum = max(maximum, active)
        try:
            both_started.wait(timeout=5)
            if not release.wait(timeout=5):
                raise AssertionError("test did not release PDF workers")
            return _success_result()
        finally:
            with lock:
                active -= 1

    async def fake_ingest(**kwargs):
        return {"source_id": 7}

    monkeypatch.setattr(ingest.subprocess, "run", fake_run)
    monkeypatch.setattr(main, "_rag_do_ingest", fake_ingest)
    client = TestClient(main.app, raise_server_exceptions=False)

    def post_pdf():
        return client.post("/rag/ingest", json=_body(), headers=_headers())

    with ThreadPoolExecutor(max_workers=4) as pool:
        accepted = [pool.submit(post_pdf) for _ in range(2)]
        both_started.wait(timeout=5)
        rejected = [pool.submit(post_pdf) for _ in range(2)]
        rejected_responses = [future.result(timeout=5) for future in rejected]
        try:
            assert [response.status_code for response in rejected_responses] == [
                429,
                429,
            ]
            for response in rejected_responses:
                assert response.json() == {
                    "detail": "PDF processing capacity exhausted"
                }
                assert response.headers["retry-after"] == "5"
            assert invocations == 2
            assert maximum == 2
        finally:
            release.set()
        assert [future.result(timeout=5).status_code for future in accepted] == [
            200,
            200,
        ]


@pytest.mark.parametrize(
    ("outcome", "expected_error"),
    [
        ("success", None),
        ("invalid", "rag"),
        ("timeout", "rag"),
        ("exception", "runtime"),
        ("cancellation", "cancelled"),
    ],
)
def test_capacity_recovers_after_every_worker_outcome(
    monkeypatch, outcome: str, expected_error: str | None
) -> None:
    _load_main(monkeypatch)
    capacity = importlib.import_module("app.rag.pdf_capacity")
    ingest = importlib.import_module("app.rag.ingest")
    monkeypatch.setattr(capacity, "PDF_WORKER_CAPACITY", capacity.PdfWorkerCapacity(1))
    encoded = base64.b64encode(b"bounded-pdf").decode()

    def first_run(*args, **kwargs):
        if outcome == "success":
            return _success_result()
        if outcome == "invalid":
            return subprocess.CompletedProcess([], 2, b"", b"private")
        if outcome == "timeout":
            raise subprocess.TimeoutExpired(["pdf-worker"], 10)
        if outcome == "exception":
            raise RuntimeError("worker failed")
        raise asyncio.CancelledError

    monkeypatch.setattr(ingest.subprocess, "run", first_run)
    if expected_error is None:
        assert asyncio.run(ingest.extract_pdf_text_with_capacity(encoded))
    elif expected_error == "rag":
        with pytest.raises(ingest.RagIngestError):
            asyncio.run(ingest.extract_pdf_text_with_capacity(encoded))
    elif expected_error == "runtime":
        with pytest.raises(RuntimeError, match="worker failed"):
            asyncio.run(ingest.extract_pdf_text_with_capacity(encoded))
    else:
        with pytest.raises(asyncio.CancelledError):
            asyncio.run(ingest.extract_pdf_text_with_capacity(encoded))

    monkeypatch.setattr(
        ingest.subprocess, "run", lambda *args, **kwargs: _success_result()
    )
    assert asyncio.run(ingest.extract_pdf_text_with_capacity(encoded)) == ("OMEGA PDF")


def test_caller_cancellation_keeps_slot_until_worker_stops(monkeypatch) -> None:
    _load_main(monkeypatch)
    capacity = importlib.import_module("app.rag.pdf_capacity")
    ingest = importlib.import_module("app.rag.ingest")
    monkeypatch.setattr(capacity, "PDF_WORKER_CAPACITY", capacity.PdfWorkerCapacity(1))
    encoded = base64.b64encode(b"bounded-pdf").decode()
    entered = threading.Event()
    release = threading.Event()
    invocations = 0
    lock = threading.Lock()

    def blocking_run(*args, **kwargs):
        nonlocal invocations
        with lock:
            invocations += 1
            invocation = invocations
        if invocation > 1:
            return _success_result()
        entered.set()
        if not release.wait(timeout=5):
            raise AssertionError("test did not release cancelled PDF worker")
        return _success_result()

    monkeypatch.setattr(ingest.subprocess, "run", blocking_run)

    async def scenario() -> None:
        task = asyncio.create_task(ingest.extract_pdf_text_with_capacity(encoded))
        assert await asyncio.to_thread(entered.wait, 5)
        task.cancel()
        try:
            with pytest.raises(ingest.RagIngestError) as rejected:
                await ingest.extract_pdf_text_with_capacity(encoded)
            assert rejected.value.status_code == 429
            assert invocations == 1
        finally:
            release.set()
        with pytest.raises(asyncio.CancelledError):
            await task

    asyncio.run(scenario())


def test_pdf_worker_capacity_defaults_to_two(monkeypatch) -> None:
    _load_main(monkeypatch)
    capacity = importlib.import_module("app.rag.pdf_capacity")

    assert capacity.configured_max_workers({}) == 2


@pytest.mark.parametrize(
    ("value", "expected"),
    [("1", 1), ("4", 4), ("", None), ("0", None), ("5", None), ("many", None)],
)
def test_pdf_worker_capacity_validates_safe_range(
    monkeypatch, value: str, expected: int | None
) -> None:
    _load_main(monkeypatch)
    capacity = importlib.import_module("app.rag.pdf_capacity")

    if expected is not None:
        assert (
            capacity.configured_max_workers({"MCP_INFRA_PDF_MAX_WORKERS": value})
            == expected
        )
        return
    with pytest.raises(RuntimeError, match="MCP_INFRA_PDF_MAX_WORKERS"):
        capacity.configured_max_workers({"MCP_INFRA_PDF_MAX_WORKERS": value})


def test_invalid_capacity_fails_module_initialization(monkeypatch) -> None:
    _load_main(monkeypatch)
    capacity = importlib.import_module("app.rag.pdf_capacity")
    monkeypatch.setenv("MCP_INFRA_PDF_MAX_WORKERS", "0")

    with pytest.raises(RuntimeError, match="MCP_INFRA_PDF_MAX_WORKERS"):
        importlib.reload(capacity)
