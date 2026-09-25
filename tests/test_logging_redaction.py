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
    "workspace":  REPO_ROOT / "workspace" / "app" / "logging_config.py",
    "mcp-infra":  REPO_ROOT / "mcp-infra" / "app" / "logging_config.py",
    "refinement": REPO_ROOT / "refinement" / "app" / "logging_config.py",
    "vault":      REPO_ROOT / "vault"  / "app" / "logging_config.py",
}


@pytest.fixture()
def logging_config(monkeypatch):
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


def test_redact_bearer_token(logging_config):
    out = logging_config._redact("Authorization: Bearer abc123def_ghi.jkl-mno")
    assert "abc123def" not in out
    assert "Bearer ***REDACTED***" in out


def test_redact_basic_authorization_header(logging_config):
    out = logging_config._redact("Authorization: Basic dXNlcjpwYXNz")
    assert "dXNlcjpwYXNz" not in out
    assert "Authorization: ***REDACTED***" in out


def test_redact_password_kv(logging_config):
    out = logging_config._redact("user=alice password=hunter2 ok")
    assert "hunter2" not in out
    assert "password=***REDACTED***" in out


def test_redact_token_kv(logging_config):
    out = logging_config._redact("calling api with token=xyz123")
    assert "xyz123" not in out
    assert "token=***REDACTED***" in out


def test_redact_enterprise_secret_shapes(logging_config):
    jwt = "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiJ1c2VyIn0.VeryLongSignature12345"
    sample = (
        f"Authorization: Bearer live-token refresh_token=refresh-123 "
        f"INTERNAL_API_KEY_CONSOLE_TO_MCP_INFRA=pair-key "
        f"hubspot_token='hub-secret' salesforce_client_secret=sf-secret "
        f"sap_password=sap-pwd vault_value=vault-secret jwt={jwt}"
    )

    out = logging_config._redact(sample)

    for secret in (
        "live-token",
        "refresh-123",
        "pair-key",
        "hub-secret",
        "sf-secret",
        "sap-pwd",
        "vault-secret",
        jwt,
    ):
        assert secret not in out
    assert "Bearer ***REDACTED***" in out
    assert "***REDACTED***" in out


def test_redact_api_key_kv(logging_config):
    out = logging_config._redact("api_key=foo and api-key=bar")
    assert "foo" not in out and "bar" not in out
    assert out.lower().count("=***redacted***") == 2


def test_redact_json_password_field(logging_config):
    out = logging_config._redact(
        '{"password": "secret", "refresh_token": "refresh-secret", "user": "alice"}'
    )
    assert "secret" not in out
    assert "refresh-secret" not in out
    assert '"password": "***REDACTED***"' in out
    assert '"refresh_token": "***REDACTED***"' in out
    assert '"user": "alice"' in out


def test_redact_normal_text_is_unchanged(logging_config):
    sample = "User alice opened workspace 42"
    assert logging_config._redact(sample) == sample


def test_redact_long_hex_string(logging_config):
    secret_hex = "a" * 40
    out = logging_config._redact(f"INTERNAL_API_KEY={secret_hex} loaded")
    assert secret_hex not in out
    assert "REDACTED" in out


def test_redact_long_hex_string_in_freeform_text(logging_config):
    secret_hex = "deadbeef" * 5
    out = logging_config._redact(f"loaded blob {secret_hex} into cache")
    assert secret_hex not in out
    assert "***REDACTED_HEX***" in out


def test_redact_empty_string_is_safe(logging_config):
    assert logging_config._redact("") == ""


def test_redact_none_is_safe(logging_config):
    assert logging_config._redact(None) is None


def test_redact_non_string_passthrough(logging_config):
    assert logging_config._redact(42) == 42


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
    assert isinstance(record.args, tuple)
    assert len(record.args) == 2


def test_filter_redacts_dict_args_by_sensitive_key(logging_config):
    record = _make_record("payload=%(payload)s")
    record.args = {"payload": {"hubspot_token": "short-but-secret", "workspace_id": "ws-1"}}
    logging_config.SecretRedactionFilter().filter(record)

    assert record.args["payload"]["hubspot_token"] == "***REDACTED***"
    assert record.args["payload"]["workspace_id"] == "ws-1"


def test_filter_returns_true_to_let_record_through(logging_config):
    record = _make_record("nothing secret here")
    assert logging_config.SecretRedactionFilter().filter(record) is True


def test_json_formatter_emits_parseable_json_with_expected_keys(logging_config):
    record = _make_record("hello")
    record.created = 1715000000.0
    out = logging_config.JSONFormatter(service_name="testsvc").format(record)
    payload = json.loads(out)
    assert payload["level"] == "INFO"
    assert payload["service"] == "testsvc"
    assert payload["logger"] == "test"
    assert payload["message"] == "hello"
    assert "timestamp" in payload


def test_json_formatter_includes_exc_info_when_present(logging_config):
    try:
        raise ValueError("kaboom token=secret-token")
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
    assert "secret-token" not in payload["exc_info"]
    assert "token=***REDACTED***" in payload["exc_info"]


def test_json_formatter_includes_extra_context_when_set(logging_config):
    record = _make_record("ok")
    record.request_id = "req-123"
    record.user_id = "u42"
    out = logging_config.JSONFormatter(service_name="testsvc").format(record)
    payload = json.loads(out)
    assert payload["request_id"] == "req-123"
    assert payload["user_id"] == "u42"


def test_setup_logging_does_not_stack_handlers(logging_config):
    logging_config.setup_logging(service_name="t1")
    n_after_first = len(logging.getLogger().handlers)
    logging_config.setup_logging(service_name="t2")
    n_after_second = len(logging.getLogger().handlers)
    assert n_after_first == 1
    assert n_after_second == 1


def test_setup_logging_emits_json_end_to_end(logging_config, capsys):
    logging_config.setup_logging(service_name="e2e")
    logging.getLogger("e2e_test").info("token=should-be-redacted-12345")
    captured = capsys.readouterr().out.strip().splitlines()
    payload = json.loads(captured[-1])
    assert payload["service"] == "e2e"
    assert payload["level"] == "INFO"
    assert "should-be-redacted-12345" not in payload["message"]
    assert "token=***REDACTED***" in payload["message"]


def test_setup_logging_redacts_secret_from_cartridge_exception(logging_config, capsys):
    logging_config.setup_logging(service_name="cartridge")
    try:
        raise RuntimeError(
            "HubSpot upstream failed Authorization: Bearer hubspot-live-token "
            "sap_password=sap-secret salesforce_client_secret=sf-secret"
        )
    except RuntimeError:
        logging.getLogger("cartridges.hubspot").exception("cartridge error")

    payload = json.loads(capsys.readouterr().out.strip().splitlines()[-1])
    combined = json.dumps(payload)
    assert "hubspot-live-token" not in combined
    assert "sap-secret" not in combined
    assert "sf-secret" not in combined
    assert "Bearer ***REDACTED***" in combined
    assert "***REDACTED***" in combined


def test_logging_config_is_textually_identical_across_services():
    digests = {
        svc: hashlib.md5(path.read_bytes()).hexdigest()
        for svc, path in LOGGING_FILES.items()
    }
    distinct = set(digests.values())
    assert len(distinct) == 1, (
        f"logging_config.py drifted across services: {digests!r}"
    )
