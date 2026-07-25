from __future__ import annotations

import pytest

from app.services.control_room.business_experience import build_business_experience
from app.services.control_room.business_surface_identity import (
    resolve_business_surface_identity,
)
from app.services.control_room.business_visible_copy import (
    MAX_VISIBLE_COPY_SCAN_LENGTH,
    VisibleCopyCause,
    classify_visible_business_copy,
)
from control_room_surface_fixtures import business_item, snapshot


def _classify(value: object, *, max_length: int = 240):
    item = business_item(
        module_id="talent_cpa_scores",
        source_dataset="talent_cpa_scores",
    )
    identity = resolve_business_surface_identity(item)
    assert identity is not None
    return classify_visible_business_copy(
        value,
        item=item,
        identity=identity,
        max_length=max_length,
    )


@pytest.mark.parametrize(
    "diagnostic",
    (
        "dataset no materializado ahora",
        "Dataset HR no materializado",
        "Error HTTP 503",
        "Access denied",
        "Permission denied",
        "Sin datos disponibles",
        "No hay datos",
        "faltan datos todavía",
        "faltan datos de SuccessFactors",
        "source unavailable now",
        "source_state missing refresh pending",
        "status=missing",
        "Error: timeout while contacting payroll",
        "diagnostic=materializer timeout",
        "status=ready",
        "data_status=degraded",
        "result.code=E42",
    ),
)
def test_explicit_diagnostic_signatures_are_rejected(diagnostic: str) -> None:
    result = _classify(diagnostic)

    assert result.allowed is False
    assert result.cause is VisibleCopyCause.DIAGNOSTIC_ONLY


@pytest.mark.parametrize(
    "business_copy",
    (
        "Datos para decisiones",
        "Operación completa",
        "Fuente operativa",
        "Contrato disponible",
        "Datos de rotación para decisiones",
        "Contrato para operación completa",
        "Fuente operativa para decisiones",
        "Tasa de error: 2%",
        "Estado de resultados 2026",
        "ROI=18%",
        "Margen = 24%",
    ),
)
def test_business_copy_is_not_rejected_by_diagnostic_word_bags(
    business_copy: str,
) -> None:
    result = _classify(business_copy)

    assert result.allowed is True
    assert result.text == business_copy


@pytest.mark.parametrize(
    "technical",
    (
        "sap:talent_cpa_scores",
        "omega:talent_cpa_scores",
        "vendor:talent_cpa_scores",
        "sap：talent_cpa_scores",
        "sap꞉talent_cpa_scores",
        "sap﹕talent_cpa_scores",
        "sap∶talent_cpa_scores",
        "vendorːtalent_cpa_scores",
        "vendor˸talent_cpa_scores",
        "vendor։talent_cpa_scores",
        "vendor׃talent_cpa_scores",
        "vendor⁚talent_cpa_scores",
    ),
)
def test_colon_namespaces_cannot_publish_technical_ids(technical: str) -> None:
    result = _classify(technical)

    assert result.allowed is False
    assert result.cause is VisibleCopyCause.TECHNICAL_IDENTIFIER


@pytest.mark.parametrize(
    "business_copy",
    (
        "Resumen: talent_cpa_scores vigente",
        "Indicador: rotación voluntaria",
    ),
)
def test_business_colon_copy_remains_visible_when_suffix_is_not_exact_id(
    business_copy: str,
) -> None:
    result = _classify(business_copy)

    assert result.allowed is True
    assert result.text == business_copy


@pytest.mark.parametrize(
    "structured",
    (
        '{"status":"missing"} raw',
        'raw {"status":"missing"}',
        '```json\n{"status":"missing"}\n```',
        "{'status':'missing'}",
        "[{'status': 'missing'}]",
        "raw {'status':'missing'} trailing",
        '{"status":"ready"',
        'raw:{"status":"ready"',
        "{'status':'ready'",
        '["ready","missing"',
        "status=missing",
        "payload status=missing retry",
    ),
)
def test_partial_structured_and_diagnostic_payloads_are_rejected(
    structured: str,
) -> None:
    result = _classify(structured)

    assert result.allowed is False
    assert result.cause in {
        VisibleCopyCause.STRUCTURED,
        VisibleCopyCause.DIAGNOSTIC_ONLY,
        VisibleCopyCause.UNSAFE_UNICODE,
    }


@pytest.mark.parametrize(
    "business_copy",
    (
        "Rotación [México]",
        "Crecimiento {estimado}",
        "[Preliminar] Rotación voluntaria",
    ),
)
def test_legitimate_bracketed_business_copy_remains_visible(
    business_copy: str,
) -> None:
    result = _classify(business_copy)

    assert result.allowed is True
    assert result.text == business_copy


def test_structured_scan_near_limit_is_bounded_and_fail_closed() -> None:
    payload = '{"status":"missing"}'
    value = f"{'x' * (MAX_VISIBLE_COPY_SCAN_LENGTH - len(payload) - 1)} {payload}"
    assert len(value) == MAX_VISIBLE_COPY_SCAN_LENGTH

    result = _classify(value)
    too_long = _classify(f"{value}x")

    assert result.cause is VisibleCopyCause.STRUCTURED
    assert too_long.cause is VisibleCopyCause.TOO_LONG


@pytest.mark.parametrize(
    "uri",
    (
        "postgres://:s3cr3t@localhost/db",
        "postgres://:s3cr3t@127.0.0.1/db",
        "POSTGRES://:s3cr3t@localhost/db",
    ),
)
def test_uri_with_empty_username_and_nonempty_password_is_sensitive(uri: str) -> None:
    result = _classify(uri)

    assert result.allowed is False
    assert result.cause is VisibleCopyCause.SENSITIVE


def test_uri_without_credentials_remains_visible() -> None:
    value = "postgres://127.0.0.1/db"

    result = _classify(value)

    assert result.allowed is True
    assert result.text == value


@pytest.mark.parametrize("mark", ("\u0903", "\u0488"))
def test_all_unicode_mark_categories_are_removed_for_technical_comparison(
    mark: str,
) -> None:
    result = _classify(f"talent{mark}_cpa_scores")

    assert result.allowed is False
    assert result.cause is VisibleCopyCause.TECHNICAL_IDENTIFIER


def test_unsafe_hazard_title_drops_fact_and_parent_section() -> None:
    item = business_item(title='raw {"status":"missing"}')

    assert build_business_experience(snapshot(items=(item,))).sections == []
