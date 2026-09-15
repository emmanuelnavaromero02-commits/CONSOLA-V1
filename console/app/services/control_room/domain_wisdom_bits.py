"""Wisdom-bit payloads for the Finance / Operations / Risk monitors.

A "wisdom bit" in this repository is not a database row and not a registry
entry: it is a handler that assembles an aggregated signal payload for the
deterministic monitor chain. Until Mission 4 exactly one existed, ``WB-TALENTO``,
built inline in ``routers/intelligence.py`` from the Talent overview and anomaly
views, and the route rejected every other id outright.

This module is the same idea for the three domains whose aggregates already
exist: it builds the payload from the Mission 2 domain KPI views
(``finance_kpis`` / ``operations_kpis`` / ``risk_kpis``) instead of inventing a
new data source. Those views already answer to the public projection, so the
text that ends up in a Control Room alert is business Spanish with no dataset
names, column names or identifiers in it.

The one design rule that matters here is how signals gate alerting. Read
``monitor_alert_policy.monitor_should_alert``: with ``min_signal_count: 1`` the
policy alerts on ANY payload carrying at least one signal, regardless of status.
So the decision "should this monitor speak?" is made *here*, by emitting a signal
only for a metric that is actually degraded or unavailable. A fully ready domain
emits zero signals and stays silent. Status then adds fail-closed silence on top:
``unavailable`` is in the policy's insufficient-status set, so a domain with no
evidence cannot alert even if it wanted to.
"""

from __future__ import annotations

from typing import Any

from app.domains.agentops.domain_monitor_support import DomainMonitorSpec

# Business Spanish for every metric the three views return. The monitor payload
# is read by Control Room and, through the alert, by a person; a metric key like
# ``labor_cost_by_department`` is technical copy and would be redacted.
METRIC_LABELS: dict[str, str] = {
    # finance
    "billable_hours_logged": "horas facturables registradas",
    # NOT "costo de nomina": Mission 2's public note says so in as many words
    # ("NO es nomina: la nomina de SAP no esta extraida"). It is Replicon
    # executed hours times an hourly cost rate.
    "labor_cost_by_department": "costo laboral estimado de consultores",
    "project_margin": "margen por proyecto",
    # operations
    "pipeline_health": "salud de las corridas de datos",
    "data_freshness_by_cartridge": "frescura de datos por cartucho",
    "absence_rate_company_by_type": "tasa de ausentismo por tipo",
    # risk
    "attrition_risk_population": "poblacion en riesgo de rotacion",
    # NOT "fin de contrato": the aggregate reads employee_360.end_date, and the
    # public note says "Es el fin del registro de empleo en SuccessFactors, NO un
    # elemento contractual".
    "employment_end_expiry": "fin de registro de empleo proximo",
    "deal_slippage": "deals con cierre vencido",
}

# Which Mission 2 view backs each domain monitor, by spec key.
VIEW_BY_KEY: dict[str, str] = {
    "finance": "finance_kpis",
    "operations": "operations_kpis",
    "risk": "risk_kpis",
}

STATUS_READY = "ready"
STATUS_DEGRADED = "degraded"
STATUS_UNAVAILABLE = "unavailable"

# A bounded number of signals: the payload is evidence for one alert, not a feed.
MAX_SIGNALS = 10


def metric_label(metric: str) -> str:
    """Business label for a metric key, falling back to the key itself.

    A new metric added to a view without a label here still travels, it just
    travels under its own name — visible and fixable, rather than dropped.
    """
    return METRIC_LABELS.get(metric, metric)


def _metric_reason(item: dict[str, Any]) -> str | None:
    """Why this metric is not ready, in business Spanish.

    ``error`` has already been mapped to a fixed public phrase by
    ``domain_kpis.public_error``; ``notes`` have already been translated. Either
    is safe to carry; the first one present wins.
    """
    error = item.get("error")
    if isinstance(error, str) and error.strip():
        return error.strip()
    notes = item.get("notes")
    if isinstance(notes, list):
        for note in notes:
            if isinstance(note, str) and note.strip():
                return note.strip()
    return None


def build_signals(metrics: dict[str, Any]) -> list[dict[str, Any]]:
    """One signal per metric that is NOT ready.

    ``monitor_alert_policy._signal_count`` refuses the whole payload unless every
    item is a non-empty dict and ``count`` equals ``len(items)`` exactly, so the
    caller must not adjust one without the other.
    """
    signals: list[dict[str, Any]] = []
    for metric, item in metrics.items():
        if not isinstance(item, dict):
            continue
        status = str(item.get("status") or "").strip().lower()
        if status == STATUS_READY:
            continue
        signal: dict[str, Any] = {
            "metric": metric_label(metric),
            "status": status or "unknown",
        }
        reason = _metric_reason(item)
        if reason:
            signal["reason"] = reason
        signals.append(signal)
        if len(signals) >= MAX_SIGNALS:
            break
    return signals


def build_coverage(metrics: dict[str, Any]) -> dict[str, list[str]]:
    coverage: dict[str, list[str]] = {"ready": [], "degraded": [], "unavailable": []}
    for metric, item in metrics.items():
        if not isinstance(item, dict):
            continue
        status = str(item.get("status") or "").strip().lower()
        bucket = coverage.get(status)
        if bucket is not None:
            bucket.append(metric_label(metric))
    return coverage


def build_payload(spec: DomainMonitorSpec, view: dict[str, Any]) -> dict[str, Any]:
    """Assemble the wisdom-bit response from one domain KPI view payload.

    Key names mirror the WB-TALENTO response so the deterministic chain, the
    alert-argument builder and the alert policy read both the same way.
    """
    metrics = view.get("metrics") if isinstance(view.get("metrics"), dict) else {}
    status = str(view.get("status") or "unknown").strip().lower()
    signals = build_signals(metrics)
    coverage = build_coverage(metrics)
    blockers = [
        f"{metric_label(metric)}: sin evidencia disponible"
        for metric in (view.get("unavailable_metrics") or [])
        if isinstance(metric, str)
    ]
    return {
        "ok": True,
        "wisdom_bit_id": spec.wisdom_bit_id,
        "cartridge_id": spec.cartridge_id,
        "domain": spec.domain,
        "decision_mode": "recommendation_only",
        "writeback_enabled": False,
        "compensation_enabled": False,
        "status": status,
        # Explicit rather than implied: with no evidence the policy must not
        # alert, and saying so is clearer than relying on the status set.
        "data_sufficient": status != STATUS_UNAVAILABLE,
        "generated_at": view.get("generated_at"),
        "profile": {
            "domain": spec.domain,
            "metrics_total": len(metrics),
            "metrics_ready": len(coverage["ready"]),
            "metrics_degraded": len(coverage["degraded"]),
            "metrics_unavailable": len(coverage["unavailable"]),
        },
        "coverage": {"status": status, **coverage},
        "blockers": blockers,
        "signals": {"count": len(signals), "items": signals},
        "notes": [note for note in (view.get("notes") or []) if isinstance(note, str)],
        "evidence": {"recommendation_only": True},
    }


async def domain_wisdom_bit(
    user: dict | None,
    spec: DomainMonitorSpec,
    *,
    control_room_service: Any,
) -> dict[str, Any]:
    """Run the domain's KPI view and wrap it as a wisdom-bit payload.

    ``control_room_service`` is injected because the views are rebound into that
    module by ``control_room.api._bind_to_core``; importing them directly would
    bypass the binding the rest of the Control Room surface goes through.
    """
    view_name = VIEW_BY_KEY.get(spec.key)
    if not view_name:
        raise KeyError(f"no domain KPI view for monitor {spec.key!r}")
    view = await getattr(control_room_service, view_name)(user)
    payload = build_payload(spec, view if isinstance(view, dict) else {})

    # Mission 4 part B4. The shared-memory write hangs off THIS path and not off
    # the domain KPI view, because the view is the body of a tool classified
    # read-only and gated on datasets.read, while this route already requires
    # control_room.write and wisdom_bits__run is an advisory write. Fire and
    # forget: the recorder never raises, and it writes at most once while an
    # unexpired finding exists.
    if spec.key == "finance":
        from app.services.intelligence import domain_memory_hooks

        await domain_memory_hooks.record_cost_center_budget_gap(user)
    return payload


__all__ = (
    "MAX_SIGNALS",
    "METRIC_LABELS",
    "VIEW_BY_KEY",
    "build_coverage",
    "build_payload",
    "build_signals",
    "domain_wisdom_bit",
    "metric_label",
)
