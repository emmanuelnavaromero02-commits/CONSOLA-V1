from __future__ import annotations

import base64
import hashlib
import hmac
import importlib
import json
import sys
import time
import zlib
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient


ROOT = Path(__file__).resolve().parents[1]
SIGNING_KEY = "pdf_ingest_signing_key_64_chars_aaaaaaaaaaaaaaaaaaaaaaaa"
PAIR_KEY = "pdf_ingest_console_pair_key_64_chars_bbbbbbbbbbbbbbbbbbbb"
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
        "INTERNAL_API_KEY": "pdf_ingest_legacy_key_64_chars_cccccccccccccccccccccc",
        "INTERNAL_API_KEY_CONSOLE_TO_MCP_INFRA": PAIR_KEY,
        "SECURITY_CONTEXT_SIGNING_KEY": SIGNING_KEY,
        "AIRFLOW_USER": "airflow",
        "AIRFLOW_PASSWORD": "airflow",
        "PG_PASSWORD": "postgres",
        "SUPERSET_USER": "admin",
        "SUPERSET_PASSWORD": "admin",
        "MINIO_SECRET_KEY": "miniosecret",
    }
    for name, value in env.items():
        monkeypatch.setenv(name, value)
    return importlib.import_module("app.main")


def _signed_context(
    *, workspace_id: str | None = "22222222-2222-2222-2222-222222222222"
) -> dict[str, Any]:
    context: dict[str, Any] = {
        "trusted": True,
        "source": "console",
        "role": "admin",
        "tenant_id": "11111111-1111-1111-1111-111111111111",
        "permissions": ["datasets.write"],
        "allowed_cartridges": ["*"],
        "_signed_at": int(time.time()),
        "_signature_version": "hmac-sha256-v1",
    }
    if workspace_id:
        context["workspace_id"] = workspace_id
    raw = json.dumps(
        context, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    ).encode()
    context["_signature"] = hmac.new(
        SIGNING_KEY.encode(), raw, hashlib.sha256
    ).hexdigest()
    return context


def _headers() -> dict[str, str]:
    return {"x-api-key": PAIR_KEY, "x-internal-service": "console"}


def _pdf_with_stream(stream: bytes, *, filter_name: bytes = b"") -> bytes:
    filter_entry = b" /Filter /" + filter_name if filter_name else b""
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        (
            b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
            b"/Resources << /Font << /F1 5 0 R >> >> /Contents 4 0 R >>"
        ),
        b"<< /Length "
        + str(len(stream)).encode()
        + filter_entry
        + b" >>\nstream\n"
        + stream
        + b"\nendstream",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    ]
    data = bytearray(b"%PDF-1.4\n")
    offsets = [0]
    for number, obj in enumerate(objects, 1):
        offsets.append(len(data))
        data.extend(f"{number} 0 obj\n".encode() + obj + b"\nendobj\n")
    xref = len(data)
    data.extend(f"xref\n0 {len(objects) + 1}\n".encode() + b"0000000000 65535 f \n")
    for offset in offsets[1:]:
        data.extend(f"{offset:010d} 00000 n \n".encode())
    data.extend(
        f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\n"
        f"startxref\n{xref}\n%%EOF\n".encode()
    )
    return bytes(data)


def _pdf_with_text(text: str) -> bytes:
    stream = f"BT /F1 12 Tf 72 720 Td ({text}) Tj ET".encode()
    return _pdf_with_stream(stream)


def _body(content: str, **overrides: Any) -> dict[str, Any]:
    return {
        "name": "policy",
        "content": content,
        "mime_type": "application/pdf",
        "security_context": _signed_context(),
        **overrides,
    }


def test_valid_pdf_is_extracted_by_real_ingest_route(monkeypatch) -> None:
    main = _load_main(monkeypatch)
    captured: dict[str, Any] = {}

    async def fake_ingest(**kwargs):
        captured.update(kwargs)
        return {"source_id": 7}

    monkeypatch.setattr(main, "_rag_do_ingest", fake_ingest)
    client = TestClient(main.app, raise_server_exceptions=False)
    encoded = base64.b64encode(_pdf_with_text("OMEGA PDF")).decode()

    response = client.post("/rag/ingest", json=_body(encoded), headers=_headers())

    assert response.status_code == 200
    assert response.json() == {"source_id": 7}
    assert captured["content"] == "OMEGA PDF"
    assert captured["name"].endswith(
        ":tenant:11111111-1111-1111-1111-111111111111"
        ":workspace:22222222-2222-2222-2222-222222222222"
    )


def test_invalid_pdf_returns_controlled_error_without_details(monkeypatch) -> None:
    main = _load_main(monkeypatch)
    client = TestClient(main.app, raise_server_exceptions=False)
    marker = "private-parser-marker"
    encoded = base64.b64encode(marker.encode()).decode()

    response = client.post("/rag/ingest", json=_body(encoded), headers=_headers())

    assert response.status_code == 400
    assert response.json() == {"detail": "Invalid or unsupported PDF"}
    rendered = response.text
    assert marker not in rendered
    assert "Traceback" not in rendered
    assert "PdfReader" not in rendered


def test_authentication_runs_before_request_body_is_read(monkeypatch) -> None:
    main = _load_main(monkeypatch)
    ingest = importlib.import_module("app.rag.ingest")
    monkeypatch.setattr(ingest, "MAX_INGEST_REQUEST_BYTES", 8)
    client = TestClient(main.app, raise_server_exceptions=False)

    response = client.post("/rag/ingest", content=b"x" * 64)

    assert response.status_code == 401


def test_ingest_requires_signed_tenant_workspace_scope(monkeypatch) -> None:
    main = _load_main(monkeypatch)
    client = TestClient(main.app, raise_server_exceptions=False)
    body = _body(base64.b64encode(_pdf_with_text("scope")).decode())
    body["security_context"] = _signed_context(workspace_id=None)

    response = client.post("/rag/ingest", json=body, headers=_headers())

    assert response.status_code == 403
    assert response.json() == {"detail": "RAG requires tenant/workspace scope"}


def test_pdf_size_limit_returns_413(monkeypatch) -> None:
    main = _load_main(monkeypatch)
    ingest = importlib.import_module("app.rag.ingest")
    monkeypatch.setattr(ingest, "MAX_PDF_BYTES", 4)
    client = TestClient(main.app, raise_server_exceptions=False)

    response = client.post(
        "/rag/ingest",
        json=_body(base64.b64encode(b"12345").decode()),
        headers=_headers(),
    )

    assert response.status_code == 413
    assert response.json() == {"detail": "PDF exceeds size limit"}


def test_request_size_limit_returns_413_before_json_decode(monkeypatch) -> None:
    main = _load_main(monkeypatch)
    ingest = importlib.import_module("app.rag.ingest")
    monkeypatch.setattr(ingest, "MAX_INGEST_REQUEST_BYTES", 32)
    client = TestClient(main.app, raise_server_exceptions=False)

    response = client.post(
        "/rag/ingest",
        content=b'{"content":"' + b"x" * 64 + b'"}',
        headers={**_headers(), "content-type": "application/json"},
    )

    assert response.status_code == 413
    assert response.json() == {"detail": "RAG ingest request exceeds size limit"}


def test_compressed_content_amplification_is_rejected_before_text_parse(
    monkeypatch,
) -> None:
    main = _load_main(monkeypatch)
    client = TestClient(main.app, raise_server_exceptions=False)
    expanded = b"q\n" * 600_000
    compressed = zlib.compress(expanded, level=9)
    payload = _pdf_with_stream(compressed, filter_name=b"FlateDecode")

    response = client.post(
        "/rag/ingest",
        json=_body(base64.b64encode(payload).decode()),
        headers=_headers(),
    )

    assert len(compressed) < 10_000
    assert response.status_code == 413
    assert response.json() == {"detail": "PDF processing limit exceeded"}


def test_foreign_scope_suffix_is_rejected(monkeypatch) -> None:
    main = _load_main(monkeypatch)
    client = TestClient(main.app, raise_server_exceptions=False)
    foreign_name = "policy:tenant:other:workspace:other"

    response = client.post(
        "/rag/ingest",
        json=_body(
            base64.b64encode(_pdf_with_text("scope")).decode(), name=foreign_name
        ),
        headers=_headers(),
    )

    assert response.status_code == 403
    assert response.json() == {"detail": "RAG source is outside caller scope"}
