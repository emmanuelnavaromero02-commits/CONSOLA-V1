from __future__ import annotations

from app.domains.pipeline.airflow_error_copy import (
    AIRFLOW_TASK_DEFAULT_ERROR_ES,
    airflow_task_error_es,
)


def test_timeout_patterns_map_to_timeout_copy():
    assert (
        airflow_task_error_es("extract task timed out after 300s")
        == "Tiempo de espera agotado al conectar con el origen"
    )
    assert (
        airflow_task_error_es("DeadlineExceeded while calling source")
        == "Tiempo de espera agotado al conectar con el origen"
    )


def test_auth_patterns_map_to_credentials_copy():
    assert (
        airflow_task_error_es("HTTP 401 Unauthorized")
        == "Credenciales rechazadas por el sistema de origen"
    )
    assert (
        airflow_task_error_es("server said: 403 Forbidden")
        == "Credenciales rechazadas por el sistema de origen"
    )
    assert (
        airflow_task_error_es("invalid_client credential rejected")
        == "Credenciales rechazadas por el sistema de origen"
    )


def test_connectivity_patterns_map_to_unreachable_copy():
    assert (
        airflow_task_error_es("connection refused by 10.0.0.5:443")
        == "No se pudo alcanzar el sistema de origen"
    )
    assert (
        airflow_task_error_es("DNS name or service not known")
        == "No se pudo alcanzar el sistema de origen"
    )


def test_unknown_or_empty_text_falls_back_to_default_copy():
    assert airflow_task_error_es("extract_employees") == AIRFLOW_TASK_DEFAULT_ERROR_ES
    assert airflow_task_error_es(None) == AIRFLOW_TASK_DEFAULT_ERROR_ES
    assert airflow_task_error_es() == AIRFLOW_TASK_DEFAULT_ERROR_ES


def test_timeout_wins_over_other_patterns():
    assert (
        airflow_task_error_es("auth call timed out")
        == "Tiempo de espera agotado al conectar con el origen"
    )
