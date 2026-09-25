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

EXPLANATION_MAX_TOKENS = 48
RECOMMENDATION_MAX_TOKENS = 32
LIMITATION_MAX_TOKENS = 20
MAX_PROSE_CHARS = 600

LLM_TIMEOUT_SECONDS = 8.0

_WORD = re.compile(r"[^\W_]+")
_DIGIT = re.compile(r"\d")
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
_STRUCTURAL_SEPARATOR = re.compile(r"[\s:/|,;>]+")
_QUOTE_CHARACTERS = "\"'«»“”‘’`"

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

_LIMITATION_KEYS: tuple[str, ...] = ("blockers", "notes", "limitations")
_LIMITATION_VALUE_KEYS: tuple[str, ...] = ("reason", "code", "error", "note")


def _mapping(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _fold(text: str) -> str:
    return unicodedata.normalize("NFKC", text).casefold()


def _word_tokens(text: str) -> list[str]:
    return _WORD.findall(text)


def _within_budget(text: str, limit: int) -> bool:
    return len(_word_tokens(text)) <= limit


def _numeric(value: Any) -> float | None:
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


def basis_code_for(
    alert_payload: Mapping[str, Any],
    engine_result: Mapping[str, Any] | None = None,
) -> str:
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


def _limitation_markers(source: Mapping[str, Any]) -> list[str]:
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
            markers.append(marker)
    return markers


def _limitations(
    alert_payload: Mapping[str, Any],
    engine_result: Mapping[str, Any] | None,
) -> tuple[str, ...]:
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
                logger.warning(
                    "narrative_service: limitation phrase over budget, "
                    "falling back to the generic note"
                )
                phrase = GENERIC_LIMITATION_NOTE
            if phrase not in phrases:
                phrases.append(phrase)
    return tuple(phrases)


def _cap(label: str, ceiling: str) -> str:
    return min(label, ceiling, key=CONFIDENCE_ORDER.index)


def _confidence_label(
    *,
    basis_code: str,
    limitations: tuple[str, ...],
    proxy_note: bool,
    status: str,
) -> str:
    label = CONFIDENCE_HIGH
    if basis_code != BASIS_WITH_SIMULATION:
        label = _cap(label, CONFIDENCE_MEDIUM)
    if limitations:
        label = _cap(label, CONFIDENCE_MEDIUM)
    if proxy_note or status != "ready":
        label = _cap(label, CONFIDENCE_LOW)
    return label


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


@dataclass(frozen=True)
class _NarrativeCore:

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
    candidate = _clean_text(text).strip(_QUOTE_CHARACTERS).strip()
    if not candidate:
        return None, "empty_prose"
    if len(candidate) > MAX_PROSE_CHARS:
        return None, "over_budget"
    if _DIGIT.search(candidate):
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
    if tool_policy._PROMPT_INJECTION_RE.search(candidate):
        return None, "injection_language"
    return candidate, "ok"


async def _framing_sentence(
    core: _NarrativeCore,
    llm_caller: Callable[[str], Any] | None,
) -> tuple[str | None, str | None]:
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


async def build_narrative(
    alert_payload: Mapping[str, Any],
    *,
    engine_result: Mapping[str, Any] | None = None,
    llm_caller: Callable[[str], Any] | None = None,
) -> dict[str, Any]:
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
