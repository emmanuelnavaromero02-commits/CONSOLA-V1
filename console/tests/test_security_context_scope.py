from __future__ import annotations

from app.services.security_context import build_security_context, rls_user_context


def test_security_context_is_backend_owned_and_scoped_to_user_cartridges():
    user = {
        "id": 42,
        "email": "analyst@example.com",
        "role": "analyst",
        "workspace_id": "ws_1",
        "tenant_id": "tenant_1",
        "allowed_cartridges": ["replicon"],
    }

    ctx = build_security_context(user)

    assert ctx["trusted"] is True
    assert ctx["source"] == "console"
    assert ctx["permissions"] and "datasets.read" in ctx["permissions"]
    assert ctx["allowed_buckets"] == ["lakehouse"]
    assert ctx["allowed_cartridges"] == ["replicon"]
    assert "raw/replicon/tenant_id=tenant_1/workspace_id=ws_1/" in ctx["allowed_prefixes"]
    assert "gold/replicon/tenant_id=tenant_1/workspace_id=ws_1/" in ctx["allowed_prefixes"]
    assert "raw/replicon/" not in ctx["allowed_prefixes"]
    assert "raw/" not in ctx["allowed_prefixes"]
    assert "_trusted_admin" not in ctx


def test_security_context_admin_can_traverse_platform_prefixes():
    ctx = build_security_context({"id": 1, "email": "admin@example.com", "role": "admin"})

    assert "_trusted_admin" not in ctx
    assert "inbound/" in ctx["allowed_prefixes"]
    assert "cartridges/" in ctx["allowed_prefixes"]


def test_non_admin_without_explicit_cartridges_gets_no_dataset_prefixes():
    ctx = build_security_context({"id": 7, "email": "viewer@example.com", "role": "analyst"})

    assert "_trusted_admin" not in ctx
    assert ctx["allowed_cartridges"] == []
    assert ctx["allowed_prefixes"] == []


def test_rls_user_context_marks_server_trusted_but_never_serializes_admin_magic_flag():
    ctx = rls_user_context({"id": 1, "email": "owner@example.com", "role": "owner"})

    assert ctx["_server_trusted_context"] is True
    assert "_trusted_admin" not in ctx
    assert ctx["role"] == "owner"


def test_anonymous_context_never_grants_access():
    ctx = build_security_context(None)

    assert ctx["trusted"] is False
    assert ctx["allowed_buckets"] == []
    assert ctx["allowed_prefixes"] == []
