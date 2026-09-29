from __future__ import annotations

import os

import pytest

os.environ.setdefault("APP_ENV", "test")
os.environ.setdefault("INTERNAL_API_KEY", "x" * 64)

from app.services import app_forge, app_html_prompt, studio_assistant  # noqa: E402
from app.services import llm_client, permissions  # noqa: E402
from app.services.permission_roles import ROLE_PERMISSIONS  # noqa: E402


TENANT = "11111111-1111-1111-1111-111111111111"
WORKSPACE = "aaaaaaaa-0000-0000-0000-000000000001"


def _user(workspace_role: str) -> dict:
    return {
        "id": 7,
        "email": "u@example.com",
        "role": "user",
        "workspace_role": workspace_role,
        "active_tenant_id": TENANT,
        "active_workspace_id": WORKSPACE,
    }


GOOD_HTML = (
    "<html><head><style>body{color:var(--text-primary)}</style></head><body>"
    "<div class='omega-card'>Ventas</div>"
    "<script>fetch('/api/data/ventas_diarias').then(r=>r.json());</script>"
    "</body></html>" + "<!-- relleno -->" * 20
)


@pytest.fixture
def forge_env(monkeypatch):
    calls: dict = {"invoke": [], "register": [], "chat": []}

    async def fake_invoke(server_id, tool, args, **kwargs):
        calls["invoke"].append((server_id, tool, dict(args)))
        if tool == "get_schema":
            return {"columns": [{"name": "fecha"}, {"name": "monto"}]}
        if tool == "publish_app":
            return {
                "published": True,
                "name": args["name"],
                "datasets_used": ["ventas_diarias"],
                "html_sha256": "a" * 64,
                "url": f"/apps/{args['name']}",
            }
        raise AssertionError(f"unexpected tool {tool}")

    async def fake_chat(*, system, messages, tools, invoke_tool, tool_server_map, **kw):
        calls["chat"].append({"system": system, "messages": messages})
        return GOOD_HTML, [], []

    async def fake_register(scope, app_name, html, datasets):
        calls["register"].append((scope, app_name, html, list(datasets)))
        return "d" * 64

    monkeypatch.setattr(app_forge.mcp_registry, "invoke", fake_invoke)
    monkeypatch.setattr(app_forge.llm_client, "chat", fake_chat)
    monkeypatch.setattr(
        app_forge.app_publication, "register_workspace_app", fake_register
    )
    return calls


@pytest.mark.asyncio
async def test_forge_happy_path_keeps_html_server_side(forge_env):
    result = await app_forge.generate_and_publish_app(
        _user("workspace_admin"),
        name="ventas_semana",
        title="Ventas de la semana",
        objective="ver ventas por semana",
        datasets=["ventas_diarias"],
    )
    assert result == {
        "name": "ventas_semana",
        "title": "Ventas de la semana",
        "url": "/analytics/viewer?app=ventas_semana",
        "datasets": ["ventas_diarias"],
    }
    tools_called = [(srv, tool) for srv, tool, _ in forge_env["invoke"]]
    assert tools_called == [
        ("refinement", "get_schema"),
        ("refinement", "publish_app"),
    ]
    publish_args = forge_env["invoke"][1][2]
    assert publish_args["html"] == app_forge._clean_html(GOOD_HTML)
    assert "cartridge_id" not in publish_args
    # Shared so every member of the workspace sees it in the gallery.
    assert publish_args["visibility"] == "shared"
    scope, app_name, html, datasets = forge_env["register"][0]
    assert scope == (TENANT, WORKSPACE)
    assert app_name == "ventas_semana"
    assert html == publish_args["html"]
    assert datasets == ["ventas_diarias"]
    # The HTML must never appear in the tool result surface.
    assert "html" not in result


@pytest.mark.asyncio
async def test_forge_permission_matrix(forge_env):
    assert "apps.write" in ROLE_PERMISSIONS["workspace_admin"]
    assert "apps.write" in ROLE_PERMISSIONS["tenant_admin"]
    assert "copilot.write" in ROLE_PERMISSIONS["tenant_admin"]
    assert "apps.write" not in ROLE_PERMISSIONS["analyst"]
    assert "copilot.write" not in ROLE_PERMISSIONS["analyst"]

    result = await app_forge.generate_and_publish_app(
        _user("tenant_admin"),
        name="ventas_ta",
        objective="objetivo",
        datasets=["ventas_diarias"],
    )
    assert result["name"] == "ventas_ta"

    with pytest.raises(app_forge.AppForgeError, match="apps.write"):
        await app_forge.generate_and_publish_app(
            _user("analyst"),
            name="ventas_analyst",
            objective="objetivo",
            datasets=["ventas_diarias"],
        )
    assert all(name != "ventas_analyst" for _, name, *_ in forge_env["register"])


@pytest.mark.asyncio
async def test_forge_requires_workspace_scope(forge_env):
    user = {"id": 1, "role": "admin"}
    assert permissions.has_permission(user, "apps.write") is True
    with pytest.raises(app_forge.AppForgeError, match="workspace"):
        await app_forge.generate_and_publish_app(
            user, objective="objetivo", datasets=["ventas_diarias"]
        )
    assert forge_env["invoke"] == []


@pytest.mark.asyncio
async def test_forge_llm_failure_is_typed_and_publishes_nothing(
    forge_env, monkeypatch
):
    async def broken_chat(**_kwargs):
        raise llm_client.LLMProviderError("proveedor caído")

    monkeypatch.setattr(app_forge.llm_client, "chat", broken_chat)
    with pytest.raises(app_forge.AppForgeError, match="No se publicó nada"):
        await app_forge.generate_and_publish_app(
            _user("workspace_admin"),
            name="ventas_fail",
            objective="objetivo",
            datasets=["ventas_diarias"],
        )
    assert all(tool != "publish_app" for _, tool, _ in forge_env["invoke"])
    assert forge_env["register"] == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "html, reason",
    (
        ("<html><script src='https://cdn.evil/x.js'></script></html>" + "x" * 300,
         "scripts externos"),
        # Empirical bypass cases from the adversarial review: `/` is a valid
        # attribute separator, so these load external code without a space.
        ("<html><script/src='https://cdn.evil/x.js'></script></html>" + "x" * 300,
         "scripts externos"),
        ("<html><script id=a/src='https://cdn.evil/x.js'></script></html>" + "x" * 300,
         "scripts externos"),
        ("<html><script>import('https://cdn.evil/x.js')</script></html>" + "x" * 300,
         "import"),
        ("<html><script>import ('https://cdn.evil/x.js')</script></html>" + "x" * 300,
         "import"),
        ("<html>corto</html>", "demasiado corto"),
        ("<html><script>fetch('/api/data/otra_tabla')</script></html>" + "y" * 300,
         "no autorizados"),
    ),
)
async def test_forge_rejects_dishonest_html(forge_env, monkeypatch, html, reason):
    async def bad_chat(**_kwargs):
        return html, [], []

    monkeypatch.setattr(app_forge.llm_client, "chat", bad_chat)
    with pytest.raises(app_forge.AppForgeError, match=reason):
        await app_forge.generate_and_publish_app(
            _user("workspace_admin"),
            name="ventas_bad",
            objective="objetivo",
            datasets=["ventas_diarias"],
        )
    assert all(tool != "publish_app" for _, tool, _ in forge_env["invoke"])
    assert forge_env["register"] == []


@pytest.mark.asyncio
async def test_forge_publish_error_stops_before_registration(forge_env, monkeypatch):
    async def failing_invoke(server_id, tool, args, **kwargs):
        if tool == "get_schema":
            return {"columns": []}
        return {"error": "workspace scope required"}

    monkeypatch.setattr(app_forge.mcp_registry, "invoke", failing_invoke)
    with pytest.raises(app_forge.AppForgeError, match="publicación"):
        await app_forge.generate_and_publish_app(
            _user("workspace_admin"),
            name="ventas_err",
            objective="objetivo",
            datasets=["ventas_diarias"],
        )
    assert forge_env["register"] == []


@pytest.mark.asyncio
async def test_forge_runs_the_pre_publish_check_after_generation(forge_env):
    order: list[str] = []

    async def check(app_name):
        order.append(f"check:{app_name}:{len(forge_env['chat'])}:{len(forge_env['invoke'])}")

    result = await app_forge.generate_and_publish_app(
        _user("workspace_admin"),
        name="ventas_hook",
        objective="objetivo",
        datasets=["ventas_diarias"],
        before_publish=check,
    )
    assert result["name"] == "ventas_hook"
    assert order == ["check:ventas_hook:1:1"]
    assert [tool for _srv, tool, _args in forge_env["invoke"]] == ["get_schema", "publish_app"]


@pytest.mark.asyncio
async def test_forge_pre_publish_rejection_publishes_nothing(forge_env):
    class NameTaken(Exception):
        pass

    async def check(app_name):
        raise NameTaken(app_name)

    with pytest.raises(NameTaken):
        await app_forge.generate_and_publish_app(
            _user("workspace_admin"),
            name="ventas_tomada",
            objective="objetivo",
            datasets=["ventas_diarias"],
            before_publish=check,
        )
    assert [tool for _srv, tool, _args in forge_env["invoke"]] == ["get_schema"]
    assert forge_env["register"] == []


@pytest.mark.asyncio
async def test_forge_rejects_unknown_datasets(forge_env, monkeypatch):
    async def failing_schema(server_id, tool, args, **kwargs):
        return {"error": "Dataset 'x' not found"}

    monkeypatch.setattr(app_forge.mcp_registry, "invoke", failing_schema)
    with pytest.raises(app_forge.AppForgeError, match="no está disponible"):
        await app_forge.generate_and_publish_app(
            _user("workspace_admin"),
            objective="objetivo",
            datasets=["desconocido"],
        )
    assert forge_env["register"] == []


def test_prompt_builder_shares_the_studio_style_rules():
    system = app_html_prompt.build_app_html_system_prompt()
    assert app_html_prompt.APP_HTML_STYLE_RULES in system
    assert app_html_prompt.APP_HTML_STYLE_RULES in studio_assistant.STEP_INSTRUCTIONS[5]
    assert "Sin información" in system
    user_prompt = app_html_prompt.build_app_html_user_prompt(
        title="T", description="", objective="O",
        dataset_schemas={"ventas": {"columns": []}},
    )
    assert "ventas" in user_prompt
    assert "Sin información" in user_prompt


@pytest.mark.asyncio
async def test_studio_publish_success_registers_the_workspace_manifest(monkeypatch):
    calls = []

    async def fake_register(scope, app_name, html, datasets):
        calls.append((scope, app_name, html, list(datasets)))
        return "d" * 64

    from app.services import app_publication

    monkeypatch.setattr(app_publication, "register_workspace_app", fake_register)
    result = await studio_assistant._register_workspace_publication(
        {"published": True, "name": "mi_app", "datasets_used": ["ventas"]},
        {"name": "mi_app", "html": "<html>app</html>"},
        _user("workspace_admin"),
    )
    assert result["workspace_registration"] == "registered"
    assert calls == [((TENANT, WORKSPACE), "mi_app", "<html>app</html>", ["ventas"])]


@pytest.mark.asyncio
async def test_studio_publish_registration_failure_is_flagged_not_fatal(monkeypatch):
    async def broken_register(scope, app_name, html, datasets):
        raise RuntimeError("db down")

    from app.services import app_publication

    monkeypatch.setattr(app_publication, "register_workspace_app", broken_register)
    result = await studio_assistant._register_workspace_publication(
        {"published": True, "name": "mi_app", "datasets_used": []},
        {"name": "mi_app", "html": "<html>app</html>"},
        _user("workspace_admin"),
    )
    assert result["workspace_registration"] == "failed"
    assert result["published"] is True


@pytest.mark.asyncio
async def test_studio_publish_without_scope_skips_registration(monkeypatch):
    from app.services import app_publication

    async def never(*_args, **_kwargs):
        raise AssertionError("must not register without scope")

    monkeypatch.setattr(app_publication, "register_workspace_app", never)
    result = await studio_assistant._register_workspace_publication(
        {"published": True, "name": "mi_app"},
        {"name": "mi_app", "html": "<html>app</html>"},
        {"id": 1, "role": "admin"},
    )
    assert "workspace_registration" not in result
