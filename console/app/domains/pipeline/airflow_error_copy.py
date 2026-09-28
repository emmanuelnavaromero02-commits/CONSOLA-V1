from __future__ import annotations

import re

AIRFLOW_TASK_DEFAULT_ERROR_ES = "La tarea falló; revisa el detalle técnico"

_PATTERNS: tuple[tuple[re.Pattern[str], str], ...] = (
    (
        re.compile(r"timed?[\s_-]*out|timeout|deadline\s*exceeded", re.IGNORECASE),
        "Tiempo de espera agotado al conectar con el origen",
    ),
    (
        re.compile(
            r"\b401\b|\b403\b|unauthoriz|forbidden|invalid[\s_-]*client"
            r"|access\s*denied|credential|auth",
            re.IGNORECASE,
        ),
        "Credenciales rechazadas por el sistema de origen",
    ),
    (
        re.compile(
            r"connection\s*refused|econnrefused|name\s*or\s*service\s*not\s*known"
            r"|getaddrinfo|nodename|dns|unreachable|no\s*route\s*to\s*host",
            re.IGNORECASE,
        ),
        "No se pudo alcanzar el sistema de origen",
    ),
)


def airflow_task_error_es(*texts: object) -> str:
    joined = " ".join(str(text) for text in texts if text)
    if joined:
        for pattern, copy in _PATTERNS:
            if pattern.search(joined):
                return copy
    return AIRFLOW_TASK_DEFAULT_ERROR_ES
