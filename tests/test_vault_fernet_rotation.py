from __future__ import annotations

import json

from cryptography.fernet import Fernet

from vault.app.crypto import decrypt_value, encrypt_value


def test_encrypt_prefixes_current_key_id(monkeypatch):
    key = Fernet.generate_key().decode()
    monkeypatch.setenv("VAULT_ENCRYPTION_KEY", key)
    monkeypatch.setenv("VAULT_ENCRYPTION_KEY_ID", "k-current")
    monkeypatch.delenv("VAULT_ENCRYPTION_KEYS", raising=False)

    ciphertext = encrypt_value({"token": "secret"})

    assert ciphertext.startswith(b"k-current:")
    assert decrypt_value(ciphertext) == {"token": "secret"}


def test_decrypt_uses_previous_key_from_keyring(monkeypatch):
    old_key = Fernet.generate_key().decode()
    new_key = Fernet.generate_key().decode()

    monkeypatch.setenv("VAULT_ENCRYPTION_KEY", old_key)
    monkeypatch.setenv("VAULT_ENCRYPTION_KEY_ID", "old")
    old_ciphertext = encrypt_value({"value": "still-readable"})

    monkeypatch.setenv("VAULT_ENCRYPTION_KEY", new_key)
    monkeypatch.setenv("VAULT_ENCRYPTION_KEY_ID", "new")
    monkeypatch.setenv("VAULT_ENCRYPTION_KEYS", f"old={old_key}")

    assert decrypt_value(old_ciphertext) == {"value": "still-readable"}
    assert encrypt_value({"value": "new"}).startswith(b"new:")


def test_legacy_unprefixed_ciphertext_can_decrypt_with_previous_key(monkeypatch):
    old_key = Fernet.generate_key()
    new_key = Fernet.generate_key().decode()
    legacy_ciphertext = Fernet(old_key).encrypt(json.dumps({"legacy": True}).encode())

    monkeypatch.setenv("VAULT_ENCRYPTION_KEY", new_key)
    monkeypatch.setenv("VAULT_ENCRYPTION_KEY_ID", "new")
    monkeypatch.setenv("VAULT_ENCRYPTION_KEYS", f"old={old_key.decode()}")

    assert decrypt_value(legacy_ciphertext) == {"legacy": True}
