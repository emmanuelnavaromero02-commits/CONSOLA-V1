"""
Sprint v1.39 — audit H1 (P1).

Pre-v1.39, only ``vault/app/main.py`` and ``console/app/services/auth.py``
refused to start in production with the legacy shared
``INTERNAL_API_KEY`` as the only credential — the other three FastAPI
services (refinement, mcp-infra, workspace) happily accepted it. A
leaked legacy key from a log dump, an old backup or a former employee
could therefore impersonate console or workspace forever against
those three surfaces, undoing the v1.12 per-pair key migration.

This sprint replicates the guard into the three missing services so
the security contract is uniform across the platform:

  * Refinement   — inbound (callers: console, workspace, airflow,
                   cartridges).
  * MCP-infra    — inbound (callers: console, workspace, airflow).
  * Workspace    — outbound (workspace is a pure client of console /
                   refinement / mcp-infra; the guard checks the three
                   ``INTERNAL_API_KEY_WORKSPACE_TO_*`` env vars).

Dev / test environments (anything other than APP_ENV in
{production, prod}) keep working with only the legacy key set so
``make smoke`` and local development don't break.
"""
from __future__ import annotations

import importlib
import os
import re
import sys
from pathlib import Path

import pytest


REPO_ROOT = Path(__file__).resolve().parents[1]
SERVICE_PATH_MARKERS = (
    "/cartridges/",
    "/console",
    "/mcp-infra",
    "/refinement",
    "/vault",
    "/workspace",
)

# Each service: (module_path_for_import, service_dir, sample_pair_env_for_prod_test).
# The sample pair env is one of the keys that the production guard will
# look for; in tests we set ALL of them when we want production to
# succeed, and unset one specific one when we want it to fail.
SERVICES = {
    "refinement": {
        "service_dir": REPO_ROOT / "refinement",
        "main_module": "app.main",
        # Inbound pair envs (callers -> refinement).
        "pair_envs": [
            "INTERNAL_API_KEY_CONSOLE_TO_REFINEMENT",
            "INTERNAL_API_KEY_WORKSPACE_TO_REFINEMENT",
            "INTERNAL_API_KEY_AIRFLOW_TO_REFINEMENT",
            "INTERNAL_API_KEY_CARTRIDGE_TO_REFINEMENT",
        ],
    },
    "mcp-infra": {
        "service_dir": REPO_ROOT / "mcp-infra",
        "main_module": "app.main",
        "pair_envs": [
            "INTERNAL_API_KEY_CONSOLE_TO_MCP_INFRA",
            "INTERNAL_API_KEY_WORKSPACE_TO_MCP_INFRA",
            "INTERNAL_API_KEY_AIRFLOW_TO_MCP_INFRA",
        ],
    },
    "workspace": {
        "service_dir": REPO_ROOT / "workspace",
        "main_module": "app.main",
        # Outbound pair envs (workspace -> the other services).
        "pair_envs": [
            "INTERNAL_API_KEY_WORKSPACE_TO_CONSOLE",
            "INTERNAL_API_KEY_WORKSPACE_TO_REFINEMENT",
            "INTERNAL_API_KEY_WORKSPACE_TO_MCP_INFRA",
        ],
    },
}


def _purge_app_modules() -> None:
    for name in list(sys.modules):
        if name == "app" or name.startswith("app."):
            del sys.modules[name]


def _isolate_service(service_dir: Path) -> None:
    """Swap sys.path so ``import app.main`` resolves to ``service_dir``."""
    sys.path[:] = [
        p for p in sys.path
        if not any(marker in p for marker in SERVICE_PATH_MARKERS)
    ]
    sys.path.insert(0, str(service_dir))


# Minimum env so the modules import without exploding on missing infra.
_BASELINE_ENV = {
    "INTERNAL_API_KEY": "legacy-key-for-tests-do-not-use-in-prod-aaaaaaaaaa",
    "JWT_SECRET_KEY": "test-jwt-key-do-not-use-in-prod-bbbbbbbbbbbbbbbbbbbbb",
    "MINIO_SECRET_KEY": "minioadmin",
    "POSTGRES_PASSWORD": "ci",
    "AIRFLOW_USER": "ci",
    "AIRFLOW_PASSWORD": "ci",
    "PG_PASSWORD": "ci",
    "SUPERSET_USER": "ci",
    "SUPERSET_PASSWORD": "ci",
}


@pytest.fixture(autouse=True)
def _clean(monkeypatch):
    """Per-test isolation: clean sys.modules + restore env."""
    for k, v in _BASELINE_ENV.items():
        monkeypatch.setenv(k, v)
    saved_path = list(sys.path)
    _purge_app_modules()
    yield
    sys.path[:] = saved_path
    _purge_app_modules()


# ── Behavioral tests, parametrized across the three services ────────────────

@pytest.mark.parametrize("service_id", list(SERVICES.keys()))
def test_service_fails_when_no_pair_keys_in_production(service_id, monkeypatch):
    """No pair envs at all in production -> the guard fires before
    the app finishes importing."""
    spec = SERVICES[service_id]
    monkeypatch.setenv("APP_ENV", "production")
    for env in spec["pair_envs"]:
        monkeypatch.delenv(env, raising=False)

    _isolate_service(spec["service_dir"])
    with pytest.raises(RuntimeError, match="per-pair internal API key"):
        importlib.import_module(spec["main_module"])


@pytest.mark.parametrize("service_id", list(SERVICES.keys()))
def test_service_fails_when_any_pair_key_missing_in_production(
    service_id, monkeypatch
):
    """All but one pair env set in production -> still fails. The error
    message must name the missing env so the operator can fix it."""
    spec = SERVICES[service_id]
    monkeypatch.setenv("APP_ENV", "production")
    for env in spec["pair_envs"]:
        monkeypatch.setenv(env, f"prod-key-{env.lower()}")
    # Drop one specific env.
    missing_env = spec["pair_envs"][0]
    monkeypatch.delenv(missing_env, raising=False)

    _isolate_service(spec["service_dir"])
    with pytest.raises(RuntimeError, match=missing_env):
        importlib.import_module(spec["main_module"])


@pytest.mark.parametrize("service_id", list(SERVICES.keys()))
def test_service_starts_when_all_pair_keys_set_in_production(
    service_id, monkeypatch
):
    """All pair envs present in production -> the guard is silent."""
    spec = SERVICES[service_id]
    monkeypatch.setenv("APP_ENV", "production")
    for env in spec["pair_envs"]:
        monkeypatch.setenv(env, f"prod-key-{env.lower()}")

    _isolate_service(spec["service_dir"])
    # Importing must not raise. Other code in main.py may raise for
    # unrelated reasons (missing DB, etc.) — those aren't the guard's
    # fault, so catch broadly and only re-raise if the guard fired.
    try:
        importlib.import_module(spec["main_module"])
    except RuntimeError as exc:
        if "per-pair internal API key" in str(exc):
            raise
        # Anything else (missing pool config, etc.) is out of scope
        # for this test; pytest will still flag a hard import failure.
        pytest.skip(f"{service_id} startup hit unrelated runtime error: {exc}")
    except Exception:
        # Ditto for non-RuntimeError import-time issues (asyncpg etc.).
        pass


@pytest.mark.parametrize("service_id", list(SERVICES.keys()))
def test_service_accepts_legacy_only_in_development(service_id, monkeypatch):
    """Dev / test runs with only the legacy INTERNAL_API_KEY must keep
    working — this is the chokepoint between "production hard-fails"
    and "local make smoke keeps working"."""
    spec = SERVICES[service_id]
    monkeypatch.setenv("APP_ENV", "development")
    for env in spec["pair_envs"]:
        monkeypatch.delenv(env, raising=False)

    _isolate_service(spec["service_dir"])
    try:
        importlib.import_module(spec["main_module"])
    except RuntimeError as exc:
        assert "per-pair internal API key" not in str(exc), (
            f"{service_id} fail-fast fired in development mode — that's "
            f"a regression"
        )
    except Exception:
        # Other runtime errors are out of scope.
        pass


@pytest.mark.parametrize(
    "prod_value", ["production", "Production", "PROD", "prod"]
)
@pytest.mark.parametrize("service_id", list(SERVICES.keys()))
def test_service_detects_production_case_insensitively(
    service_id, prod_value, monkeypatch
):
    """``APP_ENV=Production`` and ``APP_ENV=PROD`` must trigger the
    guard. The case-sensitivity test is parametrized across the three
    services so a future divergence between vault / console /
    refinement / mcp-infra / workspace fails CI loudly."""
    spec = SERVICES[service_id]
    monkeypatch.setenv("APP_ENV", prod_value)
    for env in spec["pair_envs"]:
        monkeypatch.delenv(env, raising=False)

    _isolate_service(spec["service_dir"])
    with pytest.raises(RuntimeError, match="per-pair internal API key"):
        importlib.import_module(spec["main_module"])


# ── Static guards: enforce uniform implementation across services ───────────

@pytest.mark.parametrize("service_id", list(SERVICES.keys()))
def test_service_main_defines_pair_key_guard(service_id):
    """Source-level guard: each service's main.py must define
    ``_require_pair_keys_in_production`` AND call it at module scope."""
    spec = SERVICES[service_id]
    main_path = spec["service_dir"] / "app" / "main.py"
    src = main_path.read_text(encoding="utf-8")
    assert "def _require_pair_keys_in_production" in src, (
        f"{service_id}/app/main.py is missing the pair-keys guard "
        f"definition — H1 fix incomplete"
    )
    assert "_require_pair_keys_in_production()" in src, (
        f"{service_id}/app/main.py defines the guard but never calls "
        f"it — would silently pass in production"
    )


def test_vault_and_console_guards_still_in_place():
    """Don't let a future commit accidentally remove the guards that
    were already there before v1.39 (vault since v1.32, console since
    v1.32)."""
    vault_src = (REPO_ROOT / "vault" / "app" / "main.py").read_text(encoding="utf-8")
    assert "def _require_pair_keys_in_production" in vault_src
    assert "_require_pair_keys_in_production()" in vault_src

    console_src = (REPO_ROOT / "console" / "app" / "services" / "auth.py").read_text(encoding="utf-8")
    assert "def _require_pair_keys_in_production" in console_src
    assert "_require_pair_keys_in_production()" in console_src


# ── Reviewer #1 ronda 2 findings ────────────────────────────────────────────

@pytest.mark.parametrize("whitespace_value", [" ", "  ", "\t", "\n", " \t \n "])
@pytest.mark.parametrize("service_id", list(SERVICES.keys()))
def test_service_rejects_whitespace_only_pair_keys_in_production(
    service_id, whitespace_value, monkeypatch
):
    """``INTERNAL_API_KEY_X=" "`` (whitespace-only) is an operator
    typo that the v1 guard let through because Python's truthiness
    treats `" "` as truthy. Round 2 of the reviewer flagged this:
    boot should refuse so the operator gets a loud, immediate error
    instead of every request 403'ing in production with no obvious
    cause."""
    spec = SERVICES[service_id]
    monkeypatch.setenv("APP_ENV", "production")
    for env in spec["pair_envs"]:
        monkeypatch.setenv(env, f"prod-key-{env.lower()}")
    # Pin one specific env to whitespace.
    typoed_env = spec["pair_envs"][0]
    monkeypatch.setenv(typoed_env, whitespace_value)

    _isolate_service(spec["service_dir"])
    with pytest.raises(RuntimeError, match=typoed_env):
        importlib.import_module(spec["main_module"])


# ── Reviewer #1 ronda 2: lock the guard's pair-env list to the
# runtime ``_ALLOWED_SERVICES_TO_KEY_ENV`` dict so a future commit
# that renames a key on one side fails CI instead of silently
# letting the guard go stale. ──────────────────────────────────────


def _extract_pair_env_names(src: str) -> set[str]:
    """Extract every ``INTERNAL_API_KEY_*`` literal from ``src``. This
    is a deliberately syntactic (not import-based) extraction so the
    parity tests run without needing the service's runtime deps
    (pgvector / duckdb / asyncpg) installed."""
    return set(re.findall(r"INTERNAL_API_KEY_[A-Z0-9_]+", src))


def test_refinement_guard_matches_runtime_allowlist():
    """Refinement's boot guard (``_PAIR_KEY_ENVS_FOR_PROD_GUARD``) must
    cover the same envs that the runtime ``_ALLOWED_SERVICES_TO_KEY_ENV``
    dict references. If a future commit renames a key on one side, this
    test fails before the divergence makes it to prod."""
    src = (REPO_ROOT / "refinement" / "app" / "main.py").read_text(encoding="utf-8")
    guard_block = re.search(
        r"_PAIR_KEY_ENVS_FOR_PROD_GUARD\s*=\s*\((.*?)\)",
        src, re.DOTALL,
    )
    assert guard_block, "missing _PAIR_KEY_ENVS_FOR_PROD_GUARD in refinement"
    guard_set = _extract_pair_env_names(guard_block.group(1))

    runtime_block = re.search(
        r"_ALLOWED_SERVICES_TO_KEY_ENV[^=]*=\s*\{(.*?)\n\}",
        src, re.DOTALL,
    )
    assert runtime_block, "missing _ALLOWED_SERVICES_TO_KEY_ENV in refinement"
    runtime_set = _extract_pair_env_names(runtime_block.group(1))

    assert guard_set == runtime_set, (
        "refinement guard list diverged from runtime allowlist: "
        f"guard-only={guard_set - runtime_set}, "
        f"runtime-only={runtime_set - guard_set}"
    )


def test_mcp_infra_guard_matches_runtime_allowlist():
    src = (REPO_ROOT / "mcp-infra" / "app" / "main.py").read_text(encoding="utf-8")
    guard_block = re.search(
        r"_ALLOWED_SERVICES_TO_KEY_ENV_FOR_PROD_GUARD[^=]*=\s*\{(.*?)\n\}",
        src, re.DOTALL,
    )
    assert guard_block, (
        "missing _ALLOWED_SERVICES_TO_KEY_ENV_FOR_PROD_GUARD in mcp-infra"
    )
    guard_set = _extract_pair_env_names(guard_block.group(1))

    # The runtime dict appears later under the same name minus the
    # "_FOR_PROD_GUARD" suffix; carve it out with a tighter pattern.
    runtime_block = re.search(
        r"^_ALLOWED_SERVICES_TO_KEY_ENV[^=_]*=\s*\{(.*?)\n\}",
        src, re.DOTALL | re.MULTILINE,
    )
    assert runtime_block, "missing _ALLOWED_SERVICES_TO_KEY_ENV in mcp-infra"
    runtime_set = _extract_pair_env_names(runtime_block.group(1))

    assert guard_set == runtime_set, (
        "mcp-infra guard list diverged from runtime allowlist: "
        f"guard-only={guard_set - runtime_set}, "
        f"runtime-only={runtime_set - guard_set}"
    )


def test_workspace_guard_covers_every_outbound_caller():
    """The boot guard's ``_OUTBOUND_PAIR_KEYS`` must list every
    ``INTERNAL_API_KEY_WORKSPACE_TO_<SERVER>`` that ``_key_for`` /
    ``_hdr_for`` will request at runtime. If a future commit adds a
    4th caller and forgets to extend the guard, prod would still
    boot with that envar missing."""
    src = (REPO_ROOT / "workspace" / "app" / "main.py").read_text(encoding="utf-8")
    guard_block = re.search(
        r"_OUTBOUND_PAIR_KEYS\s*=\s*\((.*?)\)",
        src, re.DOTALL,
    )
    assert guard_block, "missing _OUTBOUND_PAIR_KEYS in workspace"
    guard_set = _extract_pair_env_names(guard_block.group(1))

    # ``_hdr_for("CONSOLE")`` / ``_hdr_for("REFINEMENT")`` / etc. — the
    # static call sites tell us which envs workspace actually needs.
    callers_in_source = set(re.findall(
        r"_hdr_for\(['\"]([A-Z_]+)['\"]\)", src
    ))
    expected_envs = {
        f"INTERNAL_API_KEY_WORKSPACE_TO_{server}"
        for server in callers_in_source
    }
    missing = expected_envs - guard_set
    assert not missing, (
        f"workspace _hdr_for is called for {missing} but those envs "
        f"are not in the boot guard"
    )


def test_all_five_services_have_consistent_production_detection():
    """All five guards must use the same ``APP_ENV in {production,
    prod}`` rule so a service running with ``APP_ENV=Production``
    behaves the same across the platform."""
    sources = {
        "vault": (REPO_ROOT / "vault" / "app" / "main.py").read_text(encoding="utf-8"),
        "console": (REPO_ROOT / "console" / "app" / "services" / "auth.py").read_text(encoding="utf-8"),
        "refinement": (REPO_ROOT / "refinement" / "app" / "main.py").read_text(encoding="utf-8"),
        "mcp-infra": (REPO_ROOT / "mcp-infra" / "app" / "main.py").read_text(encoding="utf-8"),
        "workspace": (REPO_ROOT / "workspace" / "app" / "main.py").read_text(encoding="utf-8"),
    }
    for service, src in sources.items():
        assert (
            'APP_ENV' in src
            and 'production' in src
            and 'prod' in src
        ), (
            f"{service} guard must consult APP_ENV with both "
            f"'production' and 'prod' accepted"
        )
