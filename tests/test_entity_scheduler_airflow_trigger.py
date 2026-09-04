from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from airflow.dags.entity_scheduler_trigger import (
    AirflowTriggerError,
    require_all_succeeded,
    trigger_dag_run,
)


REPO = Path(__file__).resolve().parents[1]


class _Response:
    def __init__(self, status_code: int, text: str = ""):
        self.status_code = status_code
        self.text = text


class _HTTP:
    def __init__(self, patch_status: int = 200, post_status: int = 201):
        self.patch_status = patch_status
        self.post_status = post_status
        self.calls: list[tuple[str, str, dict]] = []

    def patch(self, url: str, **kwargs):
        self.calls.append(("PATCH", url, kwargs))
        return _Response(self.patch_status, "sensitive-response")

    def post(self, url: str, **kwargs):
        self.calls.append(("POST", url, kwargs))
        return _Response(self.post_status, "sensitive-response")


def _trigger(http: _HTTP) -> int:
    return trigger_dag_run(
        base_url="http://airflow:8080/airflow/",
        dag_id="banxico_extract",
        run_id="scheduled-1",
        conf={"entity": "series_observations"},
        username="admin",
        password="not-logged",
        http=http,
    )


def test_trigger_uses_production_airflow_prefix_and_unpauses_first():
    http = _HTTP()

    assert _trigger(http) == 201
    assert [call[0] for call in http.calls] == ["PATCH", "POST"]
    assert http.calls[0][1] == "http://airflow:8080/airflow/api/v1/dags/banxico_extract"
    assert http.calls[1][1].endswith("/banxico_extract/dagRuns")


def test_existing_run_is_an_idempotent_success():
    assert _trigger(_HTTP(post_status=409)) == 409


@pytest.mark.parametrize("stage,status", [("unpause", 404), ("trigger", 502)])
def test_http_failure_is_sanitized_and_fail_closed(stage: str, status: int):
    http = _HTTP(
        patch_status=status if stage == "unpause" else 200,
        post_status=status if stage == "trigger" else 201,
    )

    with pytest.raises(AirflowTriggerError) as raised:
        _trigger(http)

    assert raised.value.stage == stage
    assert raised.value.status_code == status
    assert "sensitive-response" not in str(raised.value)
    assert "not-logged" not in str(raised.value)


def test_batch_failure_is_not_reported_as_scheduler_success():
    with pytest.raises(RuntimeError, match="1 of 2 scheduled DAG trigger"):
        require_all_succeeded([{"ok": True}, {"ok": False}])


def test_aws_airflow_runtime_has_the_verified_internal_api_base():
    compose = yaml.safe_load(
        (REPO / "infra/terraform/deploy/docker-compose.aws.yml").read_text()
    )

    for service in ("airflow", "airflow-scheduler"):
        assert (
            compose["services"][service]["environment"]["AIRFLOW_URL"]
            == "http://airflow:8080/airflow"
        )


def test_gcp_scheduler_has_the_verified_internal_api_base():
    compose = yaml.safe_load(
        (REPO / "infra/terraform-gcp/templates/docker-compose.gcp.yml.tftpl")
        .read_text()
        .replace("$${", "${")
    )

    assert (
        compose["services"]["airflow-scheduler"]["environment"]["AIRFLOW_URL"]
        == "${AIRFLOW_URL:-http://airflow:8080/airflow}"
    )
