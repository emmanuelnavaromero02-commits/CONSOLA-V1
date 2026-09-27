from __future__ import annotations

import copy
from datetime import datetime, timezone

from refinement.app.catalog_overlay import CatalogAnnotations, apply_catalog_annotations


def _snapshot_output() -> dict:
    return {
        "datasets": {
            "employees": {
                "layer": "silver",
                "cartridge": "sap_successfactors",
                "row_count": 3,
                "last_refresh": "2026-09-25T10:00:00+00:00",
                "description": "",
                "columns": [
                    {
                        "name": "employee_id",
                        "type": "VARCHAR",
                        "description": "",
                        "tags": [],
                        "is_key": False,
                        "is_metric": False,
                        "example_values": [],
                        "min_value": "E001",
                        "max_value": "E003",
                    },
                    {
                        "name": "salary",
                        "type": "DECIMAL(18,2)",
                        "description": "Salario autorizado",
                        "tags": ["finance"],
                        "is_key": False,
                        "is_metric": True,
                        "example_values": [15000.0],
                        "min_value": "15000.00",
                        "max_value": "90000.00",
                    },
                    {
                        "name": "personal_email",
                        "type": "VARCHAR",
                        "description": "",
                        "tags": [],
                        "is_key": False,
                        "is_metric": False,
                        "example_values": [],
                        "min_value": "a@x.mx",
                        "max_value": "z@x.mx",
                    },
                    {
                        "name": "hire_date",
                        "type": "DATE",
                        "description": "",
                        "tags": [],
                        "is_key": False,
                        "is_metric": False,
                        "example_values": [],
                        "min_value": "2020-01-01",
                        "max_value": "2026-09-01",
                    },
                ],
            },
            "departments": {
                "layer": "silver",
                "cartridge": "sap_successfactors",
                "row_count": 2,
                "description": "Departamentos vigentes",
                "columns": [],
            },
        },
        "relationships": [
            {
                "from_dataset": "employees",
                "from_column": "department_id",
                "to_dataset": "departments",
                "to_column": "department_id",
                "join_hint": "LEFT",
                "description": "Snapshot",
            },
            {
                "from_dataset": "employees",
                "from_column": "cost_center",
                "to_dataset": "hidden_dataset",
                "to_column": "cost_center",
                "join_hint": "LEFT",
                "description": "Otro espacio",
            },
        ],
    }


def _annotations(**overrides) -> CatalogAnnotations:
    values = {
        "columns": {
            "employees": {
                "employee_id": {
                    "description": "Identificador único de empleado.",
                    "description_origin": "copilot",
                    "tags": [],
                    "is_key": False,
                    "is_metric": False,
                    "semantic_type": "identifier",
                    "classifications": [],
                    "classification_origin": None,
                    "copilot_confidence": None,
                    "copilot_evidence": {"basis": ["key:exact"]},
                },
                "salary": {
                    "description": "Salario autorizado",
                    "description_origin": "manual",
                    "tags": ["finance"],
                    "is_key": False,
                    "is_metric": True,
                    "semantic_type": "money",
                    "classifications": ["financial", "confidential"],
                    "classification_origin": "copilot",
                    "copilot_confidence": 0.85,
                    "copilot_evidence": {"basis": ["name:salary", "type:money", "BAD VALUE"]},
                },
            }
        },
        "relationships": [
            {
                "from_dataset": "employees",
                "from_column": "department_id",
                "to_dataset": "departments",
                "to_column": "department_id",
                "join_hint": "LEFT",
                "cardinality": "N:1",
                "origin": "copilot",
                "status": "active",
                "confidence": 0.95,
                "basis": {"codes": ["name:exact", "containment:1.00"], "containment": 1.0},
                "description": "Detectada por Copiloto",
            },
            {
                "from_dataset": "employees",
                "from_column": "location_id",
                "to_dataset": "departments",
                "to_column": "location_id",
                "origin": "copilot",
                "status": "rejected",
            },
        ],
        "subjects": {
            ("dataset", "employees"): {
                "status": "ready",
                "display_name": "Empleados",
                "description": "Empleados de SAP SuccessFactors: 3 registros.",
                "rules_version": "catalog-copilot/1",
                "profiled_at": datetime(2026, 9, 26, 12, 0, tzinfo=timezone.utc),
                "summary": {
                    "pii_columns": ["personal_email"],
                    "financial_columns": ["salary"],
                    "relations": 1,
                },
            }
        },
    }
    values.update(overrides)
    return CatalogAnnotations(**values)


VISIBLE = {"employees", "departments"}


def test_live_annotations_win_over_the_snapshot_and_evidence_is_untouched():
    snapshot = _snapshot_output()
    frozen = copy.deepcopy(snapshot)
    out = apply_catalog_annotations(snapshot, _annotations(), visible=VISIBLE)
    assert snapshot == frozen
    columns = {c["name"]: c for c in out["datasets"]["employees"]["columns"]}
    employee_id = columns["employee_id"]
    assert employee_id["description"] == "Identificador único de empleado."
    assert employee_id["description_origin"] == "copilot"
    assert employee_id["semantic_type"] == "identifier"
    assert employee_id["is_key"] is True
    assert employee_id["copilot_basis"] == ["key:exact"]
    assert employee_id["min_value"] == "E001"
    salary = columns["salary"]
    assert salary["description_origin"] == "manual"
    assert salary["classifications"] == ["financial", "confidential"]
    assert salary["copilot_confidence"] == 0.85
    assert salary["copilot_basis"] == ["name:salary", "type:money"]


def test_sensitive_columns_lose_literal_statistics():
    out = apply_catalog_annotations(_snapshot_output(), _annotations(), visible=VISIBLE)
    columns = {c["name"]: c for c in out["datasets"]["employees"]["columns"]}
    salary = columns["salary"]
    assert salary["stats_redacted"] is True
    assert "min_value" not in salary and "max_value" not in salary
    assert salary["example_values"] == []
    email = columns["personal_email"]
    assert email["stats_redacted"] is True, "name-based safety net before profiling"
    assert "min_value" not in email
    hire = columns["hire_date"]
    assert hire["min_value"] == "2020-01-01"
    assert "stats_redacted" not in hire


def test_dataset_level_copilot_block_and_description_fallback():
    out = apply_catalog_annotations(_snapshot_output(), _annotations(), visible=VISIBLE)
    employees = out["datasets"]["employees"]
    assert employees["description"] == (
        "Empleados de SAP SuccessFactors: 3 registros. Se vincula con Departamentos."
    )
    assert employees["description_origin"] == "copilot"
    assert employees["display_name"] == "Empleados"
    assert employees["kind"] == "dataset"
    assert employees["copilot"] == {
        "status": "ready",
        "profiled_at": "2026-09-26T12:00:00+00:00",
        "rules_version": "catalog-copilot/1",
        "pii_columns": 1,
        "financial_columns": 1,
        "relations": 1,
    }
    departments = out["datasets"]["departments"]
    assert departments["description"] == "Departamentos vigentes"
    assert departments["description_origin"] == "manual"
    assert "copilot" not in departments


def test_relationships_merge_live_wins_and_both_ends_must_be_visible():
    out = apply_catalog_annotations(_snapshot_output(), _annotations(), visible=VISIBLE)
    assert out["relationships"] == [
        {
            "from_dataset": "employees",
            "from_column": "department_id",
            "to_dataset": "departments",
            "to_column": "department_id",
            "join_hint": "LEFT",
            "description": "Detectada por Copiloto",
            "origin": "copilot",
            "status": "active",
            "cardinality": "N:1",
            "confidence": 0.95,
            "basis": ["name:exact", "containment:1.00"],
        }
    ]


def test_rejected_or_retired_live_rows_remove_snapshot_edges():
    annotations = _annotations(
        relationships=[
            {
                "from_dataset": "employees",
                "from_column": "department_id",
                "to_dataset": "departments",
                "to_column": "department_id",
                "origin": "manual",
                "status": "rejected",
            }
        ]
    )
    out = apply_catalog_annotations(_snapshot_output(), annotations, visible=VISIBLE)
    assert out["relationships"] == []


def test_degraded_annotations_are_flagged_and_still_redact_by_name():
    out = apply_catalog_annotations(
        _snapshot_output(), CatalogAnnotations.unavailable(), visible=VISIBLE
    )
    assert out["annotations_degraded"] is True
    columns = {c["name"]: c for c in out["datasets"]["employees"]["columns"]}
    assert columns["salary"]["stats_redacted"] is True
    assert columns["personal_email"]["stats_redacted"] is True
    assert columns["employee_id"]["min_value"] == "E001"


def test_bronze_subjects_only_when_requested_and_filtered():
    source_state = {
        "subject_kind": "bronze_source",
        "subject": "raw/sap_successfactors/PerEmail",
        "cartridge": "sap_successfactors",
        "status": "ready",
        "display_name": "PerEmail",
        "description": "Per email de SAP SuccessFactors: 30 registros.",
        "rules_version": "catalog-copilot/1",
        "profiled_at": "2026-09-26 12:00:00+00",
        "summary": {
            "rows": 30,
            "load_date": "2026-09-25",
            "pii_columns": ["emailAddress"],
            "columns": [
                {
                    "name": "emailAddress",
                    "type": "VARCHAR",
                    "semantic_type": "text",
                    "classifications": ["pii", "confidential"],
                    "classification_origin": "packaged",
                    "confidence": 1.0,
                    "basis": ["declared:masked", "name:email"],
                    "null_rate": 0.0,
                    "description": "Correo electrónico de la persona.",
                }
            ],
        },
    }
    annotations = _annotations(sources=[source_state])
    without = apply_catalog_annotations(_snapshot_output(), annotations, visible=VISIBLE)
    assert "raw/sap_successfactors/PerEmail" not in without["datasets"]
    out = apply_catalog_annotations(
        _snapshot_output(), annotations, visible=VISIBLE, include_sources=True
    )
    entry = out["datasets"]["raw/sap_successfactors/PerEmail"]
    assert entry["kind"] == "bronze_source"
    assert entry["layer"] == "bronze"
    assert entry["row_count"] == 30
    assert entry["columns"][0]["classification_origin"] == "packaged"
    assert entry["columns"][0]["stats_redacted"] is True
    assert entry["copilot"]["pii_columns"] == 1
    filtered = apply_catalog_annotations(
        _snapshot_output(),
        annotations,
        visible=VISIBLE,
        include_sources=True,
        cartridge="hubspot",
    )
    assert "raw/sap_successfactors/PerEmail" not in filtered["datasets"]
    silver_only = apply_catalog_annotations(
        _snapshot_output(), annotations, visible=VISIBLE, include_sources=True, layer="silver"
    )
    assert "raw/sap_successfactors/PerEmail" not in silver_only["datasets"]


def test_link_clauses_are_rendered_per_reader_and_never_leak_hidden_tables():
    """Reviewer reproduction: stored text must not name datasets a reader cannot see."""
    snapshot = _snapshot_output()
    snapshot["datasets"]["employees"]["columns"].append(
        {"name": "cost_center", "type": "VARCHAR", "description": "", "tags": [],
         "is_key": False, "is_metric": False, "example_values": []}
    )
    annotations = _annotations()
    annotations.columns["employees"]["cost_center"] = {
        "description": "Identificador de centro de costo.",
        "description_origin": "copilot",
    }
    annotations.relationships.append(
        {
            "from_dataset": "employees",
            "from_column": "cost_center",
            "to_dataset": "hidden_cost_centers",
            "to_column": "cost_center",
            "origin": "copilot",
            "status": "active",
            "cardinality": "N:1",
        }
    )
    annotations.subjects[("dataset", "hidden_cost_centers")] = {"display_name": "Centros secretos"}
    out = apply_catalog_annotations(snapshot, annotations, visible=VISIBLE)
    text = str(out)
    assert "Centros secretos" not in text
    assert "hidden_cost_centers" not in text
    employees = out["datasets"]["employees"]
    assert employees["copilot"]["relations"] == 1
    columns = {c["name"]: c for c in employees["columns"]}
    assert columns["cost_center"]["description"] == "Identificador de centro de costo."
    wider = apply_catalog_annotations(
        _snapshot_output() | {"datasets": snapshot["datasets"]},
        annotations,
        visible=VISIBLE | {"hidden_cost_centers"},
    )
    widened = {c["name"]: c for c in wider["datasets"]["employees"]["columns"]}
    assert widened["cost_center"]["description"].endswith("Enlaza con Centros secretos.")
    assert wider["datasets"]["employees"]["copilot"]["relations"] == 2
    assert "Centros secretos" in wider["datasets"]["employees"]["description"]


def test_manual_text_never_gets_generated_link_clauses():
    snapshot = _snapshot_output()
    annotations = _annotations()
    annotations.subjects[("dataset", "employees")]["description"] = ""
    snapshot["datasets"]["employees"]["description"] = "Plantilla autorizada"
    out = apply_catalog_annotations(snapshot, annotations, visible=VISIBLE)
    assert out["datasets"]["employees"]["description"] == "Plantilla autorizada"


def test_bronze_column_descriptions_are_rendered_from_compact_facts():
    state = {
        "subject": "raw/sap_successfactors/PerEmail",
        "cartridge": "sap_successfactors",
        "status": "ready",
        "summary": {
            "rows": 3,
            "columns": [
                {
                    "name": "emailAddress",
                    "type": "VARCHAR",
                    "semantic_type": "text",
                    "classifications": ["pii", "confidential"],
                    "pii_kind": "email",
                    "null_rate": 0.5,
                }
            ],
        },
    }
    out = apply_catalog_annotations(
        _snapshot_output(), _annotations(sources=[state]), visible=VISIBLE, include_sources=True
    )
    column = out["datasets"]["raw/sap_successfactors/PerEmail"]["columns"][0]
    assert column["description"].startswith("Correo electrónico de la persona")
    assert column["description"].endswith("50% de los registros no tienen dato.")
