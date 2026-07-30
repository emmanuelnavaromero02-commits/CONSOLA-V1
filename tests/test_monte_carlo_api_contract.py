from __future__ import annotations

from pathlib import Path

import pytest
from fastapi import HTTPException

from app.services.intelligence import market_context
from app.services.intelligence import monte_carlo_service


REPO = Path(__file__).resolve().parents[1]


def _payload():
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


def test_monte_carlo_router_exposes_scoped_endpoints_and_csrf_guard():
    router = (REPO / "console/app/routers/intelligence.py").read_text(encoding="utf-8")

    assert '"/monte-carlo/run"' in router
    assert '"/monte-carlo/{simulation_id}"' in router
    assert '@router.get("/monte-carlo"' in router
    assert '@v1_router.get(\n    "/monte-carlo"' in router
    assert "MonteCarloRunRequest(_StrictModel)" in router
    assert "use_external_market_context" in router
    assert 'Depends(require_permission("control_room.write"))' in router
    assert 'Depends(require_permission("datasets.read"))' in router
    assert "Depends(require_csrf)" in router
    assert "tenant_id" in router and "scope variables are not accepted" in router


def test_monte_carlo_service_uses_scoped_db_and_blocks_scope_payloads():
    service = (
        REPO / "console/app/services/intelligence/monte_carlo_service.py"
    ).read_text(encoding="utf-8")

    assert "from app.services.db_scope import scoped_db_for_user" in service
    assert "resolve_market_context_inputs" in service
    assert "async with scoped_db_for_user(pool, user)" in service
    assert "tenant_id" in service
    assert "security_context" in service

    with pytest.raises(HTTPException):
        monte_carlo_service._validate_payload({**_payload(), "workspace_id": "ws-b"})
    with pytest.raises(HTTPException) as spoofed:
        monte_carlo_service._validate_payload(
            {**_payload(), "model_version": "monte_carlo.validated.v999"}
        )
    assert spoofed.value.status_code == 422
    assert "server-owned" in str(spoofed.value.detail)
    with pytest.raises(HTTPException):
        monte_carlo_service._validate_payload(
            {
                **_payload(),
                "input_variables": {
                    "tenant_id": {"type": "fixed", "value": 1},
                },
            }
        )
    with pytest.raises(HTTPException):
        monte_carlo_service._validate_payload(
            {
                **_payload(),
                "options": [
                    {
                        "option_id": "bad",
                        "input_variables": {
                            "security_context": {"type": "fixed", "value": 1},
                        },
                    }
                ],
            }
        )


@pytest.mark.asyncio
async def test_market_context_variable_requires_explicit_opt_in():
    payload = {
        **_payload(),
        "input_variables": {
            "cost_per_day": {
                "type": "external_market_context",
                "metric_name": "usd_mxn_fix",
            }
        },
    }

    with pytest.raises(HTTPException) as exc:
        await market_context.resolve_market_context_inputs(payload, {"id": 1})

    assert exc.value.status_code == 422


@pytest.mark.asyncio
async def test_market_context_variable_resolves_to_distribution(monkeypatch):
    async def fake_query(dataset, user, limit=200):
        assert dataset == "banxico_market_context"
        return [
            {
                "metric_name": "usd_mxn_fix",
                "value": "18.50",
                "unit": "mxn_per_usd",
                "as_of": "2026-07-10",
                "usable": True,
                "freshness_status": "ready",
                "confidence": "0.95",
                "source_authority": "Banxico",
                "source_host": "www.banxico.org.mx",
                "payload_hash": "abc123payload",
                "request_hash": "req123",
            }
        ]

    monkeypatch.setattr(market_context, "query_gold_dataset_rows", fake_query)
    clean = await market_context.resolve_market_context_inputs(
        {
            **_payload(),
            "use_external_market_context": True,
            "input_variables": {
                "cost_per_day": {
                    "type": "external_market_context",
                    "metric_name": "usd_mxn_fix",
                    "uncertainty_pct": "0.10",
                }
            },
        },
        {"id": 1, "tenant_id": "tenant-a", "active_workspace_id": "ws-a"},
    )

    variable = clean["input_variables"]["cost_per_day"]
    assert variable["type"] == "triangular"
    assert variable["low"] == 16.65
    assert variable["mode"] == 18.5
    assert variable["high"] == 20.35
    assert clean["evidence_refs"][0]["type"] == "market_context"
    assumption = clean["assumptions"]["external_market_context"][0]
    assert assumption["metric_name"] == "usd_mxn_fix"
    assert assumption["unit"] == "mxn_per_usd"
    assert "source_url" not in assumption
    assert "token" not in assumption


@pytest.mark.asyncio
async def test_market_context_variable_rejects_low_confidence(monkeypatch):
    async def fake_query(dataset, user, limit=200):
        assert dataset == "banxico_market_context"
        return [
            {
                "metric_name": "usd_mxn_fix",
                "value": "18.50",
                "as_of": "2026-07-10",
                "usable": True,
                "freshness_status": "ready",
                "confidence": "0.79",
            }
        ]

    monkeypatch.setattr(market_context, "query_gold_dataset_rows", fake_query)

    with pytest.raises(HTTPException) as exc:
        await market_context.resolve_market_context_inputs(
            {
                **_payload(),
                "use_external_market_context": True,
                "input_variables": {
                    "cost_per_day": {
                        "type": "external_market_context",
                        "metric_name": "usd_mxn_fix",
                    }
                },
            },
            {"id": 1, "tenant_id": "tenant-a", "active_workspace_id": "ws-a"},
        )

    assert exc.value.status_code == 422
    assert "confidence is too low" in str(exc.value.detail)


@pytest.mark.asyncio
async def test_market_context_variable_requires_provider_in_workspace_scope(
    monkeypatch,
):
    monkeypatch.setenv("APP_ENV", "production")

    async def unexpected_query(*args, **kwargs):
        raise AssertionError("provider scope must be checked before Gold read")

    monkeypatch.setattr(market_context, "query_gold_dataset_rows", unexpected_query)

    with pytest.raises(HTTPException) as exc:
        await market_context.resolve_market_context_inputs(
            {
                **_payload(),
                "use_external_market_context": True,
                "input_variables": {
                    "cost_per_day": {
                        "type": "external_market_context",
                        "metric_name": "usd_mxn_fix",
                    }
                },
            },
            {"id": 1, "allowed_cartridges": ["sap_successfactors"]},
        )

    assert exc.value.status_code == 403
    assert "provider not allowed" in str(exc.value.detail)


def test_mcp_monte_carlo_tool_exposes_external_market_opt_in():
    source = (REPO / "mcp-infra/app/tools/control_room.py").read_text(encoding="utf-8")

    assert "use_external_market_context" in source
    assert '"use_external_market_context": bool(use_external_market_context)' in source
