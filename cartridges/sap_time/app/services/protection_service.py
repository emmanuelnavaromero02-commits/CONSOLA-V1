from __future__ import annotations

import base64
import hashlib
from pathlib import Path
from typing import Any

import yaml
from cryptography.fernet import Fernet

BASE_DIR = Path(__file__).resolve().parents[1]
ENTITIES_PATH = BASE_DIR / "config" / "entities.yaml"


def _load_entities() -> list[dict]:
    with ENTITIES_PATH.open("r", encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}
    return data.get("entities", [])


def _get_entity_protection(entity_name: str) -> dict[str, str]:
    for item in _load_entities():
        if item.get("entity") == entity_name:
            return item.get("protection", {}) or {}
    return {}


def _mask(value: Any) -> Any:
    if value is None:
        return value
    s = str(value)
    if len(s) <= 4:
        return "*" * len(s)
    return "*" * (len(s) - 4) + s[-4:]


def _shadow(value: Any) -> Any:
    if value is None:
        return value
    return hashlib.sha256(str(value).encode("utf-8")).hexdigest()


def _build_fernet() -> Fernet:
    from app.core.vault_client import get_secret
    key_str = get_secret("field_encryption_key", default="change-this-key-in-prod")
    raw_key = hashlib.sha256(key_str.encode("utf-8")).digest()
    return Fernet(base64.urlsafe_b64encode(raw_key))


def _encrypt(value: Any) -> Any:
    if value is None:
        return value
    return _build_fernet().encrypt(str(value).encode("utf-8")).decode("utf-8")


def apply_protection_for_entity(entity_name: str, rows: list[dict]) -> list[dict]:
    rules = _get_entity_protection(entity_name)
    if not rules:
        return rows

    output = []
    for row in rows:
        new_row = dict(row)
        for field_name, rule in rules.items():
            if field_name not in new_row:
                continue
            if rule == "plain":
                pass
            elif rule == "masked":
                new_row[field_name] = _mask(new_row[field_name])
            elif rule == "shadowed":
                new_row[field_name] = _shadow(new_row[field_name])
            elif rule == "encrypted":
                new_row[field_name] = _encrypt(new_row[field_name])
        output.append(new_row)
    return output
