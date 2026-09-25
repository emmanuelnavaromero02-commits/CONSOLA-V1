from __future__ import annotations

import re
from pathlib import Path


REPO = Path(__file__).resolve().parents[1]
MAKEFILE = REPO / "Makefile"
REPAIR = REPO / "scripts/local_stack_repair.sh"


def _target_body(name: str) -> str:
    body = MAKEFILE.read_text(encoding="utf-8")
    match = re.search(rf"^{name}:\s*$([\s\S]+?)(?=^\S|\Z)", body, re.MULTILINE)
    assert match, f"{name} target not found"
    return match.group(1)


def test_nuke_requires_confirm_and_local_scope():
    body = _target_body("nuke")
    assert '$(CONFIRM)" != "NUKE"' in body
    assert '$(NUKE_SCOPE)" != "local-dev"' in body
    assert "docker-compose\\.aws|terraform/deploy" in body
    assert "down -v --remove-orphans" in body


def test_bootstrap_env_target_is_reused_by_startup_and_release():
    makefile = MAKEFILE.read_text(encoding="utf-8")
    assert "bootstrap-env:" in makefile
    assert "bash infra/bootstrap.sh" in makefile
    assert "bash infra/bootstrap-keys.sh infra/.env" in makefile
    for target in ("up", "up-core", "verify-release"):
        assert "$(MAKE) bootstrap-env" in _target_body(target)


def test_local_repair_script_is_local_only_and_explicit_for_superset():
    src = REPAIR.read_text(encoding="utf-8")
    assert "LOCAL_REPAIR_SCOPE" in src
    assert "local-dev" in src
    assert "reconcile_db_passwords.sh" in src
    assert "CONFIRM_SUPERSET_METASTORE_REPAIR" in src
    assert "LOCAL_SUPERSET_REPAIR" in src
    assert "OMEGA_PRODUCTION_HOST" in src
