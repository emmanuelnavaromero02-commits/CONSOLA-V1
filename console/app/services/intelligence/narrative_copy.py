"""Closed Spanish vocabularies for the Control Room alert narrative.

Mission 5. When a scheduled monitor raises a real alert, Control Room publishes
a business narrative in Spanish. The LLM writes EXACTLY ONE framing sentence;
everything a model could get wrong — the recommendation, the confidence, the
basis of the analysis, the limitations — is assembled from the fixed strings in
this module.

Why the vocabularies are closed and why nothing here is an f-string
-------------------------------------------------------------------
Mission 2 established the rule the hard way. ``pnl_mensual.base_currency``
passes ``contains_public_technical_copy`` unchanged, which is why
``domain_kpis.PUBLIC_FILTER_VALUES`` has to map the whole phrase
``"unverified (pnl_mensual.base_currency is NULL)"`` to public copy by hand: the
public sanitizer does NOT catch a relation-qualified column embedded in prose.
So the sanitizer is not the defence. The defence is that a technical string is
never placed in prose in the first place.

Consequently no value in this module is built by interpolating data into text.
A value is either a fixed sentence or it is not published. Data travels in the
``figures`` block of the narrative, where it is a number, not a sentence.

Two of the regexes exist for reasons that are not aesthetic:

* ``FORBIDDEN_PROSE_PATTERNS`` also matches ``Aprobar``, ``Ejecutar`` and
  ``Vista previa`` as whole words. ``ExperiencePreviewFlow.dom.test.tsx`` runs
  ``/Aprobar|Ejecutar|Sí, ejecutar/`` over the WHOLE rendered container and
  ``ControlRoomExperiencePage.test.tsx`` forbids the same strings in the markup.
  A narrative sentence reading "conviene ejecutar una revision" would therefore
  break a front-end test that is not about narratives at all.
* ``SIMULATION_VOCABULARY`` exists because Finance, Operations and Risk ship
  every engine disabled (see
  ``domain_monitor_support.MONTE_CARLO_DISABLED_REASON``). Prose that mentions
  percentiles or distributions would describe a model that never ran.

Ordering rules follow ``control_room.domain_kpis``: ``LIMITATION_PHRASES`` is an
ordered tuple of ``(marker, phrase)`` like ``PUBLIC_NOTE_RULES``, first marker
contained in the text wins, and an unrecognised limitation falls back to
``_GENERIC_NOTE`` instead of disappearing. Surfacing "hay una limitacion no
clasificada" is correct; hiding it is not.
"""

from __future__ import annotations

import re

# ── Domains and alert types in play ──────────────────────────────────────────

# The four values ``agent_runtime._monitor_alert_args`` can put in ``domain``:
# the three Mission 4 domain monitors declare theirs explicitly, and the builder
# falls back to "Recursos Humanos" when a contract omits it.
FINANCE_DOMAIN_LABEL = "Finanzas"
OPERATIONS_DOMAIN_LABEL = "Operacion"
RISK_DOMAIN_LABEL = "Riesgo"
TALENT_DOMAIN_LABEL = "Recursos Humanos"
DOMAIN_LABELS: frozenset[str] = frozenset(
    {
        FINANCE_DOMAIN_LABEL,
        OPERATIONS_DOMAIN_LABEL,
        RISK_DOMAIN_LABEL,
        TALENT_DOMAIN_LABEL,
    }
)
DEFAULT_DOMAIN_LABEL = TALENT_DOMAIN_LABEL

# Every domain monitor contract leaves ``alert_type`` unset, so the builder's
# default is the only value that reaches a narrative today. Kept as a named
# constant so a future contract that sets its own is an explicit dictionary
# entry rather than a silent fallback.
MONITOR_ALERT_TYPE = "wisdombit_monitor"

# Mirrors the enum in control_room__raise_analysis_alert's input schema.
SEVERITIES: tuple[str, ...] = ("low", "medium", "high", "critical")
DEFAULT_SEVERITY = "medium"

# The severity WORD handed to the LLM. The raw code never crosses: "critical"
# is an English identifier in a Spanish sentence.
SEVERITY_WORDS: dict[str, str] = {
    "low": "baja",
    "medium": "media",
    "high": "alta",
    "critical": "critica",
}

# The only magnitude the LLM is told. A count would be a number in prose, and
# numbers belong in ``figures``.
MAGNITUDE_SINGLE = "single"
MAGNITUDE_SEVERAL = "several"
MAGNITUDE_PHRASES: dict[str, str] = {
    MAGNITUDE_SINGLE: "una señal",
    MAGNITUDE_SEVERAL: "varias señales",
}


# ── Recommendations ──────────────────────────────────────────────────────────

# Keyed by (domain, alert_type, severity). The LLM never authors a
# recommendation: a recommendation is the part of a narrative a reader acts on,
# so it is the part that must be traceable to a string a human wrote.
#
# Every value is recommendation-only phrasing — an infinitive the reader may
# choose to follow, matching ``DomainMonitorSpec.recommended_action`` — and none
# of them says the system did anything or will do anything on its own.
NARRATIVE_RECOMMENDATIONS: dict[tuple[str, str, str], str] = {
    (FINANCE_DOMAIN_LABEL, MONITOR_ALERT_TYPE, "low"): (
        "Revisar las senales de margen y costo del periodo cerrado en la "
        "proxima revision de Finanzas."
    ),
    (FINANCE_DOMAIN_LABEL, MONITOR_ALERT_TYPE, "medium"): (
        "Revisar los proyectos con margen negativo y el costo laboral estimado "
        "del mes cerrado antes de comprometer nueva capacidad."
    ),
    (FINANCE_DOMAIN_LABEL, MONITOR_ALERT_TYPE, "high"): (
        "Revisar con el Controller los proyectos con margen negativo del mes "
        "cerrado y confirmar la moneda base antes de cualquier compromiso."
    ),
    (FINANCE_DOMAIN_LABEL, MONITOR_ALERT_TYPE, "critical"): (
        "Llevar las senales de margen y costo a la siguiente revision de "
        "Finanzas y confirmar el cierre del mes antes de comprometer gasto."
    ),
    (OPERATIONS_DOMAIN_LABEL, MONITOR_ALERT_TYPE, "low"): (
        "Revisar los cartuchos con fallos recientes en la proxima ventana de "
        "mantenimiento."
    ),
    (OPERATIONS_DOMAIN_LABEL, MONITOR_ALERT_TYPE, "medium"): (
        "Revisar los cartuchos con fallos recientes y los que exceden el umbral "
        "de frescura antes de confiar en sus cifras."
    ),
    (OPERATIONS_DOMAIN_LABEL, MONITOR_ALERT_TYPE, "high"): (
        "Confirmar con Operacion el estado de los cartuchos con fallos y con "
        "frescura vencida antes de publicar cifras que dependan de ellos."
    ),
    (OPERATIONS_DOMAIN_LABEL, MONITOR_ALERT_TYPE, "critical"): (
        "Tratar las cifras de los cartuchos afectados como no confiables y "
        "confirmar con Operacion el estado de las corridas antes de usarlas."
    ),
    (RISK_DOMAIN_LABEL, MONITOR_ALERT_TYPE, "low"): (
        "Revisar los deals con cierre vencido por monto en el proximo ciclo "
        "comercial."
    ),
    (RISK_DOMAIN_LABEL, MONITOR_ALERT_TYPE, "medium"): (
        "Revisar los deals con cierre vencido por monto y los registros de "
        "empleo que terminan pronto antes del siguiente ciclo."
    ),
    (RISK_DOMAIN_LABEL, MONITOR_ALERT_TYPE, "high"): (
        "Priorizar la revision de los deals con mayor monto vencido y confirmar "
        "con Talento los registros de empleo proximos a terminar."
    ),
    (RISK_DOMAIN_LABEL, MONITOR_ALERT_TYPE, "critical"): (
        "Escalar las senales de deals vencidos y de rotacion a la revision de "
        "Riesgo antes del siguiente ciclo comercial."
    ),
    (TALENT_DOMAIN_LABEL, MONITOR_ALERT_TYPE, "low"): (
        "Revisar las bandas de riesgo de salida publicadas por Talento en la "
        "proxima revision de personas."
    ),
    (TALENT_DOMAIN_LABEL, MONITOR_ALERT_TYPE, "medium"): (
        "Revisar con Talento la poblacion en riesgo de salida y los registros "
        "de empleo proximos a terminar."
    ),
    (TALENT_DOMAIN_LABEL, MONITOR_ALERT_TYPE, "high"): (
        "Confirmar con Talento la poblacion en riesgo alto y preparar la "
        "conversacion de retencion antes del proximo ciclo."
    ),
    (TALENT_DOMAIN_LABEL, MONITOR_ALERT_TYPE, "critical"): (
        "Escalar a Talento la poblacion en riesgo alto y acordar un plan de "
        "retencion antes del proximo ciclo de personas."
    ),
}

# First fallback: the domain is known but the (alert_type, severity) pair is
# not. A new alert_type on an existing domain is the likely case, and the domain
# still determines who reads the alert and what they can act on.
DOMAIN_DEFAULT_RECOMMENDATIONS: dict[str, str] = {
    FINANCE_DOMAIN_LABEL: (
        "Revisar la evidencia agregada de Finanzas en Control Room antes de "
        "comprometer capacidad o gasto."
    ),
    OPERATIONS_DOMAIN_LABEL: (
        "Revisar el estado de las corridas y la frescura de datos en Control "
        "Room antes de confiar en las cifras del dominio."
    ),
    RISK_DOMAIN_LABEL: (
        "Revisar la evidencia agregada de Riesgo en Control Room antes del "
        "siguiente ciclo comercial."
    ),
    TALENT_DOMAIN_LABEL: (
        "Revisar con Talento la evidencia agregada de personas publicada en "
        "Control Room."
    ),
}

# Last fallback: an unknown domain. Deliberately says nothing about what the
# alert is, because at this point nothing about it is known to be true; it only
# points the reader at the evidence, which always exists because the alert does.
DEFAULT_RECOMMENDATION = (
    "Revisar la evidencia agregada de esta alerta en Control Room antes de "
    "tomar una decision."
)


def recommendation_for(domain: str, alert_type: str, severity: str) -> str:
    """Fixed recommendation for one alert, never model-authored.

    Resolution order: exact ``(domain, alert_type, severity)`` key, then the
    domain's default, then :data:`DEFAULT_RECOMMENDATION`. Every step returns a
    complete sentence, so an unknown key degrades the specificity of the advice
    and never its publishability.
    """
    exact = NARRATIVE_RECOMMENDATIONS.get((domain, alert_type, severity))
    if exact:
        return exact
    return DOMAIN_DEFAULT_RECOMMENDATIONS.get(domain, DEFAULT_RECOMMENDATION)


# ── Prose rejection vocabularies ─────────────────────────────────────────────

# Anything implying the system acted, or will act, on its own. OMEGA monitors
# are recommendation_only and have no write-back: a sentence claiming otherwise
# is a false statement about the product, not a style problem.
#
# The last two alternatives are the front-end contract described in the module
# docstring: "Aprobar", any "ejecut*" form and "Vista previa" must never reach
# rendered Control Room copy.
FORBIDDEN_PROSE_PATTERNS = re.compile(
    r"\bejecut\w*"
    r"|\bejecuci[oó]n\w*"
    r"|\bya\s+hice\b"
    r"|\bhice\b"
    r"|\bapliqu[eé]\b"
    r"|\bimplement[eé]\b"
    r"|\brealic[eé]\b"
    r"|\benvi[eé]\b"
    r"|\bcorreg[ií]\b"
    r"|\bautom[aá]ticamente\b"
    r"|\baprobar\b"
    r"|\bvista\s+previa\b",
    re.IGNORECASE,
)

# Vocabulary that implies a statistical model produced the numbers. Used to
# reject prose while the basis is aggregates-only, which is every narrative
# today: the three domain monitors ship Monte Carlo, Bayesian calibration and
# the decision orchestrator disabled for lack of input data.
SIMULATION_VOCABULARY = re.compile(
    r"\bsimulaci[oó]n\w*"
    r"|\bsimulad\w*"
    r"|\bmontecarlo\b"
    r"|\bmonte\s+carlo\b"
    r"|\bpercentil\w*"
    r"|\bp(?:10|50|90)\b"
    r"|\bdistribuci[oó]n\w*"
    r"|\bescenarios\s+simulados\b"
    r"|\bprobabilidad\w*",
    re.IGNORECASE,
)


# ── Basis of the analysis ────────────────────────────────────────────────────

BASIS_AGGREGATES_ONLY = "aggregates_only"
BASIS_WITH_SIMULATION = "with_simulation"

BASIS_NOTES: dict[str, str] = {
    BASIS_AGGREGATES_ONLY: (
        "Analisis basado en agregados, sin simulacion estadistica."
    ),
    BASIS_WITH_SIMULATION: (
        "Analisis basado en agregados y en una simulacion estadistica con "
        "escenarios publicados."
    ),
}
DEFAULT_BASIS_CODE = BASIS_AGGREGATES_ONLY


def basis_note(basis_code: str) -> str:
    """Fixed note for a basis code; unknown codes read as aggregates-only.

    Falling back to the weaker claim is the safe direction: it never asserts a
    simulation that may not have run.
    """
    return BASIS_NOTES.get(basis_code, BASIS_NOTES[BASIS_AGGREGATES_ONLY])


# ── Confidence ───────────────────────────────────────────────────────────────

CONFIDENCE_HIGH = "alta"
CONFIDENCE_MEDIUM = "media"
CONFIDENCE_LOW = "baja"

# Ascending, so ``narrative_service`` can cap a level by index instead of by a
# chain of comparisons.
CONFIDENCE_ORDER: tuple[str, ...] = (
    CONFIDENCE_LOW,
    CONFIDENCE_MEDIUM,
    CONFIDENCE_HIGH,
)

CONFIDENCE_LABELS: dict[str, str] = {
    CONFIDENCE_HIGH: "Confianza alta",
    CONFIDENCE_MEDIUM: "Confianza media",
    CONFIDENCE_LOW: "Confianza baja",
}

# One fixed reason per level. The alert's own ``confidence`` number is NOT the
# source: ``_monitor_alert_args`` writes ``float(contract.get("confidence") or
# 0.8)`` and no domain contract sets the key, so that number is the literal 0.8
# on every alert of every domain. Publishing it as if it were measured would be
# the exact kind of lie this module exists to prevent.
CONFIDENCE_REASONS: dict[str, str] = {
    CONFIDENCE_HIGH: (
        "Confianza alta: la evidencia esta completa y no hay limitaciones "
        "declaradas en esta corrida."
    ),
    CONFIDENCE_MEDIUM: (
        "Confianza media: la evidencia viene de agregados o la corrida declara "
        "limitaciones."
    ),
    CONFIDENCE_LOW: (
        "Confianza baja: parte del origen no esta listo o la evidencia es "
        "indirecta."
    ),
}


def confidence_reason(label: str) -> str:
    """Fixed reason for a confidence level; unknown levels read as low."""
    return CONFIDENCE_REASONS.get(label, CONFIDENCE_REASONS[CONFIDENCE_LOW])


# ── Limitations ──────────────────────────────────────────────────────────────

# Same shape and same discipline as ``domain_kpis.PUBLIC_NOTE_RULES``: an
# ordered tuple of (marker, public phrase), first marker contained in the text
# wins, so the more specific marker must come first.
#
# The markers are the internal strings the monitor chain actually produces —
# blocker codes from ``_monitor_with_engine_results``, engine disable reasons
# from ``domain_monitor_support``, and public notes from ``domain_kpis``. They
# are matched, never published; only the right-hand phrase is ever published.
LIMITATION_PHRASES: tuple[tuple[str, str], ...] = (
    (
        "missing_simulation_inputs",
        "sin datos de entrada de simulacion para este dominio: no hay "
        "escenarios calculados",
    ),
    (
        "sin dataset de inputs de simulacion",
        "sin datos de entrada de simulacion para este dominio: no hay "
        "escenarios calculados",
    ),
    (
        "sin historial de calibracion",
        "sin historial de calibracion para este grupo: la confianza no esta "
        "ajustada por resultados previos",
    ),
    (
        "la procedencia de wisdom bit",
        "sin procedencia durable para este dominio: no hay decision orquestada "
        "detras de la alerta",
    ),
    (
        "unsupported monitor engine",
        "un motor declarado en el contrato no esta soportado: la alerta se "
        "apoya solo en los agregados",
    ),
    (
        "monitor_engine_blocked",
        "un motor de analisis quedo bloqueado en esta corrida: la alerta se "
        "apoya solo en los agregados",
    ),
    (
        "cost_center_budget",
        "no hay presupuesto por centro de costo publicado en ningun origen",
    ),
    (
        "presupuesto por centro de costo",
        "no hay presupuesto por centro de costo publicado en ningun origen",
    ),
    (
        "sin tarifa publicada",
        "sin tarifa publicada: el monto facturable no se puede calcular",
    ),
    (
        "la plantilla no",
        "sin plantilla publicada: solo se pueden reportar los dias, no la tasa",
    ),
    (
        "sin cifra oficial de Talento",
        "sin cifra oficial de Talento: el catalogo de acciones no esta publicado",
    ),
    (
        "sin desglose",
        "sin desglose de detalle: el origen no publica esa dimension",
    ),
    (
        "sin conversion de moneda",
        "los montos llegan del origen sin conversion de moneda",
    ),
    (
        "sin verificar",
        "la moneda base no esta verificada en el origen",
    ),
    (
        "sin meses cerrados",
        "sin meses cerrados con datos en la ventana consultada",
    ),
    (
        "solo recomendacion",
        "solo recomendacion: no hay datos de compensacion detras de esta senal",
    ),
    (
        "las bandas de riesgo",
        "las bandas de riesgo las calcula Talento; aqui no se recalculan",
    ),
    (
        "el origen ya excluye oportunidades cerradas",
        "el corte corresponde a la ultima publicacion del origen",
    ),
    (
        "el origen no esta publicado",
        "el origen de este dominio no esta publicado para este workspace",
    ),
    (
        "no tiene la forma esperada",
        "el origen publicado no tiene la forma esperada",
    ),
    (
        "sin permiso de lectura",
        "sin permiso de lectura o sin workspace activo para parte de la evidencia",
    ),
    (
        "sin workspace activo",
        "sin workspace activo para parte de la evidencia",
    ),
    (
        "el origen de datos no esta disponible",
        "el origen de datos no esta disponible en este momento",
    ),
)

# An unclassified limitation is still a limitation. Reporting that one exists is
# honest and costs a reader nothing; dropping it would let a narrative read as
# unqualified when it is not.
_GENERIC_NOTE = (
    "hay una limitacion no clasificada en esta alerta: revisar la evidencia "
    "publicada en Control Room"
)
# Public alias. ``narrative_service`` needs the same fallback for a limitation
# that cannot be published as written; the underscore name stays because it is
# the one ``domain_kpis`` uses for the identical role, and renaming it would
# break the parallel a reader relies on when comparing the two modules.
GENERIC_LIMITATION_NOTE = _GENERIC_NOTE


def limitation_phrase(value: object) -> str:
    """Public phrase for one internal limitation marker.

    Matching is case-insensitive on both sides, unlike
    ``domain_kpis.public_note``: the markers here come from blocker codes and
    engine reasons written by several modules, not from one curated note list.
    An empty value still returns :data:`_GENERIC_NOTE`, because the caller only
    asks when it has already decided a limitation exists.
    """
    text = str(value or "").strip().casefold()
    if not text:
        return _GENERIC_NOTE
    for marker, phrase in LIMITATION_PHRASES:
        if marker.casefold() in text:
            return phrase
    return _GENERIC_NOTE


# ── Template narratives ──────────────────────────────────────────────────────

# The framing sentence used whenever the LLM does not produce a usable one — no
# provider configured, a provider error, a timeout, or prose the validator
# rejected.
#
# There is no "narrative pending" state. An alert exists the moment the monitor
# raises it and a reader can open it immediately, so a narrative that says it is
# not ready yet is worse than no narrative at all. Each of these is complete,
# publishable, free of digits, and written to survive the same validator the
# model's sentence has to pass.
TEMPLATE_EXPLANATIONS: dict[str, str] = {
    FINANCE_DOMAIN_LABEL: (
        "El monitor de Finanzas encontro senales agregadas de margen y costo "
        "que conviene revisar antes del proximo compromiso de capacidad."
    ),
    OPERATIONS_DOMAIN_LABEL: (
        "El monitor de Operacion encontro senales agregadas de fallos y de "
        "frescura de datos que afectan la confianza en las cifras del dominio."
    ),
    RISK_DOMAIN_LABEL: (
        "El monitor de Riesgo encontro senales agregadas de deals vencidos y de "
        "rotacion que conviene revisar en el ciclo actual."
    ),
    TALENT_DOMAIN_LABEL: (
        "El monitor de Talento encontro senales agregadas de riesgo de salida "
        "que conviene revisar con el area de personas."
    ),
}

DEFAULT_TEMPLATE_EXPLANATION = (
    "El monitor programado encontro senales agregadas en este dominio que "
    "conviene revisar junto con la evidencia publicada en Control Room."
)


def template_explanation(domain: str) -> str:
    """Complete framing sentence for one domain, with a generic fallback."""
    return TEMPLATE_EXPLANATIONS.get(domain, DEFAULT_TEMPLATE_EXPLANATION)


__all__ = (
    "BASIS_AGGREGATES_ONLY",
    "BASIS_NOTES",
    "BASIS_WITH_SIMULATION",
    "CONFIDENCE_HIGH",
    "CONFIDENCE_LABELS",
    "CONFIDENCE_LOW",
    "CONFIDENCE_MEDIUM",
    "CONFIDENCE_ORDER",
    "CONFIDENCE_REASONS",
    "DEFAULT_BASIS_CODE",
    "DEFAULT_DOMAIN_LABEL",
    "DEFAULT_RECOMMENDATION",
    "DEFAULT_SEVERITY",
    "DEFAULT_TEMPLATE_EXPLANATION",
    "DOMAIN_DEFAULT_RECOMMENDATIONS",
    "DOMAIN_LABELS",
    "FINANCE_DOMAIN_LABEL",
    "FORBIDDEN_PROSE_PATTERNS",
    "GENERIC_LIMITATION_NOTE",
    "LIMITATION_PHRASES",
    "MAGNITUDE_PHRASES",
    "MAGNITUDE_SEVERAL",
    "MAGNITUDE_SINGLE",
    "MONITOR_ALERT_TYPE",
    "NARRATIVE_RECOMMENDATIONS",
    "OPERATIONS_DOMAIN_LABEL",
    "RISK_DOMAIN_LABEL",
    "SEVERITIES",
    "SEVERITY_WORDS",
    "SIMULATION_VOCABULARY",
    "TALENT_DOMAIN_LABEL",
    "TEMPLATE_EXPLANATIONS",
    "basis_note",
    "confidence_reason",
    "limitation_phrase",
    "recommendation_for",
    "template_explanation",
)
