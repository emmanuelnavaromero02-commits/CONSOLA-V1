from __future__ import annotations

import importlib
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


GOOD_KEY = "8sXi-0kBYU5DJ5dY7CCRkW7XHJsXxLPmO6r9OYx-3a4="
OTHER_KEY = "kV8KuJBgo3-NSr-8Ev9JddXJ6r8MaYRyLb8L6kP5gtA="


def _load_crypto(monkeypatch):
    monkeypatch.setenv("VAULT_ENCRYPTION_KEY", GOOD_KEY)
    sys.modules.pop("app.crypto", None)
    return importlib.import_module("app.crypto")


def test_encrypt_value_returns_bytes_not_equal_to_plaintext(monkeypatch):
    crypto = _load_crypto(monkeypatch)
    value = {"token": "secret-token", "base_url": "https://api.example.com"}
    ciphertext = crypto.encrypt_value(value)
    assert isinstance(ciphertext, bytes)
    assert ciphertext != json.dumps(value).encode("utf-8")
    assert b"secret-token" not in ciphertext


def test_decrypt_value_round_trips(monkeypatch):
    crypto = _load_crypto(monkeypatch)
    value = {"a": 1, "nested": {"b": "two"}, "list": [1, 2, 3]}
    assert crypto.decrypt_value(crypto.encrypt_value(value)) == value


def test_encrypt_produces_different_output_for_same_input(monkeypatch):
    crypto = _load_crypto(monkeypatch)
    value = {"token": "same-input"}
    c1 = crypto.encrypt_value(value)
    c2 = crypto.encrypt_value(value)
    assert c1 != c2
    assert crypto.decrypt_value(c1) == crypto.decrypt_value(c2) == value


def test_decrypt_with_wrong_key_raises_encryption_error(monkeypatch):
    crypto = _load_crypto(monkeypatch)
    value = {"token": "encrypted-with-good-key"}
    ciphertext = crypto.encrypt_value(value)
    monkeypatch.setenv("VAULT_ENCRYPTION_KEY", OTHER_KEY)
    sys.modules.pop("app.crypto", None)
    crypto2 = importlib.import_module("app.crypto")
    with pytest.raises(crypto2.VaultEncryptionError) as exc:
        crypto2.decrypt_value(ciphertext)
    assert "wrong VAULT_ENCRYPTION_KEY" in str(exc.value) or "corrupted" in str(exc.value)


def test_get_fernet_without_env_raises(monkeypatch):
    crypto = _load_crypto(monkeypatch)
    monkeypatch.delenv("VAULT_ENCRYPTION_KEY", raising=False)
    with pytest.raises(crypto.VaultEncryptionError) as exc:
        crypto._get_fernet()
    assert "VAULT_ENCRYPTION_KEY missing" in str(exc.value)


def test_get_fernet_with_invalid_key_raises(monkeypatch):
    crypto = _load_crypto(monkeypatch)
    monkeypatch.setenv("VAULT_ENCRYPTION_KEY", "not-a-real-fernet-key")
    with pytest.raises(crypto.VaultEncryptionError) as exc:
        crypto._get_fernet()
    assert "invalid" in str(exc.value).lower()


def test_decrypt_none_raises(monkeypatch):
    crypto = _load_crypto(monkeypatch)
    with pytest.raises(crypto.VaultEncryptionError):
        crypto.decrypt_value(None)
