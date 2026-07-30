from __future__ import annotations

import pytest
from fastapi import HTTPException

from app.services.intelligence import monte_carlo_service


def _payload() -> dict:
    return {
        "source_type": "signal",
        "source_id": "signal-a",
        "iterations": 20,
        "seed": 7,
        "input_variables": {
            "baseline_value": {"type": "fixed", "value": 100},
            "expected_delta": {"type": "fixed", "value": 10},
        },
        "output_metric": "net_value",
    }


@pytest.mark.parametrize(
    "app_env", [None, "", "production", "prod", "staging", "unknown", "dev", "testing"]
)
def test_manual_fixture_rejects_nonlocal_env_even_with_synthetic_flag(
    monkeypatch, app_env
):
    if app_env is None:
        monkeypatch.delenv("APP_ENV", raising=False)
    else:
        monkeypatch.setenv("APP_ENV", app_env)
    monkeypatch.setenv("MONTE_CARLO_ALLOW_SYNTHETIC", "true")
    with pytest.raises(HTTPException) as exc:
        monte_carlo_service._validate_payload(
            {**_payload(), "source_type": "manual_fixture", "source_id": "fixture"}
        )
    assert exc.value.status_code == 403


@pytest.mark.parametrize("app_env", ["test", "local", "development"])
def test_manual_fixture_requires_local_env_and_is_marked_as_assumption(
    monkeypatch, app_env
):
    monkeypatch.setenv("APP_ENV", app_env)
    monkeypatch.delenv("MONTE_CARLO_ALLOW_SYNTHETIC", raising=False)
    clean = monte_carlo_service._validate_payload(
        {
            **_payload(),
            "source_type": "manual_fixture",
            "source_id": "fixture",
            "assumptions": {
                "scenario": "manual",
                "input_classification": "observed",
                "observed": True,
                "calibration_status": "calibrated",
            },
        }
    )
    assert clean["source_type"] == "manual_fixture"
    assert clean["assumptions"] == {
        "scenario": "manual",
        "input_classification": "scenario_assumption",
        "observed": False,
        "calibration_status": "not_calibrated",
    }


def test_signal_inputs_without_calibration_provenance_fail_closed():
    clean = monte_carlo_service._validate_payload(
        {
            **_payload(),
            "evidence_refs": [{"type": "signal", "id": "signal-a"}],
            "assumptions": {"scenario": "capacity_plan"},
        }
    )

    assert clean["evidence_refs"] == [{"type": "signal", "id": "signal-a"}]
    assert clean["assumptions"] == {
        "scenario": "capacity_plan",
        "input_classification": "scenario_assumption",
        "observed": False,
        "calibration_status": "not_calibrated",
    }


def test_each_option_is_a_not_calibrated_scenario_assumption():
    clean = monte_carlo_service._validate_payload(
        {
            **_payload(),
            "options": [
                {
                    "option_id": "different",
                    "assumptions": {
                        "scenario": "different",
                        "observed": True,
                        "calibration_status": "calibrated",
                    },
                }
            ],
        }
    )

    assert clean["options"][0]["assumptions"] == {
        "scenario": "different",
        "input_classification": "scenario_assumption",
        "observed": False,
        "calibration_status": "not_calibrated",
    }


def test_duplicate_option_identity_fails_closed():
    payload = {
        **_payload(),
        "options": [
            {"option_id": "same", "assumptions": {"scenario": "first"}},
            {"option_id": "same", "assumptions": {"scenario": "second"}},
        ],
    }

    with pytest.raises(HTTPException) as exc:
        monte_carlo_service._validate_payload(payload)

    assert exc.value.status_code == 422


@pytest.mark.asyncio
async def test_manual_fixture_is_never_a_real_historical_source():
    assert not await monte_carlo_service._source_exists(
        object(),
        workspace_id="ws-a",
        source_type="manual_fixture",
        source_id="fixture",
    )


class _HistoricalManualSignal:
    async def fetchrow(self, sql: str, *params):
        return {
            "signal_subtype": "observed",
            "source_system": "manual_fixture",
            "source_dataset": "historical_manual",
            "evidence_pack_id": "ep-manual",
            "metadata": {"input_classification": "scenario_assumption"},
        }

    async def fetchval(self, sql: str, *params):
        return 1


class _MissingSourceProvenance:
    async def fetchrow(self, sql: str, *params):
        return {"signal_subtype": "observed", "metadata": {}}


@pytest.mark.asyncio
async def test_historical_manual_signal_cannot_pass_as_real_source():
    assert not await monte_carlo_service._source_exists(
        _HistoricalManualSignal(),
        workspace_id="ws-a",
        source_type="signal",
        source_id="signal-manual",
    )


@pytest.mark.asyncio
async def test_signal_with_missing_source_provenance_fails_closed():
    assert not await monte_carlo_service._source_exists(
        _MissingSourceProvenance(),
        workspace_id="ws-a",
        source_type="signal",
        source_id="signal-missing-provenance",
    )


def test_wisdom_bit_source_is_allowlisted_without_synthetic_flag(monkeypatch):
    monkeypatch.setenv("APP_ENV", "production")
    clean = monte_carlo_service._validate_payload(
        {**_payload(), "source_type": "wisdom_bit", "source_id": "WB-TALENTO"}
    )

    assert clean["source_type"] == "wisdom_bit"
    assert clean["source_id"] == "WB-TALENTO"
