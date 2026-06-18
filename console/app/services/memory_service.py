"""Sprint v1.44.3 (Tarea D) — memory injection + extraction.

The v1.44.2 migration 51 created the three memory tables
(user_facts, user_preferences, conversation_memory_summary) and
v1.44.2's copilot_memory router exposes CRUD. This module is the
bridge between the storage layer and copilot_service.run_turn:

  build_system_prompt_with_memory(user_id, base_prompt)
      → reads the user's recent facts + every preference + recent
        conversation summaries, renders them as a structured
        appendix to the base system prompt. Empty data set =
        original base_prompt returned unchanged.

  extract_facts_from_turn(user_id, recent_messages, llm_call)
      → asks the LLM to identify durable facts mentioned in the
        most recent turn ("my fiscal year starts in July",
        "we work in CET"). Persists each as source='extracted'
        via the existing UNIQUE(user_id, fact) constraint, so
        re-observing the same fact across turns is idempotent.

  summarise_conversation(conversation_id, recent_messages, llm_call)
      → produces a single-paragraph summary of the conversation,
        upserts into conversation_memory_summary so future
        conversations can reference it.

Real LLM calls land in production (the llm_call dependency is
console/app/services/llm_client.chat). Tests inject a stub —
see tests/test_v1443_llm_integration.py for the contract.
"""
from __future__ import annotations

import json
import logging
import re
from typing import Any, Awaitable, Callable, Iterable

from app.services import auth
from app.services.db_scope import scoped_db_for_user


logger = logging.getLogger(__name__)


# ── Public types ─────────────────────────────────────────────────────────


# A minimal callable shape that the real llm_client.chat or a test
# double can satisfy. The function takes (system, messages) and
# returns the assistant's plain-text reply.
LLMTextCall = Callable[[str, list[dict]], Awaitable[str]]


# ── 1. System-prompt builder ────────────────────────────────────────────


_MAX_FACTS         = 20
_MAX_SUMMARIES     =  3


async def build_system_prompt_with_memory(
    user_id: int, base_prompt: str, *, user_context: dict | None = None
) -> str:
    """Return ``base_prompt`` with a "Contexto del usuario" section
    appended when the user has facts / preferences / recent
    summaries on record. Identity transform when empty.

    The section sits at the end of the prompt — the LLM sees the
    immutable rules first, the personalisation second. Splitting it
    out keeps the immutable rules cache-friendly across users.
    """
    facts       = await _fetch_facts(user_id, limit=_MAX_FACTS, user_context=user_context)
    preferences = await _fetch_preferences(user_id, user_context=user_context)
    summaries   = await _fetch_recent_summaries(user_id, limit=_MAX_SUMMARIES, user_context=user_context)

    if not facts and not preferences and not summaries:
        return base_prompt

    block: list[str] = ["\n\n## Contexto del usuario\n"]

    if facts:
        block.append("\n### Hechos sobre el usuario y su empresa:\n")
        for f in facts:
            # The fact is operator-controlled text — render verbatim
            # but cap line length so a 10 KB pasted artifact can't
            # blow up the prompt token count.
            block.append(f"- {f[:500]}\n")

    if preferences:
        block.append("\n### Preferencias:\n")
        for k, v in preferences.items():
            block.append(f"- {k}: {v}\n")

    if summaries:
        block.append("\n### Resumen de conversaciones recientes:\n")
        for s in summaries:
            block.append(f"- {s[:800]}\n")

    return base_prompt + "".join(block)


async def _fetch_facts(
    user_id: int, *, limit: int, user_context: dict | None = None
) -> list[str]:
    if user_context is None:
        return []
    pool = await auth.pool()
    async with scoped_db_for_user(pool, user_context) as (conn, tenant_id, workspace_id):
        rows = await conn.fetch(
            """
            SELECT fact
              FROM user_facts
             WHERE user_id = $1
               AND scope_status = 'scoped'
               AND workspace_id = $2::uuid
               AND ($3::uuid IS NULL OR tenant_id = $3::uuid)
             ORDER BY created_at DESC
             LIMIT $4
            """,
            user_id, workspace_id, tenant_id, limit,
        )
    return [r["fact"] for r in rows]


async def _fetch_preferences(user_id: int, *, user_context: dict | None = None) -> dict[str, str]:
    if user_context is None:
        return {}
    pool = await auth.pool()
    async with scoped_db_for_user(pool, user_context) as (conn, tenant_id, workspace_id):
        rows = await conn.fetch(
            """
            SELECT pref_key, pref_value
              FROM user_preferences
             WHERE user_id = $1
               AND scope_status = 'scoped'
               AND workspace_id = $2::uuid
               AND ($3::uuid IS NULL OR tenant_id = $3::uuid)
            """,
            user_id, workspace_id, tenant_id,
        )
    return {r["pref_key"]: r["pref_value"] for r in rows}


async def _fetch_recent_summaries(
    user_id: int, *, limit: int, user_context: dict | None = None
) -> list[str]:
    """Per-conversation summaries for the user's most recent N
    conversations. Joins conversations on user_id since the summary
    table itself isn't user-scoped (one row per conversation)."""
    if user_context is None:
        return []
    pool = await auth.pool()
    has_conv_summary = await pool.fetchval(
        "SELECT to_regclass('public.conversation_memory_summary')"
    )
    if not has_conv_summary:
        return []
    async with scoped_db_for_user(pool, user_context) as (conn, tenant_id, workspace_id):
        rows = await conn.fetch(
            """
            SELECT cms.summary
              FROM conversation_memory_summary cms
              JOIN conversations c ON c.id = cms.conversation_id
             WHERE c.user_id = $1
               AND cms.scope_status = 'scoped'
               AND cms.workspace_id = $2::uuid
               AND ($3::uuid IS NULL OR cms.tenant_id = $3::uuid)
             ORDER BY cms.updated_at DESC
             LIMIT $4
            """,
            user_id, workspace_id, tenant_id, limit,
        )
    return [r["summary"] for r in rows]


# ── 2. Fact extraction ──────────────────────────────────────────────────


_FACT_EXTRACTION_PROMPT = (
    "Eres un asistente que detecta hechos durables sobre el usuario y "
    "su empresa. Revisa la conversación reciente y devuelve únicamente "
    "los hechos que VALEN LA PENA RECORDAR para futuras conversaciones.\n\n"
    "REGLA INVIOLABLE: NO infieras hechos que el usuario no haya afirmado "
    "explícitamente. Si no lo dijo, no lo extraigas — preferimos perder "
    "un fact que fabricar uno. Si un fact requiere extrapolación, NO lo "
    "incluyas.\n\n"
    "NO incluyas: información sobre la consulta puntual, datos públicos "
    "(nombres de cartuchos, tools), inferencias sobre el rol/sector/"
    "comportamiento del usuario, o frases sin contenido factual.\n\n"
    "SÍ incluye: ciclos fiscales, equipos, zonas horarias, nombres de "
    "personas clave, sistemas en uso, preferencias de formato, "
    "convenciones internas — SIEMPRE que el usuario las haya mencionado "
    "explícitamente en la conversación.\n\n"
    "Devuelve UNICAMENTE un JSON array. Si no hay nada que recordar, "
    "devuelve []. Cada elemento tiene la forma:\n"
    '  {"fact": "texto del hecho en una sola frase", '
    '"category": "empresa|equipo|fecha|preferencia|sistema|otro"}\n\n'
    "NO incluyas explicación adicional fuera del array."
)


async def extract_facts_from_turn(
    user_id: int,
    conversation_history: list[dict],
    llm_call: LLMTextCall,
    *,
    max_new_facts: int = 5,
    user_context: dict | None = None,
) -> list[dict]:
    """Run an LLM extraction pass over the recent turn and persist
    every well-formed fact into user_facts with source='extracted'.

    Returns the list of facts that were actually inserted (the UNIQUE
    constraint silently drops duplicates).

    The extraction call is cheap (small max_tokens, single round-trip)
    so we run it on every turn rather than gating on heuristics —
    keeping the trigger simple makes the behaviour easier to reason
    about. The LLM is asked for a strict JSON array; we tolerate
    common malformations (trailing commas, prose preamble) but bail
    on anything we can't parse.
    """
    if not conversation_history:
        return []

    try:
        raw = await llm_call(_FACT_EXTRACTION_PROMPT, conversation_history)
    except Exception:                             # pragma: no cover
        logger.exception("fact extraction LLM call failed")
        return []

    parsed = _parse_facts_json(raw)
    if not parsed:
        return []

    # v1.44.3 R1 LLM-A2 follow-up: validate BEFORE slicing. The
    # previous slice-then-validate order silently under-counted when
    # the LLM returned a mix of valid + invalid candidates: a list
    # like [empty, empty, valid, valid, valid] with max_new_facts=3
    # would yield zero inserts because the slice took the three
    # leading empties first. Validate first, then slice.
    valid_texts: list[str] = []
    for candidate in parsed:
        fact_text = (candidate.get("fact") or "").strip()
        if not fact_text or len(fact_text) > 500:
            continue
        valid_texts.append(fact_text)
        if len(valid_texts) >= max_new_facts:
            break

    if not valid_texts:
        return []
    if user_context is None:
        return []

    pool = await auth.pool()
    inserted: list[dict] = []
    async with scoped_db_for_user(pool, user_context) as (conn, tenant_id, workspace_id):
        for fact_text in valid_texts:
            row = await conn.fetchrow(
                """
                INSERT INTO user_facts
                    (user_id, tenant_id, workspace_id, scope_status, fact, source, confidence)
                VALUES ($1, $2::uuid, $3::uuid, 'scoped', $4, 'extracted', 0.7)
                ON CONFLICT (user_id, workspace_id, fact) WHERE workspace_id IS NOT NULL
                DO NOTHING
                RETURNING id, fact
                """,
                user_id, tenant_id, workspace_id, fact_text,
            )
            if row is not None:
                inserted.append({"id": int(row["id"]), "fact": row["fact"]})
    return inserted


def _parse_facts_json(raw: str) -> list[dict]:
    """Parse the LLM's response into a list of {fact, category} dicts.

    The LLM is asked for strict JSON but real models occasionally
    wrap the array in prose or trailing-comma it. We:
      1. Try a direct json.loads.
      2. If that fails, extract the first bracketed array via regex
         and try again.
      3. Bail if neither works — fact extraction is best-effort, a
         failed parse is logged and skipped.
    """
    s = raw.strip()
    try:
        parsed = json.loads(s)
    except json.JSONDecodeError:
        match = re.search(r"\[.*\]", s, re.DOTALL)
        if not match:
            logger.debug("fact extraction: no JSON array found")
            return []
        try:
            parsed = json.loads(match.group(0))
        except json.JSONDecodeError:
            logger.debug("fact extraction: array regex matched but JSON invalid")
            return []
    if not isinstance(parsed, list):
        return []
    return [item for item in parsed if isinstance(item, dict)]


# ── 3. Conversation summary ─────────────────────────────────────────────


_SUMMARY_PROMPT = (
    "Resume la siguiente conversación en UN solo párrafo de máximo 4 "
    "frases. Captura: tema central, decisiones del usuario, datos "
    "clave consultados (cartuchos / entidades), acciones realizadas. "
    "No incluyas saludos ni texto genérico. Si la conversación no "
    "tuvo contenido sustantivo, responde literalmente 'sin contenido'."
)


async def summarise_conversation(
    conversation_id: str,
    recent_messages: list[dict],
    llm_call: LLMTextCall,
    *,
    user_context: dict | None = None,
) -> str | None:
    """Generate (or refresh) the rolling summary for a conversation.

    Stored in ``conversation_memory_summary``. Returns the summary
    text the LLM produced, or None if the summary was "sin contenido"
    (we don't store empty summaries — they'd pollute the memory
    block on future turns).
    """
    if not recent_messages:
        return None
    try:
        raw = await llm_call(_SUMMARY_PROMPT, recent_messages)
    except Exception:                             # pragma: no cover
        logger.exception("conversation summary LLM call failed")
        return None

    summary = (raw or "").strip()
    if not summary or summary.lower() == "sin contenido":
        return None
    if len(summary) > 1000:
        summary = summary[:1000].rstrip() + "…"

    if user_context is None:
        return None

    pool = await auth.pool()
    async with scoped_db_for_user(pool, user_context) as (conn, tenant_id, workspace_id):
        await conn.execute(
            """
            INSERT INTO conversation_memory_summary
                (conversation_id, tenant_id, workspace_id, scope_status,
                 summary, token_count, updated_at)
            VALUES ($1, $2::uuid, $3::uuid, 'scoped', $4, $5, NOW())
            ON CONFLICT (conversation_id) DO UPDATE
              SET tenant_id   = EXCLUDED.tenant_id,
                  workspace_id = EXCLUDED.workspace_id,
                  scope_status = 'scoped',
                  summary     = EXCLUDED.summary,
                  token_count = EXCLUDED.token_count,
                  updated_at  = NOW()
            """,
            conversation_id, tenant_id, workspace_id, summary, _approx_tokens(summary),
        )
    return summary


def _approx_tokens(text: str) -> int:
    """Cheap token estimate (4 chars/token rough mean for European
    languages). Used for token_count column; not load-bearing —
    real tokenisation happens at the LLM provider."""
    return max(1, len(text) // 4)


# ── 4. Helpers exposed for tests / future callers ───────────────────────


def render_memory_block(
    facts: Iterable[str],
    preferences: dict[str, str],
    summaries: Iterable[str],
) -> str:
    """Pure-function version of the prompt-builder body — useful for
    unit tests that don't want to mock the DB pool."""
    fact_list      = list(facts)
    summary_list   = list(summaries)
    if not fact_list and not preferences and not summary_list:
        return ""
    parts: list[Any] = ["\n\n## Contexto del usuario\n"]
    if fact_list:
        parts.append("\n### Hechos sobre el usuario y su empresa:\n")
        for f in fact_list:
            parts.append(f"- {f[:500]}\n")
    if preferences:
        parts.append("\n### Preferencias:\n")
        for k, v in preferences.items():
            parts.append(f"- {k}: {v}\n")
    if summary_list:
        parts.append("\n### Resumen de conversaciones recientes:\n")
        for s in summary_list:
            parts.append(f"- {s[:800]}\n")
    return "".join(parts)
