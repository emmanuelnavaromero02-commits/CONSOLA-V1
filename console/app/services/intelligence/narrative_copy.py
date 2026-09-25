from __future__ import annotations

import re


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

MONITOR_ALERT_TYPE = "wisdombit_monitor"

SEVERITIES: tuple[str, ...] = ("low", "medium", "high", "critical")
DEFAULT_SEVERITY = "medium"

SEVERITY_WORDS: dict[str, str] = {
    "low": "baja",
    "medium": "media",
    "high": "alta",
    "critical": "critica",
}

MAGNITUDE_SINGLE = "single"
MAGNITUDE_SEVERAL = "several"
MAGNITUDE_PHRASES: dict[str, str] = {
    MAGNITUDE_SINGLE: "una señal",
    MAGNITUDE_SEVERAL: "varias señales",
}


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

DEFAULT_RECOMMENDATION = (
    "Revisar la evidencia agregada de esta alerta en Control Room antes de "
    "tomar una decision."
)


def recommendation_for(domain: str, alert_type: str, severity: str) -> str:
    exact = NARRATIVE_RECOMMENDATIONS.get((domain, alert_type, severity))
    if exact:
        return exact
    return DOMAIN_DEFAULT_RECOMMENDATIONS.get(domain, DEFAULT_RECOMMENDATION)


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
    return BASIS_NOTES.get(basis_code, BASIS_NOTES[BASIS_AGGREGATES_ONLY])


CONFIDENCE_HIGH = "alta"
CONFIDENCE_MEDIUM = "media"
CONFIDENCE_LOW = "baja"

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
    return CONFIDENCE_REASONS.get(label, CONFIDENCE_REASONS[CONFIDENCE_LOW])


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

_GENERIC_NOTE = (
    "hay una limitacion no clasificada en esta alerta: revisar la evidencia "
    "publicada en Control Room"
)
GENERIC_LIMITATION_NOTE = _GENERIC_NOTE


def limitation_phrase(value: object) -> str:
    text = str(value or "").strip().casefold()
    if not text:
        return _GENERIC_NOTE
    for marker, phrase in LIMITATION_PHRASES:
        if marker.casefold() in text:
            return phrase
    return _GENERIC_NOTE


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
