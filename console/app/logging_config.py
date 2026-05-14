"""Sprint v1.18 — structured JSON logging + secret redaction.

Use:
    from app.logging_config import setup_logging
    setup_logging(service_name="console", level="INFO")

All log records emit a single line of JSON to stdout. Secret-shaped
values in log messages are redacted before serialization so a stray
``logger.info(f"got token={token}")`` can never leak a credential to
disk / SIEM / shipping pipeline.

This file is copied textually across console, refinement and vault —
there is no shared library between services. A test in
``tests/test_logging_redaction.py`` enforces the textual identity.
"""
from __future__ import annotations

import json
import logging
import os
import re
import sys
from datetime import datetime, timezone


# ── Patterns considered secret-bearing in log messages ─────────────────────
_REDACTION_PATTERNS = [
    # Bearer <token>
    (re.compile(r"(?i)\bbearer\s+[A-Za-z0-9._\-]+"), "Bearer ***REDACTED***"),
    # key=value style
    (re.compile(r"(?i)(password|token|api[_-]?key|secret|client[_-]?secret)\s*[=:]\s*[^\s,;}\"']+"),
     lambda m: f"{m.group(1)}=***REDACTED***"),
    # JSON-encoded same fields
    (re.compile(r'(?i)"(password|token|api[_-]?key|secret|client[_-]?secret)"\s*:\s*"[^"]*"'),
     lambda m: f'"{m.group(1)}": "***REDACTED***"'),
    # Long hex strings (>=32 chars) — likely a generated secret
    (re.compile(r"\b[a-f0-9]{32,}\b"), "***REDACTED_HEX***"),
]


def _redact(text):
    """Apply every redaction pattern. Returns input unchanged if it's
    falsy or not a string — callers may pass None / numbers safely."""
    if not text or not isinstance(text, str):
        return text
    for pattern, repl in _REDACTION_PATTERNS:
        text = pattern.sub(repl, text)
    return text


class SecretRedactionFilter(logging.Filter):
    """Redacts secrets in log records before they reach the formatter."""

    def filter(self, record: logging.LogRecord) -> bool:
        if isinstance(record.msg, str):
            record.msg = _redact(record.msg)
        if record.args:
            if isinstance(record.args, dict):
                record.args = {k: _redact(str(v)) for k, v in record.args.items()}
            elif isinstance(record.args, tuple):
                record.args = tuple(
                    _redact(str(a)) if isinstance(a, str) else a for a in record.args
                )
        return True


class JSONFormatter(logging.Formatter):
    """Emits a single-line JSON object per log record."""

    def __init__(self, service_name: str):
        super().__init__()
        self.service_name = service_name

    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "timestamp": datetime.fromtimestamp(record.created, tz=timezone.utc).isoformat(),
            "level": record.levelname,
            "service": self.service_name,
            "logger": record.name,
            "message": record.getMessage(),
        }
        if record.exc_info:
            payload["exc_info"] = self.formatException(record.exc_info)
        # Optional structured context attached via logger.x("...", extra={...})
        for key in ("request_id", "user_id", "workspace_id", "endpoint"):
            if hasattr(record, key):
                payload[key] = getattr(record, key)
        return json.dumps(payload, default=str)


def setup_logging(service_name: str = "app", level=None) -> None:
    """Configure the root logger with a JSON formatter + redaction filter.

    Idempotent: every call rebuilds the root handler set so that re-runs
    (FastAPI reload, test fixtures) don't stack duplicate handlers.
    """
    level_str = (level or os.environ.get("LOG_LEVEL", "INFO")).upper()
    log_level = getattr(logging, level_str, logging.INFO)

    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JSONFormatter(service_name=service_name))
    handler.addFilter(SecretRedactionFilter())

    root = logging.getLogger()
    for h in list(root.handlers):
        root.removeHandler(h)
    root.addHandler(handler)
    root.setLevel(log_level)

    # Tame noisy third-party loggers — uvicorn.access in particular fills
    # the log stream with one line per request, drowning everything else.
    logging.getLogger("uvicorn.access").setLevel(logging.WARNING)
    logging.getLogger("urllib3").setLevel(logging.WARNING)
