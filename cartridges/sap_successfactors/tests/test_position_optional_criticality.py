from __future__ import annotations

import os

from cryptography.fernet import Fernet

os.environ.setdefault("FIELD_ENCRYPTION_KEY", Fernet.generate_key().decode())
os.environ.setdefault("INTERNAL_API_KEY", "test-secret-key-not-default")
os.environ.setdefault("SECURITY_CONTEXT_SIGNING_KEY", "test-security-context-signing-key-12345")

_BASE_FIELDS = {
    "code",
    "externalName_defaultValue",
    "department",
    "location",
    "costCenter",
    "lastModifiedDateTime",
}
_OPTIONAL = ["criticality", "positionCriticality", "vacant", "effectiveStatus"]


def _position_config() -> dict:
    from app.services import catalog_service

    yaml_position = catalog_service._yaml_entity_map()["Position"]
    return {
        **yaml_position,
        "entity": "Position",
        "odata_entity": "Position",
        "connection_id": "tenant_sf",
        "primary_key": "code",
    }


def _prepare(metadata_fields: set[str]) -> dict:
    from app.services import catalog_service

    prepared, block = catalog_service.prepare_entity_config_for_metadata(
        _position_config(),
        metadata_entities={"Position": metadata_fields},
    )
    assert block is None
    return prepared


def test_yaml_declares_criticality_vacancy_and_status_as_optional_only():
    config = _position_config()

    assert config["optional_select_fields"] == _OPTIONAL
    assert not set(_OPTIONAL) & set(config["select_fields"])


def test_db_row_without_optional_fields_inherits_them_from_yaml():
    from app.services import catalog_service

    merged = catalog_service._merge_yaml_runtime_fields(
        {
            "entity": "Position",
            "select_fields": ["code", "lastModifiedDateTime"],
            "optional_select_fields": None,
        }
    )

    assert merged["optional_select_fields"] == _OPTIONAL
    assert merged["select_fields"] == ["code", "lastModifiedDateTime"]


def test_exposed_optional_fields_are_selected():
    prepared = _prepare(_BASE_FIELDS | set(_OPTIONAL))

    assert prepared["select_fields"][-4:] == _OPTIONAL
    assert set(_OPTIONAL) <= set(prepared["expected_select_fields"])
    assert prepared["metadata_optional_fields_selected"] == _OPTIONAL
    assert prepared["metadata_optional_fields_absent"] == []
    assert prepared["metadata_status"] == "ready"
    assert prepared["metadata_pruned_fields"] == []


def test_modern_position_criticality_and_status_are_selected_without_the_legacy_field():
    prepared = _prepare(_BASE_FIELDS | {"positionCriticality", "effectiveStatus"})

    assert {"positionCriticality", "effectiveStatus"} <= set(prepared["select_fields"])
    assert "criticality" not in prepared["select_fields"]
    assert "vacant" not in prepared["select_fields"]
    assert prepared["metadata_optional_fields_selected"] == ["positionCriticality", "effectiveStatus"]
    assert prepared["metadata_optional_fields_absent"] == ["criticality", "vacant"]
    assert prepared["metadata_status"] == "ready"


def test_absent_optional_fields_never_reach_select_or_degrade_the_run():
    from app.core.extraction_status import classify_successful_extraction

    prepared = _prepare(set(_BASE_FIELDS))

    assert not set(_OPTIONAL) & set(prepared["select_fields"])
    assert not set(_OPTIONAL) & set(prepared["expected_select_fields"])
    assert prepared["metadata_status"] == "ready"
    assert prepared["metadata_pruned_fields"] == []
    assert prepared["metadata_optional_fields_selected"] == []
    assert prepared["metadata_optional_fields_absent"] == _OPTIONAL
    classified = classify_successful_extraction(
        {
            "status": "success",
            "record_count": 3,
            "metadata_status": prepared["metadata_status"],
            "metadata_pruned_fields": prepared["metadata_pruned_fields"],
        }
    )
    assert classified["status"] == "extracted"


def test_only_the_exposed_optional_field_is_selected():
    prepared = _prepare(_BASE_FIELDS | {"vacant"})

    assert "vacant" in prepared["select_fields"]
    assert "criticality" not in prepared["select_fields"]
    assert "positionCriticality" not in prepared["select_fields"]
    assert prepared["metadata_optional_fields_absent"] == [
        "criticality",
        "positionCriticality",
        "effectiveStatus",
    ]


def test_optional_fields_do_not_rescue_a_missing_required_field():
    from app.services import catalog_service

    prepared, block = catalog_service.prepare_entity_config_for_metadata(
        _position_config(),
        metadata_entities={"Position": set(_OPTIONAL) | {"department"}},
    )

    assert prepared is None
    assert block["reason"] == "invalid_required_field"
    assert block["fields_missing"] == ["code"]


def test_entity_without_optional_fields_keeps_previous_contract():
    from app.services import catalog_service

    prepared, block = catalog_service.prepare_entity_config_for_metadata(
        {
            "entity": "JobRequisition",
            "odata_entity": "JobRequisition",
            "mode": "incremental",
            "watermark_field": "lastModifiedDateTime",
            "select_fields": ["jobReqId", "status", "lastModifiedDateTime"],
        },
        metadata_entities={"JobRequisition": {"jobReqId", "status", "lastModifiedDateTime", "criticality"}},
    )

    assert block is None
    assert prepared["select_fields"] == ["jobReqId", "status", "lastModifiedDateTime"]
    assert "metadata_optional_fields_selected" not in prepared


def _extract_all_plan(monkeypatch, metadata_fields: set[str]) -> dict:
    from app.services import catalog_service

    rows = [
        {
            **_position_config(),
            "tenant_id": "tenant-a",
            "workspace_id": "workspace-a",
        }
    ]
    monkeypatch.setattr(catalog_service, "get_all_entities", lambda: rows)
    monkeypatch.setattr(
        catalog_service,
        "_metadata_entities_for_connection",
        lambda **_kwargs: ({"Position": metadata_fields}, None),
    )
    entities, skipped = catalog_service.get_extract_all_plan(
        conn_id="tenant_sf",
        security_context={"tenant_id": "tenant-a", "workspace_id": "workspace-a"},
        target="all",
    )
    assert skipped == []
    return next(row for row in entities if row["entity"] == "Position")


def test_extract_all_plan_selects_criticality_only_when_metadata_exposes_it(monkeypatch):
    exposed = _extract_all_plan(monkeypatch, _BASE_FIELDS | set(_OPTIONAL))
    absent = _extract_all_plan(monkeypatch, set(_BASE_FIELDS))

    assert set(_OPTIONAL) <= set(exposed["select_fields"])
    assert not set(_OPTIONAL) & set(absent["select_fields"])
    assert absent["metadata_status"] == "ready"


def test_talent_plan_keeps_criticality_from_live_readiness(monkeypatch):
    from app.services import catalog_service, preflight

    rows = [{**_position_config(), "tenant_id": "tenant-a", "workspace_id": "workspace-a"}]
    monkeypatch.setattr(catalog_service, "get_all_entities", lambda: rows)
    monkeypatch.setattr(
        catalog_service,
        "_metadata_entities_for_connection",
        lambda **_kwargs: ({"Position": _BASE_FIELDS | {"criticality"}}, None),
    )
    monkeypatch.setattr(
        preflight,
        "talent_metadata_readiness",
        lambda **_kwargs: {
            "status": "partial",
            "extraction_targets": [
                {
                    "component": "role_requirements",
                    "entity": "Position",
                    "odata_entity": "Position",
                    "status": "ready_to_extract",
                    "fields_present": ["code", "department", "lastModifiedDateTime", "criticality"],
                    "primary_key": "code",
                    "watermark_field": "lastModifiedDateTime",
                }
            ],
            "blockers": [],
        },
    )

    entities, _skipped = catalog_service.get_extract_all_plan(
        conn_id="tenant_sf",
        security_context={"tenant_id": "tenant-a", "workspace_id": "workspace-a"},
        target="talent",
    )

    position = next(row for row in entities if row["entity"] == "Position")
    assert "criticality" in position["select_fields"]
    assert "vacant" not in position["select_fields"]
    assert position["metadata_optional_fields_selected"] == ["criticality"]


def test_extraction_sends_optional_fields_only_when_selected(monkeypatch):
    from app.services import catalog_service, extraction_service

    monkeypatch.setattr(extraction_service, "require_storage_access", lambda: None)
    monkeypatch.setattr(extraction_service, "create_run", lambda **_kwargs: "run-position")
    monkeypatch.setattr(extraction_service, "finish_run", lambda **_kwargs: None)
    monkeypatch.setattr(extraction_service, "fail_run", lambda **_kwargs: None)
    monkeypatch.setattr(extraction_service, "touch_watermark_attempt", lambda **_kwargs: None)
    monkeypatch.setattr(extraction_service, "get_watermark", lambda _entity: None)
    monkeypatch.setattr(extraction_service, "update_watermark", lambda **_kwargs: None)
    captured: list[dict] = []

    class FakeSapSfClient:
        def __init__(self, conn_id=None, security_context=None):
            assert conn_id == "tenant_sf"

        def fetch_entity(self, **kwargs):
            captured.append({"select": list(kwargs["select"] or [])})
            if kwargs["skip"]:
                return []
            return [{"code": "P1", "department": "D1", "lastModifiedDateTime": "2026-06-25T00:00:00Z"}]

    def fake_write(**kwargs):
        captured.append({"expected_columns": list(kwargs["expected_columns"])})
        return "s3://lakehouse/Position"

    monkeypatch.setattr(extraction_service, "SapSfClient", FakeSapSfClient)
    monkeypatch.setattr(extraction_service, "write_parquet_and_upload", fake_write)

    def run(metadata_fields: set[str]) -> dict:
        captured.clear()
        monkeypatch.setattr(
            catalog_service,
            "_metadata_entities_for_connection",
            lambda **_kwargs: ({"Position": metadata_fields}, None),
        )
        return extraction_service.run_entity_with_metadata_guard(_position_config())

    exposed = run(_BASE_FIELDS | set(_OPTIONAL))
    exposed_select = captured[0]["select"]
    exposed_columns = next(item["expected_columns"] for item in captured if "expected_columns" in item)
    absent = run(set(_BASE_FIELDS))
    absent_select = captured[0]["select"]
    absent_columns = next(item["expected_columns"] for item in captured if "expected_columns" in item)

    assert set(_OPTIONAL) <= set(exposed_select)
    assert set(_OPTIONAL) <= set(exposed_columns)
    assert not set(_OPTIONAL) & set(absent_select)
    assert not set(_OPTIONAL) & set(absent_columns)
    assert exposed["metadata_status"] == "ready"
    assert absent["metadata_status"] == "ready"
    assert absent["metadata_pruned_fields"] == []
    assert absent["record_count"] == 1


def _fake_metadata_client(preflight, metadata: dict[str, set[str]]):
    class FakeSapSfClient:
        def __init__(self, conn_id=None, security_context=None):
            self.conn_id = conn_id

        def configuration_status(self):
            return {"cartridge": "sap_successfactors", "configured": True, "missing": []}

        def metadata_entities(self):
            return metadata

        def fetch_entity(self, entity, select=None, page_size=200, **_kwargs):
            return [{"redacted": True}]

    return FakeSapSfClient


def test_people_master_readiness_reports_criticality_presence(monkeypatch):
    from app.services import preflight

    monkeypatch.setattr(
        preflight,
        "SapSfClient",
        _fake_metadata_client(preflight, {"Position": _BASE_FIELDS | {"criticality"}}),
    )
    payload = preflight.people_master_readiness(conn_id="tenant_sf", sample=False)
    position = next(item for item in payload["components"] if item["id"] == "position")
    candidate = position["candidates"][0]

    assert "criticality" in candidate["fields_optional_present"]
    assert {"vacant", "positionCriticality", "effectiveStatus"} <= set(candidate["fields_optional_missing"])
    assert position["status"] == "ready"


def test_people_master_readiness_reports_modern_criticality_and_status(monkeypatch):
    from app.services import preflight

    monkeypatch.setattr(
        preflight,
        "SapSfClient",
        _fake_metadata_client(
            preflight, {"Position": _BASE_FIELDS | {"positionCriticality", "effectiveStatus"}}
        ),
    )
    payload = preflight.people_master_readiness(conn_id="tenant_sf", sample=False)
    position = next(item for item in payload["components"] if item["id"] == "position")
    candidate = position["candidates"][0]

    assert {"positionCriticality", "effectiveStatus"} <= set(candidate["fields_optional_present"])
    assert "criticality" in candidate["fields_optional_missing"]
    assert position["status"] == "ready"


def test_people_master_readiness_is_unaffected_when_criticality_is_absent(monkeypatch):
    from app.services import preflight

    monkeypatch.setattr(
        preflight,
        "SapSfClient",
        _fake_metadata_client(preflight, {"Position": set(_BASE_FIELDS)}),
    )
    payload = preflight.people_master_readiness(conn_id="tenant_sf", sample=False)
    position = next(item for item in payload["components"] if item["id"] == "position")
    candidate = position["candidates"][0]

    assert position["status"] == "ready"
    assert candidate["fields_missing"] == []
    assert set(_OPTIONAL) <= set(candidate["fields_optional_missing"])


def test_talent_discovery_carries_criticality_into_position_target(monkeypatch):
    from app.services import preflight

    monkeypatch.setattr(
        preflight,
        "SapSfClient",
        _fake_metadata_client(preflight, {"Position": _BASE_FIELDS | set(_OPTIONAL)}),
    )
    monkeypatch.setattr(preflight, "_load_talent_alias_candidates", lambda **_kwargs: {})
    payload = preflight.talent_metadata_readiness(conn_id="tenant_sf", sample=False)
    target = next(
        item
        for item in payload["extraction_targets"]
        if item["component"] == "role_requirements" and item["entity"] == "Position"
    )

    assert set(_OPTIONAL) <= set(target["fields_present"])


def test_talent_discovery_never_invents_the_modern_field_from_the_legacy_one(monkeypatch):
    from app.services import preflight

    monkeypatch.setattr(
        preflight,
        "SapSfClient",
        _fake_metadata_client(preflight, {"Position": _BASE_FIELDS | {"criticality"}}),
    )
    monkeypatch.setattr(preflight, "_load_talent_alias_candidates", lambda **_kwargs: {})
    payload = preflight.talent_metadata_readiness(conn_id="tenant_sf", sample=False)
    target = next(
        item
        for item in payload["extraction_targets"]
        if item["component"] == "role_requirements" and item["entity"] == "Position"
    )

    assert "criticality" in target["fields_present"]
    assert "positionCriticality" not in target["fields_present"]
    assert "effectiveStatus" not in target["fields_present"]
