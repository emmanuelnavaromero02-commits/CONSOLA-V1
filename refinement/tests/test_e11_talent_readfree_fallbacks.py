"""E1.1 — la cadena de talento degrada limpia cuando el tenant no expone fuentes.

Evidencia que fija este contrato: en el ciclo autonomo de las 03:05 (target=all)
8 datasets de talento terminaron 'materialization_failed' porque el fallback de
primer nivel LEE fuentes silver/gold que el tenant demo no expone: el real
fallaba con 404, el fallback enganchaba y se caia sobre el MISMO 404, y esa
segunda excepcion volaba sin red. Ademas, la proyeccion 'no disponible' de
readiness tiene OTRA forma que el SQL real de 9box, que tronaba con Binder
Error (invalid_score_input vs blocker_count).

El arreglo: proyecciones vacias SIN lecturas con esquema FIEL al SELECT final
real (cero filas = cero hechos fabricados), enganchadas SOLO ante dependencia
faltante. Un MinIO caido o un Binder por drift real siguen tronando fuerte.
"""
from __future__ import annotations

import duckdb
import pytest

from refinement.app.successfactors_fallbacks import (
    fallback_dataset_for_successfactors,
    is_missing_successfactors_dependency_error,
    readfree_empty_dataset_for_successfactors,
)
from refinement.app.successfactors_talent_readfree_fallbacks import (
    TALENT_READFREE_EMPTY_SQL,
)

# El texto EXACTO que produce duckdb httpfs contra MinIO cuando el objeto no
# existe (capturado del ciclo real 03:05) — el marcador '404 (not found)' debe
# seguir reconociendolo aunque duckdb cambie el resto del mensaje.
REAL_DUCKDB_404 = (
    'HTTP Error: Unable to connect to URL "http://minio:9000/lakehouse/silver/'
    "sap_successfactors/sap_successfactors_performance_cycle/tenant_id%3De75f"
    '/data.parquet": 404 (Not Found).'
)

SILVER_CURATED = [
    "sap_successfactors_performance_cycle",
    "sap_successfactors_employee_competency",
    "sap_successfactors_employee_aspiration",
    "sap_successfactors_role_requirements",
    "sap_successfactors_learning_completion",
    "sap_successfactors_job_application_pipeline",
    "sap_successfactors_movement_events",
    "sap_successfactors_recruitment_pipeline",
    "sap_successfactors_compensation_full",
]


def test_covers_every_silver_curated_talent_dataset_plus_cpa_roots():
    """Cobertura completa: los 9 silver curados (el orden que corre el ciclo
    con target=all) y las dos raices gold de la cadena C/P/A."""
    for name in SILVER_CURATED:
        assert name in TALENT_READFREE_EMPTY_SQL, name
    assert "sap_successfactors_talent_employee_profile" in TALENT_READFREE_EMPTY_SQL
    assert "sap_successfactors_talent_cpa_scores" in TALENT_READFREE_EMPTY_SQL


def test_projections_are_truly_readfree_bind_and_return_zero_rows():
    con = duckdb.connect()
    for name, sql in TALENT_READFREE_EMPTY_SQL.items():
        lowered = sql.lower()
        for banned in ("read_parquet", "s3://", "http://", "https://", "pggold"):
            assert banned not in lowered, f"{name} debe ser read-free ({banned})"
        cols = con.execute(f"DESCRIBE {sql}").fetchall()
        assert len(cols) >= 7, f"{name}: esquema sospechosamente chico"
        rows = con.execute(sql).fetchall()
        assert rows == [], f"{name}: la proyeccion vacia debe dar CERO filas"


def test_shapes_keep_downstream_real_sql_bindable():
    """Las columnas que los consumidores reales referencian existen en la
    proyeccion del upstream — el anti-Binder-Error. Espejo de:
    - talent_employee_profile lee performance_cycle(user_id_hash,
      performance_rating), employee_competency(user_id, proficiency_100),
      employee_aspiration(user_id, aspiration_100)
    - talent_cpa_scores lee employee_profile(competency_score,
      performance_score, aspiration_score, invalid_score_input, blockers)
    - talent_readiness lee cpa_scores(invalid_score_input, fit_score, ...)"""
    con = duckdb.connect()

    def columns(name: str) -> set[str]:
        return {
            row[0]
            for row in con.execute(
                f"DESCRIBE {TALENT_READFREE_EMPTY_SQL[name]}"
            ).fetchall()
        }

    assert {"user_id_hash", "performance_rating", "potential_rating"} <= columns(
        "sap_successfactors_performance_cycle"
    )
    assert {"user_id", "proficiency_100", "competency_status"} <= columns(
        "sap_successfactors_employee_competency"
    )
    assert {"user_id", "aspiration_100", "aspiration_status"} <= columns(
        "sap_successfactors_employee_aspiration"
    )
    assert {
        "competency_score",
        "performance_score",
        "aspiration_score",
        "invalid_score_input",
        "blockers",
        "cpa_status",
    } <= columns("sap_successfactors_talent_employee_profile")
    assert {
        "competency_100",
        "performance_100",
        "aspiration_100",
        "invalid_score_input",
        "fit_score",
        "cpa_status",
    } <= columns("sap_successfactors_talent_cpa_scores")


def test_marker_recognizes_the_real_duckdb_404_text():
    assert is_missing_successfactors_dependency_error(Exception(REAL_DUCKDB_404))


DUCKDB_12_OBJECT_404 = (
    "HTTP Error: HTTP GET error on '/lakehouse/silver/sap_successfactors/"
    "sap_successfactors_performance_cycle/tenant_id%3De75f/workspace_id%3D1a2b/"
    "data.parquet' (HTTP 404)"
)

S3_LISTING_400 = (
    "HTTP Error: HTTP GET error on '/?encoding-type=url&list-type=2&prefix="
    "silver%2Fsap_successfactors%2Fsap_successfactors_performance_cycle%2F"
    "tenant_id%3De75f%2F' (HTTP 400)"
)
S3_OBJECT_403 = (
    "HTTP Error: HTTP GET error on '/lakehouse/silver/sap_successfactors/"
    "sap_successfactors_performance_cycle/tenant_id%3De75f/data.parquet' (HTTP 403)"
)
S3_LISTING_404 = (
    "HTTP Error: HTTP GET error on '/?encoding-type=url&list-type=2&prefix="
    "silver%2F' (HTTP 404)"
)


def test_marker_recognizes_the_duckdb_12_object_404_text():
    assert is_missing_successfactors_dependency_error(Exception(DUCKDB_12_OBJECT_404))


@pytest.mark.parametrize(
    "infra_error",
    [
        S3_LISTING_400,
        S3_OBJECT_403,
        S3_LISTING_404,
        "HTTP Error: HTTP GET error on '/lakehouse/x.parquet' (HTTP 500)",
        "HTTP Error: HTTP GET error on '/lakehouse/x.parquet' (HTTP 416) "
        "This could mean the file was changed. Try disabling the duckdb http "
        "metadata cache if enabled, and confirm the server supports range requests.",
        "IO Error: Could not establish connection error for HTTP HEAD to "
        "'http://minio:9000/lakehouse/silver/x/data.parquet' with status 0",
        "IO Error: Connection timed out error for HTTP GET to "
        "'/lakehouse/?encoding-type=url&list-type=2&prefix=silver%2F'",
    ],
)
def test_lakehouse_failures_are_never_a_missing_dependency(infra_error):
    """The regression: 'http get error' alone matched a failed S3 listing, so a lakehouse outage published an empty."""
    exc = Exception(infra_error)
    assert not is_missing_successfactors_dependency_error(exc)
    ds = {"name": "sap_successfactors_performance_cycle", "sql_def": "SELECT 1"}
    assert readfree_empty_dataset_for_successfactors(ds, exc) is None
    profile = {"name": "sap_successfactors_talent_employee_profile", "sql_def": "SELECT 1"}
    assert fallback_dataset_for_successfactors(profile, exc) is None


def test_lakehouse_failure_raises_through_the_fallback_wrapper(monkeypatch):
    """End to end through main: a failed S3 listing must leave the wrapper as an exception, so the caller records an error."""
    main = pytest.importorskip("refinement.app.main")

    calls: list[str] = []

    class _Engine:
        def materialize(self, ds, user_context):
            calls.append(ds.get("sql_def") or "")
            raise RuntimeError(S3_LISTING_400)

    monkeypatch.setattr(main, "engine", _Engine())
    ds = {
        "name": "sap_successfactors_talent_employee_profile",
        "sql_def": "SELECT real",
        "description": "perfil",
    }
    with pytest.raises(RuntimeError, match="list-type=2"):
        main._materialize_with_operational_fallback(ds, {})
    assert calls == ["SELECT real"]


def test_readfree_engages_only_on_missing_dependency():
    ds = {"name": "sap_successfactors_performance_cycle", "sql_def": "SELECT 1"}
    hit = readfree_empty_dataset_for_successfactors(ds, Exception(REAL_DUCKDB_404))
    assert hit is not None
    assert hit["sources"] == []
    assert "WHERE FALSE" in hit["sql_def"]

    # Un Binder Error (drift real de esquema) NO degrada: debe tronar fuerte.
    binder = Exception(
        'Binder Error: Referenced column "invalid_score_input" not found in FROM clause!'
    )
    assert readfree_empty_dataset_for_successfactors(ds, binder) is None
    # Una caida real de infra tampoco (jamas pisar datos buenos con vacio).
    refused = Exception("HTTP Error: Connection refused")
    assert readfree_empty_dataset_for_successfactors(ds, refused) is None
    # Dataset sin proyeccion registrada: None.
    other = {"name": "sap_successfactors_talent_9box", "sql_def": "SELECT 1"}
    assert readfree_empty_dataset_for_successfactors(other, Exception(REAL_DUCKDB_404)) is None


def test_nested_fallback_reaches_readfree_when_first_level_also_404s(monkeypatch):
    """El bug del ciclo 03:05: real 404 -> fallback core engancha -> core lee la
    MISMA fuente ausente -> segundo 404 volaba sin red. Ahora aterriza en la
    proyeccion read-free, anotada como degradada y observable."""
    main = pytest.importorskip("refinement.app.main")

    calls: list[str] = []

    class _Engine:
        def materialize(self, ds, user_context):
            calls.append(ds.get("sql_def") or "")
            # 1a llamada (real) y 2a (fallback core, tambien lee la fuente
            # ausente): 404. 3a (read-free): materializa.
            if len(calls) <= 2:
                raise RuntimeError(REAL_DUCKDB_404)
            return {"status": "ok", "row_count": 0}

    monkeypatch.setattr(main, "engine", _Engine())
    ds = {
        "name": "sap_successfactors_talent_employee_profile",
        "sql_def": "SELECT real",
        "description": "perfil",
    }
    out = main._materialize_with_operational_fallback(ds, {})
    assert len(calls) == 3
    assert "WHERE FALSE" in calls[2]
    assert out["fallback"] is True
    assert out["degraded"] is True
    assert out["degraded_reason"] == "source_entities_absent_readfree_empty"


def test_dataset_without_first_level_fallback_still_lands_readfree(monkeypatch):
    """7 de los 9 silver curados no tienen fallback de primer nivel: antes su
    404 tronaba directo. Ahora aterrizan en su proyeccion read-free."""
    main = pytest.importorskip("refinement.app.main")

    calls: list[str] = []

    class _Engine:
        def materialize(self, ds, user_context):
            calls.append(ds.get("sql_def") or "")
            if len(calls) == 1:
                raise RuntimeError(REAL_DUCKDB_404)
            return {"status": "ok", "row_count": 0}

    monkeypatch.setattr(main, "engine", _Engine())
    ds = {
        "name": "sap_successfactors_movement_events",
        "sql_def": "SELECT real",
        "description": "movimientos",
    }
    out = main._materialize_with_operational_fallback(ds, {})
    assert len(calls) == 2
    assert "WHERE FALSE" in calls[1]
    assert out["degraded_reason"] == "source_entities_absent_readfree_empty"

    # Y un error NO-404 sigue tronando fuerte, sin degradar.
    calls.clear()

    class _Broken:
        def materialize(self, ds, user_context):
            calls.append("x")
            raise RuntimeError("HTTP Error: Connection refused")

    monkeypatch.setattr(main, "engine", _Broken())
    with pytest.raises(RuntimeError, match="Connection refused"):
        main._materialize_with_operational_fallback(ds, {})
    assert len(calls) == 1
