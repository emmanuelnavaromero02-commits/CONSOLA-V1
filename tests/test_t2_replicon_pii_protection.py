from __future__ import annotations

import importlib
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]


def _load_protection():
    sys.path[:] = [p for p in sys.path if "/cartridges/" not in p]
    sys.path.insert(0, str(REPO_ROOT / "cartridges" / "replicon"))
    for mod in [m for m in list(sys.modules) if m.startswith("app.")]:
        sys.modules.pop(mod, None)
    sys.modules.pop("app", None)
    return importlib.import_module("app.services.protection_service")


def test_user_contact_identity_is_protected():
    ps = _load_protection()
    rules = ps._get_entity_protection("User")
    assert rules.get("firstname") == "masked"
    assert rules.get("lastname") == "masked"
    assert rules.get("email") == "masked"
    assert rules.get("loginname") == "masked"
    assert rules.get("externalid") == "shadowed"


def test_protection_applies_on_rows():
    ps = _load_protection()
    rows = [{
        "userid": 7, "username": "eperez",
        "firstname": "Emmanuel", "lastname": "Perez",
        "email": "emmanuel@cliente.mx", "loginname": "eperez01",
        "externalid": "EXT-991", "currenthourlycostamount": 85.5,
    }]
    out = ps.apply_protection_for_entity("User", rows)[0]
    assert out["firstname"] != "Emmanuel" and out["firstname"].startswith("*")
    assert out["lastname"] != "Perez"
    assert out["email"] != "emmanuel@cliente.mx" and "@" not in out["email"].replace("*", "x") or "*" in out["email"]
    assert out["loginname"] != "eperez01"
    assert out["externalid"] != "EXT-991", "shadowed: hash estable, no el claro"
    assert out["externalid"] == ps.apply_protection_for_entity(
        "User", [{"externalid": "EXT-991"}]
    )[0]["externalid"], "shadow determinista (los joins por externalid sobreviven)"
    assert out["username"] == "eperez", "username plano (llave de join/display)"
    assert out["currenthourlycostamount"] == 85.5, "tarifas numéricas intactas"


def test_no_other_entity_regressed():
    ps = _load_protection()
    for entity in ("Client", "Project", "TimeEntry", "Timesheet"):
        assert ps._get_entity_protection(entity) == {}
