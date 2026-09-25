from __future__ import annotations

import re
from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parents[1]
MIGRATION_89 = REPO_ROOT / "infra" / "init" / "89_sap_successfactors_seed_completion.sql"
MIGRATION_99J = REPO_ROOT / "infra" / "init" / "99j_sap_successfactors_effective_entities.sql"
MIGRATION_99ZB = REPO_ROOT / "infra" / "init" / "99zb_sap_successfactors_talent_entities.sql"
MIGRATION_99ZC = REPO_ROOT / "infra" / "init" / "99zc_sap_successfactors_talent_connection_guard.sql"
MIGRATION_99ZP = REPO_ROOT / "infra" / "init" / "99zp_sap_successfactors_kbwb_entity_expansion.sql"
ENTITIES_YAML = REPO_ROOT / "cartridges" / "sap_successfactors" / "app" / "config" / "entities.yaml"
CARTRIDGE_SEED = REPO_ROOT / "cartridges" / "sap_successfactors" / "config" / "seed.sql"
CATALOG_SERVICE = REPO_ROOT / "cartridges" / "sap_successfactors" / "app" / "services" / "catalog_service.py"


def _yaml_entities() -> set[str]:
    data = yaml.safe_load(ENTITIES_YAML.read_text(encoding="utf-8")) or {}
    return {e["entity"] for e in data.get("entities", [])}


def _entities_in_block(sql: str) -> list[str]:
    block = re.search(r"INSERT INTO entity_config.*?ON CONFLICT", sql, re.DOTALL)
    assert block, "no entity_config insert block found"
    return re.findall(r"\('sap_successfactors',\s*'([A-Za-z0-9_]+)'", block.group(0))


def test_yaml_declares_63_entities():
    assert len(_yaml_entities()) == 63, "entities.yaml must declare 63 entities"


def test_migration_89_seeds_all_30_yaml_entities():
    rows = _entities_in_block(MIGRATION_89.read_text(encoding="utf-8"))
    assert len(rows) == 30, f"migration 89 must seed 30 entities, got {len(rows)}"
    assert len(rows) == len(set(rows)), "migration 89 has duplicate entity rows"
    new_talent = {
        "FOEventReason",
        "JobApplication",
        "CompetencyEntity",
        "UserSkill",
        "SkillProfile",
        "CareerWorksheet",
        "CareerInterest",
        "SuccessionNomination",
        "LearningAssignment",
        "LearningHistory",
        "FOPayGrade",
        "FormPerfPotSummarySection",
        "FormObjective",
        "FormObjectiveDetails",
        "SimpleGoal",
        "GoalAchievements",
        "CalibrationSession",
        "CalibrationSessionSubject",
        "CalibrationSubjectRank",
        "WorkerCompetencyAssessment",
        "FormCompetency",
        "SysOverallCompetency",
        "SkillEntity",
        "DevGoal",
        "DevGoalCompetency",
        "TalentPool",
        "TalentPoolNav",
        "UserCourses",
        "UserPrograms",
        "LearningEvents",
        "Curricula",
        "CatalogsFeed",
    }
    assert set(rows) == (_yaml_entities() - {"PaymentInformationDetailV3"} - new_talent), (
        "migration 89 should cover the historical 30-entity catalog; 99j and 99zb add later entities"
    )


def test_migration_89_includes_the_15_previously_missing():
    rows = set(_entities_in_block(MIGRATION_89.read_text(encoding="utf-8")))
    previously_missing = {
        "PerPerson", "PerPersonal", "PerEmail", "PerPhone", "PerAddressDEFLT",
        "PerNationalId", "EmpPayCompRecurring", "EmpPayCompNonRecurring",
        "EmpEmploymentTermination", "FOCompany", "FOBusinessUnit", "FOJobCode",
        "EmployeeTime", "TimeAccount", "WorkSchedule",
    }
    assert previously_missing <= rows, f"missing: {previously_missing - rows}"


def test_migration_89_is_idempotent_and_scoped():
    sql = MIGRATION_89.read_text(encoding="utf-8")
    assert "ON CONFLICT (cartridge_id, entity) DO UPDATE" in sql
    assert "89_sap_successfactors_seed_completion.sql" in sql
    assert "ON CONFLICT (filename) DO NOTHING" in sql
    assert re.search(r"\bDELETE\b", sql, re.IGNORECASE) is None, "must not delete rows"
    cols = re.search(r"INSERT INTO entity_config\s*\(([^)]+)\)", sql, re.DOTALL)
    assert cols and "workspace_id" not in cols.group(1)


def test_migration_89_parses_with_sqlglot():
    import pytest
    sqlglot = pytest.importorskip("sqlglot")
    stmts = [s for s in sqlglot.parse(MIGRATION_89.read_text(encoding="utf-8"), read="postgres") if s]
    assert len(stmts) == 2


def test_cartridge_seed_completed_to_63():
    rows = _entities_in_block(CARTRIDGE_SEED.read_text(encoding="utf-8"))
    assert set(rows) == _yaml_entities(), "config/seed.sql entity_config != entities.yaml set"


def test_migration_99j_adds_payment_and_effective_dated_metadata():
    sql = MIGRATION_99J.read_text(encoding="utf-8")
    assert "PaymentInformationDetailV3" in sql
    assert "effective_dated = TRUE" in sql
    assert "99j_sap_successfactors_effective_entities.sql" in sql
    assert "ON CONFLICT (filename) DO NOTHING" in sql
    assert "DELETE" not in sql.upper()


def test_migration_99zb_adds_talent_entities_and_select_fields():
    sql = MIGRATION_99ZB.read_text(encoding="utf-8")
    rows = set(_entities_in_block(sql))
    expected = {
        "FOEventReason",
        "JobApplication",
        "PerformanceReview",
        "GoalPlan",
        "CompetencyEntity",
        "UserSkill",
        "SkillProfile",
        "CareerWorksheet",
        "CareerInterest",
        "SuccessionNomination",
        "LearningAssignment",
        "LearningHistory",
        "LearningItem",
    }
    assert rows == expected
    assert "select_fields" in sql
    assert "protection" in sql
    assert "99zb_sap_successfactors_talent_entities.sql" in sql
    assert "ON CONFLICT (filename) DO NOTHING" in sql
    assert "DELETE" not in sql.upper()


def test_migration_99zc_assigns_vault_connection_to_talent_entities():
    sql = MIGRATION_99ZC.read_text(encoding="utf-8")
    expected = {
        "FOEventReason",
        "JobApplication",
        "PerformanceReview",
        "GoalPlan",
        "CompetencyEntity",
        "UserSkill",
        "SkillProfile",
        "CareerWorksheet",
        "CareerInterest",
        "SuccessionNomination",
        "LearningItem",
        "LearningAssignment",
        "LearningHistory",
    }
    for entity in expected:
        assert f"('{entity}')" in sql
    assert MIGRATION_99ZB.name < MIGRATION_99ZC.name
    assert "connection_id = 'femsa_sf'" in sql
    assert "COALESCE(NULLIF(ec.connection_id, ''), '') = ''" in sql
    assert "99zc_sap_successfactors_talent_connection_guard.sql" in sql
    assert "ON CONFLICT (filename) DO NOTHING" in sql
    assert "DELETE" not in sql.upper()


def test_migration_99zp_adds_kbwb_pdf_entities():
    sql = MIGRATION_99ZP.read_text(encoding="utf-8")
    rows = set(_entities_in_block(sql))
    expected = {
        "FOPayGrade",
        "FormPerfPotSummarySection",
        "FormObjective",
        "FormObjectiveDetails",
        "SimpleGoal",
        "GoalAchievements",
        "CalibrationSession",
        "CalibrationSessionSubject",
        "CalibrationSubjectRank",
        "WorkerCompetencyAssessment",
        "FormCompetency",
        "SysOverallCompetency",
        "SkillEntity",
        "DevGoal",
        "DevGoalCompetency",
        "TalentPool",
        "TalentPoolNav",
        "UserCourses",
        "UserPrograms",
        "LearningEvents",
        "Curricula",
        "CatalogsFeed",
    }
    assert rows == expected
    assert "select_fields" in sql
    assert "protection" in sql
    assert "99zp_sap_successfactors_kbwb_entity_expansion.sql" in sql
    assert "ON CONFLICT (filename) DO NOTHING" in sql
    assert "DELETE" not in sql.upper()


def test_new_talent_yaml_entities_have_extract_contract():
    data = yaml.safe_load(ENTITIES_YAML.read_text(encoding="utf-8")) or {}
    entities = {item["entity"]: item for item in data.get("entities", [])}
    expected = {
        "FOEventReason",
        "JobApplication",
        "CompetencyEntity",
        "UserSkill",
        "SkillProfile",
        "CareerWorksheet",
        "CareerInterest",
        "SuccessionNomination",
        "LearningAssignment",
        "LearningHistory",
        "FOPayGrade",
        "FormPerfPotSummarySection",
        "FormObjective",
        "FormObjectiveDetails",
        "SimpleGoal",
        "GoalAchievements",
        "CalibrationSession",
        "CalibrationSessionSubject",
        "CalibrationSubjectRank",
        "WorkerCompetencyAssessment",
        "FormCompetency",
        "SysOverallCompetency",
        "SkillEntity",
        "DevGoal",
        "DevGoalCompetency",
        "TalentPool",
        "TalentPoolNav",
        "UserCourses",
        "UserPrograms",
        "LearningEvents",
        "Curricula",
        "CatalogsFeed",
    }
    for entity in expected:
        config = entities[entity]
        assert config.get("primary_key"), f"{entity}: missing primary_key"
        assert config.get("select_fields"), f"{entity}: missing select_fields"
        if entity in {
            "JobApplication",
            "PerformanceReview",
            "GoalPlan",
            "UserSkill",
            "SkillProfile",
            "CareerWorksheet",
            "CareerInterest",
            "SuccessionNomination",
            "LearningAssignment",
            "LearningHistory",
            "FormPerfPotSummarySection",
            "FormObjective",
            "FormObjectiveDetails",
            "SimpleGoal",
            "GoalAchievements",
            "CalibrationSessionSubject",
            "CalibrationSubjectRank",
            "WorkerCompetencyAssessment",
            "FormCompetency",
            "SysOverallCompetency",
            "DevGoal",
            "DevGoalCompetency",
            "TalentPoolNav",
            "UserCourses",
            "UserPrograms",
            "LearningEvents",
        }:
            assert config.get("protection"), f"{entity}: missing protection"


def test_catalog_service_always_tops_up_not_gated_on_empty():
    src = CATALOG_SERVICE.read_text(encoding="utf-8")
    assert "if count == 0:" not in src, "entity seeding is still gated on count == 0"
    assert "for e in _yaml_entities():" in src
    assert "ON CONFLICT (cartridge_id, entity) DO NOTHING" in src
