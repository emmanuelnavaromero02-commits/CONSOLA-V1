from app.services.copilot_context_service import build_recommendations_from_snapshot


def _snapshot(*sources):
    return {
        "status": "partial",
        "sources": list(sources),
    }


def _source(name, data):
    return {
        "name": name,
        "status": "ready",
        "data": data,
    }


def test_live_context_recommends_control_room_and_pipeline_actions():
    recommendations = build_recommendations_from_snapshot(
        _snapshot(
            _source(
                "control_room.ops_summary",
                {"open_items_by_severity": {"critical": 2, "high": 1}},
            ),
            _source(
                "database.operational_counts",
                {"pipeline": {"by_status": {"partial": 3, "failed": 1}}},
            ),
        )
    )

    titles = {item["title"] for item in recommendations}
    assert "Revisar señales prioritarias" in titles
    assert "Revisar sincronización parcial" in titles
    assert recommendations[0]["severity"] == "critical"


def test_live_context_recommends_talent_metadata_follow_up():
    recommendations = build_recommendations_from_snapshot(
        _snapshot(
            _source(
                "control_room.sap_successfactors_talent_kpis",
                {
                    "readiness": {"status": "insufficient_data"},
                    "blockers": [{"component": "performance"}],
                },
            ),
            _source(
                "control_room.sap_successfactors_talent_metadata_readiness",
                {
                    "status": "partial",
                    "summary": {
                        "blocked_entities": 2,
                        "live_required_total": 3,
                        "live_required_ready": 1,
                    },
                },
            ),
        )
    )

    fingerprints = {item["fingerprint"] for item in recommendations}
    assert len(fingerprints) == len(recommendations)
    assert any(item["category"] == "sap_successfactors" for item in recommendations)
    assert any("metadata" in item["title"].lower() for item in recommendations)


def test_live_context_has_healthy_fallback_when_no_findings():
    recommendations = build_recommendations_from_snapshot(_snapshot())

    assert recommendations == [
        {
            "fingerprint": recommendations[0]["fingerprint"],
            "severity": "success",
            "category": "console",
            "title": "Sin acciones urgentes",
            "body": "No se detectaron bloqueos críticos en el corte operativo actual.",
            "evidence": {"snapshot_status": "partial"},
            "action_label": "Ver consola",
            "action_href": "/control-room",
            "action_kind": "navigate",
            "required_permission": "monitor.read",
        }
    ]
