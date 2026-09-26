from __future__ import annotations

import hashlib
import re
from pathlib import Path

import yaml

from scripts.generate_app_manifest_registry import TARGET, render

REPO = Path(__file__).resolve().parents[1]
FROZEN = {
    "99zzu_analytic_app_manifest_registry.sql": "adad0c9f710ce80aa5ec46dad16b919233f1f0f20ad8fd6d9af7984197577123",
    "99zzzzm_analytic_app_manifest_registry_sap_b1.sql": "39d3cde28e6c3b1ba6a3412c4237b0068b53c44fd9f46173b0ed0cb1a1729044",
}
REGISTRY_FILES = sorted(
    path.name for path in (REPO / "infra/init").glob("*_analytic_app_manifest_registry*.sql")
)


def test_applied_registry_migrations_keep_their_bytes():
    for name, digest in FROZEN.items():
        assert hashlib.sha256((REPO / "infra/init" / name).read_bytes()).hexdigest() == digest, name


def test_the_generator_writes_a_new_migration_after_every_applied_one():
    assert TARGET.name not in FROZEN
    assert REGISTRY_FILES == sorted([*FROZEN, TARGET.name])
    assert REGISTRY_FILES[-1] == TARGET.name
    assert TARGET.read_text(encoding="utf-8") == render()


def _apply_order(text: str) -> list[str]:
    return [name for name in re.findall(r"infra/init/([0-9a-z_]+\.sql)", text) if name in REGISTRY_FILES]


def test_every_app_grant_database_applies_the_registry_files_in_order():
    for workflow in (".github/workflows/release.yml", ".github/workflows/control-room-postgres-rls.yml"):
        jobs = yaml.safe_load((REPO / workflow).read_text(encoding="utf-8"))["jobs"]
        runs = [
            step["run"]
            for job in jobs.values()
            for step in job.get("steps", [])
            if "app_grants" in str(step.get("run", "")) and "99zzt_" in str(step.get("run", ""))
        ]
        assert runs, workflow
        for run in runs:
            order = _apply_order(run)
            assert order == REGISTRY_FILES * (len(order) // len(REGISTRY_FILES)), workflow
            assert len(order) >= 2 * len(REGISTRY_FILES), workflow
    verifier = (REPO / "scripts/verify_release_test_harness.py").read_text(encoding="utf-8")
    for name in REGISTRY_FILES:
        assert f'"infra/init/{name}"' in verifier
