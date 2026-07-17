from __future__ import annotations

from contextlib import ExitStack, contextmanager
from importlib import import_module
from unittest.mock import patch

from app.routers import control_room as routes

from control_room_get_harness import ConcurrencyProbe, MutationSentinel


DEFAULT_FETCHER_READS = (
    "summary",
    "dashboard",
    "list_alerts",
    "list_anomalies",
    "get_item",
    "get_item_impact",
    "get_item_activity",
    "list_item_action_runs",
    "list_item_outcomes",
    "get_anomaly",
)


def _async_value(value):
    async def _value():
        return value

    return _value


@contextmanager
def installed_read_edges(sentinel: MutationSentinel, probe: ConcurrencyProbe):
    async def dataset_fetcher(dataset: str, _user, _limit: int):
        await probe.checkpoint()
        if dataset == "employees_anomalies":
            return [
                {
                    "pernr": "1001",
                    "full_name": "Ana Gomez",
                    "anomaly_type": "terminated_but_active",
                    "severity": "critical",
                    "details": {"salary_monthly_usd": 4200},
                    "detected_at": "2026-07-16T10:00:00Z",
                }
            ]
        return []

    async def empty_dataset(*_args, **_kwargs):
        return []

    service = routes.control_room_service
    originals = {
        name: getattr(service, name).__kwdefaults__ for name in DEFAULT_FETCHER_READS
    }
    with ExitStack() as stack:
        for name in DEFAULT_FETCHER_READS:
            function = getattr(service, name)
            function.__kwdefaults__ = {
                **(function.__kwdefaults__ or {}),
                "fetcher": dataset_fetcher,
            }
        stack.callback(
            lambda: [
                setattr(getattr(service, name), "__kwdefaults__", defaults)
                for name, defaults in originals.items()
            ]
        )
        stack.enter_context(
            patch.object(service, "query_dataset_rows", new=dataset_fetcher)
        )
        stack.enter_context(
            patch.object(service.auth, "pool", new=_async_value(sentinel))
        )
        stack.enter_context(
            patch.object(
                service,
                "_vault_connections_for_cartridge",
                new=empty_dataset,
            )
        )
        market = import_module("app.services.intelligence.market_decision_validation")
        stack.enter_context(
            patch.object(
                market,
                "query_intelligence_dataset_rows",
                new=empty_dataset,
            )
        )
        for name in ("banxico", "inegi", "sec_edgar"):
            module = import_module(f"app.services.{name}_readiness")
            stack.enter_context(
                patch.object(
                    module,
                    "query_gold_dataset_rows",
                    new=empty_dataset,
                )
            )
        stack.enter_context(
            patch.dict(
                "os.environ",
                {"OMEGA_CONTROL_ROOM_CACHE_TTL_SECONDS": "0"},
            )
        )
        yield dataset_fetcher
