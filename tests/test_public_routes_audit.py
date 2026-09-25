from __future__ import annotations

import re
from pathlib import Path

from tests.console_route_source import console_route_source

REPO_ROOT = Path(__file__).resolve().parents[1]
MAIN = REPO_ROOT / "console" / "app" / "main.py"

_CONVENTIONAL_NO_HANDLER = {"/favicon.ico"}

CRITICAL_PUBLIC = {
    "/healthz", "/readyz",
    "/login", "/auth/login", "/api/auth/login", "/auth/logout",
}

FORBIDDEN_PUBLIC = {
    "/api/users", "/api/admin/users",
    "/api/vault", "/api/datasets",
    "/studio/import", "/studio/cartridges",
}


def _main_source() -> str:
    return console_route_source()


def _public_exact() -> set[str]:
    src = _main_source()
    block = re.search(r"_AUTH_PUBLIC_EXACT = \{(.*?)\n\}", src, re.DOTALL)
    assert block, "_AUTH_PUBLIC_EXACT block not found in main.py"
    return set(re.findall(r'"([^"]+)"', block.group(1)))


def _registered_app_paths() -> set[str]:
    src = _main_source()
    return set(
        re.findall(
            r'@app\.(?:get|post|put|delete|patch|head|options)\(\s*["\']([^"\']+)["\']',
            src,
        )
    )


def test_every_public_exact_entry_maps_to_a_route():
    public = _public_exact()
    registered = _registered_app_paths()
    orphans = {p for p in public if p not in registered and p not in _CONVENTIONAL_NO_HANDLER}
    assert not orphans, f"public-exact entries with no registered route: {sorted(orphans)}"


def test_critical_paths_stay_public():
    public = _public_exact()
    missing = CRITICAL_PUBLIC - public
    assert not missing, f"critical paths dropped from _AUTH_PUBLIC_EXACT: {sorted(missing)}"


def test_sensitive_paths_are_never_public():
    public = _public_exact()
    leaked = FORBIDDEN_PUBLIC & public
    assert not leaked, f"sensitive paths must not be public: {sorted(leaked)}"


def test_public_prefixes_are_backed_by_a_mount_or_route():
    src = _main_source()
    prefixes = re.search(r"_AUTH_PUBLIC_PREFIX = \((.*?)\)", src, re.DOTALL)
    assert prefixes, "_AUTH_PUBLIC_PREFIX not found"
    for prefix in re.findall(r'"([^"]+)"', prefixes.group(1)):
        stem = prefix.rstrip("/")
        assert (
            f'app.mount("{stem}"' in src
            or re.search(rf'@app\.\w+\(\s*["\']{re.escape(stem)}/', src)
        ), f"public prefix {prefix} has no mount or route"
