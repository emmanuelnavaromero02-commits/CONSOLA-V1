from __future__ import annotations

import json
from datetime import UTC, datetime
from types import SimpleNamespace

from refinement.app import publication_public


def test_public_lineage_projects_business_fields_without_paths_or_sql(
    monkeypatch,
) -> None:
    head = {"materialization_run_id": "00000000-0000-0000-0000-000000000001"}
    evidence = {
        "lineage": {
            "source_entity": (
                "s3://lakehouse/raw/sap_successfactors/Employee/"
                "tenant_id=tenant/workspace_id=workspace/data.parquet"
            ),
            "source_load_date": "2026-07-31",
            "source_batch_id": "batch-public",
            "sql_digest": "a" * 64,
            "column_mapping_digest": "b" * 64,
        },
        "row_count": 3,
        "created_at": datetime(2026, 7, 31, tzinfo=UTC),
    }
    monkeypatch.setattr(
        publication_public,
        "PublicationReader",
        lambda: SimpleNamespace(published_head=lambda *_args: head),
    )
    monkeypatch.setattr(
        publication_public,
        "PublicationEvidenceStore",
        lambda: SimpleNamespace(read_exact=lambda *_args: evidence),
    )

    result = publication_public.published_lineage(
        {"name": "employees", "layer": "silver", "cartridge": "sap_successfactors"},
        {
            "tenant_id": "00000000-0000-0000-0000-000000000011",
            "workspace_id": "00000000-0000-0000-0000-000000000012",
        },
    )

    assert result["lineage"][0]["source_entity"] == "Employee"
    serialized = json.dumps(result)
    assert "s3://" not in serialized
    assert "sql_digest" not in serialized
    assert "column_mapping_digest" not in serialized


def test_public_lineage_fails_closed_for_unrecognized_source_path() -> None:
    assert publication_public._public_source_entity("private/path/to/value") == ""
