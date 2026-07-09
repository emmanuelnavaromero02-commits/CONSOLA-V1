"""Fuente unica de verdad del orden de materializacion de datasets gold de SF.

Las listas viven en un unico JSON de datos
(app/config/gold_dataset_orders.json) para que puedan compartirse a traves de la
frontera de despliegue: este modulo lo carga para el contenedor del cartucho
(refinement_triggers.py, job_runner.py), y refinement/scripts/
materialize_successfactors_foundation.py lo carga desde el mount
/registry/cartridges. Un test anti-drift falla si alguna copia diverge.

NO redefinir estas listas como literales en otro modulo: importar de aqui.
"""
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
    # Invariante estructural: el split contract+operational reconstruye talent
    # exactamente (mismo contenido y orden).
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
