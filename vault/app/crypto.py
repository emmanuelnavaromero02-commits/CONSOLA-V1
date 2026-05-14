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


_KEY_SEPARATOR = b":"
_DEFAULT_KID = "current"


def _parse_extra_keys(raw: str) -> dict[str, str]:
    keys: dict[str, str] = {}
    for item in (raw or "").split(","):
        item = item.strip()
        if not item:
            continue
        if "=" not in item:
            raise VaultEncryptionError(
                "VAULT_ENCRYPTION_KEYS must be comma-separated kid=fernet_key entries"
            )
        kid, key = item.split("=", 1)
        kid = kid.strip()
        key = key.strip()
        if not kid or _KEY_SEPARATOR.decode() in kid:
            raise VaultEncryptionError("Invalid Vault encryption key id")
        keys[kid] = key
    return keys


def _get_keyring() -> tuple[str, dict[str, Fernet]]:
    key = os.environ.get("VAULT_ENCRYPTION_KEY", "").strip()
    if not key:
        raise VaultEncryptionError(
            "VAULT_ENCRYPTION_KEY missing. Generate with: "
            "python3 -c 'from cryptography.fernet import Fernet; "
            "print(Fernet.generate_key().decode())'"
        )
    current_kid = os.environ.get("VAULT_ENCRYPTION_KEY_ID", _DEFAULT_KID).strip() or _DEFAULT_KID
    if _KEY_SEPARATOR.decode() in current_kid:
        raise VaultEncryptionError("VAULT_ENCRYPTION_KEY_ID cannot contain ':'")
    raw_keys = _parse_extra_keys(os.environ.get("VAULT_ENCRYPTION_KEYS", ""))
    raw_keys[current_kid] = key
    try:
        return current_kid, {kid: Fernet(raw.encode()) for kid, raw in raw_keys.items()}
    except Exception as e:  # ValueError / binascii.Error / TypeError, etc.
        raise VaultEncryptionError(f"Vault encryption key invalid: {e}")


def _get_fernet() -> Fernet:
    current_kid, keyring = _get_keyring()
    return keyring[current_kid]


def encrypt_value(value: dict) -> bytes:
    """Encrypt a dict (vault value) to bytes for storage in the BYTEA column."""
    plaintext = json.dumps(value).encode("utf-8")
    current_kid, keyring = _get_keyring()
    token = keyring[current_kid].encrypt(plaintext)
    return current_kid.encode("utf-8") + _KEY_SEPARATOR + token


def decrypt_value(ciphertext: bytes) -> dict:
    """Decrypt BYTEA bytes back to a dict.

    Raises ``VaultEncryptionError`` if the key is wrong or the ciphertext
    is corrupted — never silently returns garbage.
    """
    if ciphertext is None:
        raise VaultEncryptionError("Cannot decrypt None")
    raw = bytes(ciphertext)
    current_kid, keyring = _get_keyring()

    if _KEY_SEPARATOR in raw:
        kid_raw, token = raw.split(_KEY_SEPARATOR, 1)
        kid = kid_raw.decode("utf-8", errors="strict")
        fernet = keyring.get(kid)
        if fernet is None:
            raise VaultEncryptionError(f"Decryption failed — unknown VAULT_ENCRYPTION_KEY_ID {kid!r}")
        try:
            plaintext = fernet.decrypt(token)
        except InvalidToken:
            raise VaultEncryptionError(
                "Decryption failed — wrong Vault key for ciphertext key id or corrupted data"
            )
        return json.loads(plaintext.decode("utf-8"))

    # Legacy ciphertexts written before key ids had no prefix. Try every
    # configured key so operators can rotate once, configure previous keys,
    # and still read old rows until a rewrite migrates them.
    try:
        plaintext = keyring[current_kid].decrypt(raw)
    except InvalidToken:
        for kid, fernet in keyring.items():
            if kid == current_kid:
                continue
            try:
                plaintext = fernet.decrypt(raw)
                break
            except InvalidToken:
                continue
        else:
            raise VaultEncryptionError(
                "Decryption failed — wrong VAULT_ENCRYPTION_KEY, missing previous key, or corrupted data"
            )
    return json.loads(plaintext.decode("utf-8"))
