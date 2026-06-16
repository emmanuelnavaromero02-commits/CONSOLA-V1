#!/usr/bin/env python3
"""Probe the live Bayesian calibration loop without tenant credentials."""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path


REPO = Path(__file__).resolve().parents[1]
os.environ.setdefault("APP_ENV", "test")
sys.path.insert(0, str(REPO / "console"))


def _metric() -> dict:
    return {
        "id": "forecast_weighted",
        "name": "Forecast ponderado",
        "dataset": "forecast_mensual",
        "entity": {"kind": "seller", "id_field": "owner_id", "label_field": "vendedor"},
        "time_field": "mes",
        "value_field": "forecast_ponderado_usd",
        "expected_behavior": "higher_is_good",
        "baseline": {"method": "moving_average", "minimum_history": 2, "window": 6},
        "impact": {"currency": "USD", "unit_value": 20},
        "signal_rules": {"warning_pct": 0.20, "critical_pct": 0.45},
        "action_templates": [],
    }


def _rows() -> list[dict]:
    return [
        {
            "mes": "2026-01-01",
            "owner_id": "u1",
            "vendedor": "Sofia",
            "forecast_ponderado_usd": 100,
        },
        {
            "mes": "2026-02-01",
            "owner_id": "u1",
            "vendedor": "Sofia",
            "forecast_ponderado_usd": 120,
        },
        {
            "mes": "2026-03-01",
            "owner_id": "u1",
            "vendedor": "Sofia",
            "forecast_ponderado_usd": 110,
        },
        {
            "mes": "2026-04-01",
            "owner_id": "u1",
            "vendedor": "Sofia",
            "forecast_ponderado_usd": 200,
        },
    ]


def _check(condition: bool, name: str, details: dict | None = None) -> dict:
    return {"name": name, "status": "PASS" if condition else "FAIL", "details": details or {}}


def main() -> int:
    from app.services.intelligence import calibration
    from app.services.intelligence.baseline import build_metric_artifacts

    checks: list[dict] = []
    group = calibration.source_type_calibration_group("hubspot", "forecast_weighted")
    state = {
        "calibration_group": group,
        "posterior": {"alpha": 12.0, "beta": 18.0, "mean": 0.4},
        "metrics": {
            "sample_count": 30,
            "confidence_score": 1.0,
            "prior_source": "global",
            "partial_pooling_applied": True,
            "parent_calibration_group": calibration.global_calibration_group(
                "forecast_weighted"
            ),
            "parent_sample_count": 30,
        },
    }
    artifacts, skipped = build_metric_artifacts(
        {"cartridge": "hubspot", "domain": "Ventas"},
        _metric(),
        _rows(),
        calibration_states={group: state},
    )
    decision = artifacts[0]["decision_intelligence"] if artifacts else {}
    metadata = decision.get("calibration") if isinstance(decision, dict) else {}

    checks.append(_check(not skipped and bool(artifacts), "fixture produced live signal"))
    checks.append(
        _check(
            metadata.get("calibration_applied") is True,
            "posterior applied to live probability",
            metadata,
        )
    )
    checks.append(
        _check(
            metadata.get("raw_probability") != metadata.get("calibrated_probability"),
            "calibrated probability differs from raw",
            metadata,
        )
    )
    checks.append(
        _check(
            decision.get("anomaly_probability") == metadata.get("calibrated_probability"),
            "anomaly_probability uses calibrated probability",
            {"anomaly_probability": decision.get("anomaly_probability")},
        )
    )
    checks.append(
        _check(
            "not a calibrated Bayesian posterior" not in str(decision.get("rationale")),
            "calibrated rationale removes raw disclaimer",
            {"rationale": decision.get("rationale")},
        )
    )

    fallback = calibration.apply_calibration_to_probability(0.72, None)
    checks.append(
        _check(
            fallback["calibration_applied"] is False
            and fallback["calibrated_probability"] == fallback["raw_probability"],
            "missing state keeps raw probability",
            fallback,
        )
    )

    insufficient = calibration.apply_calibration_to_probability(
        0.72,
        {
            "posterior": {"alpha": 3.0, "beta": 1.0, "mean": 0.75},
            "metrics": {"sample_count": 2, "confidence_score": 1.0},
        },
    )
    checks.append(
        _check(
            insufficient["calibration_reason"] == "insufficient_calibration_data",
            "insufficient samples keep honest fallback",
            insufficient,
        )
    )

    parent = {
        "calibration_group": calibration.global_calibration_group("forecast_weighted"),
        "posterior": {"alpha": 71.0, "beta": 31.0, "mean": 71 / 102},
        "metrics": {"sample_count": 100},
    }
    prior = calibration.derive_partial_pooling_prior(
        parent,
        parent_calibration_group=parent["calibration_group"],
        prior_source="global",
    )
    recomputed = calibration.recompute_state(
        [{"actual_status": "hit", "predicted_metric": "forecast_weighted", "predicted_probability": 0.8}],
        calibration_group=group,
        prior=prior,
    )
    checks.append(
        _check(
            recomputed["metrics"].get("partial_pooling_applied") is True,
            "partial pooling metadata survives recompute",
            recomputed["metrics"],
        )
    )

    rls_sql = (REPO / "infra/init/99r_bayesian_calibration.sql").read_text(
        encoding="utf-8"
    )
    checks.append(
        _check(
            "USING (true)" not in rls_sql
            and "WITH CHECK (true)" not in rls_sql
            and "FORCE ROW LEVEL SECURITY" in rls_sql,
            "calibration RLS remains fail-closed",
        )
    )

    status = "PASS" if all(check["status"] == "PASS" for check in checks) else "FAIL"
    print(json.dumps({"status": status, "checks": checks}, indent=2, sort_keys=True))
    return 0 if status == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
