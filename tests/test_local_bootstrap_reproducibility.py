from __future__ import annotations

import re
from pathlib import Path


REPO = Path(__file__).resolve().parents[1]
MAKEFILE = REPO / "Makefile"
README = REPO / "README.md"
RUNBOOK_01 = REPO / "docs/runbook/01_arrancar_desde_cero.md"
RUNBOOK_06 = REPO / "docs/runbook/06_backup_restore.md"
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


def test_runbooks_do_not_bypass_guarded_nuke_for_local_resets():
    for path in (RUNBOOK_01, RUNBOOK_06):
        src = path.read_text(encoding="utf-8")
        assert "docker compose -f infra/docker-compose.yml down -v" not in src
        assert "make nuke CONFIRM=NUKE NUKE_SCOPE=local-dev" in src


def test_readme_documents_reproducible_local_bootstrap():
    src = README.read_text(encoding="utf-8")
    for needle in (
        "make preflight",
        "make up",
        "make nuke CONFIRM=NUKE NUKE_SCOPE=local-dev",
        "make repair-local-stack",
        "Never use it for AWS",
    ):
        assert needle in src
