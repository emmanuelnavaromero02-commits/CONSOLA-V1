from __future__ import annotations

import ast
from pathlib import Path


REPO = Path(__file__).resolve().parents[1]
ROLES_MODULE = REPO / "console" / "app" / "services" / "permission_roles.py"
SERVICE_ADMIN_SETS = {
    "mcp-infra": REPO / "mcp-infra" / "app" / "main.py",
    "refinement": REPO / "refinement" / "app" / "security_scope.py",
    "vault": REPO / "vault" / "app" / "main.py",
}


def _string_set(node: ast.AST) -> set[str] | None:
    if isinstance(node, ast.Call) and getattr(node.func, "id", "") in {"frozenset", "set"} and node.args:
        node = node.args[0]
    if not isinstance(node, ast.Set):
        return None
    values: set[str] = set()
    for element in node.elts:
        if not (isinstance(element, ast.Constant) and isinstance(element.value, str)):
            return None
        values.add(element.value)
    return values


def _assigned(path: Path, name: str) -> ast.AST:
    for node in ast.parse(path.read_text(encoding="utf-8")).body:
        if isinstance(node, ast.Assign) and any(
            isinstance(target, ast.Name) and target.id == name for target in node.targets
        ):
            return node.value
    raise AssertionError(f"{name} is not assigned in {path}")


def _console_platform_roles() -> set[str]:
    roles = _string_set(_assigned(ROLES_MODULE, "PLATFORM_ADMIN_ROLES"))
    assert roles, "PLATFORM_ADMIN_ROLES must be a literal set of role names"
    return roles


def test_console_declares_the_platform_and_global_role_sets_once():
    platform = _console_platform_roles()
    assert platform == {"owner", "super_admin", "admin"}
    grantors = _string_set(_assigned(ROLES_MODULE, "PLATFORM_ROLE_GRANTORS"))
    assert grantors == {"owner", "super_admin"} and grantors < platform
    global_roles = _assigned(ROLES_MODULE, "GLOBAL_ROLES")
    assert isinstance(global_roles, ast.Call)
    assert ast.unparse(global_roles) == "frozenset({*PLATFORM_ADMIN_ROLES, 'security_admin', 'auditor'})"


def test_service_admin_role_sets_equal_the_console_set():
    platform = _console_platform_roles()
    for service, path in SERVICE_ADMIN_SETS.items():
        assert _string_set(_assigned(path, "_ADMIN_ROLES")) == platform, service


def test_console_has_no_private_copy_of_the_platform_role_set():
    platform = _console_platform_roles()
    copies = []
    for path in sorted((REPO / "console" / "app").rglob("*.py")):
        if "static" in path.parts or path == ROLES_MODULE:
            continue
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            values = _string_set(node)
            if values is not None and values in (platform, platform | {"security_admin", "auditor"}):
                copies.append(f"{path.relative_to(REPO)}:{node.lineno}")
    assert copies == [], "use PLATFORM_ADMIN_ROLES / GLOBAL_ROLES from permission_roles"


def test_other_services_keep_their_inline_admin_checks_in_sync():
    platform = _console_platform_roles()
    for path in (
        REPO / "mcp-infra" / "app" / "rag" / "store.py",
        REPO / "refinement" / "app" / "duckdb_engine.py",
        REPO / "workspace" / "app" / "main.py",
    ):
        literals = [
            values
            for node in ast.walk(ast.parse(path.read_text(encoding="utf-8")))
            if (values := _string_set(node)) is not None and {"owner", "super_admin"} <= values
        ]
        assert literals, path
        assert platform in literals, path
