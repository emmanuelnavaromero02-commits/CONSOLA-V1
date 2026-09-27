from __future__ import annotations

import json
from pathlib import Path

import pytest

from refinement.app.catalog_copilot_rules import (
    CLASSIFICATION_LABEL,
    HUMAN_TYPE_LABEL,
    PII_PATTERNS,
    RULES_VERSION,
    SEMANTIC_TYPES,
    cardinality,
    classify_column,
    column_description,
    column_label,
    dataset_description,
    dataset_display_name,
    evidence_is_counts_only,
    format_count,
    format_date,
    key_class,
    name_hints,
    relationship_confidence,
    semantic_type,
)

FIXTURE = Path(__file__).resolve().parents[2] / "contracts/fixtures/catalog-human-types-v1.json"


def _fixture() -> dict:
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


def test_rules_version_is_pinned():
    assert RULES_VERSION == "catalog-copilot/1"


def test_shared_fixture_labels_match_python_labels():
    fixture = _fixture()
    assert fixture["schema_version"] == "catalog-human-types/v1"
    assert fixture["labels"] == HUMAN_TYPE_LABEL
    assert set(HUMAN_TYPE_LABEL) == set(SEMANTIC_TYPES)


@pytest.mark.parametrize(
    "case", _fixture()["cases"], ids=lambda case: f"{case['raw_type']}:{case['column']}"
)
def test_semantic_type_matches_shared_fixture(case):
    assert (
        semantic_type(case["raw_type"], case["column"], is_key=case["is_key"])
        == case["semantic_type"]
    )


def test_fixture_covers_every_semantic_type():
    covered = {case["semantic_type"] for case in _fixture()["cases"]}
    assert covered == set(SEMANTIC_TYPES)


def test_classification_labels_are_the_business_terms():
    assert CLASSIFICATION_LABEL == {
        "pii": "Dato Personal Identificable",
        "financial": "Información Financiera",
        "confidential": "Confidencial",
    }


@pytest.mark.parametrize(
    "column,expected",
    [
        ("emailAddress", ("pii",)),
        ("correo", ("pii",)),
        ("rfc", ("pii",)),
        ("curp", ("pii",)),
        ("dateOfBirth", ("pii",)),
        ("first_name", ("pii",)),
        ("apellido_paterno", ("pii",)),
        ("nationalId", ("pii",)),
        ("zipCode", ("pii",)),
        ("maritalStatus", ("pii",)),
        ("salary", ("financial",)),
        ("clabe", ("financial",)),
        ("iban", ("financial",)),
        ("bonus_amount", ("financial",)),
        ("performance_rating", ("confidential",)),
        ("termination_reason", ("confidential",)),
    ],
)
def test_name_hints_classify_business_columns(column, expected):
    hints = name_hints(column)
    found = tuple(
        name
        for name, values in (
            ("pii", hints.pii),
            ("financial", hints.financial),
            ("confidential", hints.confidential),
        )
        if values
    )
    assert found == expected


@pytest.mark.parametrize("column", ["department_name", "display_name", "status", "company"])
def test_ordinary_columns_are_not_sensitive(column):
    assert not name_hints(column).any
    assert classify_column(column, "text").classifications == ()


def test_declared_protection_is_authoritative_and_packaged():
    result = classify_column("payCompValue", "money", declared_protection="encrypted")
    assert result.classifications == ("financial", "confidential")
    assert result.confidence == 1.0
    assert result.origin == "packaged"
    assert result.basis[0] == "declared:encrypted"


def test_declared_protection_without_name_hint_defaults_to_personal_data():
    result = classify_column("candidateToken", "text", declared_protection="shadowed")
    assert result.classifications == ("pii", "confidential")
    assert result.confidence == 1.0


def test_pattern_and_name_reach_highest_inferred_confidence_with_counts_only():
    result = classify_column(
        "rfc", "text", pattern_hits={"rfc": 812}, sampled_non_null=1000
    )
    assert result.classifications == ("pii", "confidential")
    assert result.confidence == 0.97
    assert result.evidence == {
        "basis": ["name:rfc", "pattern:rfc"],
        "pattern_hits": {"rfc": 812},
        "sampled_non_null": 1000,
        "sample_method": "first_rows",
    }
    assert evidence_is_counts_only(result.evidence)


def test_pattern_alone_needs_ratio_and_sample_size():
    assert classify_column(
        "codigo_fiscal", "text", pattern_hits={"rfc": 900}, sampled_non_null=1000
    ).confidence == 0.9
    assert classify_column(
        "codigo_fiscal", "text", pattern_hits={"rfc": 500}, sampled_non_null=1000
    ).classifications == ()
    assert classify_column(
        "codigo_fiscal", "text", pattern_hits={"rfc": 19}, sampled_non_null=19
    ).classifications == ()


def test_supporting_patterns_never_classify_without_a_name_hint():
    phone_like = classify_column(
        "folio", "text", pattern_hits={"phone_mx": 1000}, sampled_non_null=1000
    )
    clabe_like = classify_column(
        "reference", "text", pattern_hits={"clabe": 1000}, sampled_non_null=1000
    )
    assert phone_like.classifications == ()
    assert clabe_like.classifications == ()
    phone = classify_column(
        "telefono", "text", pattern_hits={"phone_mx": 990}, sampled_non_null=1000
    )
    assert phone.classifications == ("pii", "confidential")
    assert phone.confidence == 0.9


def test_name_only_is_labelled_as_lower_confidence_inference():
    result = classify_column("emailAddress", "text")
    assert result.confidence == 0.7
    assert result.origin == "copilot"
    assert result.pii_kind == "email"


def test_money_with_salary_hint_is_financial_and_confidential():
    result = classify_column("salary_amount", "money")
    assert result.classifications == ("financial", "confidential")
    assert result.confidence == 0.85
    assert "type:money" in result.basis


def test_evidence_guard_rejects_value_bearing_keys():
    assert not evidence_is_counts_only({"basis": [], "values": ["x"]})
    assert not evidence_is_counts_only({"basis": [], "min": "a"})
    assert not evidence_is_counts_only({"pattern_hits": {"rfc": "ABC"}})
    assert not evidence_is_counts_only({"basis": ["ABCD800101XY1"]})
    assert evidence_is_counts_only({"basis": ["key:exact"]})


def test_pii_patterns_are_constant_re2_safe_expressions():
    assert set(PII_PATTERNS) == {
        "rfc",
        "curp",
        "email",
        "clabe",
        "card",
        "phone_mx",
        "person_name",
    }
    for pattern in PII_PATTERNS.values():
        assert "'" not in pattern
        assert "(?" not in pattern  # no lookarounds or backreferences


@pytest.mark.parametrize(
    "column,family",
    [
        ("personIdExternal", ("person", False)),
        ("employee_id", ("person", False)),
        ("PERNR", ("person", False)),
        ("companyCode", ("legal_entity", False)),
        ("bukrs", ("legal_entity", False)),
        ("department", ("department", False)),
        ("orgeh", ("department", False)),
        ("cost_center", ("cost_center", False)),
        ("kostl", ("cost_center", False)),
        ("location", ("location", False)),
        ("position", ("position", False)),
        ("job_code", ("job", False)),
        ("customer_id", ("customer", False)),
        ("lifnr", ("vendor", False)),
        ("project_code", ("project", False)),
        ("managerId", ("person", True)),
        ("supervisor_id", ("person", True)),
        ("tenant_id", None),
        ("status", None),
    ],
)
def test_key_synonym_classes(column, family):
    assert key_class(column) == family


def test_relationship_confidence_requires_containment():
    assert relationship_confidence({"name_exact": True, "types_compatible": True}) is None
    assert (
        relationship_confidence(
            {"name_exact": True, "types_compatible": True, "containment": 0.85}
        )
        is None
    )
    assert (
        relationship_confidence(
            {
                "name_exact": True,
                "types_compatible": True,
                "containment": 0.99,
                "target_key_exact": True,
            }
        )
        == 1.0
    )
    assert (
        relationship_confidence(
            {"same_class": True, "types_compatible": True, "containment": 0.92}
        )
        == 0.8
    )
    assert relationship_confidence({"same_class": True, "containment": 0.92}) is None


def test_cardinality_orientation():
    assert cardinality(False, True) == "N:1"
    assert cardinality(True, True) == "1:1"
    assert cardinality(True, False) == "1:N"
    assert cardinality(False, False) == "N:N"


def test_column_descriptions_use_real_facts_only():
    assert column_description(
        {"column": "employee_id", "semantic_type": "identifier", "is_key": True}
    ) == "Identificador único de empleado."
    assert column_description(
        {
            "column": "department_id",
            "semantic_type": "identifier",
            "links_to": "Departamentos",
        }
    ) == "Identificador de departamento. Enlaza con Departamentos."
    assert column_description(
        {
            "column": "hireDate",
            "semantic_type": "date",
            "min_value": "2020-01-01",
            "max_value": "2026-09-25",
        }
    ) == "Fecha de contratación. Cubre del 1 ene 2020 al 25 sep 2026."
    assert column_description(
        {"column": "dateOfBirth", "semantic_type": "date", "classifications": ("pii",),
         "min_value": "1960-01-01", "max_value": "2004-01-01"}
    ) == "Fecha de nacimiento. Dato personal identificable; acceso restringido."
    assert column_description(
        {"column": "is_active", "semantic_type": "boolean"}
    ) == "Indicador Sí/No de activo."
    assert column_description(
        {"column": "status", "semantic_type": "text", "distinct_count": 4, "row_count": 100}
    ) == "Categoría de estado (4 valores distintos)."
    assert column_description(
        {
            "column": "emailAddress",
            "semantic_type": "text",
            "classifications": ("pii", "confidential"),
            "pii_kind": "email",
            "null_rate": 0.35,
        }
    ) == (
        "Correo electrónico de la persona: dato personal identificable; acceso "
        "restringido. Incompleto: 35% de los registros no tienen dato."
    )
    assert column_description(
        {
            "column": "salary_amount",
            "semantic_type": "money",
            "classifications": ("financial", "confidential"),
        }
    ) == "Monto monetario de salario. Información financiera confidencial."
    assert column_description({"column": "amount", "semantic_type": "money"}) == (
        "Monto monetario."
    )


def test_column_label_translates_known_tokens():
    assert column_label("cost_center_id", "identifier") == "centro de costo"
    assert column_label("lastModifiedDateTime", "datetime") == "última modificación"


def test_dataset_display_name_and_executive_description():
    assert dataset_display_name(
        "sap_successfactors_employee_360", "sap_successfactors"
    ) == "Empleado 360"
    assert dataset_display_name("gold_department_latest") == "Departamento"
    description = dataset_description(
        {
            "display_name": "Empleados",
            "cartridge": "sap_successfactors",
            "row_count": 12480,
            "last_refresh": "2026-09-25T10:00:00+00:00",
            "type_counts": {"identifier": 3, "date": 4, "money": 2},
            "pii_columns": ["email", "rfc"],
            "pii_kinds": ["email", "rfc"],
            "financial_columns": ["salary"],
            "related": ["Departamentos", "Compañías"],
        }
    )
    assert description == (
        "Empleados de SAP SuccessFactors: 12 480 registros, actualizados el 25 sep "
        "2026. Incluye 3 identificadores, 4 fechas y 2 montos monetarios. Contiene "
        "datos personales identificables (correo electrónico, RFC) e información "
        "financiera; trátela como confidencial. Se vincula con Departamentos y "
        "Compañías."
    )


def test_dataset_description_omits_unknown_clauses():
    assert dataset_description({"display_name": "Ubicaciones"}) == "Ubicaciones."
    text = dataset_description(
        {"display_name": "X", "related": ["A", "B", "C", "D", "E"]}
    )
    assert text.endswith("Se vincula con A, B, C y 2 más.")
    assert len(dataset_description({"display_name": "Z" * 900})) <= 600


def test_formatting_helpers():
    assert format_count(1234567) == "1 234 567"
    assert format_date("2026-01-05") == "5 ene 2026"
    assert format_date("not a date") is None
