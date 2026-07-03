from __future__ import annotations

from app.domains.data_platform.lineage_payloads import lineage_graph_payload


def test_lineage_graph_payload_links_raw_and_dataset_sources():
    payload = lineage_graph_payload(
        [
            {
                "name": "employee_latest",
                "layer": "silver",
                "cartridge": "sap_successfactors",
                "sources": ["raw/sap_successfactors/User"],
            },
            {
                "name": "talent_signals",
                "layer": "gold",
                "cartridge": "sap_successfactors",
                "sources": ["employee_latest"],
            },
        ],
        source_visible=lambda _source: True,
    )

    nodes = {node["id"]: node for node in payload["nodes"]}
    assert "raw:sap_successfactors/User" in nodes
    assert "ds:employee_latest" in nodes
    assert "ds:talent_signals" in nodes
    assert {"from": "raw:sap_successfactors/User", "to": "ds:employee_latest"} in payload[
        "edges"
    ]
    assert {"from": "ds:employee_latest", "to": "ds:talent_signals"} in payload[
        "edges"
    ]


def test_lineage_graph_payload_filters_hidden_sources():
    payload = lineage_graph_payload(
        [
            {
                "name": "employee_latest",
                "layer": "silver",
                "cartridge": "sap_successfactors",
                "sources": ["raw/sap_successfactors/User"],
            }
        ],
        source_visible=lambda _source: False,
    )

    assert payload["nodes"] == [
        {
            "id": "ds:employee_latest",
            "label": "employee_latest",
            "type": "silver",
            "cartridge": "sap_successfactors",
            "is_stale": False,
            "staleness_reason": None,
            "row_count": None,
            "last_refresh": None,
        }
    ]
    assert payload["edges"] == []
