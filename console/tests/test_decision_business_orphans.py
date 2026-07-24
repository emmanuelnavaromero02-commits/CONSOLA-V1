from app.services.control_room.business_projection import filter_business_decisions


def test_manual_unlinked_decision_stays_and_control_room_orphan_is_hidden():
    decisions = [
        {"id": 1, "kpis": [{"provenance": {"origin": "manual"}}]},
        {"id": 2, "kpis": [{"provenance": {"origin": "control_room"}}]},
        {"id": 3, "kpis": [{"source": "control_room"}]},
    ]

    assert filter_business_decisions(decisions, []) == [decisions[0]]


def test_any_legacy_control_room_marker_hides_orphan_regardless_of_order():
    decision = {
        "id": 1,
        "kpis": [
            {"provenance": {"origin": "manual"}},
            {"source": "control_room"},
        ],
    }

    assert filter_business_decisions([decision], []) == []
