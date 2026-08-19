"""T2b — watermarks de sap_hcm/sap_s4hana a prueba de /Date(ms)/ OData v2.

El bug (auditoría 2026-08-17, alto): el filtro incremental interpolaba el
watermark crudo entre comillas y el retroceso usaba fromisoformat con un
except silencioso que persistía el valor sin parsear. Con SAP serializando
Edm.DateTime como '/Date(1699999999000)/': watermark envenenado → siguiente
filtro rechazado (400) o comparado como texto → incrementales rotos o filas
perdidas en silencio.

El arreglo porta el patrón de sap_successfactors (la referencia evolucionada):
parsear → literal tipado datetime'...' → persistir SOLO ISO canónico →
fallback a full snapshot ante lo no parseable → guardia monotónica en el
ON CONFLICT del watermark_service.
"""
from __future__ import annotations

import importlib
import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
CARTRIDGES = ("sap_hcm", "sap_s4hana")


def _load_filters(cartridge: str):
    sys.path[:] = [p for p in sys.path if "/cartridges/" not in p]
    sys.path.insert(0, str(REPO_ROOT / "cartridges" / cartridge))
    for mod in [m for m in list(sys.modules) if m.startswith("app.")]:
        sys.modules.pop(mod, None)
    sys.modules.pop("app", None)
    return importlib.import_module("app.services.watermark_filters")


@pytest.mark.parametrize("cartridge", CARTRIDGES)
def test_parse_supports_sap_date_epoch_and_iso(cartridge):
    wf = _load_filters(cartridge)
    sap = wf.parse_watermark_datetime("/Date(1699999999000)/")
    assert sap == datetime.fromtimestamp(1699999999, tz=timezone.utc)
    # Con offset de zona SAP también parsea.
    assert wf.parse_watermark_datetime("/Date(1699999999000+0000)/") == sap
    assert wf.parse_watermark_datetime("1699999999") == sap  # epoch segundos
    assert wf.parse_watermark_datetime("1699999999000") == sap  # epoch ms
    iso = wf.parse_watermark_datetime("2023-11-14T22:13:19Z")
    assert iso == sap
    assert wf.parse_watermark_datetime("2023-11-14 22:13:19") == sap
    assert wf.parse_watermark_datetime("garbage-value") is None
    assert wf.parse_watermark_datetime("") is None


@pytest.mark.parametrize("cartridge", CARTRIDGES)
def test_value_classification(cartridge):
    wf = _load_filters(cartridge)
    assert wf.watermark_value_type("/Date(1699999999000)/") == "sap_date_ms"
    assert wf.watermark_value_type("1699999999") == "numeric"
    assert wf.watermark_value_type("2026-08-19T10:00:00Z") == "iso8601"
    assert wf.watermark_value_type("") == "empty"
    assert wf.watermark_value_type("PT?") == "unknown"


@pytest.mark.parametrize("cartridge", CARTRIDGES)
def test_literal_is_typed_and_rejects_future_or_garbage(cartridge):
    wf = _load_filters(cartridge)
    lit = wf.odata_datetime_literal("/Date(1699999999000)/")
    assert lit == "datetime'2023-11-14T22:13:19'"
    assert wf.odata_datetime_literal("2023-11-14T22:13:19Z") == lit
    # Un watermark futuro es un reloj envenenado: full snapshot, no filtro.
    assert wf.odata_datetime_literal("2999-01-01T00:00:00Z") is None
    assert wf.odata_datetime_literal("x'; DROP--") is None
    assert "'" not in "2023-11-14T22:13:19", "el valor jamás se interpola crudo"


@pytest.mark.parametrize("cartridge", CARTRIDGES)
def test_normalized_watermark_applies_backoff_and_fails_closed(cartridge):
    wf = _load_filters(cartridge)
    out = wf.normalized_watermark("/Date(1699999999000)/", backoff_minutes=5)
    assert out == "2023-11-14T22:08:19Z", "retroceso de 5 min sobre ISO canónico"
    assert wf.normalized_watermark("garbage") is None, (
        "no parseable → None → el llamador conserva el watermark anterior"
    )


@pytest.mark.parametrize("cartridge", CARTRIDGES)
def test_cross_format_ordering_is_by_time_not_text(cartridge):
    """El bug del max() por string: '/Date(...)/' < '2023-...' como TEXTO
    aunque sea más nuevo como TIEMPO. Parseado, el orden es el correcto."""
    wf = _load_filters(cartridge)
    newer_sap = wf.parse_watermark_datetime("/Date(1799999999000)/")
    older_iso = wf.parse_watermark_datetime("2023-11-14T22:13:19Z")
    assert newer_sap > older_iso
    assert str("/Date(1799999999000)/") < str("2023-11-14T22:13:19Z"), (
        "la comparación por texto invertía el orden — la evidencia del bug"
    )


@pytest.mark.parametrize("cartridge", CARTRIDGES)
def test_extraction_service_contract(cartridge):
    src = (
        REPO_ROOT / "cartridges" / cartridge / "app" / "services"
        / "extraction_service.py"
    ).read_text(encoding="utf-8")
    assert "gt '{watermark}'" not in src, "prohibida la interpolación cruda"
    assert "odata_datetime_literal(watermark)" in src
    assert "normalized_watermark(" in src
    assert "parse_watermark_datetime" in src
    assert "fromisoformat" not in src.replace(
        "watermark_filters", ""
    ), "el parseo vive solo en watermark_filters"


@pytest.mark.parametrize("cartridge", CARTRIDGES)
def test_watermark_service_update_is_monotonic(cartridge):
    """Una corrida atrasada que termina después de una nueva NO puede regresar
    el watermark (patrón que SuccessFactors ya tenía y los gemelos no)."""
    src = (
        REPO_ROOT / "cartridges" / cartridge / "app" / "services"
        / "watermark_service.py"
    ).read_text(encoding="utf-8")
    assert (
        "WHERE COALESCE(entity_watermarks.last_watermark_value, '') <= EXCLUDED.last_watermark_value"
        in src
    )
