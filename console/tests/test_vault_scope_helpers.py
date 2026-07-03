from __future__ import annotations

import pytest
from fastapi import HTTPException

from app.domains.vault.scope import (
    require_vault_scope_visible,
    tenant_vault_conn_id,
    tenant_vault_display_conn,
    tenant_vault_prefix,
    tenant_vault_scope,
)


def _is_global_admin(user):
    return bool(user and user.get("is_global_admin"))


def _user():
    return {
        "tenant_id": "tenant-a",
        "workspace_id": "workspace-a",
    }


def test_tenant_vault_prefix_requires_workspace_scope():
    assert (
        tenant_vault_prefix(_user(), is_global_admin=_is_global_admin)
        == "tenant_tenant-a__workspace_workspace-a__"
    )
    with pytest.raises(HTTPException) as exc:
        tenant_vault_prefix({"tenant_id": "tenant-a"}, is_global_admin=_is_global_admin)
    assert exc.value.status_code == 400


def test_tenant_vault_display_conn_hides_other_workspace_prefixes():
    user = _user()
    assert tenant_vault_display_conn(
        user,
        {
            "id": "tenant_tenant-a__workspace_workspace-a__sf",
            "conn_id": "tenant_tenant-a__workspace_workspace-a__sf",
        },
        is_global_admin=_is_global_admin,
    ) == {"id": "sf", "conn_id": "sf"}
    assert (
        tenant_vault_display_conn(
            user,
            {"conn_id": "tenant_tenant-b__workspace_workspace-b__sf"},
            is_global_admin=_is_global_admin,
        )
        is None
    )
    assert tenant_vault_display_conn(
        user,
        {"conn_id": "sf"},
        is_global_admin=_is_global_admin,
    ) == {"conn_id": "sf"}


def test_tenant_vault_scope_blocks_global_scope_for_regular_users():
    with pytest.raises(HTTPException) as exc:
        tenant_vault_scope(_user(), "global", is_global_admin=_is_global_admin)
    assert exc.value.status_code == 403

    require_vault_scope_visible(
        {"is_global_admin": True},
        "global",
        is_global_admin=_is_global_admin,
    )
    assert (
        tenant_vault_scope(
            {"is_global_admin": True},
            "global",
            is_global_admin=_is_global_admin,
        )
        == "global"
    )


def test_tenant_vault_conn_id_normalizes_blank_values():
    assert tenant_vault_conn_id(_user(), " sf ", is_global_admin=_is_global_admin) == "sf"
    with pytest.raises(HTTPException) as exc:
        tenant_vault_conn_id(_user(), " ", is_global_admin=_is_global_admin)
    assert exc.value.status_code == 400
