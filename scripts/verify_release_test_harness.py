#!/usr/bin/env python3

from __future__ import annotations

import hashlib
import json
import os
import re
import sys
from collections.abc import Mapping
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
REPO = REPO_ROOT
DEFAULT_POLICY = REPO / ".github" / "release-test-skip-policy.json"
PYTEST_ROOTS = (
    "tests",
    "console/tests",
    "workspace/tests",
    "vault/tests",
    "refinement/tests",
    "mcp-infra/tests",
    "cartridges",
    "airflow",
)
DEFAULT_SEAL = REPO / ".github" / "release-test-harness-seal.json"
EXPECTED_SEAL_SHA256_ENV = "OMEGA_RELEASE_TEST_HARNESS_SHA256"
SHA256 = re.compile(r"[0-9a-f]{64}")
IMPORT_HOOK_ROOTS = (
    ".",
    "console",
    "refinement",
    "vault",
    "workspace",
    "mcp-infra",
)
FORBIDDEN_IMPORT_SHADOWS = (
    "sitecustomize.py",
    "usercustomize.py",
    "pytest.py",
    "pytest",
    "pytest_asyncio.py",
    "pytest_asyncio",
    "anyio.py",
    "anyio",
)
FIXED_FILES = {
    ".github/workflows/release.yml",
    ".github/workflows/release-runner-capacity.yml",
    ".github/release-test-skip-policy.json",
    "infra/docker-compose.yml",
    "infra/docker-compose.dev.yml",
    "infra/terraform-gcp/release/docker-compose.release.yml",
    "infra/bootstrap.sh",
    "infra/bootstrap-keys.sh",
    "Makefile",
    "pyproject.toml",
    "pytest.ini",
    "scripts/production_readiness.sh",
    "scripts/prepare_release_runner_disk.py",
    "scripts/prepare_refinement_duckdb_ci.sh",
    "scripts/apply_db_migrations.sh",
    "scripts/load_release_dotenv.py",
    "scripts/release_digest_chain.py",
    "scripts/release_digest_env.py",
    "scripts/release_docker_lock.py",
    "scripts/release_image_promotion.py",
    "scripts/run-e2e.sh",
    "scripts/run_full_stack_acceptance.sh",
    "scripts/run_multiuser_isolation_simulation.sh",
    "scripts/run_refinement_duckdb_offline_smoke.sh",
    "scripts/run_stress.sh",
    "scripts/stress_summary.py",
    "scripts/run_release_playwright.py",
    "scripts/normalize_release_runtime_permissions.py",
    "scripts/run_release_pytest.py",
    "scripts/secure_release_output.py",
    "scripts/verify_release_test_harness.py",
    "scripts/verify_release_digest_runtime.py",
    "scripts/verify_release_digest_remote.py",
    "scripts/verify_release_source_checkout.py",
    "scripts/validate_github_release_state.py",
    "scripts/verify_test_skip_policy.py",
    "scripts/smoke_test.sh",
    "scripts/wait_for_health.sh",
    "tests/fixtures/app_grants_schema.sql",
    "tests/fixtures/app_grants_seed.sql",
    "infra/init/99zzt_analytic_app_dataset_grants.sql",
    "infra/init/99zzu_analytic_app_manifest_registry.sql",
    "infra/init/99zzy_analytic_app_grants_owner_rls_repair.sql",
    "infra/init/99zzzzf_analytic_app_grant_convergence.sql",
    "infra/init/99zzzzm_analytic_app_manifest_registry_sap_b1.sql",
    "infra/init/99zzzzq_analytic_app_manifest_registry_sap_b1_poc.sql",
    "tests-e2e/global-setup.ts",
    "tests-e2e/package-lock.json",
    "tests-e2e/package.json",
    "tests-e2e/playwright.config.ts",
    "tests/fixtures/docker-compose.gold-repair.yml",
    "tests/fixtures/release-compose-mount-allowlist.json",
}


class HarnessSealError(RuntimeError):
    pass


def _policy_skip_files(repo: Path) -> set[str]:
    try:
        value = json.loads((repo / DEFAULT_POLICY.relative_to(REPO)).read_text("utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise HarnessSealError("release skip policy cannot be read") from exc
    scopes = value.get("source_scopes") if isinstance(value, dict) else None
    if not isinstance(scopes, list):
        raise HarnessSealError("release skip policy source scopes are invalid")
    paths: set[str] = set()
    for scope in scopes:
        details = scope.get("scope") if isinstance(scope, dict) else None
        files = details.get("files") if isinstance(details, dict) else None
        if not isinstance(files, list):
            raise HarnessSealError("release skip policy file inventory is invalid")
        for entry in files:
            path = entry.get("path") if isinstance(entry, dict) else None
            if not isinstance(path, str) or not path:
                raise HarnessSealError("release skip policy contains an invalid path")
            paths.add(path)
    return paths


def discover(repo: Path = REPO) -> list[str]:
    paths = set(FIXED_FILES) | _policy_skip_files(repo)
    for root_name in PYTEST_ROOTS:
        root = repo / root_name
        ancestor = root.parent
        while ancestor != repo and repo in ancestor.parents:
            candidate = ancestor / "conftest.py"
            if candidate.is_file():
                paths.add(candidate.relative_to(repo).as_posix())
            ancestor = ancestor.parent
        if root.exists():
            paths.update(
                path.relative_to(repo).as_posix()
                for path in root.rglob("*.py")
                if not any(
                    part.startswith(".") for part in path.relative_to(repo).parts
                )
            )
            for config_name in ("pytest.ini", "pyproject.toml", "setup.cfg", "tox.ini"):
                paths.update(
                    path.relative_to(repo).as_posix()
                    for path in root.rglob(config_name)
                    if not any(
                        part.startswith(".") for part in path.relative_to(repo).parts
                    )
                )
    playwright = repo / "tests-e2e"
    if playwright.exists():
        paths.update(
            path.relative_to(repo).as_posix()
            for suffix in ("*.ts", "*.js", "*.mjs", "*.cjs")
            for path in playwright.rglob(suffix)
            if not any(
                part in {"node_modules", "playwright-report", "test-results"}
                or part.startswith(".")
                for part in path.relative_to(repo).parts
            )
        )
    missing = [path for path in sorted(paths) if not (repo / path).is_file()]
    if missing:
        raise HarnessSealError(f"release harness file is missing: {missing[0]}")
    return sorted(paths)


def inventory(repo: Path = REPO) -> list[dict[str, str]]:
    return [
        {
            "path": relative,
            "sha256": hashlib.sha256((repo / relative).read_bytes()).hexdigest(),
        }
        for relative in discover(repo)
    ]


def verify(
    seal_path: Path = DEFAULT_SEAL,
    repo: Path = REPO,
    *,
    environ: Mapping[str, str] | None = None,
) -> None:
    environment = os.environ if environ is None else environ
    for root in IMPORT_HOOK_ROOTS:
        for name in FORBIDDEN_IMPORT_SHADOWS:
            if (repo / root / name).exists():
                raise HarnessSealError(
                    f"unreviewed Python import hook or runner shadow is present: {root}/{name}"
                )
    try:
        seal_bytes = seal_path.read_bytes()
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise HarnessSealError("release harness seal cannot be read") from exc
    if (
        environment.get("OMEGA_RELEASE_DIGEST_STACK") == "1"
        or environment.get("OMEGA_RELEASE_TEST_SKIP_ENVIRONMENT") == "release"
        or EXPECTED_SEAL_SHA256_ENV in environment
    ):
        expected_sha256 = environment.get(EXPECTED_SEAL_SHA256_ENV, "")
        if SHA256.fullmatch(expected_sha256) is None:
            raise HarnessSealError(
                "release harness authority SHA-256 is missing or invalid"
            )
        actual_sha256 = hashlib.sha256(seal_bytes).hexdigest()
        if actual_sha256 != expected_sha256:
            raise HarnessSealError(
                "release harness seal differs from the source-bound authority SHA-256"
            )
    try:
        seal = json.loads(seal_bytes.decode("utf-8"))
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise HarnessSealError("release harness seal cannot be read") from exc
    if not isinstance(seal, dict) or set(seal) != {"schema_version", "files"}:
        raise HarnessSealError("release harness seal schema is invalid")
    if seal.get("schema_version") != 1 or not isinstance(seal.get("files"), list):
        raise HarnessSealError("release harness seal identity is invalid")
    if seal["files"] != inventory(repo):
        raise HarnessSealError(
            "release harness full-file seal differs from the checkout"
        )


def main() -> int:
    try:
        verify()
        count = len(inventory())
    except (HarnessSealError, OSError) as exc:
        print(f"RELEASE TEST HARNESS BLOCKED: {exc}", file=sys.stderr)
        return 1
    print(f"RELEASE TEST HARNESS PASS: {count} files sealed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
