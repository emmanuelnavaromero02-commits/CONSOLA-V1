from __future__ import annotations

import base64
import binascii
import json
import subprocess
import sys
from pathlib import Path
from typing import Any

from starlette.requests import Request


MAX_PDF_BYTES = 10 * 1024 * 1024
MAX_PDF_PAGES = 250
MAX_PDF_PAGE_CONTENT_BYTES = 1024 * 1024
MAX_PDF_TOTAL_CONTENT_BYTES = 8 * 1024 * 1024
MAX_INGEST_TEXT_CHARS = 2_000_000
MAX_INGEST_REQUEST_BYTES = 15 * 1024 * 1024
PDF_PROCESS_MEMORY_BYTES = 256 * 1024 * 1024
PDF_PROCESS_CPU_SECONDS = 8
PDF_PROCESS_TIMEOUT_SECONDS = 10


class RagIngestError(ValueError):
    def __init__(self, status_code: int, detail: str) -> None:
        super().__init__(detail)
        self.status_code = status_code
        self.detail = detail


def _too_large(detail: str) -> RagIngestError:
    return RagIngestError(413, detail)


async def read_ingest_body(request: Request) -> dict[str, Any]:
    raw_length = request.headers.get("content-length")
    if raw_length:
        try:
            content_length = int(raw_length)
        except ValueError as exc:
            raise RagIngestError(400, "Invalid Content-Length") from exc
        if content_length < 0:
            raise RagIngestError(400, "Invalid Content-Length")
        if content_length > MAX_INGEST_REQUEST_BYTES:
            raise _too_large("RAG ingest request exceeds size limit")

    payload = bytearray()
    async for chunk in request.stream():
        payload.extend(chunk)
        if len(payload) > MAX_INGEST_REQUEST_BYTES:
            raise _too_large("RAG ingest request exceeds size limit")
    try:
        body = json.loads(payload)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise RagIngestError(400, "Invalid JSON body") from exc
    if not isinstance(body, dict):
        raise RagIngestError(400, "RAG ingest body must be an object")
    return body


def validate_text_content(content: Any) -> str:
    if not isinstance(content, str):
        raise RagIngestError(400, "RAG content must be text")
    if len(content) > MAX_INGEST_TEXT_CHARS:
        raise _too_large("RAG content exceeds size limit")
    return content


def _pdf_worker_command() -> list[str]:
    worker = Path(__file__).with_name("pdf_worker.py")
    return [
        sys.executable,
        "-I",
        str(worker),
        str(MAX_PDF_BYTES),
        str(MAX_PDF_PAGES),
        str(MAX_INGEST_TEXT_CHARS),
        str(MAX_PDF_PAGE_CONTENT_BYTES),
        str(MAX_PDF_TOTAL_CONTENT_BYTES),
        str(PDF_PROCESS_MEMORY_BYTES),
        str(PDF_PROCESS_CPU_SECONDS),
    ]


def _extract_in_worker(pdf_bytes: bytes) -> str:
    try:
        result = subprocess.run(
            _pdf_worker_command(),
            input=pdf_bytes,
            capture_output=True,
            check=False,
            timeout=PDF_PROCESS_TIMEOUT_SECONDS,
            env={},
        )
    except subprocess.TimeoutExpired as exc:
        raise _too_large("PDF processing limit exceeded") from exc
    if result.returncode == 4 or result.returncode < 0:
        raise _too_large("PDF processing limit exceeded")
    if result.returncode == 3:
        raise RagIngestError(400, "Could not extract text from PDF")
    if result.returncode != 0:
        raise RagIngestError(400, "Invalid or unsupported PDF")
    try:
        content = result.stdout.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise RagIngestError(400, "Invalid or unsupported PDF") from exc
    if len(content) > MAX_INGEST_TEXT_CHARS:
        raise _too_large("Extracted PDF text exceeds size limit")
    return content


def extract_pdf_text(encoded_content: Any) -> str:
    if not isinstance(encoded_content, str) or not encoded_content:
        raise RagIngestError(400, "PDF content must be base64 text")
    max_encoded_chars = 4 * ((MAX_PDF_BYTES + 2) // 3)
    if len(encoded_content) > max_encoded_chars:
        raise _too_large("PDF exceeds size limit")
    try:
        pdf_bytes = base64.b64decode(encoded_content, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise RagIngestError(400, "Invalid PDF base64 content") from exc
    if len(pdf_bytes) > MAX_PDF_BYTES:
        raise _too_large("PDF exceeds size limit")
    content = _extract_in_worker(pdf_bytes).strip()
    if not content:
        raise RagIngestError(400, "Could not extract text from PDF")
    return content
