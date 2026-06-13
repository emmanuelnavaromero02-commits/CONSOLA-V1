from __future__ import annotations

from typing import Any

from app.services.intelligence import engine as _engine
from app.services.intelligence.baseline import build_metric_artifacts
from app.services.intelligence.backtesting import (
    get_backtest,
    get_backtest_results,
    list_backtests,
    run_backtest,
)
from app.services.intelligence.contracts import load_contracts
from app.services.intelligence.external import list_sources, patch_source, run_sources
from app.services.intelligence.history import (
    calibration_report,
    get_run,
    list_history,
    list_runs,
)
from app.services.intelligence.persistence import (
    get_signal,
    list_signals,
    record_outcome,
    select_option,
)
from app.services.intelligence.utils import DatasetFetcher


async def run_intelligence(
    user: dict,
    body: dict[str, Any] | None = None,
    *,
    fetcher: DatasetFetcher | None = None,
    persist: bool = True,
) -> dict[str, Any]:
    # Keep old tests/extensions that monkeypatch app.services.intelligence_engine.load_contracts working.
    _engine.load_contracts = load_contracts
    return await _engine.run_intelligence(user, body, fetcher=fetcher, persist=persist)


__all__ = [
    "build_metric_artifacts",
    "calibration_report",
    "get_backtest",
    "get_backtest_results",
    "get_signal",
    "get_run",
    "list_backtests",
    "list_history",
    "list_runs",
    "list_signals",
    "list_sources",
    "load_contracts",
    "patch_source",
    "run_backtest",
    "record_outcome",
    "run_intelligence",
    "run_sources",
    "select_option",
]
