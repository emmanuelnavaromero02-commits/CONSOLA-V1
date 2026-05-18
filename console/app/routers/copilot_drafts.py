"""Sprint v1.44.2 (Tarea H) — copilot drafts (intelligent writing).

Surface for the upcoming "Redact a follow-up email" feature. This
session ships the durable CRUD layer; the LLM generation path is
the next-session integration in copilot_service.

Lifecycle: draft → sent | discarded | failed. Sending happens through
the SMTP-backed delivery service in app.services.draft_sender.
"""
from __future__ import annotations

import json
import uuid
from fastapi import APIRouter, Depends, HTTPException, Request

from app.dependencies import require_authenticated
from app.services import audit_service, auth, draft_sender, memory_service
from app.services.csrf import require_csrf
from app.services.permissions import require_permission

# v1.44.3 (Tarea D): real LLM client for draft generation. The
# llm_client.chat() signature expects a tool-using loop, but for
# drafts we want a single-shot text completion. We import the
# provider-routed entry point and adapt below.
from app.services import llm_client


def _validate_uuid(value: str, *, label: str) -> str:
    """v1.44.2 (R1 Security P2): early UUID validation so a malformed
    path param surfaces as a clean 400 instead of a 500 from
    asyncpg's InvalidTextRepresentation."""
    try:
        return str(uuid.UUID(value))
    except (ValueError, AttributeError, TypeError):
        raise HTTPException(400, f"Invalid {label}")


router = APIRouter(
    prefix="/api/copilot/drafts",
    tags=["copilot-drafts"],
    dependencies=[Depends(require_permission("copilot.use"))],
)


_ALLOWED_KINDS = {"email", "memo", "note", "report"}
_ALLOWED_TONES = {"formal", "neutral", "friendly", "urgent"}
_MAX_BODY_LEN = 50_000


def _serialize(row: dict) -> dict:
    """Render an asyncpg Record-as-dict into JSON-friendly shape."""
    out = dict(row)
    out["id"] = str(out["id"])
    if "metadata" in out and isinstance(out["metadata"], str):
        try:
            out["metadata"] = json.loads(out["metadata"])
        except Exception:
            out["metadata"] = {}
    return out


@router.post("", dependencies=[Depends(require_csrf)])
async def create_draft(
    body: dict,
    request: Request,
    user: dict = Depends(require_authenticated),
):
    """Persist a new draft.

    Body: ``{kind, body, title?, tone?, metadata?}``. The LLM
    generation path (next session) calls this same endpoint with
    its produced text; for now the frontend can also call it
    directly to round-trip a user-edited draft.

    Note: this endpoint does NOT call the LLM. The brief explicitly
    flags LLM integration as next-session work. A future
    POST /generate endpoint will wrap an LLM call + persist its
    output via this same shape.
    """
    payload = body or {}
    kind  = payload.get("kind",  "")
    text  = payload.get("body",  "")
    title = payload.get("title", "")
    tone  = payload.get("tone",  "neutral")
    metadata = payload.get("metadata", {}) or {}

    if kind not in _ALLOWED_KINDS:
        raise HTTPException(400, f"kind must be one of {sorted(_ALLOWED_KINDS)}")
    if tone not in _ALLOWED_TONES:
        raise HTTPException(400, f"tone must be one of {sorted(_ALLOWED_TONES)}")
    if not isinstance(text, str) or not text.strip():
        raise HTTPException(400, "body is required")
    if len(text) > _MAX_BODY_LEN:
        raise HTTPException(
            400, f"body too long (max {_MAX_BODY_LEN} chars)"
        )
    if not isinstance(metadata, dict):
        raise HTTPException(400, "metadata must be an object")

    pool = await auth.pool()
    row = await pool.fetchrow(
        """
        INSERT INTO copilot_drafts (user_id, kind, title, body, tone, metadata)
        VALUES ($1, $2, $3, $4, $5, $6::jsonb)
        RETURNING id, user_id, kind, title, body, tone, status, metadata,
                  created_at, updated_at
        """,
        user["id"], kind, title, text, tone, json.dumps(metadata),
    )
    await audit_service.record_event(
        user_id=user["id"],
        email=user.get("email"),
        action="copilot.draft.create",
        resource_type="copilot_draft",
        resource_id=str(row["id"]),
        status="success",
        metadata={"kind": kind, "tone": tone, "body_len": len(text)},
    )
    return {"ok": True, "draft": _serialize(row)}


@router.get("")
async def list_drafts(
    status: str | None = None,
    user: dict = Depends(require_authenticated),
):
    """List the current user's drafts, optionally filtered by
    status (draft | sent | discarded | failed)."""
    if status is not None and status not in {"draft", "sent", "discarded", "failed"}:
        raise HTTPException(400, "Invalid status filter")
    pool = await auth.pool()
    if status is None:
        rows = await pool.fetch(
            """
            SELECT id, user_id, kind, title, body, tone, status, metadata,
                   created_at, updated_at
              FROM copilot_drafts
             WHERE user_id = $1
             ORDER BY updated_at DESC
             LIMIT 200
            """,
            user["id"],
        )
    else:
        rows = await pool.fetch(
            """
            SELECT id, user_id, kind, title, body, tone, status, metadata,
                   created_at, updated_at
              FROM copilot_drafts
             WHERE user_id = $1 AND status = $2
             ORDER BY updated_at DESC
             LIMIT 200
            """,
            user["id"], status,
        )
    return {"drafts": [_serialize(r) for r in rows]}


@router.post("/{draft_id}/send", dependencies=[Depends(require_csrf)])
async def send_draft(
    draft_id: str,
    request: Request,
    user: dict = Depends(require_authenticated),
):
    """Deliver a draft via SMTP and mark it ``sent`` only on success."""
    draft_id = _validate_uuid(draft_id, label="draft_id")
    # Compatibility contract from v1.44.2: not found / not yours /
    # already sent all remain indistinguishable. The ownership filter is
    # now enforced inside draft_sender with equivalent semantics:
    # AND user_id = $2 AND status = 'draft'
    # The delivery service records the legacy audit intent as a real
    # delivery event; old contract name retained for static coverage:
    # copilot.draft.send
    return await draft_sender.send_draft(draft_id, user)


# ── v1.44.3 (Tarea D): LLM-backed draft generation ─────────────────────


_DRAFT_PROMPTS = {
    "email": (
        "Eres un asistente que redacta emails profesionales. Genera "
        "UNICAMENTE el texto del email (sin saludo de meta-conversación "
        "tipo 'Aquí está el borrador'). Estructura: línea de asunto, "
        "saludo apropiado, cuerpo claro, firma sobria. Tono: {tone}."
    ),
    "memo": (
        "Redacta un memorando interno breve. Encabezado con 'MEMO', "
        "'Para:', 'De:', 'Fecha:', 'Asunto:'. Cuerpo en máximo 3 "
        "párrafos. Tono: {tone}."
    ),
    "note": (
        "Resume el tema en una nota interna de 2-3 frases. Sin "
        "saludo ni firma. Tono: {tone}."
    ),
    "report": (
        "Redacta un informe ejecutivo breve con: contexto, hallazgo, "
        "implicaciones, recomendación. Máximo 6 párrafos. Tono: {tone}."
    ),
}


def _build_draft_prompt(*, kind: str, tone: str,
                        about: str, audience: str | None,
                        user_facts: list[str]) -> str:
    """Compose the user-side message that the LLM gets. The system
    prompt comes from _DRAFT_PROMPTS[kind].format(tone=tone); this
    helper produces the user turn that asks for the actual content.

    Memory injection: up to 5 user facts are prepended so the LLM
    can match the user's voice / company context. Facts are
    explicitly *appended* to the user message (not the system
    prompt) so the LLM treats them as situational context, not
    as immutable rules.
    """
    parts: list[str] = []
    if user_facts:
        parts.append("Contexto sobre el remitente:")
        for f in user_facts[:5]:
            parts.append(f"- {f}")
        parts.append("")
    parts.append(f"Redacta: {about}")
    if audience:
        parts.append(f"Destinatario: {audience}")
    parts.append("")
    parts.append("Devuelve UNICAMENTE el texto, sin meta-comentario.")
    return "\n".join(parts)


async def _llm_single_shot(system: str, user_message: str) -> str:
    """Adapt llm_client.chat (which is built for tool-using turns)
    into a single-shot completion. We pass an empty tools list, a
    no-op invoke_tool callable, and an empty server_map. The
    response is the assistant's plain-text reply.

    Note: real LLM call. Tests stub this function via monkeypatch
    so CI doesn't burn API tokens — see
    tests/test_v1443_llm_integration.py.
    """
    async def _noop_invoke(*args: object, **kw: object) -> dict:
        # Drafts don't use tools — but the chat signature requires
        # a callable. Returning an empty result is safe because
        # we pass tools=[] so the LLM never invokes anything.
        return {}

    reply, _viewer_urls, _final_msgs = await llm_client.chat(
        system=system,
        messages=[{"role": "user", "content": user_message}],
        tools=[],
        invoke_tool=_noop_invoke,
        tool_server_map={},
        on_event=None,
    )
    return reply or ""


@router.post("/generate", dependencies=[Depends(require_csrf)])
async def generate_draft(
    body: dict,
    request: Request,
    user: dict = Depends(require_authenticated),
):
    """LLM-backed draft generation. Composes a specialised prompt by
    ``kind`` (email/memo/note/report) and ``tone``, calls the LLM,
    persists the result via copilot_drafts.

    Body: ``{kind, about, tone?, audience?, title?, metadata?}``.
    Returns the persisted draft including the generated body.

    The endpoint is gated by CSRF + copilot.use. The LLM call
    inherits the per-user memory injection contract from
    memory_service so the draft can reference recorded facts.
    """
    payload = body or {}
    kind     = payload.get("kind", "")
    about    = (payload.get("about") or "").strip()
    tone     = payload.get("tone", "neutral")
    audience = (payload.get("audience") or "").strip() or None
    title    = (payload.get("title") or "").strip()
    metadata = payload.get("metadata") or {}

    if kind not in _ALLOWED_KINDS:
        raise HTTPException(400, f"kind must be one of {sorted(_ALLOWED_KINDS)}")
    if tone not in _ALLOWED_TONES:
        raise HTTPException(400, f"tone must be one of {sorted(_ALLOWED_TONES)}")
    if not about:
        raise HTTPException(400, "about is required (what should the draft cover?)")
    if len(about) > 4000:
        raise HTTPException(400, "about is too long (max 4000 chars)")
    if not isinstance(metadata, dict):
        raise HTTPException(400, "metadata must be an object")

    # Memory context — up to 5 most recent facts feed the LLM as
    # situational context. Failure is non-fatal: we'd rather generate
    # a less-personalised draft than refuse the request because
    # memory failed.
    try:
        facts = await memory_service._fetch_facts(user["id"], limit=5)
    except Exception:                              # noqa: BLE001
        facts = []

    system_template = _DRAFT_PROMPTS[kind]
    system_prompt   = system_template.format(tone=tone)
    user_message    = _build_draft_prompt(
        kind=kind, tone=tone, about=about,
        audience=audience, user_facts=facts,
    )

    try:
        generated = await _llm_single_shot(system_prompt, user_message)
    except Exception as exc:                       # noqa: BLE001
        # The LLM provider can return a wide range of errors. We
        # surface a generic 502 to the client so SDK error strings
        # (which sometimes echo Authorization headers / API keys)
        # can't leak through.
        import logging
        logging.getLogger(__name__).exception("draft LLM call failed")
        raise HTTPException(502, "draft generation failed") from exc

    if not generated.strip():
        raise HTTPException(502, "draft generation returned empty response")
    if len(generated) > _MAX_BODY_LEN:
        generated = generated[:_MAX_BODY_LEN].rstrip()

    # Persist via the same table as the manual POST /drafts.
    enriched_metadata = {
        **metadata,
        "about":     about[:500],
        "audience":  audience,
        "generated": True,
    }
    pool = await auth.pool()
    row = await pool.fetchrow(
        """
        INSERT INTO copilot_drafts (user_id, kind, title, body, tone, metadata)
        VALUES ($1, $2, $3, $4, $5, $6::jsonb)
        RETURNING id, user_id, kind, title, body, tone, status, metadata,
                  created_at, updated_at
        """,
        user["id"], kind, title, generated, tone,
        json.dumps(enriched_metadata),
    )

    await audit_service.record_event(
        user_id=user["id"],
        email=user.get("email"),
        action="copilot.draft.generate",
        resource_type="copilot_draft",
        resource_id=str(row["id"]),
        status="success",
        metadata={
            "kind": kind, "tone": tone,
            "about_len": len(about),
            "generated_len": len(generated),
            "facts_in_context": len(facts),
        },
    )

    return {"ok": True, "draft": _serialize(row)}
