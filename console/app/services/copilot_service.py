"""Sprint v1.42 — Copilot brain.

This module wires four pieces that already existed in v1.41:

  * ``llm_client`` — provider-agnostic chat (Anthropic default, Gemini
    or Ollama via env). Owns the tool-use loop and streams events.
  * ``mcp_registry`` — POSTs to each cartridge / mcp-infra service's
    ``/mcp/invoke``.
  * ``tool_manifest`` — classifies every tool as ``read`` / ``write`` /
    ``destructive`` and stamps ``requires_approval``.
  * ``audit_service`` — non-blocking ``audit_events`` writer that since
    v1.41.0 captures ``tool_name`` / ``tool_args`` / ``risk_level`` /
    ``conversation_id`` / ``ip`` / ``user_agent``.

What's new in v1.42:

  * **Persistence**: each turn writes rows to the ``conversations`` /
    ``conversation_messages`` tables created (empty) by migration 38.
  * **Approval gate**: ``destructive`` tools never auto-execute. The
    gated ``invoke_tool`` callback returns an "approval required"
    envelope to the LLM (so it explains itself) and stores the pending
    action in the assistant message's ``tool_calls`` JSONB. The UI
    renders an Aprobar / Cancelar card; Aprobar calls
    :func:`approve_pending_action` which executes the captured tool.
  * **RBAC**: every tool call checks ``permissions.has_permission``
    against the risk → permission map below.
  * **Secret scrubbing**: ``tool_args`` is sanitised before audit
    (passwords / tokens / api_keys replaced with ``"***"``).
  * **Ownership check**: a user can only read / send to their own
    conversations. Admins may read any.

The studio assistant (``studio_assistant.py``) is intentionally
untouched — it stays the wizard helper for cartridge configuration.
"""
from __future__ import annotations

import asyncio
import json
import uuid
from typing import Any

from fastapi import HTTPException

from app.services import audit_service, auth, llm_client, mcp_registry, permissions
from app.services import tool_manifest


SYSTEM_PROMPT = (
    "Eres el copiloto de OMEGA, una plataforma de integraciones enterprise "
    "(Replicon, SAP HCM, SAP S/4HANA, SAP SuccessFactors). Ayudas al usuario "
    "a consultar datos, ejecutar extracciones, revisar el estado de los DAGs "
    "de Airflow y operar la plataforma.\n\n"
    "REGLAS INVIOLABLES:\n"
    "- NUNCA inventes datos. Si una tool no devuelve la información, dilo "
    "explícitamente ('No encontré ese dato' / 'La consulta no devolvió "
    "resultados'). Jamás rellenes huecos con suposiciones.\n"
    "- Cada hecho que afirmes debe venir de una tool call real ejecutada en "
    "esta conversación.\n"
    "- Las acciones destructivas (delete, drop, truncate, set_variable, "
    "create_dag) requieren aprobación explícita del usuario. Cuando intentes "
    "ejecutar una de estas tools y la plataforma te devuelva "
    "'approval_required', NO la reintentes — explica al usuario qué vas a "
    "hacer y espera su confirmación.\n"
    "- Si el usuario no tiene permisos para una tool, la plataforma te lo "
    "dirá con 'permission_denied'. En ese caso explícale qué permiso necesita "
    "y para qué.\n"
    "- Cita siempre la fuente: cartucho, entidad, run_id o timestamp.\n"
    "- Lenguaje claro y conciso; sin jerga técnica innecesaria. Castellano "
    "por defecto, salvo que el usuario te escriba en otro idioma."
)


# Risk-level → permission required.
# read → ``copilot.use``  (granted to viewer / analyst / auditor / admin)
# write → ``copilot.write``  (admin + workspace_admin)
# destructive → ``copilot.execute`` AND an explicit approval card.
_PERMISSION_BY_RISK = {
    "read":        "copilot.use",
    "write":       "copilot.write",
    "destructive": "copilot.execute",
}


# Keys whose values are replaced with "***" before persisting tool_args
# to audit_events. The redaction filter in logging_config already covers
# logs; this protects the durable JSONB column.
_SECRET_KEYS = frozenset({
    "password", "passwd", "pass", "token", "secret", "api_key",
    "apikey", "api-key", "client_secret", "private_key",
    "auth_token", "bearer", "x-api-key", "internal_api_key",
})


# Older messages remain in the DB for audit / UI but don't reach the model.
_HISTORY_LIMIT = 40

# Hard cap on user_message size at the service layer.
MAX_USER_MESSAGE_CHARS = 16_000


# ── Helpers ─────────────────────────────────────────────────────────────────

def _scrub_args(args: Any) -> Any:
    """Recursively replace any value whose key looks secret with '***'."""
    if isinstance(args, dict):
        out: dict[str, Any] = {}
        for k, v in args.items():
            if str(k).lower() in _SECRET_KEYS:
                out[k] = "***"
            else:
                out[k] = _scrub_args(v)
        return out
    if isinstance(args, list):
        return [_scrub_args(v) for v in args]
    return args


def _required_permission(risk_level: str | None) -> str:
    return _PERMISSION_BY_RISK.get(risk_level or "write", "copilot.write")


def _is_admin_or_owner(conv_user_id: int, user: dict) -> bool:
    if user.get("id") == conv_user_id:
        return True
    role = (user.get("role") or "").lower()
    return role in {"admin", "owner", "super_admin"}


def _approval_key(bare_name: str, args: dict) -> str:
    return f"{bare_name}|{json.dumps(args, sort_keys=True, default=str)}"


async def _load_conversation(conn, conversation_id: str) -> dict | None:
    row = await conn.fetchrow(
        """
        SELECT id, user_id, workspace_id, title, created_at, updated_at
        FROM conversations
        WHERE id = $1::uuid
        """,
        conversation_id,
    )
    return dict(row) if row else None


async def _load_history(conn, conversation_id: str) -> list[dict]:
    """Reconstruct the message list shaped for the LLM. Tool calls and
    results are turned back into the structured blocks Anthropic's API
    expects so a follow-up turn sees the same context the LLM produced."""
    rows = await conn.fetch(
        """
        SELECT role, content, tool_calls, tool_results
        FROM conversation_messages
        WHERE conversation_id = $1::uuid
        ORDER BY created_at
        LIMIT $2
        """,
        conversation_id,
        _HISTORY_LIMIT,
    )
    out: list[dict] = []
    for r in rows:
        role = r["role"]
        content = r["content"] or ""
        tool_calls = r["tool_calls"]
        tool_results = r["tool_results"]
        if isinstance(tool_calls, str):
            try: tool_calls = json.loads(tool_calls)
            except Exception: tool_calls = None
        if isinstance(tool_results, str):
            try: tool_results = json.loads(tool_results)
            except Exception: tool_results = None

        if role == "assistant" and tool_calls:
            blocks: list[dict] = []
            if content:
                blocks.append({"type": "text", "text": content})
            for c in tool_calls:
                blocks.append({
                    "type": "tool_use",
                    "id": c.get("id") or str(uuid.uuid4()),
                    "name": c.get("name"),
                    "input": c.get("input") or {},
                })
            out.append({"role": "assistant", "content": blocks})
        elif role == "tool" and tool_results:
            out.append({
                "role": "user",
                "content": [
                    {"type": "tool_result",
                     "tool_use_id": tr.get("tool_use_id"),
                     "content": tr.get("content") or ""}
                    for tr in tool_results
                ],
            })
        else:
            out.append({"role": role, "content": content})
    return out


async def _persist_message(
    conn,
    *,
    conversation_id: str,
    role: str,
    content: str | None = None,
    tool_calls: list[dict] | None = None,
    tool_results: list[dict] | None = None,
    model: str | None = None,
) -> str:
    """Insert one row in conversation_messages, bump the parent
    conversation's updated_at, return the new message's UUID."""
    row = await conn.fetchrow(
        """
        INSERT INTO conversation_messages
            (conversation_id, role, content, tool_calls, tool_results, model)
        VALUES ($1::uuid, $2, $3, $4::jsonb, $5::jsonb, $6)
        RETURNING id
        """,
        conversation_id,
        role,
        content,
        json.dumps(tool_calls) if tool_calls is not None else None,
        json.dumps(tool_results) if tool_results is not None else None,
        model,
    )
    await conn.execute(
        "UPDATE conversations SET updated_at = NOW() WHERE id = $1::uuid",
        conversation_id,
    )
    return str(row["id"])


async def _build_tools_for_llm() -> tuple[list[dict], dict[str, str], dict[str, dict]]:
    """Return (tools_for_llm, tool_server_map, classifications_by_full_name).

    Tool name format follows the llm_client convention
    ``{server}___{bare_name}`` so the server can be recovered on the
    tool_use callback without a second registry lookup.
    """
    manifest = await tool_manifest.build_manifest()
    tools: list[dict] = []
    server_map: dict[str, str] = {}
    classifications: dict[str, dict] = {}
    for srv_id, srv_tools in (manifest.get("servers") or {}).items():
        for t in srv_tools:
            bare = t["name"]
            full = f"{srv_id}___{bare}"
            tools.append({
                "name": full,
                "description": t.get("description", ""),
                "input_schema": t.get("input_schema") or {"type": "object", "properties": {}},
            })
            server_map[full] = srv_id
            classifications[full] = {
                "bare_name": bare,
                "server": srv_id,
                "risk_level": t.get("risk_level", "write"),
                "requires_approval": bool(t.get("requires_approval")),
            }
    return tools, server_map, classifications


def _audit(
    *,
    user: dict,
    server: str,
    bare_name: str,
    args: dict,
    risk_level: str,
    conversation_id: str,
    ip: str | None,
    user_agent: str | None,
    status: str,
    error: str | None = None,
) -> None:
    """Fire-and-forget audit. Failures here MUST NOT break the turn."""
    metadata: dict[str, Any] = {"server": server}
    if error:
        metadata["error"] = error[:500]
    try:
        asyncio.create_task(audit_service.record_event(
            user_id=user.get("id"),
            email=user.get("email"),
            action=f"copilot.tool.{bare_name}",
            resource_type="mcp_tool",
            resource_id=f"{server}/{bare_name}",
            ip=ip,
            user_agent=user_agent,
            status=status,
            metadata=metadata,
            tool_name=bare_name,
            tool_args=_scrub_args(args),
            tool_result_status=status,
            risk_level=risk_level,
            conversation_id=conversation_id,
        ))
    except Exception:
        pass


# ── Public CRUD ─────────────────────────────────────────────────────────────

async def create_conversation(
    *, user_id: int, workspace_id: str | None = None, title: str | None = None,
) -> dict:
    pool = await auth.pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            INSERT INTO conversations (user_id, workspace_id, title)
            VALUES ($1, $2::uuid, $3)
            RETURNING id, user_id, workspace_id, title, created_at, updated_at
            """,
            user_id,
            workspace_id,
            (title or "Nueva conversación")[:200],
        )
    return {**dict(row), "id": str(row["id"])}


async def list_conversations(
    *, user_id: int, workspace_id: str | None = None, limit: int = 50,
) -> dict:
    pool = await auth.pool()
    async with pool.acquire() as conn:
        if workspace_id:
            rows = await conn.fetch(
                """
                SELECT id, title, created_at, updated_at
                FROM conversations
                WHERE user_id = $1 AND workspace_id = $2::uuid AND archived_at IS NULL
                ORDER BY updated_at DESC
                LIMIT $3
                """,
                user_id, workspace_id, limit,
            )
        else:
            rows = await conn.fetch(
                """
                SELECT id, title, created_at, updated_at
                FROM conversations
                WHERE user_id = $1 AND archived_at IS NULL
                ORDER BY updated_at DESC
                LIMIT $2
                """,
                user_id, limit,
            )
    return {"conversations": [
        {**dict(r), "id": str(r["id"])} for r in rows
    ]}


async def get_conversation_messages(*, conversation_id: str, user: dict) -> dict:
    pool = await auth.pool()
    async with pool.acquire() as conn:
        conv = await _load_conversation(conn, conversation_id)
        if not conv:
            raise HTTPException(404, "conversation not found")
        if not _is_admin_or_owner(conv["user_id"], user):
            raise HTTPException(403, "not your conversation")
        rows = await conn.fetch(
            """
            SELECT id, role, content, tool_calls, tool_results, created_at
            FROM conversation_messages
            WHERE conversation_id = $1::uuid
            ORDER BY created_at
            """,
            conversation_id,
        )
    return {
        "conversation": {**conv, "id": str(conv["id"])},
        "messages": [
            {
                "id": str(r["id"]),
                "role": r["role"],
                "content": r["content"],
                "tool_calls": json.loads(r["tool_calls"]) if isinstance(r["tool_calls"], str) else r["tool_calls"],
                "tool_results": json.loads(r["tool_results"]) if isinstance(r["tool_results"], str) else r["tool_results"],
                "created_at": r["created_at"],
            }
            for r in rows
        ],
    }


# ── The turn loop ───────────────────────────────────────────────────────────

async def _run_loop(
    *,
    conversation_id: str,
    user: dict,
    ip: str | None,
    user_agent: str | None,
    approved_keys: set[str] | None = None,
) -> dict:
    """Shared core used by ``run_turn`` and ``approve_pending_action``."""
    approved_keys = approved_keys or set()
    pending_actions: list[dict] = []
    tool_calls_log: list[dict] = []
    tool_results_log: list[dict] = []

    tools, server_map, classifications = await _build_tools_for_llm()

    async def invoke_tool(server_id: str, bare_name: str, args: dict) -> dict:
        full = f"{server_id}___{bare_name}"
        meta = classifications.get(full) or {
            "risk_level": "write", "requires_approval": True,
        }
        risk = meta["risk_level"]
        needed = _required_permission(risk)

        # 1. RBAC. Denied tool calls become an error envelope to the LLM,
        #    never an exception — that would let one user spend the LLM
        #    on tools they can't run.
        if not permissions.has_permission(user, needed):
            _audit(
                user=user, server=server_id, bare_name=bare_name, args=args,
                risk_level=risk, conversation_id=conversation_id,
                ip=ip, user_agent=user_agent, status="denied",
                error=f"missing permission {needed}",
            )
            return {
                "error": "permission_denied",
                "required_permission": needed,
                "message": (
                    f"El usuario no tiene el permiso '{needed}' necesario "
                    f"para invocar {bare_name} ({risk}). Explica al usuario "
                    f"qué permiso necesita y para qué."
                ),
            }

        # 2. Destructive → approval gate. Never auto-execute.
        key = _approval_key(bare_name, args)
        if risk == "destructive" and key not in approved_keys:
            pending_actions.append({
                "server": server_id,
                "tool": bare_name,
                "args": args,
                "risk_level": risk,
                "approval_key": key,
            })
            _audit(
                user=user, server=server_id, bare_name=bare_name, args=args,
                risk_level=risk, conversation_id=conversation_id,
                ip=ip, user_agent=user_agent, status="pending_approval",
            )
            return {
                "error": "approval_required",
                "message": (
                    f"La acción '{bare_name}' es destructiva y requiere "
                    f"aprobación explícita del usuario. NO la reintentes — "
                    f"explica al usuario qué hará y espera su confirmación."
                ),
                "tool": bare_name,
                "args": _scrub_args(args),
            }

        # 3. Actually execute via mcp_registry.
        try:
            result = await mcp_registry.invoke(server_id, bare_name, args)
        except Exception as exc:                    # noqa: BLE001
            _audit(
                user=user, server=server_id, bare_name=bare_name, args=args,
                risk_level=risk, conversation_id=conversation_id,
                ip=ip, user_agent=user_agent, status="error",
                error=str(exc),
            )
            return {"error": f"invocation failed: {exc}"}

        is_error = isinstance(result, dict) and bool(result.get("error"))
        _audit(
            user=user, server=server_id, bare_name=bare_name, args=args,
            risk_level=risk, conversation_id=conversation_id,
            ip=ip, user_agent=user_agent,
            status="error" if is_error else "success",
            error=str(result.get("error")) if is_error else None,
        )
        tool_calls_log.append({
            "server": server_id, "tool": bare_name, "args": _scrub_args(args),
            "risk_level": risk,
        })
        tool_results_log.append({"tool": bare_name, "result": result})
        return result

    pool = await auth.pool()
    async with pool.acquire() as conn:
        history = await _load_history(conn, conversation_id)

    try:
        reply_text, _viewer_urls, _final_msgs = await llm_client.chat(
            system=SYSTEM_PROMPT,
            messages=history,
            tools=tools,
            invoke_tool=invoke_tool,
            tool_server_map=server_map,
            on_event=None,
        )
    except Exception as exc:                    # noqa: BLE001
        pool = await auth.pool()
        async with pool.acquire() as conn:
            await _persist_message(
                conn, conversation_id=conversation_id,
                role="assistant",
                content=f"⚠️ El proveedor LLM devolvió un error: {str(exc)[:200]}",
            )
        raise HTTPException(502, f"llm provider error: {exc}")

    pool = await auth.pool()
    async with pool.acquire() as conn:
        message_id = await _persist_message(
            conn, conversation_id=conversation_id,
            role="assistant",
            content=reply_text,
            tool_calls=(tool_calls_log + pending_actions) or None,
            tool_results=tool_results_log or None,
        )

    return {
        "message_id": message_id,
        "reply": reply_text,
        "tool_calls": tool_calls_log,
        "tool_results": tool_results_log,
        "pending_actions": pending_actions,
        "requires_approval": bool(pending_actions),
    }


async def run_turn(
    *,
    conversation_id: str,
    user_message: str,
    user: dict,
    ip: str | None = None,
    user_agent: str | None = None,
) -> dict:
    """Handle one user → assistant exchange."""
    if not permissions.has_permission(user, "copilot.use"):
        raise HTTPException(403, "permission required: copilot.use")
    if not user_message or not user_message.strip():
        raise HTTPException(400, "empty message")
    if len(user_message) > MAX_USER_MESSAGE_CHARS:
        raise HTTPException(413, f"message too long (max {MAX_USER_MESSAGE_CHARS} chars)")

    pool = await auth.pool()
    async with pool.acquire() as conn:
        conv = await _load_conversation(conn, conversation_id)
        if not conv:
            raise HTTPException(404, "conversation not found")
        if conv["user_id"] != user.get("id"):
            raise HTTPException(403, "not your conversation")
        await _persist_message(
            conn, conversation_id=conversation_id,
            role="user", content=user_message,
        )

    return await _run_loop(
        conversation_id=conversation_id,
        user=user, ip=ip, user_agent=user_agent,
        approved_keys=None,
    )


async def approve_pending_action(
    *,
    conversation_id: str,
    message_id: str,
    user: dict,
    ip: str | None = None,
    user_agent: str | None = None,
) -> dict:
    """Execute every pending destructive action stored in the given
    assistant message, then continue the conversation so the LLM can
    respond to the new tool results."""
    if not permissions.has_permission(user, "copilot.execute"):
        raise HTTPException(403, "permission required: copilot.execute")

    pool = await auth.pool()
    async with pool.acquire() as conn:
        conv = await _load_conversation(conn, conversation_id)
        if not conv:
            raise HTTPException(404, "conversation not found")
        if conv["user_id"] != user.get("id"):
            raise HTTPException(403, "not your conversation")
        msg = await conn.fetchrow(
            "SELECT tool_calls FROM conversation_messages "
            "WHERE id = $1::uuid AND conversation_id = $2::uuid",
            message_id, conversation_id,
        )
    if not msg:
        raise HTTPException(404, "message not found")
    raw_calls = msg["tool_calls"]
    if isinstance(raw_calls, str):
        try: raw_calls = json.loads(raw_calls)
        except Exception: raw_calls = None
    if not raw_calls:
        raise HTTPException(400, "message has no pending actions")
    approved_keys = {
        c["approval_key"] for c in raw_calls
        if isinstance(c, dict) and c.get("approval_key")
        and c.get("risk_level") == "destructive"
    }
    if not approved_keys:
        raise HTTPException(400, "no destructive actions pending in this message")

    return await _run_loop(
        conversation_id=conversation_id,
        user=user, ip=ip, user_agent=user_agent,
        approved_keys=approved_keys,
    )
