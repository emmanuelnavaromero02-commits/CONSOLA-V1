from __future__ import annotations

from fastapi import FastAPI

from app.routers import control_room as routes
from app.schemas.control_room_diagnostic_enums import (
    DiagnosticInstallationStatus,
    DiagnosticItemKind,
    DiagnosticItemStatus,
    DiagnosticReadinessStatus,
    DiagnosticSourceStatus,
)
from app.services.control_room.diagnostic_redaction import redact_diagnostic_value
from app.services.control_room.operational_diagnostics import (
    build_operational_diagnostics,
)
from control_room_surface_fixtures import (
    installation,
    snapshot,
    source_state,
    source_status,
)


def _source_payload(**updates: object) -> dict[str, object]:
    response = build_operational_diagnostics(
        snapshot(sources=(source_status(**updates),))
    )
    return response.model_dump(mode="json", exclude_none=True)["sources"][0]


def test_absent_null_blank_bool_invalid_and_negative_counts_are_unknown():
    cases = (
        {},
        {"count": None},
        {"count": ""},
        {"count": False},
        {"count": "0"},
        {"count": -1},
        {"count": 0.5},
    )

    for updates in cases:
        row = source_status(**updates)
        if not updates:
            row.pop("count")
        payload = build_operational_diagnostics(snapshot(sources=(row,))).model_dump(
            mode="json", exclude_none=True
        )["sources"][0]
        assert "count" not in payload


def test_zero_requires_explicit_successful_dated_evaluation():
    assert "count" not in _source_payload(
        status="missing",
        data_readiness="missing",
        count=0,
    )
    assert (
        _source_payload(
            status="ok",
            data_readiness="ready",
            count=0,
        )["count"]
        == 0
    )
    assert "count" not in _source_payload(
        status="ok",
        data_readiness="ready",
        count=0,
        checked_at=None,
    )
    assert (
        _source_payload(
            status="missing",
            data_readiness="missing",
            count=7,
        )["count"]
        == 7
    )


def test_camel_pascal_cloud_and_pii_keys_are_redacted_by_key():
    keys = (
        "clientSecret",
        "client-secret",
        "access.token",
        "private key",
        "accessToken",
        "refreshToken",
        "sessionToken",
        "privateKey",
        "apiKey",
        "awsAccessKeyId",
        "awsSecretAccessKey",
        "AccessKeyId",
        "accountKey",
        "secretAccessKey",
        "authorizationHeader",
        "databaseUrl",
        "connectionString",
        "serviceAccountKey",
        "azureStorageAccountKey",
        "gcpPrivateKeyData",
        "subscriptionKey",
        "phoneNumber",
        "ownerUserId",
        "taxId",
    )
    opaque = {
        key: {"nested": ["opaque-value", {"value": "still-opaque"}]} for key in keys
    }
    payload = redact_diagnostic_value(
        {
            "safeField": "visible",
            "nested": opaque,
            "items": [opaque],
        }
    )

    assert payload["safeField"] == "visible"
    for container in (payload["nested"], payload["items"][0]):
        assert set(container) == set(keys)
        assert all(value == "[REDACTED]" for value in container.values())

    surface = _source_payload(
        readiness_blockers=[{"accountKey": "blocker-opaque"}],
        contract_warnings=[{"AccessKeyId": "warning-opaque"}],
    )
    assert "blocker-opaque" not in str(surface)
    assert "warning-opaque" not in str(surface)


def test_unknown_states_fail_closed_and_runtime_aliases_are_canonical():
    unknown = build_operational_diagnostics(
        snapshot(
            sources=(source_status(status="banana", data_readiness="banana"),),
            diagnostics=(
                source_state(
                    kind="banana",
                    status="banana",
                    data_status="banana",
                    readiness_status="banana",
                    source_status="banana",
                ),
            ),
            installations=(installation(installation_status="banana"),),
        )
    ).model_dump(mode="json", exclude_none=True)

    assert unknown["sources"][0]["status"] == "unknown"
    assert unknown["sources"][0]["data_readiness"] == "unknown"
    assert unknown["diagnostic_items"][0]["kind"] == "unknown"
    assert unknown["diagnostic_items"][0]["status"] == "unknown"
    assert unknown["diagnostic_items"][0]["data_status"] == "unknown"
    assert unknown["diagnostic_items"][0]["readiness_status"] == "unknown"
    assert unknown["diagnostic_items"][0]["source_status"] == "unknown"
    assert unknown["installations"][0]["status"] == "unknown"

    aliases = build_operational_diagnostics(
        snapshot(
            diagnostics=(
                source_state(
                    data_status="gold_ready",
                    readiness_status="metadata_ready",
                    source_status="permission_denied",
                ),
            ),
            installations=(installation(installation_status="active"),),
        )
    ).model_dump(mode="json", exclude_none=True)
    item = aliases["diagnostic_items"][0]
    assert item["data_status"] == "ready"
    assert item["readiness_status"] == "partial"
    assert item["source_status"] == "no_permission"
    assert aliases["installations"][0]["status"] == "ready"


def test_openapi_exposes_closed_diagnostic_enums():
    app = FastAPI()
    app.include_router(routes.router)
    schemas = app.openapi()["components"]["schemas"]
    expected = {
        "DiagnosticSourceStatus": DiagnosticSourceStatus,
        "DiagnosticReadinessStatus": DiagnosticReadinessStatus,
        "DiagnosticItemKind": DiagnosticItemKind,
        "DiagnosticItemStatus": DiagnosticItemStatus,
        "DiagnosticInstallationStatus": DiagnosticInstallationStatus,
    }

    for name, enum_type in expected.items():
        assert set(schemas[name]["enum"]) == {value.value for value in enum_type}
