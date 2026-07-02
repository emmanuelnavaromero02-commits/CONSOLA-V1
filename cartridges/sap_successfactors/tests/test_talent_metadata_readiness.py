from __future__ import annotations

import sys
from pathlib import Path


def _import_modules():
    root = str(Path(__file__).resolve().parents[1])
    if root in sys.path:
        sys.path.remove(root)
    sys.path.insert(0, root)
    for name in [n for n in sys.modules if n == "app" or n.startswith("app.")]:
        del sys.modules[name]
    from app.core.sap_client import SapSfClient
    from app.services import preflight

    return SapSfClient, preflight


def test_parse_metadata_entities_extracts_fields():
    SapSfClient, _ = _import_modules()
    metadata = """<?xml version="1.0" encoding="utf-8"?>
    <edmx:Edmx xmlns:edmx="http://schemas.microsoft.com/ado/2007/06/edmx">
      <edmx:DataServices>
        <Schema xmlns="http://schemas.microsoft.com/ado/2008/09/edm" Namespace="SFOData">
          <EntityType Name="FormHeader">
            <Property Name="formDataId" Type="Edm.String" />
            <Property Name="formSubjectId" Type="Edm.String" />
          </EntityType>
          <EntityType Name="CareerInterest">
            <Property Name="userId" Type="Edm.String" />
            <Property Name="interest" Type="Edm.String" />
          </EntityType>
        </Schema>
      </edmx:DataServices>
    </edmx:Edmx>
    """

    entities = SapSfClient.parse_metadata_entities(metadata)

    assert entities["FormHeader"] == {"formDataId", "formSubjectId"}
    assert entities["CareerInterest"] == {"userId", "interest"}


def test_parse_metadata_entities_maps_entitysets_to_entitytype_fields():
    SapSfClient, _ = _import_modules()
    metadata = """<?xml version="1.0" encoding="utf-8"?>
    <edmx:Edmx xmlns:edmx="http://schemas.microsoft.com/ado/2007/06/edmx">
      <edmx:DataServices>
        <Schema xmlns="http://schemas.microsoft.com/ado/2008/09/edm" Namespace="SFOData">
          <EntityType Name="FormHeaderType">
            <Property Name="formDataId" Type="Edm.String" />
            <Property Name="lastModifiedDateTime" Type="Edm.DateTime" />
          </EntityType>
          <EntityContainer Name="EntityContainer">
            <EntitySet Name="FormHeader" EntityType="SFOData.FormHeaderType" />
          </EntityContainer>
        </Schema>
      </edmx:DataServices>
    </edmx:Edmx>
    """

    entities = SapSfClient.parse_metadata_entities(metadata)

    assert entities["FormHeader"] == {"formDataId", "lastModifiedDateTime"}


def test_talent_metadata_readiness_reports_live_cpa_blockers(monkeypatch):
    _, preflight = _import_modules()

    class FakeSapSfClient:
        def __init__(self, conn_id=None, security_context=None):
            self.conn_id = conn_id
            self.security_context = security_context

        def configuration_status(self):
            return {
                "cartridge": "sap_successfactors",
                "configured": True,
                "missing": [],
                "base_url": "https://example.successfactors.com/odata/v2",
            }

        def metadata_entities(self):
            return {
                "FormHeader": {"formDataId", "formSubjectId", "overallRating", "lastModifiedDateTime", "status"},
                "CompetencyEntity": {"externalCode", "name", "lastModifiedDateTime"},
                "SkillProfile": {"userId", "skill", "proficiency", "lastModifiedDateTime"},
                "Position": {"code", "jobCode", "department", "lastModifiedDateTime"},
                "FOJobCode": {"externalCode", "name", "lastModifiedDateTime"},
            }

        def fetch_entity(self, entity, select=None, page_size=200, **_kwargs):
            assert page_size == 1
            assert select
            return [{"redacted": True}]

    monkeypatch.setattr(preflight, "SapSfClient", FakeSapSfClient)

    payload = preflight.talent_metadata_readiness(
        conn_id="femsa_sf",
        security_context={"trusted": True, "tenant_id": "t1", "workspace_id": "w1"},
        sample=True,
    )

    assert payload["status"] == "partial"
    assert payload["summary"]["required_ready"] == 2
    assert payload["summary"]["required_total"] == 3
    assert payload["summary"]["optional_total"] == 4
    assert payload["privacy"] == {"pii_exposed": False, "sample_values_returned": False}
    blocked = {item["component"] for item in payload["blockers"]}
    assert "aspiration" in blocked
    performance = next(item for item in payload["components"] if item["id"] == "performance")
    assert performance["status"] == "ready"
    assert performance["selected_entity"] == "FormHeader"
    assert performance["entity"] == "PerformanceReview"
    assert performance["odata_entity"] == "FormHeader"
    assert performance["ready_to_extract"] is True
    assert "formDataId" in performance["fields_found"]
    targets = {(item["component"], item["entity"], item["odata_entity"]) for item in payload["extraction_targets"]}
    assert ("performance", "PerformanceReview", "FormHeader") in targets
    assert ("competency", "CompetencyEntity", "CompetencyEntity") in targets
    assert ("competency", "SkillProfile", "SkillProfile") in targets
    assert all("fields_found" in item for item in payload["extraction_targets"])
    assert all("fields_missing" in item for item in payload["extraction_targets"])
    assert all("ready_to_extract" in item for item in payload["extraction_targets"])
    assert all("redacted" not in item for item in payload["extraction_targets"])


def test_talent_metadata_readiness_uses_approved_tenant_aliases(monkeypatch):
    _, preflight = _import_modules()

    class FakeSapSfClient:
        def __init__(self, conn_id=None, security_context=None):
            self.conn_id = conn_id
            self.security_context = security_context

        def configuration_status(self):
            return {
                "cartridge": "sap_successfactors",
                "configured": True,
                "missing": [],
                "base_url": "https://example.successfactors.com/odata/v2",
            }

        def metadata_entities(self):
            return {
                "cust_PerformanceTalent": {
                    "externalCode",
                    "worker",
                    "rating",
                    "lastModifiedDateTime",
                },
                "CompetencyEntity": {"externalCode", "name", "lastModifiedDateTime"},
                "SkillProfile": {"externalCode", "skill", "lastModifiedDateTime"},
                "CareerInterest": {"externalCode", "userId", "interest", "lastModifiedDateTime"},
                "Position": {"code", "lastModifiedDateTime"},
            }

        def fetch_entity(self, entity, select=None, page_size=200, **_kwargs):
            assert entity != "PerformanceReview"
            return [{"redacted": True}]

    monkeypatch.setattr(preflight, "SapSfClient", FakeSapSfClient)
    monkeypatch.setattr(
        preflight,
        "_load_talent_alias_candidates",
        lambda **_kwargs: {
            "performance": [
                {
                    "entity": "cust_PerformanceTalent",
                    "odata_entity": "cust_PerformanceTalent",
                    "extract_entity": "PerformanceReview",
                    "group": "performance",
                    "scope": "tenant_config_alias",
                    "standard": False,
                    "fields_required": ("externalCode", "worker", "rating"),
                    "fields_optional": ("lastModifiedDateTime",),
                    "primary_key": "externalCode",
                    "watermark_field": "lastModifiedDateTime",
                    "alias_id": "alias-1",
                    "alias_source": "tenant_config",
                    "field_aliases": {"formSubjectId": "worker", "overallRating": "rating"},
                }
            ]
        },
    )

    payload = preflight.talent_metadata_readiness(
        conn_id="femsa_sf",
        security_context={"tenant_id": "t1", "workspace_id": "w1"},
        sample=True,
    )

    performance = next(item for item in payload["components"] if item["id"] == "performance")
    assert performance["status"] == "ready"
    assert performance["selected_entity"] == "cust_PerformanceTalent"
    assert performance["entity"] == "PerformanceReview"
    assert payload["summary"]["configured_aliases"] == 1

    target = next(item for item in payload["extraction_targets"] if item["component"] == "performance")
    assert target["entity"] == "PerformanceReview"
    assert target["odata_entity"] == "cust_PerformanceTalent"
    assert target["primary_key"] == "externalCode"
    assert target["watermark_field"] == "lastModifiedDateTime"
    assert target["field_aliases"]["overallRating"] == "rating"


def test_talent_metadata_readiness_discovers_custom_metadata_targets(monkeypatch):
    _, preflight = _import_modules()

    class FakeSapSfClient:
        def __init__(self, conn_id=None, security_context=None):
            self.conn_id = conn_id
            self.security_context = security_context

        def configuration_status(self):
            return {
                "cartridge": "sap_successfactors",
                "configured": True,
                "missing": [],
                "base_url": "https://example.successfactors.com/odata/v2",
            }

        def metadata_entities(self):
            return {
                "cust_TalentPerformanceReview": {
                    "externalCode",
                    "worker",
                    "rating",
                    "lastModifiedDateTime",
                },
                "cust_EmployeeSkillProfile": {
                    "externalCode",
                    "worker",
                    "competencyId",
                    "proficiency",
                    "lastModifiedDateTime",
                },
                "cust_CareerAspiration": {
                    "externalCode",
                    "worker",
                    "targetRole",
                    "readiness",
                    "lastModifiedDateTime",
                },
            }

        def fetch_entity(self, entity, select=None, page_size=200, **_kwargs):
            assert entity.startswith("cust_")
            assert page_size == 1
            assert select
            return [{"redacted": True}]

    monkeypatch.setattr(preflight, "SapSfClient", FakeSapSfClient)
    monkeypatch.setattr(preflight, "_load_talent_alias_candidates", lambda **_kwargs: {})

    payload = preflight.talent_metadata_readiness(
        conn_id="femsa_sf",
        security_context={"tenant_id": "t1", "workspace_id": "w1"},
        sample=True,
    )

    assert payload["status"] == "ready"
    assert payload["summary"]["configured_aliases"] == 0
    assert payload["summary"]["discovered_aliases"] >= 3
    targets = {
        (item["component"], item["entity"], item["odata_entity"]): item
        for item in payload["extraction_targets"]
    }
    performance = targets[("performance", "PerformanceReview", "cust_TalentPerformanceReview")]
    assert performance["alias_source"] == "metadata_discovery"
    assert performance["field_aliases"]["formSubjectId"] == "worker"
    assert performance["field_aliases"]["overallRating"] == "rating"
    competency = targets[("competency", "SkillProfile", "cust_EmployeeSkillProfile")]
    assert competency["field_aliases"]["userId"] == "worker"
    assert competency["field_aliases"]["skill"] == "competencyId"
    aspiration = targets[("aspiration", "CareerInterest", "cust_CareerAspiration")]
    assert aspiration["field_aliases"]["userId"] == "worker"


def test_talent_metadata_readiness_blocks_when_metadata_unavailable(monkeypatch):
    _, preflight = _import_modules()

    class FakeSapSfClient:
        def __init__(self, conn_id=None, security_context=None):
            pass

        def configuration_status(self):
            return {
                "cartridge": "sap_successfactors",
                "configured": True,
                "missing": [],
                "base_url": "https://example.successfactors.com/odata/v2",
            }

        def metadata_entities(self):
            from app.core.sap_client import SAPClientError

            raise SAPClientError("metadata HTTP 403")

    monkeypatch.setattr(preflight, "SapSfClient", FakeSapSfClient)

    payload = preflight.talent_metadata_readiness(conn_id="femsa_sf")

    assert payload["status"] == "blocked"
    assert payload["blockers"][0]["reason"] == "metadata_unavailable"
