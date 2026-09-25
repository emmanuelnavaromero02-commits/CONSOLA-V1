from __future__ import annotations

import ast
from pathlib import Path

from tests.console_route_source import console_route_source


ROOT = Path(__file__).resolve().parents[1]


def _route_has_permission(
    source: str, *, method: str, path: str, permission: str
) -> bool:
    for node in ast.walk(ast.parse(source)):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        for decorator in node.decorator_list:
            if not (
                isinstance(decorator, ast.Call)
                and isinstance(decorator.func, ast.Attribute)
                and decorator.func.attr == method
                and decorator.args
                and isinstance(decorator.args[0], ast.Constant)
                and decorator.args[0].value == path
            ):
                continue
            dependencies = next(
                (
                    keyword.value
                    for keyword in decorator.keywords
                    if keyword.arg == "dependencies"
                ),
                None,
            )
            if not isinstance(dependencies, (ast.List, ast.Tuple)):
                continue
            for dependency in dependencies.elts:
                if not (
                    isinstance(dependency, ast.Call)
                    and isinstance(dependency.func, ast.Name)
                    and dependency.func.id == "Depends"
                    and len(dependency.args) == 1
                ):
                    continue
                gate = dependency.args[0]
                if (
                    isinstance(gate, ast.Call)
                    and isinstance(gate.func, ast.Name)
                    and gate.func.id == "require_permission"
                    and len(gate.args) == 1
                    and isinstance(gate.args[0], ast.Constant)
                    and gate.args[0].value == permission
                ):
                    return True
    return False


def test_explorer_delete_requires_permission_csrf_scope_audit_and_confirmation():
    source = console_route_source()
    ast.parse(source)

    assert '"/api/explorer/object"' in source
    assert "Depends(require_csrf)" in source
    assert 'Depends(require_permission("pipelines.write"))' in source
    assert "_resolve_explorer_bucket(bucket, user)" in source
    assert "_explorer_path_allowed(key, user, object_access=True)" in source
    assert "strong confirmation required" in source
    assert 'action="explorer.object.delete"' in source


def test_explorer_frontend_sends_strong_delete_confirmation():
    source = (ROOT / "console/app/static/js/explorer.js").read_text(encoding="utf-8")

    assert "prompt(`Escribe la ruta completa" in source
    assert "typed !== key" in source
    assert "confirm: typed" in source


def test_explorer_list_filters_child_prefixes_and_objects_by_scope():
    source = console_route_source()
    ast.parse(source)

    assert '"/api/explorer/list"' in source
    assert (
        '_explorer_path_allowed(o.get("Key", ""), user, object_access=True)' in source
    )
    assert '_explorer_path_allowed(p.get("Prefix", ""), user)' in source


def test_v1_explorer_routes_require_scoped_object_access():
    source = (ROOT / "console/app/routers/v1/data.py").read_text(encoding="utf-8")
    ast.parse(source)

    assert (
        '_explorer_path_allowed(o.get("Key", ""), user, object_access=True)' in source
    )
    assert '_explorer_path_allowed(p.get("Prefix", ""), user)' in source
    assert "_explorer_path_allowed(key, user, object_access=True)" in source


def test_dataset_metadata_and_lineage_are_sanitized_before_console_response():
    source = console_route_source()
    ast.parse(source)

    assert "def _sanitize_dataset_metadata_for_user" in source
    assert "def _sanitize_datasets_payload_for_user" in source
    visibility_source = (
        ROOT / "console/app/domains/data_platform/source_visibility.py"
    ).read_text(encoding="utf-8")
    ast.parse(visibility_source)
    assert (
        'is_physical_reference = "://" in value or "tenant_id=" in value or "workspace_id=" in value'
        in visibility_source
    )
    assert 'or f"tenant_id={tenant_id}" not in candidate' in visibility_source
    assert 'or f"workspace_id={workspace_id}" not in candidate' in visibility_source
    assert "_sanitize_datasets_payload_for_user(user, payload or {})" in source
    assert "if not _dataset_source_visible_for_user(user, source):" in source


def test_sensitive_viewer_apis_require_explicit_permissions():
    source = console_route_source()
    ast.parse(source)

    assert (
        '@app.get("/api/tools/manifest", dependencies=[Depends(require_permission("agents.read"))])'
        in source
    )
    assert (
        '@app.get("/api/schema", dependencies=[Depends(require_permission("datasets.read"))])'
        in source
    )
    assert (
        '@app.get("/api/sources", dependencies=[Depends(require_permission("datasets.read"))])'
        in source
    )
    assert (
        '@app.get("/api/datasets/{name}/detail", dependencies=[Depends(require_permission("datasets.read"))])'
        in source
    )
    assert (
        '@app.get("/api/datasets/{name}/lineage", dependencies=[Depends(require_permission("datasets.read"))])'
        in source
    )
    assert (
        '@app.get("/api/rag/sources", dependencies=[Depends(require_permission("datasets.read"))])'
        in source
    )
    assert (
        '@app.get("/api/semantic", dependencies=[Depends(require_permission("datasets.read"))])'
        in source
    )
    assert (
        '@app.get("/api/catalog", dependencies=[Depends(require_permission("datasets.read"))])'
        in source
    )
    assert (
        '@app.get("/api/pipeline", dependencies=[Depends(require_permission("pipelines.read"))])'
        in source
    )
    assert (
        '@app.get("/api/pipeline_runs", dependencies=[Depends(require_permission("pipelines.read"))])'
        in source
    )
    assert (
        '@app.get("/api/jobs", dependencies=[Depends(require_permission("monitor.read"))])'
        in source
    )


def test_sensitive_mutation_and_tool_apis_require_functional_permissions():
    source = console_route_source()
    v1_data = (ROOT / "console/app/routers/v1/data.py").read_text(encoding="utf-8")
    v1_rag = (ROOT / "console/app/routers/v1/rag.py").read_text(encoding="utf-8")
    v1_monitoring = (ROOT / "console/app/routers/v1/monitoring.py").read_text(
        encoding="utf-8"
    )
    v1_misc = (ROOT / "console/app/routers/v1/misc.py").read_text(encoding="utf-8")
    ast.parse(source)
    for route_source in (v1_data, v1_rag, v1_monitoring, v1_misc):
        ast.parse(route_source)

    assert (
        '"/assistant/chat",\n    dependencies=[Depends(require_csrf), Depends(require_permission("copilot.use"))]'
        in source
    )
    assert (
        '"/api/bronze/query",\n    dependencies=[Depends(require_csrf), Depends(require_permission("datasets.write"))]'
        in source
    )
    assert (
        '"/api/dags/parse",\n    dependencies=[Depends(require_csrf), Depends(require_permission("studio.read"))]'
        in source
    )
    assert (
        '"/monitoring/invoke",\n    dependencies=[Depends(require_csrf), Depends(require_permission("monitor.read"))]'
        in source
    )
    assert (
        '"/api/apps/{name}",\n    dependencies=[\n        Depends(require_csrf),\n        Depends(require_permission("apps.write"))'
        in source
    )
    assert (
        '"/api/rag/sources/{source_id}",\n    dependencies=[\n        Depends(require_csrf),\n        Depends(require_permission("datasets.write"))'
        in source
    )
    assert (
        '"/api/catalog/entries",\n    dependencies=[\n        Depends(require_csrf),\n        Depends(require_permission("datasets.write"))'
        in source
    )
    assert (
        '"/api/catalog/relationships",\n    dependencies=[\n        Depends(require_csrf),\n        Depends(require_permission("datasets.write"))'
        in source
    )

    assert (
        '"/api/bronze/query",\n    dependencies=[Depends(require_csrf), Depends(require_permission("datasets.write"))]'
        in v1_data
    )
    assert (
        '_merge_declared_and_inferred_bronze_sources(body.get("sources"), sql)'
        in v1_data
    )
    assert (
        '"/api/catalog/entries",\n    dependencies=[\n        Depends(require_csrf),\n        Depends(require_permission("datasets.write"))'
        in v1_data
    )
    assert (
        '"/api/rag/sources/{source_id}",\n    dependencies=[\n        Depends(require_csrf),\n        Depends(require_permission("datasets.write"))'
        in v1_rag
    )
    assert (
        '"/api/dags/parse",\n    dependencies=[Depends(require_csrf), Depends(require_permission("studio.read"))]'
        in v1_monitoring
    )
    assert (
        '"/monitoring/invoke",\n    dependencies=[Depends(require_csrf), Depends(require_permission("monitor.read"))]'
        in v1_monitoring
    )
    assert (
        '"/api/apps/{name}",\n    dependencies=[\n        Depends(require_csrf),\n        Depends(require_permission("apps.write"))'
        in v1_misc
    )


def test_workspace_system_apis_have_explicit_permission_gates():
    dashboard_source = (ROOT / "console/app/routers/dashboard.py").read_text(
        encoding="utf-8"
    )
    freshness_source = (ROOT / "console/app/routers/freshness.py").read_text(
        encoding="utf-8"
    )
    onboarding_source = (ROOT / "console/app/routers/onboarding.py").read_text(
        encoding="utf-8"
    )
    operations_source = (ROOT / "console/app/routers/operations.py").read_text(
        encoding="utf-8"
    )
    for route_source in (
        dashboard_source,
        freshness_source,
        onboarding_source,
        operations_source,
    ):
        ast.parse(route_source)

    assert 'Depends(require_permission("workspace.access"))' in dashboard_source
    assert (
        'dependencies=[Depends(require_permission("pipelines.read"))]'
        in freshness_source
    )
    assert (
        'dependencies=[Depends(require_permission("workspace.access"))]'
        in onboarding_source
    )
    assert 'Depends(require_permission("operations.read"))' in operations_source


def test_studio_metadata_apis_require_explicit_permissions_in_both_routers():
    source = console_route_source()
    v1_source = (ROOT / "console/app/routers/v1/pipeline_studio.py").read_text(
        encoding="utf-8"
    )
    ast.parse(source)
    ast.parse(v1_source)

    for route_source, marker in ((source, "@app"), (v1_source, "@router")):
        assert (
            f'{marker}.get("/studio/cartridges", dependencies=[Depends(require_permission("cartridges.read"))])'
            in route_source
        )
        assert (
            '"/studio/cartridges/{cartridge_id}/status",\n    dependencies=[Depends(require_permission("cartridges.read"))]'
            in route_source
            or f'{marker}.get("/studio/cartridges/{{cartridge_id}}/status", dependencies=[Depends(require_permission("cartridges.read"))])'
            in route_source
        )
        assert (
            f'{marker}.get("/studio/cartridges/{{cartridge_id}}", dependencies=[Depends(require_permission("cartridges.read"))])'
            in route_source
            or '"/studio/cartridges/{cartridge_id}", dependencies=[Depends(require_permission("cartridges.read"))]'
            in route_source
        )
        assert (
            '"/studio/cartridges/{cartridge_id}/connections",\n    dependencies=[Depends(require_permission("vault.connections.read"))]'
            in route_source
            or f'{marker}.get("/studio/cartridges/{{cartridge_id}}/connections", dependencies=[Depends(require_permission("vault.connections.read"))])'
            in route_source
        )


def test_decision_apis_are_permissioned_and_workspace_scoped_in_both_routers():
    source = console_route_source()
    v1_source = (ROOT / "console/app/routers/v1/admin_decisions.py").read_text(
        encoding="utf-8"
    )
    ast.parse(source)
    ast.parse(v1_source)

    for route_source, marker in ((source, "@app"), (v1_source, "@router")):
        assert (
            f'{marker}.get("/api/decisions", dependencies=[Depends(require_permission("datasets.read"))])'
            in route_source
        )
        assert (
            '"/api/decisions",\n    dependencies=[Depends(require_csrf), Depends(require_permission("control_room.write"))]'
            in route_source
            or f'{marker}.post("/api/decisions", dependencies=[Depends(require_csrf), Depends(require_permission("control_room.write"))])'
            in route_source
        )
        assert (
            f'{marker}.get("/api/decisions/{{decision_id}}", dependencies=[Depends(require_permission("datasets.read"))])'
            in route_source
        )
        assert 'Depends(require_permission("control_room.write"))' in route_source
        assert 'if not workspace_id:\n        return {"decisions": []}' in route_source
        assert "scoped_db_for_user(pool, user)" in route_source


def test_data_api_routes_require_dataset_read_permission_in_both_routers():
    source = console_route_source()
    v1_source = (ROOT / "console/app/routers/v1/data.py").read_text(encoding="utf-8")
    ast.parse(source)
    ast.parse(v1_source)

    for route_source in (source, v1_source):
        for method, path in (
            ("get", "/api/data/{dataset}"),
            ("get", "/api/data/{dataset}/options"),
            ("post", "/api/data/{dataset}/query"),
        ):
            assert _route_has_permission(
                route_source,
                method=method,
                path=path,
                permission="datasets.read",
            ), f"{method.upper()} {path} must require datasets.read"


def test_vault_proxy_hides_foreign_prefixed_connection_ids():
    source = (ROOT / "console/app/domains/vault/scope.py").read_text(encoding="utf-8")
    ast.parse(source)

    assert 'elif key.startswith("tenant_") and "__workspace_" in key:' in source
    assert "return None" in source
    assert "display_key = key" in source
