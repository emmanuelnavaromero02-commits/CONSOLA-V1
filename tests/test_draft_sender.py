from __future__ import annotations

import asyncio
import copy
import json
import sys
import uuid
from pathlib import Path
from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException


REPO = Path(__file__).resolve().parents[1]


def run(coro):
    return asyncio.new_event_loop().run_until_complete(coro)


@pytest.fixture()
def draft_sender_module(monkeypatch):
    for name in list(sys.modules):
        if name == "app" or name.startswith("app."):
            del sys.modules[name]
    sys.path.insert(0, str(REPO / "console"))
    from app.services import draft_sender

    monkeypatch.setenv("APP_ENV", "test")
    return draft_sender


class FakePool:
    def __init__(self):
        self.draft_id = str(uuid.uuid4())
        self.user_id = 7
        self.row = {
            "id": self.draft_id,
            "user_id": self.user_id,
            "kind": "email",
            "title": "Fallback subject",
            "body": "Hola,\nNecesito validar el error.",
            "tone": "neutral",
            "status": "draft",
            "metadata": {
                "to": "rrhh@example.com",
                "subject": "Error detectado",
            },
        }
        self.updates: list[tuple[str, tuple]] = []

    async def fetchrow(self, query: str, *args):
        q = " ".join(query.split())
        if q.startswith("SELECT id, user_id, kind"):
            return copy.deepcopy(self.row) if args[0] == self.draft_id else None
        raise AssertionError(f"unmocked fetchrow: {q[:120]}")

    async def execute(self, query: str, *args):
        q = " ".join(query.split())
        self.updates.append((q, args))
        if q.startswith("UPDATE copilot_drafts SET status = 'sent'"):
            self.row["status"] = "sent"
            self.row["delivery_log"] = json.loads(args[1])
            return "UPDATE 1"
        if q.startswith("UPDATE copilot_drafts SET status = 'failed'"):
            self.row["status"] = "failed"
            self.row["delivery_log"] = json.loads(args[1])
            return "UPDATE 1"
        raise AssertionError(f"unmocked execute: {q[:120]}")


@pytest.fixture()
def fake_pool(draft_sender_module, monkeypatch):
    pool = FakePool()
    monkeypatch.setattr(draft_sender_module.auth, "pool", AsyncMock(return_value=pool))
    return pool


@pytest.fixture()
def user(fake_pool):
    return {"id": fake_pool.user_id, "email": "emmanuel@local.ai", "role": "analyst"}


def test_send_draft_calls_email_service_with_correct_args(draft_sender_module, fake_pool, user, monkeypatch):
    send_email = AsyncMock(return_value=True)
    monkeypatch.setattr(draft_sender_module.email_service, "send_email", send_email)
    monkeypatch.setattr(draft_sender_module.audit_service, "record_event", AsyncMock())

    out = run(draft_sender_module.send_draft(fake_pool.draft_id, user))

    assert out["status"] == "sent"
    send_email.assert_awaited_once()
    kwargs = send_email.await_args.kwargs
    assert kwargs["to"] == "rrhh@example.com"
    assert kwargs["subject"] == "Error detectado"
    assert kwargs["text"] == "Hola,\nNecesito validar el error."


def test_send_draft_marks_status_sent_on_success(draft_sender_module, fake_pool, user, monkeypatch):
    monkeypatch.setattr(draft_sender_module.email_service, "send_email", AsyncMock(return_value=True))
    monkeypatch.setattr(draft_sender_module.audit_service, "record_event", AsyncMock())

    run(draft_sender_module.send_draft(fake_pool.draft_id, user))

    assert fake_pool.row["status"] == "sent"
    assert fake_pool.row["delivery_log"]["ok"] is True
    assert any("sent_at = NOW()" in q for q, _args in fake_pool.updates)


def test_send_draft_marks_status_failed_on_smtp_error(draft_sender_module, fake_pool, user, monkeypatch):
    monkeypatch.setattr(draft_sender_module.email_service, "send_email", AsyncMock(return_value=False))
    monkeypatch.setattr(draft_sender_module.audit_service, "record_event", AsyncMock())

    out = run(draft_sender_module.send_draft(fake_pool.draft_id, user))

    assert out["ok"] is False
    assert fake_pool.row["status"] == "failed"
    assert fake_pool.row["delivery_log"]["error"] == "smtp_delivery_failed"


def test_send_draft_validates_ownership(draft_sender_module, fake_pool, monkeypatch):
    monkeypatch.setattr(draft_sender_module.email_service, "send_email", AsyncMock(return_value=True))
    monkeypatch.setattr(draft_sender_module.audit_service, "record_event", AsyncMock())
    other_user = {"id": 999, "email": "other@example.com", "role": "viewer"}

    with pytest.raises(HTTPException) as exc:
        run(draft_sender_module.send_draft(fake_pool.draft_id, other_user))

    assert exc.value.status_code == 404


@pytest.mark.parametrize(
    "field, value, message",
    [
        ("to", "", "recipient"),
        ("subject", "", "subject"),
        ("body", "", "body"),
    ],
)
def test_send_draft_validates_required_fields(draft_sender_module, fake_pool, user, monkeypatch, field, value, message):
    monkeypatch.setattr(draft_sender_module.email_service, "send_email", AsyncMock(return_value=True))
    monkeypatch.setattr(draft_sender_module.audit_service, "record_event", AsyncMock())
    if field in {"to", "subject"}:
        fake_pool.row["metadata"][field] = value
        if field == "subject":
            fake_pool.row["title"] = ""
    else:
        fake_pool.row[field] = value

    with pytest.raises(HTTPException) as exc:
        run(draft_sender_module.send_draft(fake_pool.draft_id, user))

    assert exc.value.status_code == 400
    assert message in exc.value.detail


def test_send_draft_audit_event_no_body_in_metadata(draft_sender_module, fake_pool, user, monkeypatch):
    monkeypatch.setattr(draft_sender_module.email_service, "send_email", AsyncMock(return_value=True))
    audit = AsyncMock()
    monkeypatch.setattr(draft_sender_module.audit_service, "record_event", audit)

    run(draft_sender_module.send_draft(fake_pool.draft_id, user))

    metadata = audit.await_args.kwargs["metadata"]
    assert metadata["to"] == "rrhh@example.com"
    assert metadata["subject"] == "Error detectado"
    assert "body" not in metadata


def test_send_draft_fails_in_prod_without_smtp_host(draft_sender_module, fake_pool, user, monkeypatch):
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.delenv("SMTP_HOST", raising=False)
    send_email = AsyncMock(return_value=True)
    monkeypatch.setattr(draft_sender_module.email_service, "send_email", send_email)
    monkeypatch.setattr(draft_sender_module.audit_service, "record_event", AsyncMock())

    with pytest.raises(HTTPException) as exc:
        run(draft_sender_module.send_draft(fake_pool.draft_id, user))

    assert exc.value.status_code == 500
    assert fake_pool.row["status"] == "failed"
    send_email.assert_not_awaited()
