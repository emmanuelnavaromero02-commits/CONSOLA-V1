"""No-schedule, non-serving BigQuery parity DAG for Talent 9-Box."""

from __future__ import annotations

import os
from datetime import datetime, timedelta

from airflow import DAG
from airflow.operators.python import PythonOperator
from airflow.utils.trigger_rule import TriggerRule

import _bigquery_talent_9box_shadow_runtime as runtime


def _config(ctx) -> runtime.ShadowRunConfig:
    conf = (ctx.get("dag_run").conf if ctx.get("dag_run") else {}) or {}
    return runtime.ShadowRunConfig.load(conf)


def fetch_postgres_baseline(**ctx):
    return runtime.fetch_postgres_baseline(_config(ctx))


def verify_source_before_load(**ctx):
    config = _config(ctx)
    baseline = ctx["ti"].xcom_pull(task_ids="fetch_postgres_baseline")
    _, storage_client = runtime.google_clients(config)
    return runtime.verify_gcs_object(config, baseline, storage_client)


def load_immutable_table(**ctx):
    config = _config(ctx)
    baseline = ctx["ti"].xcom_pull(task_ids="fetch_postgres_baseline")
    bigquery_client, _ = runtime.google_clients(config)
    return runtime.load_immutable_table(config, baseline, bigquery_client)


def dry_run_query(**ctx):
    config = _config(ctx)
    pull = ctx["ti"].xcom_pull
    baseline = pull(task_ids="fetch_postgres_baseline")
    loaded = pull(task_ids="load_immutable_table")
    bigquery_client, _ = runtime.google_clients(config)
    return runtime.dry_run_aggregate_query(
        config, baseline, loaded, bigquery_client
    )


def execute_aggregate_query(**ctx):
    config = _config(ctx)
    pull = ctx["ti"].xcom_pull
    baseline = pull(task_ids="fetch_postgres_baseline")
    loaded = pull(task_ids="load_immutable_table")
    query_plan = pull(task_ids="dry_run_query")
    bigquery_client, _ = runtime.google_clients(config)
    return runtime.execute_aggregate_query(
        config, baseline, loaded, query_plan, bigquery_client
    )


def verify_source_after_query(**ctx):
    config = _config(ctx)
    baseline = ctx["ti"].xcom_pull(task_ids="fetch_postgres_baseline")
    _, storage_client = runtime.google_clients(config)
    return runtime.verify_gcs_object(config, baseline, storage_client)


def compare_with_postgres(**ctx):
    pull = ctx["ti"].xcom_pull
    return runtime.compare_aggregates(
        pull(task_ids="fetch_postgres_baseline"),
        pull(task_ids="execute_aggregate_query"),
    )


def audit_xcom_and_logs(**ctx):
    pull = ctx["ti"].xcom_pull
    payloads = [
        pull(task_ids=task_id)
        for task_id in (
            "fetch_postgres_baseline",
            "verify_source_before_load",
            "load_immutable_table",
            "dry_run_query",
            "execute_aggregate_query",
            "verify_source_after_query",
            "compare_with_postgres",
        )
    ]
    return runtime.combined_pii_audit(
        payloads=payloads,
        base_log_folder=os.environ.get(
            "AIRFLOW__LOGGING__BASE_LOG_FOLDER", "/opt/airflow/logs"
        ),
        dag_id="bigquery_talent_9box_shadow",
        run_id=str(ctx.get("run_id") or ""),
    )


def record_shadow_evidence(**ctx):
    config = _config(ctx)
    pull = ctx["ti"].xcom_pull
    result = runtime.record_shadow_evidence(
        config,
        airflow_dag_run_id=str(ctx.get("run_id") or ""),
        baseline=pull(task_ids="fetch_postgres_baseline"),
        source_before=pull(task_ids="verify_source_before_load"),
        loaded=pull(task_ids="load_immutable_table"),
        dry_run=pull(task_ids="dry_run_query"),
        bigquery_result=pull(task_ids="execute_aggregate_query"),
        source_after=pull(task_ids="verify_source_after_query"),
        comparison=pull(task_ids="compare_with_postgres"),
        pii_audit=pull(task_ids="audit_xcom_and_logs"),
    )
    if result.get("status") != "success":
        raise RuntimeError("BigQuery Talent shadow verification failed")
    return result


default_args = {
    "owner": "omega-data-platform",
    "retries": 1,
    "retry_delay": timedelta(minutes=2),
    "retry_exponential_backoff": True,
}

dag = DAG(
    dag_id="bigquery_talent_9box_shadow",
    description="Aggregate-only parity; PostgreSQL Gold remains authoritative.",
    default_args=default_args,
    schedule_interval=None,
    start_date=datetime(2026, 9, 1),
    catchup=False,
    max_active_runs=2,
    # schedule=None plus the runtime flag is the activation gate. Keeping the
    # DAG unpaused allows programmatic canary triggers to execute immediately.
    is_paused_upon_creation=False,
    tags=["sap_successfactors", "talent", "9box", "bigquery", "shadow"],
)

t_baseline = PythonOperator(
    task_id="fetch_postgres_baseline", python_callable=fetch_postgres_baseline, dag=dag
)
t_verify_before = PythonOperator(
    task_id="verify_source_before_load", python_callable=verify_source_before_load, dag=dag
)
t_load = PythonOperator(
    task_id="load_immutable_table", python_callable=load_immutable_table, dag=dag
)
t_dry_run = PythonOperator(
    task_id="dry_run_query", python_callable=dry_run_query, dag=dag
)
t_query = PythonOperator(
    task_id="execute_aggregate_query", python_callable=execute_aggregate_query, dag=dag
)
t_verify_after = PythonOperator(
    task_id="verify_source_after_query", python_callable=verify_source_after_query, dag=dag
)
t_compare = PythonOperator(
    task_id="compare_with_postgres", python_callable=compare_with_postgres, dag=dag
)
t_audit = PythonOperator(
    task_id="audit_xcom_and_logs", python_callable=audit_xcom_and_logs, dag=dag
)
t_record = PythonOperator(
    task_id="record_shadow_evidence",
    python_callable=record_shadow_evidence,
    trigger_rule=TriggerRule.ALL_DONE,
    dag=dag,
)

(
    t_baseline
    >> t_verify_before
    >> t_load
    >> t_dry_run
    >> t_query
    >> t_verify_after
    >> t_compare
    >> t_audit
    >> t_record
)
