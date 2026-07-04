from __future__ import annotations

from typing import Any


TALENT_BOX_DEFINITIONS: list[dict[str, Any]] = [
    {
        "box_id": "enigma",
        "box_label": "Enigma",
        "potential_band": "high",
        "performance_band": "low",
        "movement_action": "Cambio de rol o coaching de fit",
        "display_order": 1,
    },
    {
        "box_id": "crecimiento",
        "box_label": "Crecimiento",
        "potential_band": "high",
        "performance_band": "medium",
        "movement_action": "Asignacion de estiramiento y rotacion",
        "display_order": 2,
    },
    {
        "box_id": "estrella",
        "box_label": "Estrella",
        "potential_band": "high",
        "performance_band": "high",
        "movement_action": "Sucesion, promocion y retencion",
        "display_order": 3,
    },
    {
        "box_id": "dilema",
        "box_label": "Dilema",
        "potential_band": "medium",
        "performance_band": "low",
        "movement_action": "Plan de mejora o reubicacion",
        "display_order": 4,
    },
    {
        "box_id": "core",
        "box_label": "Core",
        "potential_band": "medium",
        "performance_band": "medium",
        "movement_action": "Retener y desarrollo continuo",
        "display_order": 5,
    },
    {
        "box_id": "alto_impacto",
        "box_label": "Alto Impacto",
        "potential_band": "medium",
        "performance_band": "high",
        "movement_action": "Promocion a siguiente nivel",
        "display_order": 6,
    },
    {
        "box_id": "riesgo",
        "box_label": "Riesgo",
        "potential_band": "low",
        "performance_band": "low",
        "movement_action": "PIP o gestion de salida",
        "display_order": 7,
    },
    {
        "box_id": "efectivo",
        "box_label": "Efectivo",
        "potential_band": "low",
        "performance_band": "medium",
        "movement_action": "Mantener en rol",
        "display_order": 8,
    },
    {
        "box_id": "experto",
        "box_label": "Experto",
        "potential_band": "low",
        "performance_band": "high",
        "movement_action": "Via tecnica y retencion en rol",
        "display_order": 9,
    },
]
