from __future__ import annotations

from pathlib import Path

import pytest

from refinement.app.successfactors_fallbacks import (
    TALENT_GOLD_FALLBACK_SQL,
    SUCCESSFACTORS_GOLD_FALLBACK_SOURCES,
    annotate_operational_fallback,
)

_REPO = Path(__file__).resolve().parents[2]


# Performance fix: the real overall rating lives in FormHeader.rating (not the
# always-null overallRating); Performance must flow to performance_score
# independently of the tenant-blocked Competency/Aspiration silvers.


def test_performancereview_extraction_selects_the_real_rating_field():
    yaml_text = (_REPO / "cartridges/sap_successfactors/app/config/entities.yaml").read_text(encoding="utf-8")
    block = yaml_text.split("entity: PerformanceReview", 1)[1].split("\n  - entity:", 1)[0]
    assert "- rating" in block
    assert "- isRated" in block


def test_performancereview_silver_maps_rating_to_performance_rating():
    sql = (_REPO / "cartridges/sap_successfactors/datasets/sap_successfactors_performancereview_latest.sql").read_text(encoding="utf-8")
    # rating (guarded by isRated) is the source, overallRating is only a fallback
    assert "TRY_CAST(rating AS DOUBLE)" in sql
    assert "isRated" in sql
    assert "COALESCE(" in sql
    assert "AS performance_rating" in sql


def test_employee_profile_fallback_wires_performance_and_never_fakes_c_a():
    sql = TALENT_GOLD_FALLBACK_SQL["sap_successfactors_talent_employee_profile"]
    # Performance flows from performance_cycle -> performance_score
    assert "sap_successfactors_performance_cycle" in sql
    assert "performance.performance_score AS performance_score" in sql
    # Competency and Aspiration stay explicitly NULL (tenant/SAP dependency, never simulated)
    assert "NULL::DOUBLE AS competency_score" in sql
    assert "NULL::DOUBLE AS aspiration_score" in sql
    srcs = SUCCESSFACTORS_GOLD_FALLBACK_SOURCES["sap_successfactors_talent_employee_profile"]
    assert any("performance_cycle" in s for s in srcs)


def test_performance_cycle_degrades_without_optional_goal_sources():
    sql = TALENT_GOLD_FALLBACK_SQL["sap_successfactors_performance_cycle"]
    # cycle still materializes from the guaranteed review silver; no goalplan/goalachievements reads
    assert "sap_successfactors_performancereview_latest" in sql
    assert "goalplan" not in sql.lower()
    assert "goalachievements" not in sql.lower()
    assert "AS performance_rating" in sql or "performance_rating," in sql


# P2: el fallback operativo hacia gold vacio debe ser OBSERVABLE, no un exito
# silencioso. Estos tests fijan que:
#   - un fallback foundation con 0 filas se marca degraded (y en modo estricto, error);
#   - un fallback con filas reales NO se marca degraded;
#   - las claves previas del contrato (status/fallback/fallback_reason) se preservan.


def test_empty_foundation_fallback_is_marked_degraded_without_flipping_success():
    result = {"name": "sap_successfactors_employee_360", "row_count": 0}

    annotated = annotate_operational_fallback(
        "sap_successfactors_employee_360", result, "no files found"
    )

    assert annotated["degraded"] is True
    assert annotated["degraded_reason"] == "foundation_source_missing_empty_gold"
    # Por defecto (modo no estricto) NO se emite 'error': no voltea la semantica
    # de un entorno desplegado sin aviso.
    assert "error" not in annotated
    # Contrato previo intacto.
    assert annotated["status"] == "partial"
    assert annotated["fallback"] is True
    assert annotated["fallback_reason"] == "missing_materialized_dependency"


def test_empty_foundation_fallback_emits_error_in_strict_mode(monkeypatch):
    monkeypatch.setenv("REFINEMENT_SF_FALLBACK_STRICT", "true")
    result = {"name": "sap_successfactors_employee_360", "row_count": 0}

    annotated = annotate_operational_fallback(
        "sap_successfactors_employee_360", result, "source_files_missing"
    )

    assert annotated["degraded"] is True
    # En modo estricto el chain (dataset_refresh_chain) marcara este dataset no-ok
    # porque el body trae 'error'.
    assert "error" in annotated
    assert "gold vacio" in annotated["error"]


def test_non_empty_fallback_is_not_degraded():
    result = {"name": "sap_successfactors_talent_role_profile", "row_count": 128}

    annotated = annotate_operational_fallback(
        "sap_successfactors_talent_role_profile", result, "missing_materialized_dependencies"
    )

    assert annotated["degraded"] is False
    assert "error" not in annotated
    assert "degraded_reason" not in annotated


def test_empty_non_foundation_fallback_degraded_but_never_hard_errors(monkeypatch):
    # Un dataset de talento vacio (cascada aguas abajo) se marca degraded pero
    # el modo estricto solo aplica el 'error' duro a los foundation (la raiz accionable).
    monkeypatch.setenv("REFINEMENT_SF_FALLBACK_STRICT", "true")
    result = {"name": "sap_successfactors_talent_cpa_scores", "row_count": 0}

    annotated = annotate_operational_fallback(
        "sap_successfactors_talent_cpa_scores", result, "no files found"
    )

    assert annotated["degraded"] is True
    assert annotated["degraded_reason"] == "upstream_dependency_empty_gold"
    assert "error" not in annotated


@pytest.mark.parametrize("bad_row_count", [None, "x", ""])
def test_unparseable_row_count_is_treated_as_empty(bad_row_count):
    result = {"name": "sap_successfactors_org_structure", "row_count": bad_row_count}

    annotated = annotate_operational_fallback(
        "sap_successfactors_org_structure", result, "no files found"
    )

    assert annotated["degraded"] is True


# user_id_hash: Performance's user key is shadowed (sha256 of formSubjectId) while
# foundation user_id is raw. Talent joins must use a common technical hash key
# (user_id_hash = sha256(raw user_id)) so Performance can join to the profile
# WITHOUT de-shadowing any PII.

_DS = _REPO / "cartridges/sap_successfactors/datasets"


def test_employee_360_exposes_user_id_hash_matching_shadowing():
    sql = (_DS / "sap_successfactors_employee_360.sql").read_text(encoding="utf-8")
    # sha256 hex == protection_service._shadow, so it equals the shadowed formSubjectId
    assert "sha256(CAST(e.user_id AS VARCHAR)) AS user_id_hash" in sql


def test_performance_cycle_exposes_user_id_hash_and_ranks_rated_first():
    sql = (_DS / "sap_successfactors_performance_cycle.sql").read_text(encoding="utf-8")
    assert "AS user_id_hash" in sql
    # ranking picks the latest RATED form, not just the latest form
    assert "(performance_rating IS NOT NULL) DESC" in sql


def test_employee_profile_joins_talent_by_user_id_hash_not_raw():
    sql = (_DS / "sap_successfactors_talent_employee_profile.sql").read_text(encoding="utf-8")
    assert "performance.user_id_hash = emp.user_id_hash" in sql
    assert "competency.user_id_hash = emp.user_id_hash" in sql
    assert "aspiration.user_id_hash = emp.user_id_hash" in sql
    # manager_hierarchy stays on the raw foundation user_id
    assert "hier.user_id = emp.user_id" in sql


def test_talent_fallbacks_join_by_user_id_hash_and_rank_rated_first():
    ep = TALENT_GOLD_FALLBACK_SQL["sap_successfactors_talent_employee_profile"]
    assert "performance.user_id_hash = emp.user_id_hash" in ep
    # Competency/Aspiration stay explicitly NULL — tenant/SAP dependency, not simulated
    assert "NULL::DOUBLE AS competency_score" in ep
    assert "NULL::DOUBLE AS aspiration_score" in ep
    pc = TALENT_GOLD_FALLBACK_SQL["sap_successfactors_performance_cycle"]
    assert "AS user_id_hash" in pc
    assert "(performance_rating IS NOT NULL) DESC" in pc
