from __future__ import annotations

import pytest
from fastapi import HTTPException

from app.domains.admin.user_mutations import delete_admin_user_payload


class FakeAuth:
    def __init__(self, *, ok: bool = True, error: RuntimeError | None = None):
        self.ok = ok
        self.error = error
        self.deleted_user_ids: list[int] = []

    async def delete_user(self, user_id: int):
        self.deleted_user_ids.append(user_id)
        if self.error:
            raise self.error
        return self.ok


class FakeAudit:
    def __init__(self):
        self.events: list[dict] = []

    async def record_event(
        self,
        actor_id,
        actor_email,
        action,
        resource,
        resource_id,
        **kwargs,
    ):
        self.events.append(
            {
                "actor_id": actor_id,
                "actor_email": actor_email,
                "action": action,
                "resource": resource,
                "resource_id": resource_id,
                **kwargs,
            }
        )


@pytest.mark.asyncio
async def test_delete_admin_user_payload_deletes_and_audits():
    audit = FakeAudit()
    scope_checks: list[tuple[dict, int]] = []

    async def assert_can_manage(admin_user, user_id):
        scope_checks.append((admin_user, user_id))

    result = await delete_admin_user_payload(
        user_id=42,
        admin_user={"id": 1, "email": "admin@example.com"},
        request_ip="127.0.0.1",
        user_agent="pytest",
        auth_service=FakeAuth(ok=True),
        audit_service=audit,
        assert_can_manage_target_user=assert_can_manage,
    )

    assert result == {"deleted": True, "id": 42}
    assert scope_checks == [({"id": 1, "email": "admin@example.com"}, 42)]
    assert audit.events == [
        {
            "actor_id": 1,
            "actor_email": "admin@example.com",
            "action": "user.deleted",
            "resource": "user",
            "resource_id": "42",
            "ip": "127.0.0.1",
            "user_agent": "pytest",
        }
    ]


@pytest.mark.asyncio
async def test_delete_admin_user_payload_rejects_self_delete_before_scope_check():
    async def assert_can_manage(_admin_user, _user_id):
        raise AssertionError("scope check should not run for self-delete")

    with pytest.raises(HTTPException) as exc:
        await delete_admin_user_payload(
            user_id=1,
            admin_user={"id": 1, "email": "admin@example.com"},
            request_ip=None,
            user_agent=None,
            auth_service=FakeAuth(ok=True),
            audit_service=FakeAudit(),
            assert_can_manage_target_user=assert_can_manage,
        )

    assert exc.value.status_code == 400
    assert exc.value.detail == "you cannot delete your own account"


@pytest.mark.asyncio
async def test_delete_admin_user_payload_maps_missing_user_to_404():
    async def assert_can_manage(_admin_user, _user_id):
        return None

    with pytest.raises(HTTPException) as exc:
        await delete_admin_user_payload(
            user_id=42,
            admin_user={"id": 1, "email": "admin@example.com"},
            request_ip=None,
            user_agent=None,
            auth_service=FakeAuth(ok=False),
            audit_service=FakeAudit(),
            assert_can_manage_target_user=assert_can_manage,
        )

    assert exc.value.status_code == 404
    assert exc.value.detail == "user not found"


@pytest.mark.asyncio
async def test_delete_admin_user_payload_maps_runtime_error_to_409():
    async def assert_can_manage(_admin_user, _user_id):
        return None

    with pytest.raises(HTTPException) as exc:
        await delete_admin_user_payload(
            user_id=42,
            admin_user={"id": 1, "email": "admin@example.com"},
            request_ip=None,
            user_agent=None,
            auth_service=FakeAuth(error=RuntimeError("has dependencies")),
            audit_service=FakeAudit(),
            assert_can_manage_target_user=assert_can_manage,
        )

    assert exc.value.status_code == 409
    assert exc.value.detail == "has dependencies"
