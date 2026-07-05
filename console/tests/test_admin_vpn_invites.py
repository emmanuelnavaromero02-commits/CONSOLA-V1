from __future__ import annotations

import pytest
from fastapi import HTTPException

from app.domains.admin.vpn_invites import reissue_vpn_for_user_payload


class FakeAuth:
    def __init__(self, user: dict | None):
        self.user = user

    async def get_user_by_id(self, user_id: int):
        return self.user


class FakeAudit:
    def __init__(self):
        self.events: list[dict] = []

    async def record_event(self, actor_id, actor_email, action, resource, resource_id, **kwargs):
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
async def test_reissue_vpn_for_user_payload_validates_existing_user():
    async def assert_can_manage(_admin_user, _user_id):
        raise AssertionError("scope check should not run for missing users")

    with pytest.raises(HTTPException) as exc:
        await reissue_vpn_for_user_payload(
            user_id=123,
            admin_user={"id": 1, "email": "admin@example.com"},
            request_ip="127.0.0.1",
            user_agent="pytest",
            auth_service=FakeAuth(None),
            audit_service=FakeAudit(),
            assert_can_manage_target_user=assert_can_manage,
            issue_vpn_for_user=lambda *_args: None,
        )

    assert exc.value.status_code == 404
    assert exc.value.detail == "user not found"


@pytest.mark.asyncio
async def test_reissue_vpn_for_user_payload_issues_vpn_and_audits():
    audit = FakeAudit()
    scope_checks: list[tuple[dict, int]] = []
    issued_args: list[tuple[int, str, str | None]] = []

    async def assert_can_manage(admin_user, user_id):
        scope_checks.append((admin_user, user_id))

    async def issue_vpn_for_user(user_id, email, name):
        issued_args.append((user_id, email, name))
        return {"issued": True, "email_sent": True, "wg_client_id": "wg-123"}

    result = await reissue_vpn_for_user_payload(
        user_id=42,
        admin_user={"id": 1, "email": "admin@example.com"},
        request_ip="127.0.0.1",
        user_agent="pytest",
        auth_service=FakeAuth({"id": 42, "email": "user@example.com", "name": "User"}),
        audit_service=audit,
        assert_can_manage_target_user=assert_can_manage,
        issue_vpn_for_user=issue_vpn_for_user,
    )

    assert result == {
        "reissued": True,
        "issued": True,
        "email_sent": True,
        "wg_client_id": "wg-123",
    }
    assert scope_checks == [({"id": 1, "email": "admin@example.com"}, 42)]
    assert issued_args == [(42, "user@example.com", "User")]
    assert audit.events == [
        {
            "actor_id": 1,
            "actor_email": "admin@example.com",
            "action": "vpn.reissued",
            "resource": "user",
            "resource_id": "42",
            "ip": "127.0.0.1",
            "user_agent": "pytest",
            "metadata": {"issued": True, "email_sent": True},
        }
    ]
