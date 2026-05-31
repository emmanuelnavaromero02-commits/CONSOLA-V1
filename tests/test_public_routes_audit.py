"""P12 — audit of console's unauthenticated (public) route allow-lists.

console/app/main.py exempts a fixed set of paths from the session-auth
middleware via ``_AUTH_PUBLIC_EXACT`` (exact paths) and ``_AUTH_PUBLIC_PREFIX``
(prefixes). A path listed there is reachable without a session, so the list is
security-sensitive: a stale entry (a route that no longer exists) is dead
config, and an accidental sensitive entry is a disclosure/operation hole.

These static checks pin the audited state (main.py is imported as source text
because importing the app pulls the full runtime stack):

  * every exact entry maps to a real ``@app.<method>`` route (no orphans),
    except the conventional ``/favicon.ico`` which has no handler on purpose;
  * the login / health paths the platform depends on stay public;
  * a negative set of clearly sensitive paths is never public.
"""
from __future__ import annotations

import re
from pathlib import Path

from tests.console_route_source import console_route_source

REPO_ROOT = Path(__file__).resolve().parents[1]
MAIN = REPO_ROOT / "console" / "app" / "main.py"

# Paths that are public by browser/well-known convention and intentionally have
# no route handler (the entry only suppresses a pointless login redirect).
_CONVENTIONAL_NO_HANDLER = {"/favicon.ico"}

# Must always stay public or login / monitoring break.
CRITICAL_PUBLIC = {
    "/healthz", "/readyz",
    "/login", "/auth/login", "/api/auth/login", "/auth/logout",
}

# Must NEVER be public — sensitive data or state-changing operations.
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
    paths = set(
        re.findall(
            r'@app\.(?:get|post|put|delete|patch|head|options)\(\s*["\']([^"\']+)["\']',
            src,
        )
    )
    # Also recognize routes declared in APIRouter modules (e.g. the Teams
    # channel webhook in routers/msteams.py). console_route_source() only
    # covers main.py + the v1 routers, so a public-exact path served by any
    # other router would otherwise look like an orphan. We compose each
    # router's prefix with its @router.<verb>("...") paths. This only ADDS to
    # the recognized set, so it can never make the orphan check stricter.
    paths.update(_router_module_paths())
    return paths


def _router_module_paths() -> set[str]:
    routers_dir = REPO_ROOT / "console" / "app" / "routers"
    out: set[str] = set()
    for path in routers_dir.glob("*.py"):
        if path.name == "__init__.py":
            continue
        text = path.read_text(encoding="utf-8")
        prefix_match = re.search(r"APIRouter\([^)]*prefix\s*=\s*[\"']([^\"']*)[\"']", text)
        prefix = prefix_match.group(1) if prefix_match else ""
        for sub in re.findall(
            r'@\w+\.(?:get|post|put|delete|patch|head|options)\(\s*["\']([^"\']*)["\']',
            text,
        ):
            composed = (prefix + sub) or "/"
            out.add(composed)
    return out


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
        # Either a StaticFiles mount or a route declared under the prefix.
        assert (
            f'app.mount("{stem}"' in src
            or re.search(rf'@app\.\w+\(\s*["\']{re.escape(stem)}/', src)
        ), f"public prefix {prefix} has no mount or route"
