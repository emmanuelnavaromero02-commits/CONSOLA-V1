from __future__ import annotations

from pathlib import Path

import yaml


ENTITIES_PATH = Path(__file__).resolve().parents[1] / "app" / "config" / "entities.yaml"


def _entity_map() -> dict[str, dict]:
    data = yaml.safe_load(ENTITIES_PATH.read_text(encoding="utf-8")) or {}
    return {row["entity"]: row for row in data.get("entities", [])}


def test_live_scoped_entities_have_odata_v2_metadata():
    entities = _entity_map()
    expected = {
        "PerPersonal",
        "PerEmail",
        "EmpEmployment",
        "EmpJob",
        "PaymentInformationDetailV3",
        "FOLocation",
        "FOCompany",
        "FODepartment",
        "FODivision",
        "FOBusinessUnit",
        "FOJobCode",
        "EmpEmploymentTermination",
    }

    assert expected <= set(entities)
    for name in expected:
        cfg = entities[name]
        assert cfg.get("page_size")
        assert cfg.get("select_fields"), f"{name} must pin OData $select fields"
        if cfg.get("mode") == "incremental":
            assert cfg.get("watermark_field") == "lastModifiedDateTime"

    assert entities["PerPersonal"]["effective_dated"] is True
    assert entities["PerPersonal"]["date_field"] == "startDate"
    assert entities["PerPersonal"]["effective_from_date"] == "1900-01-01"
    assert entities["PerPersonal"]["effective_to_date"] == "9999-12-31"

    assert entities["EmpJob"]["effective_dated"] is True
    assert entities["EmpJob"]["date_field"] == "startDate"
    assert entities["EmpJob"]["effective_from_date"] == "1900-01-01"
    assert entities["EmpJob"]["effective_to_date"] == "9999-12-31"

    assert "assignmentClass" in entities["EmpEmployment"]["select_fields"]
    assert "employeeClass" not in entities["EmpEmployment"]["select_fields"]

    assert entities["EmpEmploymentTermination"]["date_field"] == "endDate"
    assert entities["EmpEmploymentTermination"]["select_fields"] == [
        "userId",
        "endDate",
        "lastModifiedDateTime",
    ]
    for name in ("FOCompany", "FODepartment", "FODivision", "FOBusinessUnit", "FOJobCode"):
        assert entities[name]["mode"] == "full"
        assert "externalCode" in entities[name]["select_fields"]
        assert "name_defaultValue" in entities[name]["select_fields"]


def test_candidate_uses_minimal_odata_select_fields():
    entities = _entity_map()
    fields = set(entities["Candidate"].get("select_fields") or [])
    assert {"candidateId", "firstName", "lastName", "lastModifiedDateTime"} <= fields


def test_compensation_entities_use_live_successfactors_field_names():
    entities = _entity_map()

    recurring = entities["EmpPayCompRecurring"]
    assert {"payComponent", "paycompvalue", "currencyCode"} <= set(recurring["select_fields"])
    assert recurring["protection"]["paycompvalue"] == "encrypted"

    non_recurring = entities["EmpPayCompNonRecurring"]
    assert {"payComponentCode", "value", "currencyCode"} <= set(non_recurring["select_fields"])
    assert non_recurring["protection"]["value"] == "encrypted"
