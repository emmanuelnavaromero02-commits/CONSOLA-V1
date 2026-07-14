from __future__ import annotations

from pathlib import Path

import pytest
from fastapi import HTTPException

from app.services.intelligence import calibration_service
from app.services.intelligence.evidence_refs import attach_external_evidence_metadata


REPO = Path(__file__).resolve().parents[1]


def _calibration_payload() -> dict:
    return {
        "source_type": "monte_carlo_simulation",
        "source_id": "mc-abc",
        "predicted_metric": "talent_readiness_delta",
        "predicted_probability": 0.62,
        "actual_status": "hit",
        "calibration_group": "talent",
        "evidence_refs": [
            {"type": "market_context", "id": "banxico:usd_mxn_fix:2026-07-10:abc123"}
        ],
    }


def test_bayes_accepts_market_context_refs_as_evidence_only():
    clean = calibration_service._validate_payload(_calibration_payload())

    assert clean["predicted_probability"] == 0.62
    assert clean["evidence_refs"] == [
        {"type": "market_context", "id": "banxico:usd_mxn_fix:2026-07-10:abc123"}
    ]

    metrics = attach_external_evidence_metadata({"sample_count": 1}, clean["evidence_refs"])

    assert metrics["sample_count"] == 1
    assert metrics["external_evidence"]["market_context_policy"] == "evidence_only"
    assert metrics["external_evidence"]["market_context_ref_count"] == 1


def test_bayes_does_not_accept_market_context_as_observation_source():
    with pytest.raises(HTTPException) as exc:
        calibration_service._validate_payload(
            {
                **_calibration_payload(),
                "source_type": "market_context",
                "source_id": "banxico:usd_mxn_fix",
            }
        )

    assert exc.value.status_code == 422
    assert "unsupported source_type" in str(exc.value.detail)


def test_calibration_bayesian_state_mcp_remains_read_only():
    main = (REPO / "mcp-infra/app/main.py").read_text(encoding="utf-8")
    read_section = main.split("_CONTROL_ROOM_READ_TOOLS = {", 1)[1].split("}", 1)[0]
    analysis_section = main.split("_CONTROL_ROOM_ANALYSIS_TOOLS = {", 1)[1].split("}", 1)[0]

    assert '"calibration__bayesian_state"' in read_section
    assert '"calibration__bayesian_state"' not in analysis_section
