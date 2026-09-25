from __future__ import annotations

import json
from pathlib import Path

import yaml


REPO = Path(__file__).resolve().parents[1]
WORKFLOW = REPO / ".github/workflows/release.yml"
SKIP_POLICY = REPO / ".github/release-test-skip-policy.json"
HARNESS_VERIFIER = REPO / "scripts/verify_release_test_harness.py"


def _validate_release() -> dict[str, object]:
    workflow = yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))
    return workflow["jobs"]["validate-release"]


def _named_step(job: dict[str, object], name: str) -> dict[str, object]:
    return next(step for step in job["steps"] if step.get("name") == name)


def test_validate_release_runs_app_grant_regressions_on_real_postgres():
    validate = _validate_release()
    preload = _named_step(
        validate, "Preload PostgreSQL images for pull-never release tests"
    )
    provision = _named_step(
        validate, "Provision app-grant PostgreSQL for detected root tests"
    )
    root_tests = _named_step(validate, "Run detected root tests")
    cleanup = _named_step(validate, "Remove app-grant PostgreSQL")

    assert provision["id"] == "app_grants_postgres"
    assert provision["if"] == (
        "needs.detect-release-changes.outputs.root_tests == 'true' && "
        "contains(format(' {0} ', "
        "needs.detect-release-changes.outputs.root_test_targets), "
        "' tests/test_analytic_app_dataset_grants.py ')"
    )
    container_name = (
        "omega-release-app-grants-${{ github.run_id }}-${{ github.run_attempt }}"
    )
    assert provision["env"]["APP_GRANTS_POSTGRES_CONTAINER"] == container_name
    source = provision["run"]
    for needle in (
        "docker run --pull=never --detach --rm",
        "--publish 127.0.0.1:55432:5432",
        "postgres:15.18",
        "for _attempt in $(seq 1 60)",
        '"SELECT 1" >/dev/null 2>&1',
        'docker exec -i "${APP_GRANTS_POSTGRES_CONTAINER}"',
        "-v ON_ERROR_STOP=1",
        "grants_before=",
        "events_before=",
        'test "${grants_before}" -gt 0',
        'test "${events_before}" -gt 0',
        'test "${grants_after}" = "${grants_before}"',
        'test "${events_after}" = "${events_before}"',
        "dsn=postgresql://postgres:postgres@127.0.0.1:55432/app_grants",
    ):
        assert needle in source
    ordered_inputs = (
        "tests/fixtures/app_grants_schema.sql",
        "infra/init/99zzt_analytic_app_dataset_grants.sql",
        "infra/init/99zzu_analytic_app_manifest_registry.sql",
        "infra/init/99zzy_analytic_app_grants_owner_rls_repair.sql",
        "tests/fixtures/app_grants_seed.sql",
        "infra/init/99zzzzf_analytic_app_grant_convergence.sql",
        "infra/init/99zzzzm_analytic_app_manifest_registry_sap_b1.sql",
    )
    for path in ordered_inputs:
        assert path in source
    assert [source.index(path) for path in ordered_inputs] == sorted(
        source.index(path) for path in ordered_inputs
    )
    assert "|| true" not in source

    assert root_tests["env"]["APP_GRANTS_DSN"] == (
        "${{ steps.app_grants_postgres.outputs.dsn }}"
    )
    assert 'export OMEGA_TEST_GRANTS_DSN="${APP_GRANTS_DSN}"' in root_tests["run"]
    assert "unset OMEGA_TEST_GRANTS_DSN" in root_tests["run"]
    policy = json.loads(SKIP_POLICY.read_text(encoding="utf-8"))
    pytest_scope = next(
        scope for scope in policy["runtime_scopes"] if scope["runner"] == "pytest"
    )
    assert not any(
        authorization["nodeid"].startswith("tests/test_analytic_app_dataset_grants.py")
        for authorization in pytest_scope["scope"]["authorizations"]
    )
    assert cleanup["if"] == (
        "always() && steps.app_grants_postgres.outcome != 'skipped'"
    )
    assert cleanup["env"]["APP_GRANTS_POSTGRES_CONTAINER"] == container_name
    assert "docker container inspect" in cleanup["run"]
    assert "docker rm --force --volumes" in cleanup["run"]

    steps = validate["steps"]
    assert (
        steps.index(preload)
        < steps.index(provision)
        < steps.index(root_tests)
        < steps.index(cleanup)
    )


def test_app_grant_database_inputs_are_source_bound_and_failed_version_is_not_reused():
    verifier = HARNESS_VERIFIER.read_text(encoding="utf-8")
    for path in (
        "tests/fixtures/app_grants_schema.sql",
        "tests/fixtures/app_grants_seed.sql",
        "infra/init/99zzt_analytic_app_dataset_grants.sql",
        "infra/init/99zzu_analytic_app_manifest_registry.sql",
        "infra/init/99zzy_analytic_app_grants_owner_rls_repair.sql",
        "infra/init/99zzzzf_analytic_app_grant_convergence.sql",
        "infra/init/99zzzzm_analytic_app_manifest_registry_sap_b1.sql",
    ):
        assert f'"{path}"' in verifier
    assert (REPO / "VERSION").read_text(encoding="utf-8").strip() != (
        "1.45.225-beta"
    )
