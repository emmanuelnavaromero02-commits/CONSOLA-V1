from __future__ import annotations

from datetime import datetime, timedelta, timezone

from app.services import copilot_context_service as service
from app.services import pipeline_attention


def _snapshot(pipeline: dict, *, status: str = "ready") -> dict:
    return {
        "status": "ready",
        "sources": [
            {
                "name": "database.operational_counts",
                "status": status,
                "data": {"pipeline": pipeline, "datasets": {"total": 3}},
            }
        ],
    }


def _item(entity: str, status: str, *, cartridge: str = "sap_successfactors", rollup: bool = False) -> dict:
    return {
        "entity": entity,
        "status": status,
        "cartridge_id": cartridge,
        "rollup": rollup,
        "total": 1,
    }


def _pipeline_rec(pipeline: dict) -> dict | None:
    recs = service.build_recommendations_from_snapshot(_snapshot(pipeline))
    return next((item for item in recs if item["category"] == "pipeline"), None)


def _prod_week() -> dict:
    return {
        "window_days": 7,
        "attention": {
            "failed": 0,
            "blocked": 0,
            "partial": 2,
            "affected": [
                _item("EmployeeTime", "partial"),
                _item("talent_attrition_exposure", "partial"),
            ],
        },
    }


def test_partial_week_is_one_warning_card_naming_the_affected_entities():
    rec = _pipeline_rec(_prod_week())

    assert rec is not None
    assert rec["severity"] == "warning"
    assert rec["title"] == "Revisar sincronización parcial"
    assert rec["body"] == (
        "Últimos 7 días: 2 ejecuciones parciales. "
        "Con incidencias: EmployeeTime, talent_attrition_exposure."
    )
    assert rec["action_href"] == "/viewer?type=pipeline&cartridge=sap_successfactors"
    assert rec["evidence"]["counts"] == {"failed": 0, "blocked": 0, "partial": 2}


def test_only_failed_runs_in_the_window_make_the_card_critical():
    pipeline = _prod_week()
    pipeline["attention"]["failed"] = 1
    pipeline["attention"]["affected"].append(_item("PerPerson", "failed"))

    rec = _pipeline_rec(pipeline)

    assert rec["severity"] == "critical"
    assert rec["title"] == "Revisar sincronización con fallas"
    assert rec["body"].startswith(
        "Últimos 7 días: 1 ejecución fallida y 2 ejecuciones parciales."
    )
    assert "Con incidencias: PerPerson, EmployeeTime" in rec["body"]


def test_title_follows_the_worst_status_and_counts_are_named():
    blocked_only = {
        "window_days": 7,
        "attention": {
            "failed": 0,
            "blocked": 1,
            "partial": 0,
            "affected": [_item("prueba", "blocked")],
        },
    }
    rec = _pipeline_rec(blocked_only)

    assert rec["severity"] == "warning"
    assert rec["title"] == "Revisar sincronización bloqueada"
    assert rec["body"] == "Últimos 7 días: 1 ejecución bloqueada. Con incidencias: prueba."

    mixed = _prod_week()
    mixed["attention"]["blocked"] = 2
    assert _pipeline_rec(mixed)["body"].startswith(
        "Últimos 7 días: 2 ejecuciones bloqueadas y 2 ejecuciones parciales."
    )


def test_failed_monitors_alone_do_not_claim_the_sync_failed():
    pipeline = {
        "window_days": 7,
        "attention": {
            "failed": 1,
            "blocked": 0,
            "partial": 1,
            "affected": [
                _item("AgentOpsMonitors", "failed", cartridge="sap_successfactors"),
                _item("DatasetRefreshChain", "partial", rollup=True),
            ],
        },
    }
    rec = _pipeline_rec(pipeline)

    assert rec["severity"] == "critical"
    assert rec["title"] == "Revisar monitores tras la actualización"
    assert "Monitores tras la actualización, Actualización de datasets" in rec["body"]

    pipeline["attention"]["failed"] = 2
    pipeline["attention"]["affected"].append(_item("PerPerson", "failed"))
    assert _pipeline_rec(pipeline)["title"] == "Revisar sincronización con fallas"


def test_fingerprint_is_stable_when_the_counts_change():
    first = _pipeline_rec(_prod_week())
    changed = _prod_week()
    changed["attention"].update({"failed": 4, "blocked": 1, "partial": 9})
    second = _pipeline_rec(changed)

    assert first["fingerprint"] == second["fingerprint"]
    assert first["body"] != second["body"]


def test_clean_window_emits_no_pipeline_card():
    pipeline = {
        "window_days": 7,
        "by_status": {"success": 40},
        "attention": {"failed": 0, "blocked": 0, "partial": 0, "affected": []},
    }
    recs = service.build_recommendations_from_snapshot(_snapshot(pipeline))

    assert [item["category"] for item in recs] == ["console"]


def test_rollups_are_labelled_and_unsafe_names_are_not_listed():
    pipeline = {
        "window_days": 7,
        "attention": {
            "failed": 2,
            "blocked": 0,
            "partial": 1,
            "affected": [
                _item("__extract_all__", "failed", rollup=True),
                _item("owner.name@example.com", "failed"),
                _item("AgentOpsMonitors", "partial"),
            ],
        },
    }
    rec = _pipeline_rec(pipeline)

    assert "Extracción completa" in rec["body"]
    assert "Monitores tras la actualización" in rec["body"]
    assert "example.com" not in rec["body"]


def test_many_entities_are_truncated_and_mixed_cartridges_link_the_whole_view():
    affected = [_item(f"Entity{index}", "partial") for index in range(9)]
    affected.append(_item("Account", "partial", cartridge="hubspot"))
    rec = _pipeline_rec(
        {
            "window_days": 7,
            "attention": {"failed": 0, "blocked": 0, "partial": 10, "affected": affected},
        }
    )

    assert rec["body"].endswith("y 4 más.")
    assert rec["action_href"] == "/viewer?type=pipeline"


def test_card_survives_the_operator_projection_with_body_and_link():
    rec = _pipeline_rec(_prod_week())
    projected = service.project_operator_recommendations(
        {"recommendations": [{**rec, "status": "active"}]}
    )["recommendations"][0]

    assert projected["body"] == rec["body"]
    assert projected["action_href"] == rec["action_href"]
    assert projected["severity"] == "warning"


def test_operational_projection_reports_the_window_and_counts_only():
    projected = service.project_operator_snapshot(
        {
            "status": "ready",
            "sources": [
                {
                    "name": "database.operational_counts",
                    "status": "ready",
                    "data": {"pipeline": {**_prod_week(), "by_status": {"partial": 4}}},
                }
            ],
        }
    )
    pipeline = projected["sources"][0]["diagnostic"]["pipeline"]

    assert pipeline == {
        "by_status": {"partial": 4},
        "window_days": 7,
        "attention": {"failed": 0, "blocked": 0, "partial": 2},
    }


def test_failed_sources_retain_their_recommendation_categories():
    snapshot = {
        "sources": [
            {"name": "database.operational_counts", "status": "failed"},
            {"name": "control_room.ops_summary", "status": "ready"},
            {"name": "control_room.agents_ops", "status": "failed"},
        ]
    }

    assert service.retained_recommendation_categories(snapshot) == [
        "agentops",
        "datasets",
        "pipeline",
    ]


def test_unevaluated_briefing_retains_briefing_cards():
    snapshot = {
        "sources": [
            {"name": "database.operational_counts", "status": "ready", "data": {}},
            {
                "name": "copilot.proactive_briefing",
                "status": "ready",
                "data": {"highlights": [], "evaluated": False},
            },
        ]
    }
    retained = service.retained_recommendation_categories(snapshot)

    assert {"freshness", "volume", "pending", "failures", "briefing"} <= set(retained)
    assert "pipeline" not in retained
    snapshot["sources"][1]["data"] = {"highlights": []}
    assert service.retained_recommendation_categories(snapshot) == []


def _analyzer(category: str, cartridge: str, severity: str = "warning", calls=None):
    async def analyze(user_context=None):
        if calls is not None:
            calls.append((category, user_context))
        return [
            {
                "id": f"{category}:{cartridge}",
                "severity": severity,
                "category": category,
                "cartridge": cartridge,
                "title": f"{category} {cartridge}",
            }
        ]

    return analyze


def _patch_briefing(monkeypatch, *, calls, failing=()):
    proactive_service = service.proactive_service

    async def boom(user_context=None):
        raise RuntimeError("relation missing")

    for name, category in (
        ("analyze_freshness", "freshness"),
        ("analyze_volume_anomaly", "volume"),
        ("analyze_pending_actions", "pending"),
        ("analyze_extraction_failures", "failures"),
    ):
        monkeypatch.setattr(
            proactive_service,
            name,
            boom if category in failing else _analyzer(category, "sap_successfactors", calls=calls),
        )

    async def personal(*_args, **_kwargs):
        raise AssertionError("workspace briefing must not read personal dismissals")

    monkeypatch.setattr(proactive_service, "briefing_for_user", personal)
    cartridges = []

    async def entitled(workspace_id):
        cartridges.append(workspace_id)
        return ["sap_successfactors"]

    monkeypatch.setattr(service, "_workspace_briefing_cartridges", entitled)
    return cartridges


def test_scheduler_and_manual_refresh_evaluate_the_same_workspace_briefing(monkeypatch):
    import asyncio

    calls: list = []
    scoped = _patch_briefing(monkeypatch, calls=calls)
    system = service._system_user("tenant-1", "ws-1")
    manual = {
        "id": 77,
        "role": "viewer",
        "active_tenant_id": "tenant-1",
        "active_workspace_id": "ws-1",
        "allowed_cartridges": ["hubspot"],
    }

    tick = asyncio.run(service._proactive_briefing_for_context(system))
    refresh = asyncio.run(service._proactive_briefing_for_context(manual))

    assert tick == refresh
    assert "unevaluated_categories" not in tick
    assert [item["id"] for item in tick["highlights"]] == [
        "freshness:sap_successfactors",
        "volume:sap_successfactors",
        "pending:sap_successfactors",
        "failures:sap_successfactors",
    ]
    assert scoped == ["ws-1", "ws-1"]
    contexts = [context for _category, context in calls]
    assert all(context["active_workspace_id"] == "ws-1" for context in contexts)
    assert all(context["allowed_cartridges"] == ["sap_successfactors"] for context in contexts)
    assert all(context["role"] not in {"admin", "owner", "super_admin"} for context in contexts)
    snapshot = {
        "sources": [
            {"name": "copilot.proactive_briefing", "status": "ready", "data": tick}
        ]
    }
    assert service.retained_recommendation_categories(snapshot) == []


def test_briefing_drops_cartridges_the_workspace_does_not_have(monkeypatch):
    import asyncio

    _patch_briefing(monkeypatch, calls=[])
    monkeypatch.setattr(
        service.proactive_service, "analyze_freshness", _analyzer("freshness", "replicon")
    )
    tick = asyncio.run(
        service._proactive_briefing_for_context(service._system_user(None, "ws-1"))
    )

    assert "freshness:replicon" not in {item["id"] for item in tick["highlights"]}


def test_failed_briefing_analyzer_keeps_only_its_category(monkeypatch):
    import asyncio

    _patch_briefing(monkeypatch, calls=[], failing=("freshness",))
    tick = asyncio.run(
        service._proactive_briefing_for_context(service._system_user("t", "ws-1"))
    )

    assert tick["unevaluated_categories"] == ["freshness"]
    assert "freshness:sap_successfactors" not in {item["id"] for item in tick["highlights"]}
    snapshot = {
        "sources": [
            {"name": "copilot.proactive_briefing", "status": "ready", "data": tick},
            {
                "name": "control_room.ops_summary",
                "status": "ready",
                "data": {"unevaluated_categories": ["pipeline", "volume"]},
            },
        ]
    }
    assert service.retained_recommendation_categories(snapshot) == ["freshness"]


def test_unreadable_workspace_scope_retains_every_briefing_category(monkeypatch):
    import asyncio

    _patch_briefing(monkeypatch, calls=[])

    async def unavailable(workspace_id):
        raise RuntimeError("entitlements unavailable")

    monkeypatch.setattr(service, "_workspace_briefing_cartridges", unavailable)
    source = asyncio.run(
        service._source(
            "copilot.proactive_briefing",
            lambda: service._proactive_briefing_for_context(service._system_user("t", "ws-1")),
        )
    )

    assert source["status"] == "failed"
    retained = service.retained_recommendation_categories({"sources": [source]})
    assert {"freshness", "volume", "pending", "failures", "briefing"} <= set(retained)


def test_daily_failure_alert_counts_only_extraction_attempts():
    import asyncio
    import re
    from contextlib import asynccontextmanager

    from app.services import proactive_service

    captured: dict = {}

    class _Conn:
        async def fetch(self, sql, *args):
            captured["sql"], captured["args"] = sql, args
            return [{"cartridge_id": "sap_successfactors", "failures": 1}]

    @asynccontextmanager
    async def scoped(_pool, _user):
        yield _Conn(), "tenant-1", "ws-1"

    async def pool():
        return object()

    user = {
        "role": "tenant_admin",
        "active_tenant_id": "tenant-1",
        "active_workspace_id": "ws-1",
        "allowed_cartridges": ["sap_successfactors"],
    }
    original = (proactive_service.scoped_db_for_user, proactive_service.auth.pool)
    proactive_service.scoped_db_for_user = scoped
    proactive_service.auth.pool = pool
    try:
        highlights = asyncio.run(proactive_service.analyze_extraction_failures(user))
    finally:
        proactive_service.scoped_db_for_user, proactive_service.auth.pool = original

    assert [(item["title"], item["body"]) for item in highlights] == [
        (
            "1 extracción fallida en sap_successfactors",
            "Hubo 1 extracción fallida en sap_successfactors en las últimas 24 horas.",
        )
    ]
    excluded = set(captured["args"][2])
    assert excluded == {
        "AgentOpsMonitors",
        "AgentRunner",
        "DatasetRefreshChain",
        "EntityScheduler",
        "SuccessFactorsDailyExtract",
    }
    assert captured["args"][3] == ["sap_successfactors"]
    sql = re.sub(r"\s+", " ", captured["sql"])
    assert "AND NOT (pipeline_runs.entity = ANY($3::text[]))" in sql
    assert "cartridge_id = ANY($4::text[])" in sql
    assert "$5" not in sql


def test_attention_summary_ignores_unknown_statuses():
    summary = pipeline_attention.attention_summary(
        [
            {"status": "failed", "cartridge_id": "c", "entity": "A", "rollup": False, "total": 2},
            {"status": "running", "cartridge_id": "c", "entity": "B", "rollup": False, "total": 5},
            {"status": "partial", "cartridge_id": "c", "entity": "__x__", "rollup": True, "total": 1},
        ]
    )

    assert (summary["failed"], summary["blocked"], summary["partial"]) == (2, 0, 1)
    assert [item["entity"] for item in summary["affected"]] == ["A", "__x__"]


def test_attention_sql_excludes_disabled_entities_and_explained_rollups():
    sql = pipeline_attention.ATTENTION_SQL
    assert "ec.enabled IS FALSE" in sql
    assert "c.severity_rank >= w.severity_rank" in sql
    assert "AND NOT c.process" in sql
    assert "make_interval(days => $3::int)" in sql
    assert "ec.enabled" not in pipeline_attention.ATTENTION_SQL_WITHOUT_ENTITY_CONFIG
    assert "AgentOpsMonitors" not in pipeline_attention.ROLLUP_ENTITIES
    assert "AgentOpsMonitors" in pipeline_attention.PROCESS_ENTITIES
    for text in (sql, pipeline_attention.ATTENTION_SQL_WITHOUT_ENTITY_CONFIG):
        assert "entity_idempotency_key" in text and "{row}" not in text


def test_mirror_predicate_accepts_only_known_aliases():
    import pytest

    assert "pipeline_runs.run_id IN" in pipeline_attention.mirror_duplicate_predicate(
        "pipeline_runs"
    )
    with pytest.raises(ValueError):
        pipeline_attention.mirror_duplicate_predicate("x; DROP TABLE y")


def test_dashboard_dismissal_applies_only_to_the_current_episode():
    from app.routers import copilot

    start = datetime(2026, 10, 1, tzinfo=timezone.utc)
    item = {"first_seen_at": start.isoformat()}

    assert copilot._dismissed_this_episode(item, start + timedelta(hours=1)) is True
    assert copilot._dismissed_this_episode(item, start - timedelta(days=3)) is False
    assert copilot._dismissed_this_episode(item, None) is False


def test_live_dismissal_keys_are_workspace_bound():
    from app.services import proactive_service

    assert proactive_service.live_dismissal_key("live:abc", "ws-a") == "live:abc@ws-a"
    assert proactive_service.live_dismissal_key("live:abc", None) == "live:abc@none"
