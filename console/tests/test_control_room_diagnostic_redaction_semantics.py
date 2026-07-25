from __future__ import annotations

import json

import pytest
from fastapi import FastAPI

from app.routers import control_room as routes
from app.services.control_room.diagnostic_redaction import redact_diagnostic_value
from app.services.control_room.operational_diagnostics import (
    build_operational_diagnostics,
)
from control_room_surface_fixtures import snapshot, source_status


SENSITIVE_KEYS = (
    "apikey",
    "clientsecret",
    "accesskeyid",
    "apiKeys",
    "clientSecrets",
    "accessTokens",
    "refreshTokens",
    "connectionStrings",
    "SecretString",
    "SecretBinary",
    "secretValues",
    "privateKeys",
    "accountKeys",
    "credentialsList",
    "tokens",
    "keys",
    "secrets",
    "credentials",
    "strings",
    "binaries",
    "azure.client-secrets",
    "gcp service account key",
    "AWS_SECRET_ACCESS_KEY",
)


class UnsafeString:
    def __str__(self) -> str:
        raise AssertionError("arbitrary objects must not be stringified")


def _source_payload(**updates: object) -> dict[str, object]:
    return build_operational_diagnostics(
        snapshot(sources=(source_status(**updates),))
    ).model_dump(mode="json", exclude_none=True)["sources"][0]


def test_compact_plural_and_cloud_container_keys_redact_entire_values() -> None:
    opaque_values = {
        key: {
            "mapping": {"opaque": f"mapping-{index}"},
            "list": [f"list-{index}"],
            "bytes": f"bytes-{index}".encode(),
        }
        for index, key in enumerate(SENSITIVE_KEYS)
    }

    redacted = redact_diagnostic_value(opaque_values)

    assert set(redacted) == set(SENSITIVE_KEYS)
    assert all(value == "[REDACTED]" for value in redacted.values())
    serialized = json.dumps(redacted)
    for index in range(len(SENSITIVE_KEYS)):
        assert f"mapping-{index}" not in serialized
        assert f"list-{index}" not in serialized
        assert f"bytes-{index}" not in serialized


def test_separator_variants_and_nested_sensitive_containers_fail_closed() -> None:
    payload = redact_diagnostic_value(
        {
            "safe": "visible",
            "nested": [
                {"Client Secrets": ["opaque-one"]},
                {"access.tokens": {"value": "opaque-two"}},
                {"PRIVATE-KEYS": b"opaque-three"},
            ],
        }
    )

    assert payload == {
        "safe": "visible",
        "nested": [
            {"Client Secrets": "[REDACTED]"},
            {"access.tokens": "[REDACTED]"},
            {"PRIVATE-KEYS": "[REDACTED]"},
        ],
    }


@pytest.mark.parametrize(
    ("message", "secret"),
    (
        ("Authorization=Basic dXNlcjpwYXNz", "dXNlcjpwYXNz"),
        (
            "privateKey=-----BEGIN PRIVATE KEY-----\nopaque-body\n"
            "-----END PRIVATE KEY-----",
            "opaque-body",
        ),
        ("GOOGLE_APPLICATION_CREDENTIALS_JSON=gcp-opaque", "gcp-opaque"),
        ("tokensByRegion=token-opaque", "token-opaque"),
        ("awsAccessKeyIds=aws-opaque", "aws-opaque"),
        ("apiKeys[0]=api-opaque", "api-opaque"),
    ),
)
def test_freeform_sensitive_assignments_redact_complete_values(
    message: str,
    secret: str,
) -> None:
    redacted = redact_diagnostic_value(message)

    assert secret not in str(redacted)
    assert "[REDACTED]" in str(redacted)


def test_known_token_counters_remain_visible() -> None:
    payload = redact_diagnostic_value(
        {
            "token_count": 7,
            "inputTokens": 11,
            "output_tokens": 13,
            "totalTokens": 31,
            "apiKeys": "opaque",
        }
    )

    assert payload == {
        "token_count": 7,
        "inputTokens": 11,
        "output_tokens": 13,
        "totalTokens": 31,
        "apiKeys": "[REDACTED]",
    }


def test_blockers_and_warnings_keep_only_sanitized_strings() -> None:
    payload = _source_payload(
        readiness_blockers=[
            "clientSecret=blocker-opaque",
            {"SecretString": "mapping-opaque"},
            ["list-opaque"],
            b"bytes-opaque",
            UnsafeString(),
            "Retry after 5 seconds",
        ],
        contract_warnings=[
            "SecretBinary: warning-opaque",
            "AWS_SECRET_ACCESS_KEY=aws-warning-opaque",
            "GOOGLE_APPLICATION_CREDENTIALS=gcp-warning-opaque",
            "AZURE_CLIENT_SECRET=azure-warning-opaque",
            "credentialsList=list-warning-opaque",
            {"accessTokens": ["nested-opaque"]},
            UnsafeString(),
            "Schema version changed",
        ],
    )
    serialized = json.dumps(payload)

    for secret in (
        "blocker-opaque",
        "mapping-opaque",
        "list-opaque",
        "bytes-opaque",
        "warning-opaque",
        "aws-warning-opaque",
        "gcp-warning-opaque",
        "azure-warning-opaque",
        "list-warning-opaque",
        "nested-opaque",
    ):
        assert secret not in serialized
    assert payload["blockers"] == [
        "clientSecret=[REDACTED]",
        "Retry after 5 seconds",
    ]
    assert payload["warnings"] == [
        "SecretBinary: [REDACTED]",
        "AWS_SECRET_ACCESS_KEY=[REDACTED]",
        "GOOGLE_APPLICATION_CREDENTIALS=[REDACTED]",
        "AZURE_CLIENT_SECRET=[REDACTED]",
        "credentialsList=[REDACTED]",
        "Schema version changed",
    ]


def test_sensitive_values_never_reach_payload_or_openapi_examples() -> None:
    opaque = "opaque-never-publish"
    payload = _source_payload(
        readiness_blockers=[f"apiKeys={opaque}"],
        contract_warnings=[{"credentialsList": [opaque]}],
    )
    app = FastAPI()
    app.include_router(routes.router)

    assert opaque not in json.dumps(payload)
    assert opaque not in json.dumps(app.openapi())
