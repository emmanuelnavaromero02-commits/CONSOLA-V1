from __future__ import annotations

import json
from pathlib import Path

_ORDERS_PATH = Path(__file__).resolve().parent.parent / "config" / "gold_dataset_orders.json"


def _load() -> dict[str, list[str]]:
    data = json.loads(_ORDERS_PATH.read_text(encoding="utf-8"))
    foundation = list(data["foundation_order"])
    silver_curated = list(data["silver_talent_curated_order"])
    talent = list(data["talent_order"])
    contract = list(data["talent_contract_order"])
    operational = list(data["talent_operational_order"])
    if contract + operational != talent:
        raise ValueError(
            "gold_dataset_orders.json invalido: "
            "talent_contract_order + talent_operational_order != talent_order"
        )
    return {
        "foundation": foundation,
        "silver_curated": silver_curated,
        "talent": talent,
        "contract": contract,
        "operational": operational,
    }


_ORDERS = _load()

SUCCESSFACTORS_GOLD_FOUNDATION_ORDER = _ORDERS["foundation"]
SUCCESSFACTORS_SILVER_TALENT_CURATED_ORDER = _ORDERS["silver_curated"]
SUCCESSFACTORS_GOLD_TALENT_ORDER = _ORDERS["talent"]
SUCCESSFACTORS_GOLD_TALENT_CONTRACT_ORDER = _ORDERS["contract"]
SUCCESSFACTORS_GOLD_TALENT_OPERATIONAL_ORDER = _ORDERS["operational"]

__all__ = [
    "SUCCESSFACTORS_GOLD_FOUNDATION_ORDER",
    "SUCCESSFACTORS_SILVER_TALENT_CURATED_ORDER",
    "SUCCESSFACTORS_GOLD_TALENT_ORDER",
    "SUCCESSFACTORS_GOLD_TALENT_CONTRACT_ORDER",
    "SUCCESSFACTORS_GOLD_TALENT_OPERATIONAL_ORDER",
]
