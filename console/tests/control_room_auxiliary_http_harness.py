from __future__ import annotations

from control_room_public_http_harness import DATASET_READER, OPERATOR, poison


SCOPED_OPERATOR = {
    **OPERATOR,
    "allowed_cartridges": ["banxico", "inegi", "sec_edgar", "sap_successfactors"],
}
SCOPED_READER = {
    **DATASET_READER,
    "allowed_cartridges": ["banxico", "inegi", "sec_edgar", "sap_successfactors"],
}
AUXILIARY_FORBIDDEN_KEYS = {
    "cartridge_id",
    "dataset",
    "details",
    "external_action_id",
    "id",
    "item_id",
    "metadata",
    "orchestration_id",
    "series_id",
    "simulation_id",
    "source_authority",
    "source_decision_id",
    "tenant_id",
    "workspace_id",
}


def auxiliary_poison() -> dict:
    return {
        **poison(),
        "tenant_id": "private-tenant",
        "workspace_id": "private-workspace",
    }


def readiness_payload() -> dict:
    return {
        **auxiliary_poison(),
        "cartridge_id": "banxico",
        "dataset": "private_market_dataset",
        "status": "ready",
        "series_count": 1,
        "usable_count": 0,
        "series": [
            {
                **auxiliary_poison(),
                "series_id": "technical-series-id",
                "source_authority": "private-provenance",
                "metric_name": "Tipo de cambio",
                "as_of": "2026-07-26",
                "unit": "MXN",
                "value": 0,
                "confidence": 0,
                "status": "ready",
                "usable": False,
            }
        ],
    }


def market_payload() -> dict:
    return {
        **auxiliary_poison(),
        "status": "ready",
        "source": {
            **auxiliary_poison(),
            "dataset": "private_simulation_inputs",
            "source_id": "technical-source-id",
            "input_status": "ready",
            "source_mode": "cpa_real",
            "employee_count": 0,
            "confidence": 0,
        },
        "market_context": {
            **auxiliary_poison(),
            "provider": "banxico",
            "metric_name": "Tipo de cambio",
            "as_of": "2026-07-26",
            "unit": "MXN",
            "confidence": 0,
            "freshness_status": "ready",
            "distribution": {"metadata": poison()},
        },
        "simulation": {
            **auxiliary_poison(),
            "available": True,
            "simulation_id": "technical-simulation-id",
            "model_version": "private-model",
            "output_metric": "cost",
            "p10": 0,
            "p50": 0,
            "p90": 0,
            "market_evidence_count": 0,
        },
        "bayes": {
            **auxiliary_poison(),
            "calibration_group": "private-group",
            "status": "ready",
            "sample_count": 0,
            "evidence_policy": "evidence_only",
        },
        "orchestration": {
            **auxiliary_poison(),
            "available": True,
            "orchestration_id": "technical-orchestration-id",
            "external_action_id": "technical-action-id",
            "problem_type": "validation",
            "action_recommended": False,
        },
        "policy": {
            **auxiliary_poison(),
            "recommendation_only": True,
            "automatic_action": False,
            "external_writeback": False,
        },
    }


def threshold_payload() -> dict:
    row = {
        **auxiliary_poison(),
        "id": 91,
        "cartridge_id": "sap_hcm",
        "anomaly_type": "capacity_risk",
        "metric": "utilization",
        "warning_value": 0,
        "critical_value": 0,
        "currency": "USD",
        "enabled": False,
        "created_at": "2026-07-26T10:00:00Z",
        "updated_at": "2026-07-26T10:00:00Z",
        "metadata": poison(),
    }
    return {
        **auxiliary_poison(),
        "thresholds": [row],
        "summary": {
            **auxiliary_poison(),
            "total": 1,
            "active": 0,
            "disabled": 1,
            "by_cartridge": {"sap_hcm": 1},
            "recent": [row],
        },
    }


def lessons_payload() -> dict:
    row = {
        **auxiliary_poison(),
        "id": 92,
        "item_id": "technical-item-id",
        "source_decision_id": 93,
        "cartridge_id": "sap_hcm",
        "anomaly_type": "capacity_risk",
        "rule": "Mantener revision humana",
        "confidence": 0,
        "created_at": "2026-07-26T10:00:00Z",
        "metadata": poison(),
    }
    return {
        **auxiliary_poison(),
        "lessons": [row],
        "summary": {
            **auxiliary_poison(),
            "total": 1,
            "recent": [row],
            "by_cartridge": {"sap_hcm": 1},
            "top_patterns": [
                {
                    **row,
                    "count": 1,
                    "latest_rule": "Mantener revision humana",
                    "last_seen_at": "2026-07-26T10:00:00Z",
                    "avg_confidence": 0,
                }
            ],
        },
    }
