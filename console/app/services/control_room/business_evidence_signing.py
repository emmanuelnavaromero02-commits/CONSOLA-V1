from __future__ import annotations

import hashlib
import hmac
import json
import os
import re
from dataclasses import dataclass
from typing import Any


CURRENT_KEY_ENV = "CONTROL_ROOM_EVIDENCE_SIGNING_KEY"
CURRENT_KEY_ID_ENV = "CONTROL_ROOM_EVIDENCE_SIGNING_KEY_ID"
PREVIOUS_KEYS_ENV = "CONTROL_ROOM_EVIDENCE_SIGNING_PREVIOUS_KEYS"
_MIN_KEY_LENGTH = 32
_KEY_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")
_DOMAIN = b"omega-control-room-evidence-v1\x00"


class EvidenceSigningConfigurationError(RuntimeError):
    pass


@dataclass(frozen=True)
class EvidenceSigningKeyring:
    current_key_id: str
    keys: dict[str, bytes]


def _key_id(value: Any, *, env_name: str) -> str:
    normalized = str(value or "").strip()
    if not _KEY_ID_PATTERN.fullmatch(normalized):
        raise EvidenceSigningConfigurationError(f"{env_name} is required and invalid")
    return normalized


def _key_bytes(value: Any, *, env_name: str) -> bytes:
    normalized = str(value or "").strip()
    if len(normalized) < _MIN_KEY_LENGTH:
        raise EvidenceSigningConfigurationError(
            f"{env_name} must contain at least {_MIN_KEY_LENGTH} characters"
        )
    security_context_key = (
        os.environ.get("SECURITY_CONTEXT_SIGNING_KEY") or ""
    ).strip()
    if security_context_key and hmac.compare_digest(normalized, security_context_key):
        raise EvidenceSigningConfigurationError(
            f"{env_name} must be distinct from SECURITY_CONTEXT_SIGNING_KEY"
        )
    return normalized.encode("utf-8")


def _previous_keys() -> dict[str, bytes]:
    raw = (os.environ.get(PREVIOUS_KEYS_ENV) or "").strip()
    if not raw:
        return {}
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise EvidenceSigningConfigurationError(
            f"{PREVIOUS_KEYS_ENV} must be a JSON object"
        ) from exc
    if not isinstance(parsed, dict):
        raise EvidenceSigningConfigurationError(
            f"{PREVIOUS_KEYS_ENV} must be a JSON object"
        )
    return {
        _key_id(key_id, env_name=PREVIOUS_KEYS_ENV): _key_bytes(
            key, env_name=PREVIOUS_KEYS_ENV
        )
        for key_id, key in parsed.items()
    }


def evidence_signing_keyring() -> EvidenceSigningKeyring:
    current_key_id = _key_id(
        os.environ.get(CURRENT_KEY_ID_ENV), env_name=CURRENT_KEY_ID_ENV
    )
    current_key = _key_bytes(os.environ.get(CURRENT_KEY_ENV), env_name=CURRENT_KEY_ENV)
    previous = _previous_keys()
    if current_key_id in previous:
        raise EvidenceSigningConfigurationError(
            f"{PREVIOUS_KEYS_ENV} cannot redefine the active key id"
        )
    for previous_id, previous_key in previous.items():
        if hmac.compare_digest(current_key, previous_key):
            raise EvidenceSigningConfigurationError(
                f"previous evidence key {previous_id!r} must differ from the active key"
            )
    return EvidenceSigningKeyring(
        current_key_id=current_key_id,
        keys={current_key_id: current_key, **previous},
    )


def active_evidence_signing_key_id() -> str:
    return evidence_signing_keyring().current_key_id


def _message(payload: bytes, *, purpose: str, key_id: str) -> bytes:
    normalized_purpose = str(purpose or "").strip()
    normalized_key_id = _key_id(key_id, env_name="attestation key id")
    if not isinstance(payload, bytes) or not normalized_purpose:
        raise ValueError("payload bytes and signing purpose are required")
    purpose_bytes = normalized_purpose.encode("utf-8")
    key_id_bytes = normalized_key_id.encode("utf-8")
    return b"".join(
        (
            _DOMAIN,
            len(purpose_bytes).to_bytes(4, "big"),
            purpose_bytes,
            len(key_id_bytes).to_bytes(4, "big"),
            key_id_bytes,
            payload,
        )
    )


def sign_control_room_evidence(payload: bytes, *, purpose: str, key_id: str) -> str:
    keyring = evidence_signing_keyring()
    if not hmac.compare_digest(str(key_id), keyring.current_key_id):
        raise EvidenceSigningConfigurationError("evidence must use the active key id")
    return hmac.new(
        keyring.keys[keyring.current_key_id],
        _message(payload, purpose=purpose, key_id=key_id),
        hashlib.sha256,
    ).hexdigest()


def verify_control_room_evidence(
    payload: bytes,
    *,
    purpose: str,
    key_id: str,
    signature: str,
) -> bool:
    try:
        keyring = evidence_signing_keyring()
        key = keyring.keys.get(str(key_id))
        if key is None:
            return False
        expected = hmac.new(
            key,
            _message(payload, purpose=purpose, key_id=key_id),
            hashlib.sha256,
        ).hexdigest()
        return hmac.compare_digest(str(signature), expected)
    except (EvidenceSigningConfigurationError, ValueError):
        return False


__all__ = (
    "EvidenceSigningConfigurationError",
    "active_evidence_signing_key_id",
    "sign_control_room_evidence",
    "verify_control_room_evidence",
)
