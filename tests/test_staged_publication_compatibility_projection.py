from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace

from refinement.app import publication_public


SCOPE = {
    "tenant_id": "11111111-1111-1111-1111-111111111111",
    "workspace_id": "22222222-2222-2222-2222-222222222222",
}


def _install_head():
    head = {
        "materialization_run_id": "00000000-0000-0000-0000-000000000001",
        "row_count": 3,
        "published_at": datetime(2026, 8, 2, tzinfo=UTC),
        "status": "published",
    }
    evidence = {
        "catalog": [
            {
                "name": "revenue",
                "type": "BIGINT",
                "description": "Ingresos reconocidos",
                "tags": ["finance"],
                "is_key": False,
                "is_metric": True,
                "example_values": [0, 100],
            }
        ],
        "lineage": {
            "source_entity": "Employee",
            "source_load_date": "2026-08-02",
            "source_batch_id": "batch-public",
            "public_sources": ["Employee"],
            "public_metadata": {
                "description": "Indicadores financieros",
                "relationships": [
                    {
                        "from_column": "employee_id",
                        "to_dataset": "employees",
                        "to_column": "employee_id",
                        "join_hint": "LEFT",
                        "description": "Empleado responsable",
                    }
                ],
            },
        },
        "row_count": 3,
        "created_at": head["published_at"],
    }
    snapshot = SimpleNamespace(
        head=head, evidence=evidence, validate_snapshot=lambda: None
    )
    return SimpleNamespace(published_snapshot=lambda *_args: snapshot)


def test_published_projection_preserves_prior_safe_business_metadata() -> None:
    resolver = _install_head()
    dataset = {"name": "finance", "layer": "gold", "cartridge": "acceptance"}
    metadata = publication_public.published_dataset_metadata(dataset, SCOPE, resolver)
    catalog = publication_public.published_catalog(
        [dataset], SCOPE, tags=["finance"], resolver=resolver
    )
    item = catalog["datasets"]["finance"]
    assert metadata["description"] == "Indicadores financieros"
    assert metadata["sources"] == ["Employee"]
    assert item["columns"][0]["tags"] == ["finance"]
    assert item["columns"][0]["description"] == "Ingresos reconocidos"
    assert item["description"] == "Indicadores financieros"
    assert catalog["relationships"][0]["from_dataset"] == "finance"
    assert catalog["relationships"][0]["to_dataset"] == "employees"
