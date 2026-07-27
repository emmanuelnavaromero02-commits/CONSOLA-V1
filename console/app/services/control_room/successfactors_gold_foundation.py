from __future__ import annotations

from typing import Any

from app.services.control_room.successfactors_gold_observations import (
    _sf_gold_headcount_rows,
    _sf_gold_headcount_widget_status,
    _sf_gold_summary_total,
    _sf_gold_usable_rows,
    _sf_load_foundation_gold_results,
)
from app.services.control_room.successfactors_headcount_coverage import (
    dimension_coverage,
    exact_population,
)


_DIMENSIONS = (
    (
        "headcount_by_company",
        "sf_headcount_by_company",
        "Headcount por compania",
        "company_id",
        "company_name",
    ),
    (
        "headcount_by_location",
        "sf_headcount_by_location",
        "Headcount por ubicacion",
        "location_id",
        "location_name",
    ),
    (
        "headcount_by_department",
        "sf_headcount_by_department",
        "Headcount por departamento",
        "department_id",
        "department_name",
    ),
)


def _sf_foundation_gold_datasets() -> dict[str, str]:
    return {
        "employee_360": "sap_successfactors_employee_360",
        "headcount_by_company": "sap_successfactors_headcount_by_company",
        "headcount_by_location": "sap_successfactors_headcount_by_location",
        "headcount_by_department": "sap_successfactors_headcount_by_department",
    }


async def _sf_foundation_gold_results(
    datasets: dict[str, str],
    user: dict | None,
) -> dict[str, dict[str, Any]]:
    return await _sf_load_foundation_gold_results(datasets, user)


def _sf_foundation_gold_rows(
    results: dict[str, dict[str, Any]],
) -> dict[str, list[dict[str, Any]] | None]:
    return {
        key: _sf_gold_usable_rows(result)
        for key, result in results.items()
        if key != "active_headcount"
    }


def _dataset_href(dataset: str) -> str:
    return (
        "/data/catalog?layer=gold&cartridge=sap_successfactors" f"&datasets={dataset}"
    )


def _widget(
    widget_id: str,
    title: str,
    value: int | None,
    dataset: str,
    rows: list[dict[str, Any]],
    status: str,
    error: str | None,
    *,
    coverage_observed: bool = False,
) -> dict[str, Any]:
    return {
        "id": widget_id,
        "title": title,
        "value": value,
        "dataset": dataset,
        "href": _dataset_href(dataset),
        "rows": rows,
        "status": status,
        "error": error,
        "_coverage_observed": coverage_observed,
    }


def _dimension_widget(
    *,
    datasets: dict[str, str],
    results: dict[str, dict[str, Any]],
    rows: dict[str, list[dict[str, Any]] | None],
    exact_result: dict[str, Any],
    key: str,
    widget_id: str,
    title: str,
    id_key: str,
    name_key: str,
) -> dict[str, Any]:
    raw_rows = rows.get(key) or []
    observations = _sf_gold_headcount_rows(raw_rows, (id_key, name_key))
    total = _sf_gold_summary_total(results[key], observations)
    status = _sf_gold_headcount_widget_status(
        results[key].get("status"), raw_rows, observations
    )
    if status == "ready" and total is None:
        status = "invalid_schema"
    source = {**results[key], "status": status, "total": total}
    coverage = dimension_coverage(source, exact_population(exact_result))
    return _widget(
        widget_id,
        title,
        coverage.value,
        datasets[key],
        observations[:5]
        if coverage.publish_observed_subset or coverage.status == "ready"
        else [],
        coverage.status,
        coverage.error,
        coverage_observed=coverage.publish_observed_subset,
    )


def _sf_foundation_gold_widgets(
    datasets: dict[str, str],
    results: dict[str, dict[str, Any]],
    rows: dict[str, list[dict[str, Any]] | None],
) -> list[dict[str, Any]]:
    exact_result = results["active_headcount"]
    exact = exact_population(exact_result)
    widgets = [
        _widget(
            "sf_active_headcount",
            "Headcount total activo",
            exact.value,
            datasets["employee_360"],
            [],
            exact.status,
            exact.error,
        )
    ]
    widgets.extend(
        _dimension_widget(
            datasets=datasets,
            results=results,
            rows=rows,
            exact_result=exact_result,
            key=key,
            widget_id=widget_id,
            title=title,
            id_key=id_key,
            name_key=name_key,
        )
        for key, widget_id, title, id_key, name_key in _DIMENSIONS
    )
    return widgets


__all__ = (
    "_sf_foundation_gold_datasets",
    "_sf_foundation_gold_results",
    "_sf_foundation_gold_rows",
    "_sf_foundation_gold_widgets",
)
