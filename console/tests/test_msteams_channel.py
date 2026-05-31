"""Microsoft Teams channel — unit tests.

Covers the required v0.1 contract: disabled ignores, DM normalization,
user/tenant/conversation allowlist rejection, copilot→Teams rendering,
safe errors (no secret/stacktrace leakage), Graph-disabled basic bot,
transcript-disabled clear status, JWT strict fail-closed, mention gating,
audit privacy (length not text).

The copilot, DB pool and audit are mocked — the live path is covered by the
stack/CI. We assert the channel calls the EXISTING copilot, never a fake.
"""
from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from app.channels.msteams import adapter, config, meetings, security, service
from app.channels.msteams.config import MsTeamsConfig
from app.channels.msteams.schemas import InternalCopilotResponse

AAD = "11111111-1111-1111-1111-111111111111"
CONV = "19:abc123@thread.tacv2"


def _cfg(**overrides) -> MsTeamsConfig:
    base = dict(
        enabled=True,
        app_id="app-123",
        app_password="secret-shh",
        tenant_id="tenant-1",
        webhook_path="/api/msteams/messages",
        require_mention=True,
        dm_policy="allowlist",
        group_policy="allowlist",
        allowed_users=(AAD,),
        allowed_conversations=(CONV,),
        allowed_tenants=("tenant-1",),
        user_map={AAD.lower(): "agent@org.com"},
        default_user_email="",
        jwt_mode="disabled",  # most tests skip JWT; JWT has its own tests
        graph_enabled=False,
        transcripts_enabled=False,
        sharepoint_site_id="",
        dangerously_allow_name_matching=False,
    )
    base.update(overrides)
    return MsTeamsConfig(**base)


def _dm_activity(text="hola copiloto", aad=AAD, tenant="tenant-1"):
    return {
        "type": "message",
        "id": "msg-1",
        "text": text,
        "serviceUrl": "https://smba.trafficmanager.net/teams",
        "from": {"id": "29:from", "aadObjectId": aad, "name": "Display Name"},
        "recipient": {"id": "28:bot"},
        "conversation": {"id": "a:dmconv", "conversationType": "personal", "tenantId": tenant},
    }


def _channel_activity(text="<at>Bot</at> hola", conv=CONV, aad=AAD, mention_bot=True):
    ents = []
    if mention_bot:
        ents.append({"type": "mention", "mentioned": {"id": "28:bot"}, "text": "<at>Bot</at>"})
    return {
        "type": "message",
        "id": "msg-2",
        "text": text,
        "serviceUrl": "https://smba.trafficmanager.net/teams",
        "from": {"id": "29:from", "aadObjectId": aad, "name": "Display Name"},
        "recipient": {"id": "28:bot"},
        "conversation": {"id": conv, "conversationType": "channel", "tenantId": "tenant-1"},
        "entities": ents,
    }


def _patch_backend(reply="respuesta del copiloto", citations=None, raise_turn=False):
    """Patch the three external collaborators on the service module."""
    run_turn = AsyncMock(
        side_effect=RuntimeError("boom: secret-shh /internal/path") if raise_turn
        else None,
        return_value={"reply": reply, "citations": citations or [], "requires_approval": False},
    )
    fake_conn = AsyncMock()
    fake_conn.fetchrow = AsyncMock(return_value={"conversation_id": "00000000-0000-0000-0000-0000000000aa"})

    class _PoolCtx:
        async def __aenter__(self):
            return fake_conn

        async def __aexit__(self, *a):
            return False

    fake_pool = AsyncMock()
    fake_pool.acquire = lambda: _PoolCtx()

    return patch.multiple(
        service,
        copilot_service=AsyncMock(
            run_turn=run_turn,
            create_conversation=AsyncMock(return_value={"id": "00000000-0000-0000-0000-0000000000aa"}),
        ),
        auth=AsyncMock(
            get_user_by_email=AsyncMock(return_value={"id": 42, "email": "agent@org.com", "is_active": True, "role": "analyst"}),
            pool=AsyncMock(return_value=fake_pool),
        ),
        audit_service=AsyncMock(record_event=AsyncMock()),
    ), run_turn


# ── Level 0: disabled ────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_disabled_channel_ignores_everything():
    cfg = _cfg(enabled=False)
    with _patch_backend()[0]:
        res = await service.handle_activity(_dm_activity(), cfg=cfg)
    assert res.status == "disabled"
    assert res.activity is None


# ── Inbound parsing / normalization ──────────────────────────────────

def test_parse_dm_activity_to_internal_request():
    event = adapter.parse_activity(_dm_activity(text="cuánto vendimos"))
    assert event.mode() == "dm"
    assert event.aad_object_id == AAD
    assert event.text == "cuánto vendimos"
    from app.channels.msteams.schemas import PermissionsContext
    req = adapter.to_internal_request(event, PermissionsContext())
    assert req.source == "msteams"
    assert req.text == "cuánto vendimos"
    assert req.metadata["mode"] == "dm"


def test_mention_is_stripped_from_channel_text():
    event = adapter.parse_activity(_channel_activity(text="<at>Bot</at> resume el día"))
    assert event.mentioned_bot is True
    assert "Bot" not in event.text
    assert event.text.strip() == "resume el día"


# ── Happy path: DM → copilot → Teams reply ───────────────────────────

@pytest.mark.asyncio
async def test_valid_dm_drives_copilot_and_renders_reply():
    ctx, run_turn = _patch_backend(reply="Vendimos 100", citations=[{"title": "ventas_gold"}])
    with ctx:
        res = await service.handle_activity(_dm_activity(), cfg=_cfg())
    assert res.status == "ok"
    assert res.activity["type"] == "message"
    assert "Vendimos 100" in res.activity["text"]
    assert "ventas_gold" in res.activity["text"]  # citation footer
    run_turn.assert_awaited_once()
    # Drove the REAL copilot with the user's text + resolved console user +
    # the mapped console conversation id (continuity, not None).
    kwargs = run_turn.await_args.kwargs
    assert kwargs["user_message"] == "hola copiloto"
    assert kwargs["user"]["id"] == 42
    assert kwargs["conversation_id"] == "00000000-0000-0000-0000-0000000000aa"


@pytest.mark.asyncio
async def test_channel_mention_allowlisted_is_accepted():
    ctx, run_turn = _patch_backend(reply="ok")
    with ctx:
        res = await service.handle_activity(
            _channel_activity(text="<at>Bot</at> resume el dia"), cfg=_cfg()
        )
    assert res.status == "ok"
    run_turn.assert_awaited_once()
    # The bot mention must be stripped before the text reaches the copilot,
    # and the allowlisted conversation must have produced a real conversation.
    kwargs = run_turn.await_args.kwargs
    assert kwargs["user_message"] == "resume el dia"
    assert "Bot" not in kwargs["user_message"]
    assert kwargs["conversation_id"] == "00000000-0000-0000-0000-0000000000aa"


@pytest.mark.asyncio
async def test_group_empty_user_allowlist_rejects_even_in_allowlisted_conversation():
    # Security regression: an allowlisted conversation with an EMPTY user
    # allowlist must reject (empty allowlist = nobody), not fall open.
    ctx, run_turn = _patch_backend()
    with ctx:
        res = await service.handle_activity(
            _channel_activity(), cfg=_cfg(allowed_users=())
        )
    assert res.status == "unauthorized"
    run_turn.assert_not_awaited()


@pytest.mark.asyncio
async def test_group_open_policy_allows_any_member_with_mention():
    ctx, run_turn = _patch_backend(reply="ok")
    with ctx:
        res = await service.handle_activity(
            _channel_activity(aad="00000000-0000-0000-0000-000000000000"),
            # open mode trusts channel members; they still need a console
            # identity — here a shared service account resolves them.
            cfg=_cfg(group_policy="open", allowed_users=(), user_map={}, default_user_email="agent@org.com"),
        )
    assert res.status == "ok"  # open mode trusts channel members (mention-gated)
    run_turn.assert_awaited_once()


# ── Rejections ───────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_user_not_allowed_is_rejected_silently():
    ctx, run_turn = _patch_backend()
    with ctx:
        res = await service.handle_activity(_dm_activity(aad="99999999-9999-9999-9999-999999999999"), cfg=_cfg())
    assert res.status == "unauthorized"
    assert res.activity is None
    run_turn.assert_not_awaited()


@pytest.mark.asyncio
async def test_tenant_not_allowed_is_rejected():
    ctx, run_turn = _patch_backend()
    with ctx:
        res = await service.handle_activity(_dm_activity(tenant="evil-tenant"), cfg=_cfg())
    assert res.status == "unauthorized"
    run_turn.assert_not_awaited()


@pytest.mark.asyncio
async def test_conversation_not_allowed_rejected_when_group_allowlist():
    ctx, run_turn = _patch_backend()
    with ctx:
        res = await service.handle_activity(
            _channel_activity(conv="19:NOTallowed@thread.tacv2"), cfg=_cfg()
        )
    assert res.status == "unauthorized"
    run_turn.assert_not_awaited()


@pytest.mark.asyncio
async def test_mention_required_blocks_unmentioned_channel_message():
    ctx, run_turn = _patch_backend()
    with ctx:
        res = await service.handle_activity(
            _channel_activity(text="hola sin mencion", mention_bot=False), cfg=_cfg()
        )
    assert res.status == "unauthorized"  # mention_required → denied
    run_turn.assert_not_awaited()


@pytest.mark.asyncio
async def test_no_console_mapping_is_rejected():
    ctx, run_turn = _patch_backend()
    # Allowed user, but no user_map entry and no default service account.
    with ctx:
        res = await service.handle_activity(_dm_activity(), cfg=_cfg(user_map={}, default_user_email=""))
    assert res.status == "unauthorized"
    run_turn.assert_not_awaited()


@pytest.mark.asyncio
async def test_empty_text_is_ignored():
    ctx, _ = _patch_backend()
    with ctx:
        res = await service.handle_activity(_dm_activity(text="   "), cfg=_cfg())
    assert res.status == "ignored"


# ── Safe errors: no secret/stacktrace/path leakage ───────────────────

@pytest.mark.asyncio
async def test_internal_error_does_not_leak_secrets_or_paths():
    ctx, _ = _patch_backend(raise_turn=True)
    with ctx:
        res = await service.handle_activity(_dm_activity(), cfg=_cfg())
    assert res.status == "error"
    blob = (res.detail or "") + (res.activity["text"] if res.activity else "")
    for needle in ("secret-shh", "/internal/path", "Traceback", "RuntimeError", "boom"):
        assert needle not in blob, f"leaked {needle!r}"
    # The user still gets a friendly, generic reply.
    assert res.activity and res.activity["type"] == "message"


# ── Audit privacy: length, not raw text ──────────────────────────────

@pytest.mark.asyncio
async def test_audit_records_length_not_raw_text():
    ctx, _ = _patch_backend()
    captured = {}
    with ctx:
        service.audit_service.record_event.side_effect = lambda **kw: captured.update(kw)
        await service.handle_activity(_dm_activity(text="dato confidencial 123"), cfg=_cfg())
    md = captured.get("metadata", {})
    assert md.get("channel") == "msteams"
    assert "text_len" in md and md["text_len"] == len("dato confidencial 123")
    blob = repr(captured)
    assert "dato confidencial 123" not in blob, "raw message text must not be audited"


# ── JWT posture ──────────────────────────────────────────────────────

def test_jwt_strict_fails_closed_without_verifier():
    cfg = _cfg(jwt_mode="strict")
    d = security.verify_jwt("Bearer abc.def.ghi", cfg)
    assert d.allowed is False
    assert d.reason == "strict_signature_verification_unavailable"


def test_jwt_missing_bearer_rejected_when_not_disabled():
    cfg = _cfg(jwt_mode="claims")
    assert security.verify_jwt(None, cfg).allowed is False
    assert security.verify_jwt("", cfg).allowed is False


def test_jwt_disabled_allows():
    assert security.verify_jwt(None, _cfg(jwt_mode="disabled")).allowed is True


def _mint(claims: dict) -> str:
    # Use python-jose to match console's runtime JWT library (PyJWT is NOT in
    # console/requirements.txt → would break collection in CI). The signature
    # is irrelevant because claims mode never verifies it.
    from jose import jwt as _jwt
    return "Bearer " + _jwt.encode(claims, "x" * 32, algorithm="HS256")


def test_jwt_claims_mode_accepts_valid_and_rejects_bad_aud_and_expired():
    import time
    cfg = _cfg(jwt_mode="claims")
    good = _mint({
        "iss": "https://api.botframework.com",
        "aud": "app-123",  # == cfg.app_id
        "exp": int(time.time()) + 3600,
    })
    assert security.verify_jwt(good, cfg).allowed is True
    # Wrong audience → rejected.
    bad_aud = _mint({"iss": "https://api.botframework.com", "aud": "someone-else", "exp": int(time.time()) + 3600})
    assert security.verify_jwt(bad_aud, cfg).allowed is False
    # Expired → rejected.
    expired = _mint({"iss": "https://api.botframework.com", "aud": "app-123", "exp": int(time.time()) - 3600})
    assert security.verify_jwt(expired, cfg).allowed is False
    # Unknown issuer → rejected.
    bad_iss = _mint({"iss": "https://evil.example.com", "aud": "app-123", "exp": int(time.time()) + 3600})
    assert security.verify_jwt(bad_iss, cfg).allowed is False


@pytest.mark.asyncio
async def test_default_user_email_used_when_no_user_map_entry():
    # No per-user map, but a default service account is configured → resolves.
    ctx, run_turn = _patch_backend(reply="ok")
    cfg = _cfg(user_map={}, default_user_email="agent@org.com")
    with ctx:
        res = await service.handle_activity(_dm_activity(), cfg=cfg)
    assert res.status == "ok"
    run_turn.assert_awaited_once()


@pytest.mark.asyncio
async def test_conversation_mapping_degrades_to_adhoc_on_pool_failure():
    # If the mapping store is unavailable, the bot still answers (ad-hoc
    # conversation), never 500s — and the copilot is still driven.
    ctx, run_turn = _patch_backend(reply="ok")
    with ctx:
        service.auth.pool.side_effect = RuntimeError("db down")
        res = await service.handle_activity(_dm_activity(), cfg=_cfg())
        # Assert inside the patch context (copilot_service is the mock here).
        service.copilot_service.create_conversation.assert_awaited()
    assert res.status == "ok"
    run_turn.assert_awaited_once()
    # The fallback still produced a real conversation id for the copilot turn.
    assert run_turn.await_args.kwargs["conversation_id"] == "00000000-0000-0000-0000-0000000000aa"


@pytest.mark.asyncio
async def test_non_numeric_user_id_yields_safe_error_not_500():
    ctx, run_turn = _patch_backend()
    with ctx:
        service.auth.get_user_by_email.return_value = {
            "id": "not-a-number", "email": "agent@org.com", "is_active": True, "role": "analyst",
        }
        res = await service.handle_activity(_dm_activity(), cfg=_cfg())
    assert res.status == "error"
    assert res.activity and res.activity["type"] == "message"  # safe generic reply
    run_turn.assert_not_awaited()


# ── Name matching is off by default ──────────────────────────────────

def test_display_name_is_never_used_for_authorization():
    # A sender whose display name matches an allowlisted thing but whose
    # stable id is NOT allowlisted must be rejected.
    cfg = _cfg(allowed_users=(AAD,))
    event = adapter.parse_activity(_dm_activity(aad="00000000-0000-0000-0000-000000000000"))
    decision, _ = security.authorize(event, cfg)
    assert decision.allowed is False


# ── Level 3 scaffolding: graph/transcripts disabled ──────────────────

def test_graph_disabled_does_not_break_basic_bot():
    # The basic bot config has graph_enabled=False; status reports it cleanly.
    snap = service.status_snapshot(_cfg(graph_enabled=False))
    assert snap["graph_enabled"] is False
    assert snap["enabled"] is True


def test_transcript_disabled_returns_clear_status_not_exception():
    # Graph on, transcripts off → the gate isolates the transcript reason.
    cfg = _cfg(graph_enabled=True, transcripts_enabled=False)
    st = meetings.transcript_ingestion("meeting-1", cfg=cfg)
    assert st.available is False
    assert st.reason == "transcripts_disabled"  # clear, not an exception
    st2 = meetings.minutes_generator("meeting-1", cfg=cfg)
    assert st2.available is False
    # And with the whole channel disabled it still returns a status (no raise).
    st3 = meetings.transcript_ingestion("m", cfg=_cfg(enabled=False))
    assert st3.available is False and st3.reason == "channel_disabled"


def test_meeting_capabilities_status_shape():
    cfg = _cfg(graph_enabled=True, transcripts_enabled=True)
    cap = meetings.capabilities_status(cfg)
    # Both flags on, but the real Graph implementation is a future PR →
    # honestly reports not-implemented, never fabricated availability.
    assert cap["graph_enabled"] is True
    assert cap["transcripts_enabled"] is True
    assert cap["post_meeting_available"] is False


# ── Status snapshot never leaks secrets ──────────────────────────────

def test_status_snapshot_has_no_secrets():
    snap = service.status_snapshot(_cfg())
    blob = repr(snap).lower()
    for needle in ("secret-shh", "app_password", "password"):
        assert needle not in blob, f"status leaked {needle!r}"


# ── Config: default-closed ───────────────────────────────────────────

# ── Router HTTP contract ─────────────────────────────────────────────

def _router_client():
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from app.routers import msteams as r
    app = FastAPI()
    app.include_router(r.router)
    return TestClient(app), r


def test_webhook_returns_200_when_disabled():
    client, r = _router_client()
    with patch.object(r, "load_config", return_value=_cfg(enabled=False)):
        resp = client.post("/api/msteams/messages", json={"type": "message", "text": "hi"})
    assert resp.status_code == 200
    assert resp.content in (b"", b"null")


def test_webhook_returns_reply_activity_on_ok():
    from app.channels.msteams.schemas import ChannelResult
    client, r = _router_client()
    activity = {"type": "message", "textFormat": "markdown", "text": "hola desde el copiloto"}
    with patch.object(r, "load_config", return_value=_cfg()), \
         patch.object(r.service, "handle_activity",
                      AsyncMock(return_value=ChannelResult(status="ok", activity=activity))):
        resp = client.post("/api/msteams/messages", json=_dm_activity())
    assert resp.status_code == 200
    assert resp.json()["text"] == "hola desde el copiloto"


def test_webhook_unauthorized_returns_empty_200():
    from app.channels.msteams.schemas import ChannelResult
    client, r = _router_client()
    with patch.object(r, "load_config", return_value=_cfg()), \
         patch.object(r.service, "handle_activity",
                      AsyncMock(return_value=ChannelResult(status="unauthorized"))):
        resp = client.post("/api/msteams/messages", json=_dm_activity(aad="nope"))
    assert resp.status_code == 200
    assert resp.content in (b"", b"null")  # bot never advertised


def test_webhook_bad_json_returns_400_when_enabled():
    client, r = _router_client()
    with patch.object(r, "load_config", return_value=_cfg()):
        resp = client.post("/api/msteams/messages", data="{not json",
                           headers={"content-type": "application/json"})
    assert resp.status_code == 400


def test_webhook_path_is_public_in_main_auth_allowlist():
    # The Bot Framework JWT — not a console session — authenticates Teams, so
    # the webhook must bypass console auth. Guard the allowlist entry.
    import re
    from pathlib import Path
    main_src = (Path(__file__).resolve().parents[1] / "app" / "main.py").read_text(encoding="utf-8")
    assert '"/api/msteams/messages"' in main_src, (
        "/api/msteams/messages must be in _AUTH_PUBLIC_EXACT"
    )
    block = main_src.split("_AUTH_PUBLIC_EXACT", 1)[1].split("}", 1)[0]
    assert "/api/msteams/messages" in block


def test_config_defaults_are_closed(monkeypatch):
    for var in (
        "MSTEAMS_ENABLED", "MSTEAMS_DM_POLICY", "MSTEAMS_GROUP_POLICY",
        "MSTEAMS_ALLOWED_USERS", "MSTEAMS_REQUIRE_MENTION",
    ):
        monkeypatch.delenv(var, raising=False)
    cfg = config.load_config()
    assert cfg.enabled is False
    assert cfg.dm_policy == "allowlist"
    assert cfg.group_policy == "allowlist"
    assert cfg.require_mention is True
    assert config.active_level(cfg) == 0
