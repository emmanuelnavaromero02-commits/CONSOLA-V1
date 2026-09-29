from __future__ import annotations

import re
import unicodedata
from collections.abc import Mapping
from typing import Any

from app.services.control_room.business_cartridge_scope import allowed_business_cartridges
from app.services.control_room.domain_kpis import PUBLIC_CARTRIDGE_LABELS
from app.services.mcp_gateway.errors import GatewayError


_NON_ALNUM = re.compile(r"[^a-z0-9]+")
SYNONYMS: dict[str, str] = {
    "successfactors": "sap_successfactors",
    "sap successfactors": "sap_successfactors",
    "business one": "sap_b1",
    "sap business one": "sap_b1",
    "sap b1": "sap_b1",
    "b1": "sap_b1",
    "s4hana": "sap_s4hana",
    "s 4hana": "sap_s4hana",
    "sap s4hana": "sap_s4hana",
    "sap s 4hana": "sap_s4hana",
    "sap hcm": "sap_hcm",
    "sec": "sec_edgar",
    "edgar": "sec_edgar",
    "sec edgar": "sec_edgar",
    "banco de mexico": "banxico",
}
AMBIGUOUS = frozenset({"sap", "sf"})
HIDDEN_SOURCES = frozenset({"platform"})


def normalize(value: object) -> str:
    text = unicodedata.normalize("NFKD", str(value or "")).casefold()
    text = "".join(char for char in text if not unicodedata.combining(char))
    return " ".join(_NON_ALNUM.sub(" ", text).split())


def source_label(source_id: str) -> str:
    key = str(source_id or "").strip()
    if not key:
        return "Sin información"
    return PUBLIC_CARTRIDGE_LABELS.get(key) or key.replace("_", " ")


def enabled_sources(user: Mapping[str, Any]) -> list[str]:
    allowed = allowed_business_cartridges(user)
    if allowed is None:
        return sorted(set(PUBLIC_CARTRIDGE_LABELS))
    return sorted(source for source in allowed if source and source not in HIDDEN_SOURCES)


def enabled_source_labels(user: Mapping[str, Any]) -> list[str]:
    return sorted({source_label(source) for source in enabled_sources(user)}, key=str.casefold)


def _lookup(extra_ids: list[str]) -> dict[str, str]:
    table: dict[str, str] = {}
    for source_id in (*PUBLIC_CARTRIDGE_LABELS, *extra_ids):
        table[normalize(source_id)] = source_id
    for source_id, label in PUBLIC_CARTRIDGE_LABELS.items():
        table[normalize(label)] = source_id
    table.update(SYNONYMS)
    for ambiguous in AMBIGUOUS:
        table.pop(ambiguous, None)
    return table


def _unknown_source_error(user: Mapping[str, Any]) -> GatewayError:
    labels = enabled_source_labels(user)
    listed = ", ".join(labels) if labels else "ninguna"
    return GatewayError(
        400,
        "argumentos_invalidos",
        f"No reconozco esa fuente de datos. Fuentes habilitadas en este espacio de trabajo: {listed}.",
    )


def resolve_source(value: object, user: Mapping[str, Any]) -> str:
    key = normalize(value)
    if not key or key in AMBIGUOUS:
        raise _unknown_source_error(user)
    enabled = enabled_sources(user)
    source_id = _lookup(enabled).get(key)
    if source_id is None:
        raise _unknown_source_error(user)
    if source_id not in enabled:
        raise GatewayError(403, "fuente_no_habilitada")
    return source_id


__all__ = (
    "AMBIGUOUS",
    "SYNONYMS",
    "enabled_source_labels",
    "enabled_sources",
    "normalize",
    "resolve_source",
    "source_label",
)
