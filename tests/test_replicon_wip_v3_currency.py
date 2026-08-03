from __future__ import annotations

from pathlib import Path

import duckdb
import pandas as pd

from cartridges.replicon.app.services.base_currency_frame import base_currency_frame
from tests.test_operational_truth_data_kb_config import _packaged


def _write_inputs(tmp_path: Path) -> dict[str, Path]:
    months = [
        ("2026-01-15", "usd", 100.0, "USD"),
        ("2026-02-15", "mxn-fx", 1000.0, "MXN"),
        ("2026-03-15", "mxn-no-fx", 1000.0, "MXN"),
        ("2026-04-15", "eur", 100.0, "EUR"),
        ("2026-05-15", "missing", 100.0, "USD"),
        ("2026-06-15", "unknown", 100.0, "ZZZ"),
        ("2026-01-15", "mixed-missing", 100.0, "USD"),
        ("2026-01-15", "time-nonfinite", 100.0, "USD"),
        ("2026-01-15", "invoice-nonfinite", 100.0, "USD"),
        ("2026-01-15", "billing-hours-nonfinite", 100.0, "USD"),
        ("2026-01-15", "billable-flag-missing", 100.0, "USD"),
        ("2026-07-15", "usd-zero-rate", 0.0, "USD"),
        ("2026-08-15", "usd-negative-rate", -100.0, "USD"),
        ("2026-09-15", "mxn-zero-rate", 0.0, "MXN"),
        ("2026-10-15", "mxn-negative-rate", -1000.0, "MXN"),
    ]
    billing = pd.DataFrame(
        [
            {
                "entrydate": date,
                "projectid": project,
                "billabledurationhours": 10.0,
                "billableamountbasecurrency": amount,
            }
            for date, project, amount, _currency in months
        ]
    )
    billing.loc[len(billing)] = {
        "entrydate": "2026-01-15",
        "projectid": "mixed-missing",
        "billabledurationhours": 10.0,
        "billableamountbasecurrency": None,
    }
    billing.loc[len(billing)] = {
        **billing.loc[billing["projectid"] == "billing-hours-nonfinite"].iloc[0],
        "billabledurationhours": float("inf"),
    }
    time_entries = pd.DataFrame(
        [
            {
                "entrydate": date,
                "projectid": project,
                "projectname": project,
                "clientname": "client",
                "userid": "user-1",
                "username": "User One",
                "durationhours": 10.0,
                "isbillable": True,
            }
            for date, project, _amount, _currency in months
        ]
    )
    time_entries.loc[len(time_entries)] = {
        **time_entries.loc[time_entries["projectid"] == "time-nonfinite"].iloc[0],
        "durationhours": float("inf"),
    }
    time_entries.loc[len(time_entries)] = {
        **time_entries.loc[time_entries["projectid"] == "billable-flag-missing"].iloc[
            0
        ],
        "isbillable": None,
    }
    invoices = pd.DataFrame(
        [
            {
                "invoice_date": date,
                "project_id": project,
                "currency": currency,
                "billed_amount": amount,
                "invoice_status": "Emitida",
            }
            for date, project, amount, currency in months
        ]
    )
    invoices.loc[len(invoices)] = {
        **invoices.loc[invoices["project_id"] == "invoice-nonfinite"].iloc[0],
        "billed_amount": float("inf"),
    }
    fx = pd.DataFrame(
        [
            {"year_month": "2026-02-01", "avg_rate": 0.05},
            {"year_month": "2026-09-01", "avg_rate": 0.05},
            {"year_month": "2026-10-01", "avg_rate": 0.05},
        ]
    )
    frames = {
        "billing": billing,
        "time": time_entries,
        "invoices": invoices,
        "fx": fx,
    }
    paths = {}
    for name, frame in frames.items():
        path = tmp_path / f"{name}.parquet"
        frame.to_parquet(path, index=False)
        paths[name] = path
    return paths


def _resolved_sql(sql: str, paths: dict[str, Path]) -> str:
    replacements = {
        "s3://{bucket}/raw/replicon/BillingItem/load_date=*/batch_id=*/*.parquet": paths[
            "billing"
        ],
        "s3://{bucket}/raw/replicon/TimeEntry/load_date=*/batch_id=*/*.parquet": paths[
            "time"
        ],
        "s3://{bucket}/raw/fx_rates/mxn_usd/fx_rates.parquet": paths["fx"],
        "s3://{bucket}/raw/excel_billing/invoices/load_date=*/*.parquet": paths[
            "invoices"
        ],
    }
    for source, target in replacements.items():
        sql = sql.replace(source, str(target))
    return sql


def test_wip_v3_uses_authoritative_currency_by_period_and_real_fx(
    tmp_path: Path,
) -> None:
    paths = _write_inputs(tmp_path)
    config = base_currency_frame(
        [
            {"effective_from": start, "effective_to": end, "currency": currency}
            for start, end, currency in (
                ("2026-01-01", "2026-02-01", "USD"),
                ("2026-02-01", "2026-04-01", "MXN"),
                ("2026-04-01", "2026-05-01", "EUR"),
                ("2026-06-01", "2026-07-01", "ZZZ"),
                ("2026-07-01", "2026-09-01", "USD"),
                ("2026-09-01", None, "MXN"),
            )
        ]
    )
    conn = duckdb.connect()
    try:
        conn.register("replicon_base_currency", config)
        definitions = _packaged()
        monthly = conn.execute(
            _resolved_sql(definitions["kb_wip_mensual"]["sql"], paths)
        ).df()
        summary = conn.execute(
            _resolved_sql(definitions["kb_wip_resumen"]["sql"], paths)
        ).df()
    finally:
        conn.close()

    statuses = dict(zip(monthly["projectid"], monthly["financial_status"], strict=True))
    assert statuses == {
        "usd": "ready",
        "mxn-fx": "ready",
        "mxn-no-fx": "missing_fx",
        "eur": "missing_fx",
        "missing": "missing_base_currency",
        "unknown": "missing_fx",
        "mixed-missing": "insufficient_data",
        "time-nonfinite": "insufficient_data",
        "invoice-nonfinite": "insufficient_data",
        "billing-hours-nonfinite": "insufficient_data",
        "billable-flag-missing": "insufficient_data",
        "usd-zero-rate": "insufficient_data",
        "usd-negative-rate": "insufficient_data",
        "mxn-zero-rate": "insufficient_data",
        "mxn-negative-rate": "insufficient_data",
    }
    by_project = monthly.set_index("projectid")
    assert by_project.loc["usd", "billing_rate_usd"] == 10.0
    assert by_project.loc["mxn-fx", "billing_rate_usd"] == 5.0
    assert by_project.loc["eur", "original_billed_currency"] == "EUR"
    assert by_project.loc["eur", "original_billed_amount"] == 100.0
    assert by_project.loc["unknown", "original_billed_currency"] == "ZZZ"
    assert by_project.loc["unknown", "original_billed_amount"] == 100.0
    for project in (
        "mxn-no-fx",
        "eur",
        "missing",
        "unknown",
        "mixed-missing",
        "time-nonfinite",
        "invoice-nonfinite",
        "billing-hours-nonfinite",
        "billable-flag-missing",
        "usd-zero-rate",
        "usd-negative-rate",
        "mxn-zero-rate",
        "mxn-negative-rate",
    ):
        assert pd.isna(by_project.loc[project, "billable_amount_usd"])
        assert pd.isna(by_project.loc[project, "wip_amount_usd"])
        assert pd.isna(by_project.loc[project, "billing_completion_pct"])
    for project in (
        "mxn-no-fx",
        "eur",
        "missing",
        "unknown",
        "mixed-missing",
        "time-nonfinite",
        "billing-hours-nonfinite",
        "billable-flag-missing",
        "usd-zero-rate",
        "usd-negative-rate",
        "mxn-zero-rate",
        "mxn-negative-rate",
    ):
        assert pd.isna(by_project.loc[project, "billing_rate_usd"])
    assert (
        dict(zip(summary["projectid"], summary["financial_status"], strict=True))
        == statuses
    )


def test_wip_v3_contract_has_no_cross_period_or_default_currency_rate() -> None:
    for definition in _packaged().values():
        sql = definition["sql"]
        assert "project_rate_default" not in sql
        assert "COALESCE(rm.rate_usd, rd.rate_usd)" not in sql
        assert "billableamountbasecurrency) /" not in sql
        assert "missing_base_currency" in sql
        assert "missing_fx" in sql
        assert "replicon_base_currency" in sql
