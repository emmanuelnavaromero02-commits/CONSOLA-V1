from __future__ import annotations

from unittest.mock import AsyncMock

from app.services import control_room_service
from app.services.mcp_gateway import adapters, catalog, openapi
from ia_gateway_fixtures import GatewayHarness, gateway  # noqa: F401


ACTION = "consultar_control_room_resumen"


def _run(gateway: GatewayHarness) -> dict:
    token = gateway.issue(scopes=("lectura",))
    response = gateway.client.post(
        f"/api/ia/v1/actions/{ACTION}", json={}, headers=gateway.bearer(token)
    )
    assert response.status_code == 200, response.text
    return response.json()


def _summary(monkeypatch, payload: dict) -> None:
    async def summary(user):
        return payload

    monkeypatch.setattr(adapters, "control_room_summary", summary)


def test_open_signals_are_reported_apart_from_anomalies(gateway: GatewayHarness, monkeypatch):
    _summary(
        monkeypatch,
        {
            "total_anomalies": 0,
            "open_decisions": 0,
            "intelligence_signals": {"total": 3, "critical": 0, "high": 1, "medium": 2, "low": 0},
        },
    )

    body = _run(gateway)

    assert body["datos"]["anomalias_totales"] == 0
    assert body["datos"]["senales_inteligencia_abiertas"] == {
        "total": 3,
        "criticas": 0,
        "altas": 1,
        "medias": 2,
        "bajas": 0,
    }
    assert body["resumen"] == (
        "0 anomalías, 3 señales de inteligencia abiertas (1 alta, 2 medias) y "
        "0 decisiones abiertas en el Control Room. Que no haya anomalías no significa "
        "que no pase nada: hay señales abiertas por revisar."
    )


def test_summary_without_open_signals_states_zero_explicitly(gateway: GatewayHarness, monkeypatch):
    _summary(
        monkeypatch,
        {
            "total_anomalies": 1,
            "open_decisions": 1,
            "intelligence_signals": {"total": 0, "critical": 0, "high": 0, "medium": 0, "low": 0},
        },
    )

    body = _run(gateway)

    assert body["datos"]["senales_inteligencia_abiertas"]["total"] == 0
    assert body["resumen"] == (
        "1 anomalía, 0 señales de inteligencia abiertas y 1 decisión abierta en el Control Room."
    )


def test_missing_signal_counts_are_reported_as_unknown_not_zero(gateway: GatewayHarness, monkeypatch):
    _summary(monkeypatch, {"total_anomalies": 0, "open_decisions": 0})

    body = _run(gateway)

    assert body["datos"]["senales_inteligencia_abiertas"] == {
        "total": None,
        "criticas": None,
        "altas": None,
        "medias": None,
        "bajas": None,
    }
    assert "0 señales" not in body["resumen"]
    assert body["resumen"].endswith("Señales de inteligencia abiertas: sin información.")


def test_gateway_reads_signal_counts_through_the_public_summary_projection(
    gateway: GatewayHarness, monkeypatch
):
    monkeypatch.setattr(
        control_room_service,
        "summary",
        AsyncMock(
            return_value={
                "total_anomalies": 0,
                "open_decisions": 0,
                "intelligence_signals": {
                    "total": 1,
                    "critical": 1,
                    "workspace_id": "private-workspace",
                },
            }
        ),
    )

    body = _run(gateway)

    assert body["datos"]["senales_inteligencia_abiertas"] == {
        "total": 1,
        "criticas": 1,
        "altas": 0,
        "medias": 0,
        "bajas": 0,
    }
    assert "1 señal de inteligencia abierta (1 crítica)" in body["resumen"]
    assert "private-workspace" not in str(body)


def test_unknown_signals_from_the_public_projection_are_not_reported_as_zero(
    gateway: GatewayHarness, monkeypatch
):
    monkeypatch.setattr(
        control_room_service,
        "summary",
        AsyncMock(
            return_value={
                "total_anomalies": 0,
                "open_decisions": 0,
                "intelligence_signals": None,
            }
        ),
    )

    body = _run(gateway)

    assert body["datos"]["senales_inteligencia_abiertas"]["total"] is None
    assert "0 señales" not in body["resumen"]
    assert body["resumen"] == (
        "0 anomalías y 0 decisiones abiertas en el Control Room. "
        "Señales de inteligencia abiertas: sin información."
    )


def test_catalog_and_openapi_describe_signals_as_a_separate_field():
    description = catalog.get_action(ACTION).description
    assert "anomalías de fuentes listas" in description
    assert "señales de inteligencia abiertas por severidad (en un campo aparte)" in description
    assert len(description) <= catalog.MAX_DESCRIPTION_CHARS

    document = openapi.build_document(url="https://consola.example.test/api/ia/v1")
    operation = document["paths"][f"/actions/{ACTION}"]["post"]
    assert operation["description"] == description
    assert operation["x-openai-isConsequential"] is False
