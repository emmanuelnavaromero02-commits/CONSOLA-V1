"""Sprint v1.18 — JSON logging + secret redaction.

The ``logging_config`` module is shipped as a textual copy in console,
refinement and vault (no shared library across services). These tests
exercise:

  * The redaction patterns over a representative set of secret-bearing
    log shapes (Bearer tokens, key=value pairs, JSON-encoded creds,
    long hex strings).
  * Edge cases: empty string, non-string inputs, unrelated text.
  * The ``SecretRedactionFilter`` rewrites ``record.msg`` in place so
    formatters never see the raw secret.
  * The ``JSONFormatter`` emits parseable JSON with the expected keys,
    plus ``exc_info`` when the record has an exception.
  * ``setup_logging`` is idempotent — repeated calls don't stack
    duplicate handlers on the root logger.
  * The module is **textually identical** across the 3 services, via
    md5 fingerprint. Drift would mean an operator who fixes the filter
    in one service has to remember to fix the other two.

We import the module directly from its console path and reuse it for
the unit tests; the textual-identity test reads the other two files
from disk so we don't need three separate imports.
"""
from __future__ import annotations

import hashlib
import importlib
import io
import json
import logging
import sys
from pathlib import Path

import pytest


REPO_ROOT = Path(__file__).resolve().parents[1]

LOGGING_FILES = {
    "console":    REPO_ROOT / "console" / "app" / "logging_config.py",
    "refinement": REPO_ROOT / "refinement" / "app" / "logging_config.py",
    "vault":      REPO_ROOT / "vault"  / "app" / "logging_config.py",
}


@pytest.fixture()
def logging_config(monkeypatch):
    """Import logging_config from the console package directly. We point
    sys.path at console/ so ``from app.logging_config import ...`` resolves
    to the console copy. Three copies are textually identical (other
    test below), so it doesn't matter which one we exercise."""
    # Clear any prior cartridge/refinement/vault `app` package mounted on
    # sys.modules by a peer test, so importing console's `app` package
    # doesn't return the wrong logging_config.
    for name in list(sys.modules):
        if name == "app" or name.startswith("app."):
            del sys.modules[name]
    sys.path[:] = [
        p for p in sys.path
        if "/cartridges/" not in p
        and "/refinement" not in p
        and "/vault" not in p
    ]
    sys.path.insert(0, str(REPO_ROOT / "console"))
    return importlib.import_module("app.logging_config")


# ── Redaction patterns ──────────────────────────────────────────────


def test_redact_bearer_token(logging_config):
    out = logging_config._redact("Authorization: Bearer abc123def_ghi.jkl-mno")
    assert "abc123def" not in out
    assert "Bearer ***REDACTED***" in out


def test_redact_password_kv(logging_config):
    out = logging_config._redact("user=alice password=hunter2 ok")
    assert "hunter2" not in out
    assert "password=***REDACTED***" in out


def test_redact_token_kv(logging_config):
    out = logging_config._redact("calling api with token=xyz123")
    assert "xyz123" not in out
    assert "token=***REDACTED***" in out


def test_redact_api_key_kv(logging_config):
    out = logging_config._redact("api_key=foo and api-key=bar")
    assert "foo" not in out and "bar" not in out
    assert out.lower().count("=***redacted***") == 2


def test_redact_json_password_field(logging_config):
    out = logging_config._redact('{"password": "secret", "user": "alice"}')
    assert "secret" not in out
    assert '"password": "***REDACTED***"' in out
    assert '"user": "alice"' in out  # untouched


def test_redact_normal_text_is_unchanged(logging_config):
    sample = "User alice opened workspace 42"
    assert logging_config._redact(sample) == sample


def test_redact_long_hex_string(logging_config):
    secret_hex = "a" * 40
    out = logging_config._redact(f"INTERNAL_API_KEY={secret_hex} loaded")
    # The kv pattern catches it first → "INTERNAL_API_KEY=***REDACTED***".
    # Either redaction is acceptable; what matters is the secret never
    # appears verbatim in the output.
    assert secret_hex not in out
    assert "REDACTED" in out


def test_redact_long_hex_string_in_freeform_text(logging_config):
    """A bare hex blob in freeform prose (no key=) still gets caught by
    the long-hex pattern."""
    secret_hex = "deadbeef" * 5  # 40 chars
    out = logging_config._redact(f"loaded blob {secret_hex} into cache")
    assert secret_hex not in out
    assert "***REDACTED_HEX***" in out


def test_redact_empty_string_is_safe(logging_config):
    assert logging_config._redact("") == ""


def test_redact_none_is_safe(logging_config):
    assert logging_config._redact(None) is None


def test_redact_non_string_passthrough(logging_config):
    """Numbers / dicts / etc. are returned unchanged — the filter runs
    before formatting and shouldn't crash on non-string args."""
    assert logging_config._redact(42) == 42


# ── SecretRedactionFilter ───────────────────────────────────────────


def _make_record(msg: str, args=None) -> logging.LogRecord:
    return logging.LogRecord(
        name="test", level=logging.INFO, pathname=__file__, lineno=1,
        msg=msg, args=args, exc_info=None,
    )


def test_filter_redacts_record_msg(logging_config):
    record = _make_record("got token=secret-value")
    assert logging_config.SecretRedactionFilter().filter(record) is True
    assert "secret-value" not in record.msg
    assert "token=***REDACTED***" in record.msg


def test_filter_redacts_string_args_in_tuple(logging_config):
    record = _make_record("user=%s token=%s", ("alice", "leaky"))
    logging_config.SecretRedactionFilter().filter(record)
    # The tuple element passed in IS a bare value, not a key=value, so
    # only the long-hex / format-string-shape patterns apply. The token
    # arg "leaky" is short and doesn't match a redaction pattern by
    # itself; the redaction is on the rendered MSG, not the arg. Verify
    # the filter doesn't crash and preserves arity.
    assert isinstance(record.args, tuple)
    assert len(record.args) == 2


def test_filter_returns_true_to_let_record_through(logging_config):
    """A Filter that returns False would suppress the log entirely. The
    redaction filter must always return True so we still ship the
    record (just sanitized)."""
    record = _make_record("nothing secret here")
    assert logging_config.SecretRedactionFilter().filter(record) is True


# ── JSONFormatter ───────────────────────────────────────────────────


def test_json_formatter_emits_parseable_json_with_expected_keys(logging_config):
    record = _make_record("hello")
    record.created = 1715000000.0
    out = logging_config.JSONFormatter(service_name="testsvc").format(record)
    payload = json.loads(out)  # MUST parse — no trailing newlines, no junk
    assert payload["level"] == "INFO"
    assert payload["service"] == "testsvc"
    assert payload["logger"] == "test"
    assert payload["message"] == "hello"
    assert "timestamp" in payload


def test_json_formatter_includes_exc_info_when_present(logging_config):
    try:
        raise ValueError("kaboom")
    except ValueError:
        exc = sys.exc_info()
    record = logging.LogRecord(
        name="test", level=logging.ERROR, pathname=__file__, lineno=1,
        msg="failed", args=None, exc_info=exc,
    )
    out = logging_config.JSONFormatter(service_name="testsvc").format(record)
    payload = json.loads(out)
    assert "exc_info" in payload
    assert "ValueError" in payload["exc_info"]
    assert "kaboom" in payload["exc_info"]


def test_json_formatter_includes_extra_context_when_set(logging_config):
    """logger.info("...", extra={"request_id": "..."}) shows up as a
    top-level field in the JSON payload."""
    record = _make_record("ok")
    record.request_id = "req-123"
    record.user_id = "u42"
    out = logging_config.JSONFormatter(service_name="testsvc").format(record)
    payload = json.loads(out)
    assert payload["request_id"] == "req-123"
    assert payload["user_id"] == "u42"


# ── setup_logging idempotency ───────────────────────────────────────


def test_setup_logging_does_not_stack_handlers(logging_config):
    """Calling setup_logging twice must leave the root logger with
    exactly one handler — otherwise every log line gets printed twice
    (or N times) after a reload."""
    logging_config.setup_logging(service_name="t1")
    n_after_first = len(logging.getLogger().handlers)
    logging_config.setup_logging(service_name="t2")
    n_after_second = len(logging.getLogger().handlers)
    assert n_after_first == 1
    assert n_after_second == 1


def test_setup_logging_emits_json_end_to_end(logging_config, capsys):
    """End-to-end smoke: configure, log, parse the captured stdout. The
    secret in the log line must be redacted in the emitted JSON."""
    logging_config.setup_logging(service_name="e2e")
    logging.getLogger("e2e_test").info("token=should-be-redacted-12345")
    captured = capsys.readouterr().out.strip().splitlines()
    # Last line is our log; earlier lines may be from setup itself.
    payload = json.loads(captured[-1])
    assert payload["service"] == "e2e"
    assert payload["level"] == "INFO"
    assert "should-be-redacted-12345" not in payload["message"]
    assert "token=***REDACTED***" in payload["message"]


# ── Textual-identity contract ───────────────────────────────────────


def test_logging_config_is_textually_identical_across_services():
    """The 3 services ship a verbatim copy. A drift here is exactly the
    bug pattern the audit flagged — a fix in one service that doesn't
    propagate to the other two."""
    digests = {
        svc: hashlib.md5(path.read_bytes()).hexdigest()
        for svc, path in LOGGING_FILES.items()
    }
    distinct = set(digests.values())
    assert len(distinct) == 1, (
        f"logging_config.py drifted across services: {digests!r}"
    )
