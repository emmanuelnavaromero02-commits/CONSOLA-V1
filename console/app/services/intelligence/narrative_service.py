"""Spanish business narrative for a Control Room alert raised by a monitor.

Mission 5. A scheduled monitor raises an advisory alert; this module turns that
alert into something a business reader understands, in Spanish, without giving a
language model the chance to state a fact that is not true.

Why the LLM writes exactly ONE sentence
---------------------------------------
Everything in a narrative that a reader could act on — the recommendation, the
confidence, the basis of the analysis, the limitations, the figures — is either
a fixed string from :mod:`app.services.intelligence.narrative_copy` or a number
copied out of the alert. None of it is authored by a model, because every one of
those fields is a claim that can be wrong in a way a reader cannot detect.

What is left for the model is framing: one sentence that says, in business
language, what kind of thing happened. That sentence is worth having — a bare
dictionary lookup reads like a machine — and it is the only part of the output
where being slightly off is harmless, because it asserts nothing the structured
fields do not already assert.

The one sentence is still not trusted. It is written from a skeleton that
contains only already-public strings (the domain label, the severity word, a
magnitude bucket, the limitation phrases, the basis phrase), assembled by an
explicit allowlist: the payload itself is never handed to the model, so a
dataset name, a relation, an ``entity_key``, an ``engine_run_id``, an agent slug
or a UUID cannot be echoed back because it was never sent. What comes back is
then validated structurally — forbidden action verbs, simulation vocabulary,
digits, the publication budget, the technical strings present in THIS payload,
and prompt-injection language — and rejected to a template on any hit.

Rejection is cheap on purpose. A template narrative is complete and publishable;
there is no "narrative pending" state, because the alert exists the moment the
monitor raises it and a reader can open it immediately.

Two structural facts, not prompt instructions
---------------------------------------------
* ``basis_code`` is computed. It reads ``with_simulation`` only when the engine
  really is Monte Carlo and p10/p50/p90 are all present and numeric. Finance,
  Operations and Risk ship every engine disabled (see
  ``domain_monitor_support.MONTE_CARLO_DISABLED_REASON``), so every narrative
  today reads ``aggregates_only``. That is the honest answer and it is derived,
  never asked for.
* ``confidence_label`` is computed. The alert's own ``confidence`` field is the
  literal ``0.8`` on every alert of every domain, because
  ``agent_runtime._monitor_alert_args`` writes
  ``float(contract.get("confidence") or 0.8)`` and no contract sets the key. It
  is a constant, not a measurement, so it is not published as one.

This module NEVER raises. The alert already exists; a failure to narrate it must
degrade to a template with a reason code from :data:`NARRATIVE_REASONS`, the same
closed-reason discipline as ``agent_memory.ERROR_REASONS``: no raw exception text
ever reaches a stored field, because the narrator writes through SQL and the
narrative is read by other agents.
"""

from __future__ import annotations

import asyncio
import hashlib
import inspect
import json
import logging
import re
import unicodedata
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from math import isfinite
from typing import Any, Callable

from app.services import tool_policy
from app.services.intelligence.narrative_copy import (
    BASIS_AGGREGATES_ONLY,
    BASIS_WITH_SIMULATION,
    CONFIDENCE_HIGH,
    CONFIDENCE_LOW,
    CONFIDENCE_MEDIUM,
    CONFIDENCE_ORDER,
    DEFAULT_DOMAIN_LABEL,
    DEFAULT_RECOMMENDATION,
    DEFAULT_SEVERITY,
    DOMAIN_LABELS,
    FORBIDDEN_PROSE_PATTERNS,
    GENERIC_LIMITATION_NOTE,
    MAGNITUDE_PHRASES,
    MAGNITUDE_SEVERAL,
    MAGNITUDE_SINGLE,
    MONITOR_ALERT_TYPE,
    SEVERITIES,
    SEVERITY_WORDS,
    SIMULATION_VOCABULARY,
    basis_note,
    confidence_reason,
    limitation_phrase,
    recommendation_for,
    template_explanation,
)

logger = logging.getLogger(__name__)

NARRATIVE_VERSION = 1
NARRATOR_VERSION = "narrative.v1"

STATUS_READY = "ready"
STATUS_TEMPLATE = "template"

# Publication budgets, in WORD TOKENS rather than words, because that is the
# unit the public projection counts: ``business_diagnostic_grammar`` tokenises
# with ``re.compile(r"[^\W_]+")`` and refuses a string over
# ``_MAX_GRAMMAR_TOKENS = 64``. Under that rule "cost_center_budget" is THREE
# tokens and "2026-08-01" is three, so a sentence that looks short in words can
# still be redacted whole.
#
# The caps sit well under 64 so that a narrative field still survives when it is
# concatenated with a label by a downstream surface.
EXPLANATION_MAX_TOKENS = 48
RECOMMENDATION_MAX_TOKENS = 32
LIMITATION_MAX_TOKENS = 20
# A character bound applied before any regex runs, so pathological model output
# is refused without scanning it. 48 tokens of Spanish is ~350 characters.
MAX_PROSE_CHARS = 600

LLM_TIMEOUT_SECONDS = 8.0

_WORD = re.compile(r"[^\W_]+")
_DIGIT = re.compile(r"\d")
# Mission 5 red-team additions. FORBIDDEN_PROSE_PATTERNS catches first-person
# and infinitive action verbs; these catch what slipped past it: impersonal or
# passive completion ("ya se aplicaron", "se aprobo el pago"), unsupported
# accusations no aggregate can back, figures spelled out as words, technical
# vocabulary, and instructions addressed to a downstream agent.
_COMPLETED_ACTION = re.compile(
    r"\bya\s+se\b"
    r"|\bse\s+(?:aplic|aprob|ejecut|corrig|realiz|implement|envi|pag|confirm"
    r"|resolv|resolvi|cerr|ajust|autoriz|cancel|elimin|modific|despid|sancion)\w*"
    r"|\b(?:aprobad|aplicad|autorizad|pagad|confirmad|cancelad|eliminad"
    r"|modificad|corregid|implementad|enviad|despedid|sancionad)[oa]s?\b"
    r"|\bresuelt[oa]s?\b"
    r"|\b(?:fraude|delito|ilegal|robo|malversaci[oó]n|corrupci[oó]n)\w*",
    re.IGNORECASE,
)
_NUMBER_WORDS = re.compile(
    r"\b(?:cero|uno|dos|tres|cuatro|cinco|seis|siete|ocho|nueve|diez|once|doce"
    r"|trece|catorce|quince|diecis\w+|veinte\w*|veinti\w+|treinta|cuarenta"
    r"|cincuenta|sesenta|setenta|ochenta|noventa|cien|ciento|cientos"
    r"|doscient\w*|trescient\w*|cuatrocient\w*|quinient\w*|seiscient\w*"
    r"|setecient\w*|ochocient\w*|novecient\w*|mil|miles|mill[oó]n\w*|millones"
    r"|docena\w*|decena\w*|centena\w*|mitad|doble|triple|porcentaje\w*)\b"
    r"|\bpor\s+ciento\b",
    re.IGNORECASE,
)
_TECHNICAL_VOCABULARY = re.compile(
    r"\b(?:tabla\w*|columna\w*|dataset\w*|gold|silver|bronze|sql|query|queries"
    r"|esquema\w*|uuid|json|endpoint\w*|api|parquet|bucket\w*|lakehouse)\b",
    re.IGNORECASE,
)
_INSTRUCTION_LANGUAGE = re.compile(
    r"\b(?:asistente|instrucci[oó]n\w*|restricci[oó]n\w*|omit[ae]\w*|ignor[ae]\w*"
    r"|olvid[ae]\w*|revel[ae]\w*|prompt\w*|contrase[nñ]a\w*)\b",
    re.IGNORECASE,
)
_WHITESPACE = re.compile(r"\s+")
# Structural separators only. A composite key is split on these but NOT on "_"
# or "-": splitting "WB-FINANZAS" would put the public domain word "Finanzas" on
# the denylist and reject every Finance narrative ever written.
_STRUCTURAL_SEPARATOR = re.compile(r"[\s:/|,;>]+")
_QUOTE_CHARACTERS = "\"'«»“”‘’`"

# The only reasons that are ever stored. Mirrors agent_memory.ERROR_REASONS: an
# interpolated provider error reaches the next reader either as driver text or,
# once it is long enough, as "[REDACTED]", and neither tells an agent anything.
NARRATIVE_REASONS: dict[str, str] = {
    "no_llm": "sin narrador disponible en este entorno",
    "llm_timeout": "el narrador tardo demasiado en responder",
    "llm_error": "el narrador no pudo responder",
    "empty_prose": "el narrador devolvio texto vacio",
    "forbidden_prose": "la redaccion sugeria una accion que el sistema no toma",
    "simulation_claim": "la redaccion implicaba una simulacion que no se corrio",
    "over_budget": "la redaccion excedia el presupuesto de texto publicable",
    "contains_digits": "la redaccion incluia cifras: las cifras van en figures",
    "technical_leak": "la redaccion repetia identificadores tecnicos de la alerta",
    "injection_language": "la redaccion contenia lenguaje de instrucciones",
    "invalid_payload": "la alerta no traia los campos minimos para narrar",
}

# Keys whose STRING values are technical identifiers: dataset names, entity
# keys, slugs, run ids. Collected from this payload only, and used as a
# per-call denylist over the model's sentence.
_DENYLIST_KEYS: frozenset[str] = frozenset(
    {
        "agent_slug",
        "alert_type",
        "analysis_type",
        "calibration_group",
        "cartridge_id",
        "dataset",
        "dedup_key",
        "engine",
        "engine_run_id",
        "entity_key",
        "entity_label",
        "input_dataset",
        "kind",
        "model_version",
        "orchestration_id",
        "simulation_id",
        "slug",
        "source_dataset",
        "source_id",
        "state_id",
        "wisdom_bit_id",
    }
)
_DENYLIST_MAX_DEPTH = 6
_DENYLIST_MAX_TERMS = 200

# Keys whose values may carry a limitation. ``blockers`` is built by
# ``agent_runtime._monitor_with_engine_results``; ``notes`` is the public note
# list from ``control_room.domain_kpis``.
_LIMITATION_KEYS: tuple[str, ...] = ("blockers", "notes", "limitations")
_LIMITATION_VALUE_KEYS: tuple[str, ...] = ("reason", "code", "error", "note")


# ── Small helpers ────────────────────────────────────────────────────────────


def _mapping(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _fold(text: str) -> str:
    """NFKC-normalise and casefold, the comparison form used for every check."""
    return unicodedata.normalize("NFKC", text).casefold()


def _word_tokens(text: str) -> list[str]:
    """Word tokens exactly as the public projection counts them."""
    return _WORD.findall(text)


def _within_budget(text: str, limit: int) -> bool:
    return len(_word_tokens(text)) <= limit


def _numeric(value: Any) -> float | None:
    """Finite float, or None. Booleans are not numbers here."""
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        number = float(value)
        return number if isfinite(number) else None
    if isinstance(value, str):
        try:
            number = float(value.strip())
        except ValueError:
            return None
        return number if isfinite(number) else None
    return None


def _count(value: Any) -> int:
    number = _numeric(value)
    if number is None or number < 0:
        return 0
    return int(number)


def _clean_text(value: Any) -> str:
    return _WHITESPACE.sub(" ", str(value or "")).strip()


# ── Basis of the analysis ────────────────────────────────────────────────────


def basis_code_for(
    alert_payload: Mapping[str, Any],
    engine_result: Mapping[str, Any] | None = None,
) -> str:
    """``with_simulation`` only when a Monte Carlo run really produced quantiles.

    Both shapes the chain produces are accepted: the ``analysis_evidence`` dict
    built by ``control_room__raise_analysis_alert`` (quantiles nested under
    ``quantiles``) and a raw engine result carrying ``p10``/``p50``/``p90`` at
    the top level.

    Anything else is ``aggregates_only``. A missing quantile, a null one, a
    non-numeric one, or an engine that is not Monte Carlo all mean the same
    thing: no distribution was computed, so the narrative must not be allowed to
    speak as if one had been.
    """
    engine_block = _mapping(engine_result)
    engine = _clean_text(
        engine_block.get("engine") or alert_payload.get("engine")
    ).casefold()
    if engine != "monte_carlo":
        return BASIS_AGGREGATES_ONLY
    quantiles = _mapping(engine_block.get("quantiles"))
    for key in ("p10", "p50", "p90"):
        raw = quantiles.get(key, engine_block.get(key, alert_payload.get(key)))
        if _numeric(raw) is None:
            return BASIS_AGGREGATES_ONLY
    return BASIS_WITH_SIMULATION


# ── Limitations ──────────────────────────────────────────────────────────────


def _limitation_markers(source: Mapping[str, Any]) -> list[str]:
    """Internal limitation markers carried by one mapping, in order."""
    markers: list[str] = []
    for key in _LIMITATION_KEYS:
        items = source.get(key)
        if not isinstance(items, (list, tuple)):
            continue
        for item in items:
            if isinstance(item, Mapping):
                parts = [
                    _clean_text(item.get(field))
                    for field in _LIMITATION_VALUE_KEYS
                    if item.get(field) not in (None, "")
                ]
                marker = " ".join(part for part in parts if part)
            else:
                marker = _clean_text(item)
            # An empty marker still counts: a blocker with no readable reason is
            # a limitation nobody classified, which is what _GENERIC_NOTE is for.
            markers.append(marker)
    return markers


def _limitations(
    alert_payload: Mapping[str, Any],
    engine_result: Mapping[str, Any] | None,
) -> tuple[str, ...]:
    """Public limitation phrases for this alert, deduplicated, order preserved.

    No length cap is applied and none is needed: every marker resolves through
    :func:`narrative_copy.limitation_phrase` into one of a closed set of
    phrases, so the deduplicated result is bounded by the vocabulary itself. A
    cap would mean dropping a limitation, and a limitation is never dropped.
    """
    sources: list[Mapping[str, Any]] = [alert_payload]
    evidence = _mapping(alert_payload.get("evidence"))
    engine_results = evidence.get("engine_results")
    if isinstance(engine_results, (list, tuple)):
        sources.extend(item for item in engine_results if isinstance(item, Mapping))
    if isinstance(engine_result, Mapping):
        sources.append(engine_result)

    phrases: list[str] = []
    for source in sources:
        for marker in _limitation_markers(source):
            phrase = limitation_phrase(marker)
            if not _within_budget(phrase, LIMITATION_MAX_TOKENS):
                # Cannot happen with the shipped vocabulary; if a phrase is ever
                # edited past the budget the limitation is still reported, just
                # unspecifically. Reporting nothing is the one option ruled out.
                logger.warning(
                    "narrative_service: limitation phrase over budget, "
                    "falling back to the generic note"
                )
                phrase = GENERIC_LIMITATION_NOTE
            if phrase not in phrases:
                phrases.append(phrase)
    return tuple(phrases)


# ── Confidence ───────────────────────────────────────────────────────────────


def _cap(label: str, ceiling: str) -> str:
    """The lower of two confidence levels."""
    return min(label, ceiling, key=CONFIDENCE_ORDER.index)


def _confidence_label(
    *,
    basis_code: str,
    limitations: tuple[str, ...],
    proxy_note: bool,
    status: str,
) -> str:
    """Deterministic confidence. No model input, no alert ``confidence`` field.

    The ceilings compose, weakest wins:

    * any limitation caps at ``media``;
    * a proxy note, or a domain status that is not ``ready``, caps at ``baja``;
    * ``aggregates_only`` can never read ``alta``, because a narrative with no
      distribution behind it has no basis for the strongest claim.

    In practice every alert that reaches here is ``aggregates_only``, and
    ``monitor_should_alert`` lets a ``degraded`` domain alert while suppressing
    ``unavailable``/``partial``, so ``baja`` is the common answer. That is the
    honest reading of what the evidence supports.
    """
    label = CONFIDENCE_HIGH
    if basis_code != BASIS_WITH_SIMULATION:
        label = _cap(label, CONFIDENCE_MEDIUM)
    if limitations:
        label = _cap(label, CONFIDENCE_MEDIUM)
    if proxy_note or status != "ready":
        label = _cap(label, CONFIDENCE_LOW)
    return label


# ── Per-call technical denylist ──────────────────────────────────────────────


def _collect_terms(value: Any, terms: set[str], *, depth: int = 0) -> None:
    if depth > _DENYLIST_MAX_DEPTH or len(terms) >= _DENYLIST_MAX_TERMS:
        return
    if isinstance(value, Mapping):
        for key, item in value.items():
            if str(key) in _DENYLIST_KEYS and isinstance(item, str):
                terms.add(item)
            _collect_terms(item, terms, depth=depth + 1)
        return
    if isinstance(value, (list, tuple)):
        for item in value:
            _collect_terms(item, terms, depth=depth + 1)


def _denylist_sequences(
    alert_payload: Mapping[str, Any],
    engine_result: Mapping[str, Any] | None,
) -> frozenset[tuple[str, ...]]:
    """Token sequences from THIS payload that the sentence may not contain.

    Each technical string becomes a tuple of word tokens, compared
    case-insensitively after NFKC normalisation. Token sequences rather than raw
    substrings, because a substring test is both too loose and too tight:
    ``"agent"`` as a substring would reject the ordinary Spanish word *agente*,
    while ``"sap_s4hana"`` as a substring would never match the spaced form a
    model would actually write.

    A one-token sequence shorter than four characters is skipped as noise, and a
    sequence that is entirely public vocabulary (a domain label, a severity
    word) is skipped too: those words are in the skeleton the model was given,
    so forbidding them would reject a sentence for using its own input.
    """
    raw: set[str] = set()
    _collect_terms(alert_payload, raw)
    if isinstance(engine_result, Mapping):
        _collect_terms(engine_result, raw)

    public_tokens: set[str] = set()
    for label in DOMAIN_LABELS:
        public_tokens.update(_word_tokens(_fold(label)))
    for word in SEVERITY_WORDS.values():
        public_tokens.update(_word_tokens(_fold(word)))

    sequences: set[tuple[str, ...]] = set()
    for term in raw:
        folded = _fold(term)
        for part in [folded, *_STRUCTURAL_SEPARATOR.split(folded)]:
            tokens = tuple(_word_tokens(part))
            if not tokens:
                continue
            if len(tokens) == 1 and len(tokens[0]) < 4:
                continue
            if all(token in public_tokens for token in tokens):
                continue
            sequences.add(tokens)
    return frozenset(sequences)


def _matches_denylist(text: str, denylist: frozenset[tuple[str, ...]]) -> bool:
    tokens = _word_tokens(_fold(text))
    if not tokens:
        return False
    for sequence in denylist:
        size = len(sequence)
        if size > len(tokens):
            continue
        for start in range(len(tokens) - size + 1):
            if tuple(tokens[start : start + size]) == sequence:
                return True
    return False


# ── Deterministic core ───────────────────────────────────────────────────────


@dataclass(frozen=True)
class _NarrativeCore:
    """Everything the narrative asserts, computed before the LLM is involved."""

    domain: str
    public_domain: str | None
    severity: str
    status: str
    basis_code: str
    limitations: tuple[str, ...]
    recommendation: str
    confidence_label: str
    magnitude: str
    figures: dict[str, Any]
    source_fingerprint: str
    denylist: frozenset[tuple[str, ...]]


def source_fingerprint(severity: str, metrics: Mapping[str, Any]) -> str:
    """sha256 of a canonical JSON of ``(severity, metrics)``.

    This is how a later reader tells a fresh narrative from one that describes a
    previous occurrence. ``control_room__raise_alert`` deduplicates on the
    entity key and overwrites ``severity`` in place, leaving the narrative row
    from the earlier occurrence behind; recomputing this digest from the alert
    as it stands now and comparing it to the stored one detects exactly that.

    ``metrics`` is taken whole, including ``scheduled_fire_at``, so two runs of
    the same monitor differ even when their counts happen to match. That is the
    point: the question is "does this narrative describe THIS occurrence", not
    "do the numbers look similar".
    """
    canonical = json.dumps(
        {"severity": severity, "metrics": dict(metrics)},
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        default=str,
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _build_core(
    alert_payload: Mapping[str, Any],
    engine_result: Mapping[str, Any] | None,
) -> _NarrativeCore:
    payload = _mapping(alert_payload)
    metrics = _mapping(payload.get("metrics"))

    domain = _clean_text(payload.get("domain")) or DEFAULT_DOMAIN_LABEL
    # Only an allowlisted label is ever shown to the model. An unrecognised
    # domain is NOT coerced to the default the way the alert builder coerces a
    # missing one: filing Finance under "Recursos Humanos" in prose would be a
    # false statement, so the skeleton simply omits the domain instead.
    public_domain = domain if domain in DOMAIN_LABELS else None

    severity = _clean_text(payload.get("severity")).casefold()
    if severity not in SEVERITIES:
        severity = DEFAULT_SEVERITY
    alert_type = _clean_text(payload.get("alert_type")) or MONITOR_ALERT_TYPE
    status = _clean_text(metrics.get("status") or payload.get("status")).casefold()

    proxy_note = bool(
        _clean_text(payload.get("proxy_note"))
        or _clean_text(metrics.get("proxy_note"))
        or _clean_text(_mapping(engine_result).get("proxy_note"))
    )

    basis_code = basis_code_for(payload, engine_result)
    limitations = _limitations(payload, engine_result)

    recommendation = recommendation_for(domain, alert_type, severity)
    if not _within_budget(recommendation, RECOMMENDATION_MAX_TOKENS):
        # Same rule as the explanation: refused, never truncated. A half
        # recommendation is worse than a generic one.
        logger.warning(
            "narrative_service: recommendation over budget for domain=%s "
            "alert_type=%s severity=%s",
            domain,
            alert_type,
            severity,
        )
        recommendation = DEFAULT_RECOMMENDATION

    signals = _count(metrics.get("signal_count") or payload.get("signal_count"))
    blockers = metrics.get("blocker_count")
    if blockers is None and isinstance(payload.get("blockers"), (list, tuple)):
        blockers = len(payload["blockers"])
    figures: dict[str, Any] = {
        "senales": signals,
        "bloqueos": _count(blockers),
        "impacto_estimado": _numeric(payload.get("impact_estimate")),
    }

    return _NarrativeCore(
        domain=domain,
        public_domain=public_domain,
        severity=severity,
        status=status,
        basis_code=basis_code,
        limitations=limitations,
        recommendation=recommendation,
        confidence_label=_confidence_label(
            basis_code=basis_code,
            limitations=limitations,
            proxy_note=proxy_note,
            status=status,
        ),
        magnitude=MAGNITUDE_SEVERAL if signals > 1 else MAGNITUDE_SINGLE,
        figures=figures,
        source_fingerprint=source_fingerprint(severity, metrics),
        denylist=_denylist_sequences(payload, engine_result),
    )


def _fallback_core() -> _NarrativeCore:
    """Core for a payload that could not be read at all.

    Asserts nothing about the alert beyond the fact that it exists, and carries
    an empty fingerprint so a reader cannot mistake it for a description of a
    known occurrence.
    """
    return _NarrativeCore(
        domain=DEFAULT_DOMAIN_LABEL,
        public_domain=None,
        severity=DEFAULT_SEVERITY,
        status="",
        basis_code=BASIS_AGGREGATES_ONLY,
        limitations=(GENERIC_LIMITATION_NOTE,),
        recommendation=DEFAULT_RECOMMENDATION,
        confidence_label=CONFIDENCE_LOW,
        magnitude=MAGNITUDE_SINGLE,
        figures={"senales": 0, "bloqueos": 0, "impacto_estimado": None},
        source_fingerprint="",
        denylist=frozenset(),
    )


# ── The one sentence ─────────────────────────────────────────────────────────

# The prompt is the LAST line of defence, not the first: the skeleton below it
# is built by allowlist, and everything that comes back is validated
# structurally. These rules exist so a well-behaved model does not waste a call,
# not because following them is what makes the output safe.
_PROMPT_INSTRUCTIONS = (
    "Eres el narrador de negocio de Control Room. Escribe EXACTAMENTE UNA frase "
    "en espanol neutro que enmarque esta alerta para una persona de negocio.\n"
    "Reglas:\n"
    "- Una sola frase, breve.\n"
    "- Sin cifras, sin numeros y sin porcentajes: las cifras se publican aparte.\n"
    "- No afirmes que algo se hizo, se corrigio o se aplico: el sistema observa "
    "y recomienda, no actua.\n"
    "- No menciones simulaciones, percentiles, distribuciones ni "
    "probabilidades.\n"
    "- No inventes causas, nombres, sistemas, tablas ni identificadores.\n"
    "- No recomiendes acciones: la recomendacion se publica aparte.\n"
    "- Responde solo con la frase, sin comillas ni prefijos.\n"
    "Contexto publicable:\n"
)


def _skeleton(core: _NarrativeCore) -> dict[str, Any]:
    """The ONLY thing the model sees, built key by key.

    Every value is either a fixed string from ``narrative_copy`` or an
    allowlisted domain label. The payload is never passed through, so there is
    no path by which a dataset name, a relation, a column, an ``entity_key``, an
    ``engine_run_id``, a ``source_dataset``, an agent slug, a UUID or the raw
    title/message can reach the model.
    """
    skeleton: dict[str, Any] = {
        "severidad": SEVERITY_WORDS.get(core.severity, SEVERITY_WORDS[DEFAULT_SEVERITY]),
        "magnitud": MAGNITUDE_PHRASES[core.magnitude],
        "limitaciones": list(core.limitations),
        "base": basis_note(core.basis_code),
    }
    if core.public_domain:
        skeleton["dominio"] = core.public_domain
    return skeleton


def _prompt(core: _NarrativeCore) -> str:
    return _PROMPT_INSTRUCTIONS + json.dumps(
        _skeleton(core), ensure_ascii=False, sort_keys=True
    )


def _validate_prose(
    text: Any,
    *,
    basis_code: str,
    denylist: frozenset[tuple[str, ...]],
) -> tuple[str | None, str]:
    """Return the publishable sentence, or ``(None, reason)``.

    Follows ``agent_memory._normalise_summary``: over budget returns None and
    the caller falls back, with a warning log. Nothing is ever truncated — a
    half sentence misleads the next reader, and the next reader here may be
    another agent rather than a person.
    """
    candidate = _clean_text(text).strip(_QUOTE_CHARACTERS).strip()
    if not candidate:
        return None, "empty_prose"
    if len(candidate) > MAX_PROSE_CHARS:
        return None, "over_budget"
    if _DIGIT.search(candidate):
        # Digits belong in ``figures``, where a reader can see what they measure.
        # This is also what keeps the token count down: "2026-08-01" is three
        # tokens and "cost_center_budget" is three.
        return None, "contains_digits"
    if FORBIDDEN_PROSE_PATTERNS.search(candidate) or _COMPLETED_ACTION.search(
        candidate
    ):
        return None, "forbidden_prose"
    if _NUMBER_WORDS.search(candidate):
        return None, "contains_digits"
    if basis_code != BASIS_WITH_SIMULATION and SIMULATION_VOCABULARY.search(candidate):
        return None, "simulation_claim"
    if not _within_budget(candidate, EXPLANATION_MAX_TOKENS):
        return None, "over_budget"
    if _matches_denylist(candidate, denylist) or _TECHNICAL_VOCABULARY.search(
        candidate
    ):
        return None, "technical_leak"
    if _INSTRUCTION_LANGUAGE.search(candidate):
        return None, "injection_language"
    # tool_policy only runs on LLM-issued TOOL ARGS, and the narrator does not
    # call a tool: it writes through SQL. Its injection detector is therefore
    # applied here by hand, because the narrative is read by other agents and a
    # stored instruction would be read as one.
    if tool_policy._PROMPT_INJECTION_RE.search(candidate):
        return None, "injection_language"
    return candidate, "ok"


async def _framing_sentence(
    core: _NarrativeCore,
    llm_caller: Callable[[str], Any] | None,
) -> tuple[str | None, str | None]:
    """One validated sentence, or ``(None, reason)``. Never raises.

    ``llm_caller`` takes the prompt string and returns the sentence, either
    directly or as an awaitable; an awaitable is bounded by
    :data:`LLM_TIMEOUT_SECONDS`.
    """
    if llm_caller is None:
        return None, "no_llm"
    try:
        result = llm_caller(_prompt(core))
        if inspect.isawaitable(result):
            result = await asyncio.wait_for(result, timeout=LLM_TIMEOUT_SECONDS)
    except asyncio.TimeoutError:
        logger.warning("narrative_service: narrator timed out for domain=%s", core.domain)
        return None, "llm_timeout"
    except Exception as exc:
        # The provider's text stays in the log. What crosses is a code from
        # NARRATIVE_REASONS and its fixed Spanish phrase.
        logger.warning(
            "narrative_service: narrator failed for domain=%s: %s",
            core.domain,
            exc,
            exc_info=True,
        )
        return None, "llm_error"

    prose, reason = _validate_prose(
        result, basis_code=core.basis_code, denylist=core.denylist
    )
    if prose is None:
        logger.warning(
            "narrative_service: prose rejected for domain=%s: %s", core.domain, reason
        )
        return None, reason
    return prose, None


# ── Public entry point ───────────────────────────────────────────────────────


async def build_narrative(
    alert_payload: Mapping[str, Any],
    *,
    engine_result: Mapping[str, Any] | None = None,
    llm_caller: Callable[[str], Any] | None = None,
) -> dict[str, Any]:
    """Build the Spanish narrative for one monitor alert. Never raises.

    ``status`` is ``ready`` when the model's sentence survived validation and
    ``template`` otherwise; ``reason`` carries the code from
    :data:`NARRATIVE_REASONS` explaining the fallback, and is ``None`` when the
    status is ``ready``. Both forms are complete and publishable: there is no
    state in which a caller has to wait for a narrative, because the alert does
    not wait for one either.
    """
    if not isinstance(alert_payload, Mapping):
        logger.warning("narrative_service: alert payload is not a mapping")
        core = _fallback_core()
        prose, reason = None, "invalid_payload"
    else:
        try:
            core = _build_core(alert_payload, engine_result)
        except Exception as exc:
            logger.warning(
                "narrative_service: could not read the alert payload: %s",
                exc,
                exc_info=True,
            )
            core = _fallback_core()
            prose, reason = None, "invalid_payload"
        else:
            prose, reason = await _framing_sentence(core, llm_caller)
    return _assemble(core, prose, reason)


def reconcile_narrative(
    alert_payload: Mapping[str, Any],
    stored: Any,
    *,
    engine_result: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """The narrative to publish for an alert as it stands NOW. Never raises.

    Everything is recomputed from the alert. The only thing taken from
    ``stored`` is the model's framing sentence, and only when that narrative
    describes this very occurrence (same ``source_fingerprint``) and the
    sentence passes, again, the validation it passed when it was written. The
    stored copy lives in ``control_room_items.metadata``, which mcp-infra can
    also write, so none of its other fields are believed: not the
    recommendation, not the confidence, not the basis, not the limitations.
    """
    if not isinstance(alert_payload, Mapping):
        return _assemble(_fallback_core(), None, "invalid_payload")
    try:
        core = _build_core(alert_payload, engine_result)
    except Exception as exc:
        logger.warning(
            "narrative_service: could not read the alert payload: %s",
            exc,
            exc_info=True,
        )
        return _assemble(_fallback_core(), None, "invalid_payload")

    previous = _mapping(stored)
    current = bool(core.source_fingerprint) and (
        previous.get("source_fingerprint") == core.source_fingerprint
    )
    prose: str | None = None
    reason: str | None = "no_llm"
    if current and previous.get("status") == STATUS_READY:
        prose, why = _validate_prose(
            previous.get("explanation"),
            basis_code=core.basis_code,
            denylist=core.denylist,
        )
        reason = None if prose is not None else why
    elif current and previous.get("reason") in NARRATIVE_REASONS:
        reason = str(previous.get("reason"))
    return _assemble(core, prose, reason)


def _assemble(
    core: _NarrativeCore, prose: str | None, reason: str | None
) -> dict[str, Any]:
    if prose is not None:
        explanation = prose
        status = STATUS_READY
        reason = None
    else:
        explanation = template_explanation(core.domain)
        status = STATUS_TEMPLATE
        reason = reason if reason in NARRATIVE_REASONS else "llm_error"

    return {
        "version": NARRATIVE_VERSION,
        "status": status,
        "reason": reason,
        "explanation": explanation,
        "recommendation": core.recommendation,
        "confidence_label": core.confidence_label,
        "confidence_reason": confidence_reason(core.confidence_label),
        "basis_code": core.basis_code,
        "basis_note": basis_note(core.basis_code),
        "limitations": list(core.limitations),
        "figures": dict(core.figures),
        # Second precision on purpose: a microsecond ISO stamp is redacted as
        # technical copy by the public projection (see domain_kpis).
        "generated_at": datetime.now(UTC).replace(microsecond=0).isoformat(),
        "source_fingerprint": core.source_fingerprint,
        "narrator_version": NARRATOR_VERSION,
    }


__all__ = (
    "EXPLANATION_MAX_TOKENS",
    "LIMITATION_MAX_TOKENS",
    "LLM_TIMEOUT_SECONDS",
    "NARRATIVE_REASONS",
    "NARRATIVE_VERSION",
    "NARRATOR_VERSION",
    "RECOMMENDATION_MAX_TOKENS",
    "STATUS_READY",
    "STATUS_TEMPLATE",
    "basis_code_for",
    "build_narrative",
    "reconcile_narrative",
    "source_fingerprint",
)
