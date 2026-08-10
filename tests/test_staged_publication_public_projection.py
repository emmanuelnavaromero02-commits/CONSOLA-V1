from __future__ import annotations

import json
from datetime import UTC, datetime
from types import SimpleNamespace

from refinement.app import publication_public


def test_public_lineage_projects_business_fields_without_paths_or_sql() -> None:
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
    resolver = SimpleNamespace(
        published_snapshot=lambda *_args: SimpleNamespace(
            head=head,
            evidence=evidence,
            scope=SimpleNamespace(layer="silver"),
        )
    )

    result = publication_public.published_lineage(
        {"name": "employees", "layer": "silver", "cartridge": "sap_successfactors"},
        {
            "tenant_id": "00000000-0000-0000-0000-000000000011",
            "workspace_id": "00000000-0000-0000-0000-000000000012",
        },
        resolver,
    )

    assert result["lineage"][0]["source_entity"] == "raw/sap_successfactors/Employee"
    serialized = json.dumps(result)
    assert "s3://" not in serialized
    assert "sql_digest" not in serialized
    assert "column_mapping_digest" not in serialized


def test_public_lineage_fails_closed_for_unrecognized_source_path() -> None:
    assert publication_public._public_source_entity("private/path/to/value") == ""


def test_catalog_columns_and_relationships_share_one_pinned_snapshot() -> None:
    published_at = datetime(2026, 8, 1, tzinfo=UTC)
    snapshots = [
        SimpleNamespace(
            head={"status": "published", "row_count": 1, "published_at": published_at},
            evidence={
                "catalog": [{"name": "column_a", "type": "INTEGER"}],
                "lineage": {
                    "public_metadata": {
                        "relationships": [
                            {
                                "from_column": "column_a",
                                "to_dataset": "dimension_a",
                                "to_column": "id",
                            }
                        ]
                    }
                },
            },
            validate_snapshot=lambda: None,
        ),
        SimpleNamespace(
            head={"status": "published", "row_count": 2, "published_at": published_at},
            evidence={
                "catalog": [{"name": "column_b", "type": "TEXT"}],
                "lineage": {
                    "public_metadata": {
                        "relationships": [
                            {
                                "from_column": "column_b",
                                "to_dataset": "dimension_b",
                                "to_column": "id",
                            }
                        ]
                    }
                },
            },
            validate_snapshot=lambda: None,
        ),
    ]

    class Resolver:
        calls = 0

        def published_snapshots(self, datasets, *_args):
            value = snapshots[min(self.calls, 1)]
            self.calls += 1
            return [value for _ in datasets]

    resolver = Resolver()
    result = publication_public.published_catalog(
        [{"name": "fact", "layer": "gold", "cartridge": "acceptance"}],
        {
            "tenant_id": "00000000-0000-0000-0000-000000000011",
            "workspace_id": "00000000-0000-0000-0000-000000000012",
        },
        resolver=resolver,
    )

    assert resolver.calls == 1
    assert [item["name"] for item in result["datasets"]["fact"]["columns"]] == [
        "column_a"
    ]
    assert result["relationships"][0]["to_dataset"] == "dimension_a"
