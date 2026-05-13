"""Fernet-based encryption for vault secrets at rest (Sprint v1.15).

The master key lives in the ``VAULT_ENCRYPTION_KEY`` environment variable
(Fernet 32-byte base64). If absent or invalid, the vault service refuses
to start — the alternative would be writing plaintext secrets to disk
under the audit's nose.

Plaintext format on the wire / in code is a dict (matches the existing
JSONB shape). Ciphertext is the Fernet token (URL-safe base64 with a
version byte + timestamp + IV + HMAC) stored in a BYTEA column.
"""
from __future__ import annotations

import json
import os

from cryptography.fernet import Fernet, InvalidToken


class VaultEncryptionError(Exception):
    """Raised when the master key is missing, invalid, or cannot decrypt."""


def _get_fernet() -> Fernet:
    key = os.environ.get("VAULT_ENCRYPTION_KEY", "").strip()
    if not key:
        raise VaultEncryptionError(
            "VAULT_ENCRYPTION_KEY missing. Generate with: "
            "python3 -c 'from cryptography.fernet import Fernet; "
            "print(Fernet.generate_key().decode())'"
        )
    try:
        return Fernet(key.encode())
    except Exception as e:  # ValueError / binascii.Error / TypeError, etc.
        raise VaultEncryptionError(f"VAULT_ENCRYPTION_KEY invalid: {e}")


def encrypt_value(value: dict) -> bytes:
    """Encrypt a dict (vault value) to bytes for storage in the BYTEA column."""
    plaintext = json.dumps(value).encode("utf-8")
    return _get_fernet().encrypt(plaintext)


def decrypt_value(ciphertext: bytes) -> dict:
    """Decrypt BYTEA bytes back to a dict.

    Raises ``VaultEncryptionError`` if the key is wrong or the ciphertext
    is corrupted — never silently returns garbage.
    """
    if ciphertext is None:
        raise VaultEncryptionError("Cannot decrypt None")
    try:
        plaintext = _get_fernet().decrypt(bytes(ciphertext))
    except InvalidToken:
        raise VaultEncryptionError(
            "Decryption failed — wrong VAULT_ENCRYPTION_KEY or corrupted data"
        )
    return json.loads(plaintext.decode("utf-8"))
