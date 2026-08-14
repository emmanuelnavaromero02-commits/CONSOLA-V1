#!/usr/bin/env python3
"""Verify the complete release-test harness against a reviewed full-file seal."""

from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from scripts.verify_test_skip_policy import DEFAULT_POLICY, PYTEST_ROOTS, REPO

DEFAULT_SEAL = REPO / ".github" / "release-test-harness-seal.json"
FIXED_FILES = {
    ".github/workflows/release.yml",
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
    "scripts/apply_db_migrations.sh",
    "scripts/load_release_dotenv.py",
    "scripts/release_digest_chain.py",
    "scripts/release_digest_env.py",
    "scripts/release_docker_lock.py",
    "scripts/run-e2e.sh",
    "scripts/run_full_stack_acceptance.sh",
    "scripts/run_multiuser_isolation_simulation.sh",
    "scripts/run_stress.sh",
    "scripts/stress_summary.py",
    "scripts/run_release_playwright.py",
    "scripts/run_release_pytest.py",
    "scripts/verify_release_test_harness.py",
    "scripts/verify_release_digest_runtime.py",
    "scripts/verify_release_digest_remote.py",
    "scripts/verify_release_source_checkout.py",
    "scripts/validate_github_release_state.py",
    "scripts/verify_test_skip_policy.py",
    "scripts/smoke_test.sh",
    "scripts/wait_for_health.sh",
    "tests-e2e/global-setup.ts",
    "tests-e2e/package-lock.json",
    "tests-e2e/package.json",
    "tests-e2e/playwright.config.ts",
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
                        part.startswith(".")
                        for part in path.relative_to(repo).parts
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


def verify(seal_path: Path = DEFAULT_SEAL, repo: Path = REPO) -> None:
    try:
        seal = json.loads(seal_path.read_text("utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise HarnessSealError("release harness seal cannot be read") from exc
    if not isinstance(seal, dict) or set(seal) != {"schema_version", "files"}:
        raise HarnessSealError("release harness seal schema is invalid")
    if seal.get("schema_version") != 1 or not isinstance(seal.get("files"), list):
        raise HarnessSealError("release harness seal identity is invalid")
    if seal["files"] != inventory(repo):
        raise HarnessSealError("release harness full-file seal differs from the checkout")


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
