"""Behavioral tests for the Studio cartridge Autopilot.

These assert the autopilot turns an introspected schema into a COMPLETE,
coherent cartridge blueprint (entities+datasets+KBs+agent+semantics) with
real semantic auto-mapping (PII/money/key/date), in the exact shape
``cartridge_service.create_full_cartridge`` consumes.
"""
from __future__ import annotations

from app.services import cartridge_autopilot as ap


def _hubspot_like_schema() -> list[dict]:
    return [
        {
            "name": "deals",
            "fields": [
                {"name": "deal_id", "type": "string", "nullable": False, "primary_key": True},
                {"name": "amount", "type": "float", "nullable": True, "primary_key": False},
                {"name": "owner_email", "type": "string", "nullable": True, "primary_key": False},
                {"name": "close_date", "type": "date", "nullable": True, "primary_key": False},
                {"name": "stage", "type": "string", "nullable": True, "primary_key": False},
            ],
        },
        {
            "name": "contacts",
            "fields": [
                {"name": "id", "type": "string", "nullable": False, "primary_key": True},
                {"name": "email", "type": "string", "nullable": True, "primary_key": False},
                {"name": "created_at", "type": "timestamp", "nullable": True, "primary_key": False},
            ],
        },
    ]


def test_classify_field_detects_roles():
    assert ap.classify_field({"name": "amount", "type": "float"})["role"] == "money"
    assert ap.classify_field({"name": "owner_email", "type": "string"})["role"] == "pii"
    assert ap.classify_field({"name": "owner_email", "type": "string"})["protected"] is True
    assert ap.classify_field({"name": "deal_id", "type": "string", "primary_key": True})["role"] == "key"
    assert ap.classify_field({"name": "close_date", "type": "date"})["role"] == "date"
    assert ap.classify_field({"name": "score", "type": "int"})["role"] == "metric"
    assert ap.classify_field({"name": "stage", "type": "string"})["role"] == "dimension"


def test_build_blueprint_full_shape():
    bp = ap.build_blueprint(
        cartridge_id="demo_crm",
        name="Demo CRM",
        entities=_hubspot_like_schema(),
        pattern="rest",
        category="crm",
    )
    # Manifest-level fields create_full_cartridge needs.
    for key in ("id", "name", "pattern", "category", "bronze_path", "entities",
                "datasets", "kbs", "agents", "semantic_terms", "dags"):
        assert key in bp, f"missing manifest key {key}"
    assert bp["id"] == "demo_crm"
    assert bp["bronze_path"] == "raw/demo_crm"

    # 2 entities -> 2 silver + at least 1 gold (deals has money).
    layers = [d["layer"] for d in bp["datasets"]]
    assert layers.count("silver") == 2
    assert layers.count("gold") >= 1

    # Gold derives from the money entity (deals.amount).
    gold = next(d for d in bp["datasets"] if d["layer"] == "gold")
    assert "total_amount" in gold["sql"]
    assert "silver_deals" in gold["sql"]

    # An agent + KBs per entity + semantic terms exist.
    assert len(bp["agents"]) == 1
    assert bp["agents"][0]["slug"] == "demo_crm_watchdog"
    assert len(bp["kbs"]) == 2
    assert any(t["term"] == "amount" for t in bp["semantic_terms"])


def test_pii_is_flagged_for_encryption():
    bp = ap.build_blueprint(
        cartridge_id="demo_crm", name="Demo CRM", entities=_hubspot_like_schema()
    )
    deals = next(e for e in bp["entities"] if e["entity"] == "deals")
    assert "owner_email" in deals["protection"]["encrypt"]
    contacts = next(e for e in bp["entities"] if e["entity"] == "contacts")
    assert "email" in contacts["protection"]["encrypt"]


def test_silver_sql_is_silver_shaped():
    """Silver SQL must satisfy refinement.llm_sql Silver validator contract."""
    bp = ap.build_blueprint(
        cartridge_id="demo_crm", name="Demo CRM", entities=_hubspot_like_schema()
    )
    silver = next(d for d in bp["datasets"] if d["name"] == "silver_deals")
    sql = silver["sql"].lower()
    assert "read_parquet" in sql
    assert "{latest_date}" in silver["sql"]
    # deals has a primary key -> dedup with ROW_NUMBER.
    assert "row_number()" in sql


def test_gold_sql_not_silver_shaped():
    """Gold SQL must NOT carry the Silver-only markers (reads silver table)."""
    bp = ap.build_blueprint(
        cartridge_id="demo_crm", name="Demo CRM", entities=_hubspot_like_schema()
    )
    gold = next(d for d in bp["datasets"] if d["layer"] == "gold")
    assert "read_parquet" not in gold["sql"].lower()
    assert "{latest_date}" not in gold["sql"]


def test_incremental_mode_from_watermark():
    bp = ap.build_blueprint(
        cartridge_id="demo_crm", name="Demo CRM", entities=_hubspot_like_schema()
    )
    deals = next(e for e in bp["entities"] if e["entity"] == "deals")
    # close_date is a date field -> entity becomes incremental with a watermark.
    assert deals["mode"] == "incremental"
    assert deals["watermark_field"] in {"close_date"}


def test_summary_counts():
    bp = ap.build_blueprint(
        cartridge_id="demo_crm", name="Demo CRM", entities=_hubspot_like_schema()
    )
    s = ap.summarize_blueprint(bp)
    assert s["entities"] == 2
    assert s["silver"] == 2
    assert s["gold"] >= 1
    assert s["agents"] == 1
    assert "owner_email" in s["pii_protected"]


def test_partial_fields_without_nullable_or_pk():
    """Regression: introspection often yields fields lacking nullable/primary_key.

    The autopilot must not KeyError on a 'raw' schema — it should default
    nullable=True / primary_key=False and still emit a coherent blueprint.
    """
    bp = ap.build_blueprint(
        cartridge_id="sf",
        name="SF",
        entities=[{
            "name": "opportunity",
            "fields": [
                {"name": "opp_id", "type": "string", "primary_key": True},
                {"name": "amount", "type": "float"},        # no nullable/pk keys
                {"name": "close_date", "type": "date"},      # no nullable/pk keys
            ],
        }],
    )
    ent = bp["entities"][0]
    # defaults applied, shape intact
    amount = next(f for f in ent["fields"] if f["name"] == "amount")
    assert amount["nullable"] is True
    assert amount["primary_key"] is False
    assert any(d["layer"] == "gold" for d in bp["datasets"])


def test_requires_entities():
    try:
        ap.build_blueprint(cartridge_id="x", name="X", entities=[])
        assert False, "should have raised"
    except ValueError:
        pass
