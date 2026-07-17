import asyncio
from contextlib import ExitStack
from importlib import import_module
from unittest.mock import AsyncMock, patch

import pytest

from app.routers import control_room as routes

USER = {
    "id": 7,
    "role": "super_admin",
    "active_tenant_id": "tenant-A",
    "active_workspace_id": "workspace-A",
    "allowed_cartridges": ["sap_hcm", "sap_successfactors"],
}
ITEM_ID = routes.control_room_service._encode_id(
    {
        "dataset": "employees_anomalies",
        "type": "terminated_but_active",
        "entity": "1001",
    }
)


class MutationSentinel:
    def __init__(self) -> None:
        self.attempts: list[str] = []

    @staticmethod
    def _statement(query: str) -> str:
        return " ".join(str(query).split()).upper()

    async def execute(self, query: str, *_args):
        statement = self._statement(query)
        if statement.startswith("SELECT SET_CONFIG"):
            return None
        if statement.startswith(("INSERT ", "UPDATE ", "DELETE ")):
            self.attempts.append(statement)
            raise AssertionError(f"DML attempted from GET: {statement[:80]}")
        return None

    async def fetch(self, query: str, *_args):
        statement = self._statement(query)
        assert not statement.startswith(("INSERT ", "UPDATE ", "DELETE "))
        return []

    async def fetchrow(self, query: str, *_args):
        await self.fetch(query, *_args)
        return None

    async def fetchval(self, query: str, *_args):
        await self.fetch(query, *_args)
        return 0

    def add(self, *_args):
        self.attempts.append("ADD")
        raise AssertionError("ORM add attempted from GET")

    def delete(self, *_args):
        self.attempts.append("DELETE")
        raise AssertionError("ORM delete attempted from GET")

    async def flush(self):
        self.attempts.append("FLUSH")
        raise AssertionError("ORM flush attempted from GET")

    async def commit(self):
        self.attempts.append("COMMIT")
        raise AssertionError("commit attempted from GET")


GET_CASES = {
    "/api/control-room/summary": lambda: routes.control_room_summary(user=USER),
    "/api/control-room/dashboard": lambda: routes.control_room_dashboard(user=USER),
    "/api/control-room/sap-successfactors/gold-kpis": lambda: routes.control_room_sap_successfactors_gold_kpis(
        user=USER
    ),
    "/api/control-room/sap-successfactors/talent-kpis": lambda: routes.control_room_sap_successfactors_talent_kpis(
        user=USER
    ),
    "/api/control-room/sap-successfactors/talent/overview": lambda: routes.control_room_sap_successfactors_talent_overview(
        user=USER
    ),
    "/api/control-room/sap-successfactors/talent/9box": lambda: routes.control_room_sap_successfactors_talent_9box(
        user=USER
    ),
    "/api/control-room/sap-successfactors/talent/9box/{box_id}": lambda: routes.control_room_sap_successfactors_talent_9box_box(
        "core", user=USER
    ),
    "/api/control-room/sap-successfactors/talent/anomalies": lambda: routes.control_room_sap_successfactors_talent_anomalies(
        user=USER
    ),
    "/api/control-room/sap-successfactors/talent/metadata-readiness": lambda: routes.control_room_sap_successfactors_talent_metadata_readiness(
        user=USER
    ),
    "/api/control-room/banxico/readiness": lambda: routes.control_room_banxico_readiness(
        user=USER
    ),
    "/api/control-room/inegi/readiness": lambda: routes.control_room_inegi_readiness(
        user=USER
    ),
    "/api/control-room/sec-edgar/readiness": lambda: routes.control_room_sec_edgar_readiness(
        user=USER
    ),
    "/api/control-room/sap-successfactors/market-validation": lambda: routes.control_room_sap_successfactors_market_validation(
        user=USER
    ),
    "/api/control-room/ops/summary": lambda: routes.control_room_ops_summary(user=USER),
    "/api/control-room/agents/ops": lambda: routes.control_room_agents_ops(
        limit=12, user=USER
    ),
    "/api/control-room/decision-intelligence/runs": lambda: routes.control_room_decision_intelligence_runs(
        limit=50, user=USER
    ),
    "/api/control-room/decision-intelligence/runs/{run_id}": lambda: routes.control_room_decision_intelligence_run_detail(
        "run-1", user=USER
    ),
    "/api/control-room/decision-intelligence/history": lambda: routes.control_room_decision_intelligence_history(
        limit=100, user=USER
    ),
    "/api/control-room/decision-intelligence/calibration": lambda: routes.control_room_decision_intelligence_calibration(
        min_outcomes_required=10, user=USER
    ),
    "/api/control-room/alerts": lambda: routes.control_room_alerts(user=USER),
    "/api/control-room/anomalies": lambda: routes.control_room_anomalies(user=USER),
    "/api/control-room/items/{item_id}": lambda: routes.control_room_item_detail(
        ITEM_ID, user=USER
    ),
    "/api/control-room/items/{item_id}/impact": lambda: routes.control_room_item_impact(
        ITEM_ID, user=USER
    ),
    "/api/control-room/items/{item_id}/activity": lambda: routes.control_room_item_activity(
        ITEM_ID, user=USER
    ),
    "/api/control-room/items/{item_id}/action-runs": lambda: routes.control_room_item_action_runs(
        ITEM_ID, user=USER
    ),
    "/api/control-room/items/{item_id}/outcomes": lambda: routes.control_room_item_outcomes(
        ITEM_ID, user=USER
    ),
    "/api/control-room/anomalies/{anomaly_id}": lambda: routes.control_room_anomaly_detail(
        ITEM_ID, user=USER
    ),
    "/api/control-room/thresholds": lambda: routes.control_room_thresholds(user=USER),
    "/api/control-room/lessons": lambda: routes.control_room_lessons(
        cartridge_id="sap_successfactors",
        anomaly_type="risk",
        item_id=ITEM_ID,
        user=USER,
    ),
}


FETCHER_READS = (
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


async def _dataset_fetcher(dataset: str, _user, _limit: int) -> list[dict]:
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


async def _installations(_user):
    return [
        {
            "cartridge_id": "sap_hcm",
            "installation_status": "ready",
            "connection_id": "hcm-test",
            "auth_method": "test",
        }
    ]


def _with_dataset_fetcher(fn):
    async def _read(*args, **kwargs):
        kwargs.setdefault("fetcher", _dataset_fetcher)
        return await fn(*args, **kwargs)

    return _read


async def _external_read(*_args, **_kwargs):
    return {"status": "ready"}


def _stable_payload(value):
    if isinstance(value, dict):
        return {
            key: _stable_payload(item)
            for key, item in value.items()
            if key
            not in {"checked_at", "due_at", "generated_at", "latency_ms", "updated_at"}
        }
    if isinstance(value, list):
        return [_stable_payload(item) for item in value]
    return value


def _install_read_stubs(stack: ExitStack, sentinel: MutationSentinel) -> None:
    readiness_modules = [
        import_module(f"app.services.{name}_readiness")
        for name in ("banxico", "inegi", "sec_edgar")
    ]
    for name in FETCHER_READS:
        original = getattr(routes.control_room_service, name)
        stack.enter_context(
            patch.object(
                routes.control_room_service,
                name,
                new=_with_dataset_fetcher(original),
            )
        )
    stack.enter_context(
        patch.object(
            routes.control_room_service,
            "query_dataset_rows",
            new=_dataset_fetcher,
        )
    )
    stack.enter_context(
        patch.object(
            routes.control_room_service,
            "_installed_cartridges",
            new=AsyncMock(side_effect=_installations),
        )
    )
    for target, name in (
        (routes.market_decision_validation, "get_validation"),
        (routes.intelligence_history, "list_runs"),
        (routes.intelligence_history, "get_run"),
        (routes.intelligence_history, "list_history"),
        (routes.intelligence_history, "calibration_report"),
    ):
        stack.enter_context(patch.object(target, name, new=_external_read))
    for target in readiness_modules:
        stack.enter_context(
            patch.object(
                target,
                "query_gold_dataset_rows",
                new=AsyncMock(return_value=[]),
            )
        )
    stack.enter_context(
        patch.object(
            routes.control_room_service.auth,
            "pool",
            new=AsyncMock(return_value=sentinel),
        )
    )
    stack.enter_context(
        patch.dict("os.environ", {"OMEGA_CONTROL_ROOM_CACHE_TTL_SECONDS": "0"})
    )


@pytest.mark.asyncio
async def test_all_29_control_room_get_routes_are_pure_repeatable_and_concurrent():
    discovered = {
        route.path
        for route in routes.router.routes
        if "GET" in (route.methods or set())
    }
    assert len(GET_CASES) == 29
    assert discovered == set(GET_CASES)
    sentinel = MutationSentinel()
    routes._CONTROL_ROOM_READ_CACHE.clear()
    routes._CONTROL_ROOM_READ_CACHE_LOCKS.clear()

    with ExitStack() as stack:
        _install_read_stubs(stack, sentinel)
        for call in GET_CASES.values():
            first = await call()
            second = await call()
            concurrent = await asyncio.gather(call(), call())
            assert _stable_payload(first) == _stable_payload(second)
            assert _stable_payload(first) == _stable_payload(concurrent[0])
            assert _stable_payload(first) == _stable_payload(concurrent[1])

    assert sentinel.attempts == []
