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
    from app.core.sap_client import SAPClientError
    from app.services import preflight

    return SAPClientError, preflight


_FULL_METADATA = {
    "User": {"userId", "lastModifiedDateTime", "username", "status", "department", "manager"},
    "EmpEmployment": {"personIdExternal", "userId", "startDate", "lastModifiedDateTime"},
    "EmpJob": {"userId", "jobCode", "department", "managerId", "lastModifiedDateTime"},
    "PerPersonal": {"personIdExternal", "startDate", "lastName", "lastModifiedDateTime"},
    "PerPerson": {"personIdExternal", "personId", "lastModifiedDateTime"},
    "FOJobCode": {"externalCode", "name_defaultValue", "status", "lastModifiedDateTime"},
    "Position": {"code", "department", "location", "lastModifiedDateTime"},
}


def _client_factory(preflight, *, metadata, deny=(), empty=()):
    from app.core.sap_client import SAPClientError

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
            return {k: set(v) for k, v in metadata.items()}

        def fetch_entity(self, entity, select=None, page_size=200, **_kwargs):
            assert page_size == 1
            assert select
            if entity in deny:
                raise SAPClientError(
                    f"SuccessFactors HTTP 403 para {entity}; sentinel-body-must-not-leak"
                )
            if entity in empty:
                return []
            return [{"redacted": True}]

    return FakeSapSfClient


def test_people_master_all_required_ready(monkeypatch):
    _, preflight = _import_modules()
    monkeypatch.setattr(
        preflight, "SapSfClient", _client_factory(preflight, metadata=_FULL_METADATA)
    )

    payload = preflight.people_master_readiness(
        conn_id="femsa_sf",
        security_context={"trusted": True, "tenant_id": "t1", "workspace_id": "w1"},
        sample=True,
    )

    assert payload["status"] == "ready"
    assert payload["summary"]["required_ready"] == 3
    assert payload["summary"]["required_total"] == 3
    assert payload["summary"]["optional_total"] == 4
    assert payload["blockers"] == []
    by_id = {c["id"]: c for c in payload["components"]}
    for required_id in ("user", "emp_employment", "emp_job"):
        assert by_id[required_id]["status"] == "ready"
        assert by_id[required_id]["ready_to_extract"] is True
    assert payload["privacy"] == {"pii_exposed": False, "sample_values_returned": False}
    assert all("redacted" not in t for t in payload["extraction_targets"])


def test_people_master_permission_blocked_surfaces_per_entity_checklist(monkeypatch):
    _, preflight = _import_modules()
    monkeypatch.setattr(
        preflight,
        "SapSfClient",
        _client_factory(preflight, metadata=_FULL_METADATA, deny=("EmpJob",)),
    )

    payload = preflight.people_master_readiness(conn_id="femsa_sf", sample=True)

    assert payload["status"] == "partial"
    assert payload["summary"]["required_ready"] == 2
    blocked_components = {b["component"] for b in payload["blockers"]}
    assert "emp_job" in blocked_components

    emp_job_blocker = next(b for b in payload["blockers"] if b["component"] == "emp_job")
    statuses = {s["entity"]: s for s in emp_job_blocker["candidate_statuses"]}
    assert statuses["EmpJob"]["status"] == "permission_blocked"
    assert statuses["EmpJob"]["reason"] == "permission_denied"
    emp_job_component = next(c for c in payload["components"] if c["id"] == "emp_job")
    denied = next(c for c in emp_job_component["candidates"] if c["entity"] == "EmpJob")
    assert denied["failure_code"] == "metadata_access_denied"
    assert "error" not in denied
    assert "sentinel-body-must-not-leak" not in repr(payload)


def test_people_master_missing_entity_reported(monkeypatch):
    _, preflight = _import_modules()
    metadata = {k: v for k, v in _FULL_METADATA.items() if k != "EmpJob"}
    monkeypatch.setattr(
        preflight, "SapSfClient", _client_factory(preflight, metadata=metadata)
    )

    payload = preflight.people_master_readiness(conn_id="femsa_sf", sample=True)

    assert payload["status"] == "partial"
    emp_job = next(c for c in payload["components"] if c["id"] == "emp_job")
    candidate = emp_job["candidates"][0]
    assert candidate["status"] == "missing"
    assert candidate["reason"] == "entity_not_exposed_in_sap"


def test_people_master_blocked_when_not_configured(monkeypatch):
    _, preflight = _import_modules()

    class UnconfiguredClient:
        def __init__(self, *_a, **_k):
            pass

        def configuration_status(self):
            return {"cartridge": "sap_successfactors", "configured": False, "missing": ["SF_BASE_URL"]}

    monkeypatch.setattr(preflight, "SapSfClient", UnconfiguredClient)

    payload = preflight.people_master_readiness(conn_id=None, sample=True)
    assert payload["status"] == "blocked"
    assert payload["configured"] is False
    assert payload["summary"]["required_total"] == 3
    assert payload["summary"]["optional_total"] == 4
