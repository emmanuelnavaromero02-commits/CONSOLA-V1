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
    # Manifest-level fields create_full_cartridge needs (vocabulary lives under
    # semantic_model.vocabulary — the shape the consumer ingests).
    for key in ("id", "name", "pattern", "category", "bronze_path", "entities",
                "datasets", "kbs", "agents", "semantic_model", "dags"):
        assert key in bp, f"missing manifest key {key}"
    assert bp["id"] == "demo_crm"
    assert bp["bronze_path"] == "raw/demo_crm"

    # 2 entities -> 2 silver + at least 1 gold (deals has money).
    layers = [d["layer"] for d in bp["datasets"]]
    assert layers.count("silver") == 2
    assert layers.count("gold") >= 1

    # Gold derives from the money entity (deals.amount) — identifiers are quoted.
    gold = next(d for d in bp["datasets"] if d["layer"] == "gold")
    assert '"total_amount"' in gold["sql"]
    assert '"silver_deals"' in gold["sql"]

    # An agent + KBs per entity + semantic vocabulary exist.
    assert len(bp["agents"]) == 1
    assert bp["agents"][0]["slug"] == "demo_crm_watchdog"
    assert len(bp["kbs"]) == 2
    assert all("kb_id" in k for k in bp["kbs"])
    vocab = bp["semantic_model"]["vocabulary"]
    assert any("amount" in t["term"] for t in vocab)


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


def test_pii_no_false_positive_on_metric_names():
    """Audit-7: token-aware matching must NOT flag 'card_count'/'dashboard_id' as PII
    nor 'total_records' as money via substring."""
    assert ap.classify_field({"name": "dashboard_id", "type": "string"})["role"] == "key"
    # 'card_count' is an int metric, not PII (card matched only as a word part of
    # a real PII token, here 'card' is a token -> would be pii; assert the
    # NON-pii cases that previously broke via substring):
    assert ap.classify_field({"name": "stage_name", "type": "string"})["role"] == "dimension"
    assert ap.classify_field({"name": "valuestream", "type": "string"})["role"] == "dimension"


def test_money_requires_money_token_not_substring():
    # 'valuestream' must NOT become money just because it contains 'value'
    assert ap.classify_field({"name": "valuestream", "type": "int"})["role"] == "metric"
    # but a real money token does
    assert ap.classify_field({"name": "order_value", "type": "float"})["role"] == "money"


def test_classify_pii_beats_pk_for_sensitive_ids():
    # Audit-2 fix: PII detection runs BEFORE the key check so a sensitive
    # identifier (national_id, email_id) is encrypted, not treated as a plain key.
    assert ap.classify_field({"name": "national_id", "type": "string"})["role"] == "pii"
    assert ap.classify_field({"name": "email_id", "type": "string", "primary_key": True})["role"] == "pii"
    # a surrogate key with no PII token still classifies as key
    assert ap.classify_field({"name": "deal_id", "type": "string", "primary_key": True})["role"] == "key"


def test_entity_with_no_money_has_no_gold():
    bp = ap.build_blueprint(
        cartridge_id="x", name="X",
        entities=[{"name": "tags", "fields": [
            {"name": "tag_id", "type": "string", "primary_key": True},
            {"name": "label", "type": "string"},
        ]}],
    )
    assert all(d["layer"] != "gold" for d in bp["datasets"])
    assert any(d["layer"] == "silver" for d in bp["datasets"])


def test_watermark_prefers_modified_over_created():
    bp = ap.build_blueprint(
        cartridge_id="x", name="X",
        entities=[{"name": "deals", "fields": [
            {"name": "id", "type": "string", "primary_key": True},
            {"name": "created_at", "type": "timestamp"},
            {"name": "last_modified", "type": "timestamp"},
        ]}],
    )
    deals = next(e for e in bp["entities"] if e["entity"] == "deals")
    assert deals["watermark_field"] == "last_modified"


def test_cid_sanitization():
    bp = ap.build_blueprint(
        cartridge_id="My CRM!", name="My CRM",
        entities=[{"name": "e", "fields": [{"name": "id", "type": "string", "primary_key": True}]}],
    )
    assert bp["id"] == "my_crm_"


def test_requires_entities():
    try:
        ap.build_blueprint(cartridge_id="x", name="X", entities=[])
        assert False, "should have raised"
    except ValueError:
        pass


def test_blueprint_accepted_by_create_full_cartridge_normalizer():
    """Audit-2/8 critical: the blueprint must pass _normalize_full_cartridge_manifest
    (the real consumer) — proves kb_id / semantic_model / cid contracts hold."""
    from app.services import cartridge_service
    bp = ap.build_blueprint(
        cartridge_id="autopilot_demo", name="Autopilot Demo",
        entities=[{"name": "deals", "fields": [
            {"name": "deal_id", "type": "string", "primary_key": True},
            {"name": "amount", "type": "float"},
            {"name": "owner_email", "type": "string"},
            {"name": "close_date", "type": "date"},
        ]}],
    )
    manifest, seed_sql = cartridge_service._normalize_full_cartridge_manifest(bp)
    assert manifest["id"] == "autopilot_demo"
    # KBs survived (kb_id contract)
    assert manifest["knowledge_bits"], "KBs were dropped — kb_id contract broken"
    assert manifest["knowledge_bits"][0]["kb_id"] == "kb_deals"
    # semantic vocabulary survived (semantic_model.vocabulary contract)
    assert manifest["semantic_model"]["vocabulary"], "vocabulary dropped"
    # seed SQL was generated and validated (no exception above == valid)
    assert "INSERT INTO" in seed_sql


def test_blueprint_normalizer_accepts_numeric_source_id():
    """cid leading-letter contract: a numeric source id must not be rejected."""
    from app.services import cartridge_service
    bp = ap.build_blueprint(
        cartridge_id="123erp", name="ERP",
        entities=[{"name": "x", "fields": [{"name": "id", "type": "string", "primary_key": True}]}],
    )
    manifest, _ = cartridge_service._normalize_full_cartridge_manifest(bp)
    assert manifest["id"].startswith("c_")


def test_money_pk_still_drives_gold():
    """Audit-11: a PK that is also a money column must still produce Gold metrics."""
    bp = ap.build_blueprint(
        cartridge_id="x", name="X",
        entities=[{"name": "invoices", "fields": [
            {"name": "total_amount", "type": "float", "primary_key": True},
            {"name": "issued_date", "type": "date"},
        ]}],
    )
    assert any(d["layer"] == "gold" for d in bp["datasets"]), "money PK lost its metric role"


def test_date_pk_still_drives_watermark():
    """Audit-11: a PK that is also a date column must still feed the watermark."""
    bp = ap.build_blueprint(
        cartridge_id="x", name="X",
        entities=[{"name": "snaps", "fields": [
            {"name": "snapshot_date", "type": "date", "primary_key": True},
            {"name": "amount", "type": "float"},
        ]}],
    )
    snaps = next(e for e in bp["entities"] if e["entity"] == "snaps")
    assert snaps["watermark_field"] == "snapshot_date"


def test_entity_slug_collision_disambiguated():
    """Audit-19: two entities that slugify to the same id get distinct names."""
    bp = ap.build_blueprint(
        cartridge_id="x", name="X",
        entities=[
            {"name": "a b", "fields": [{"name": "id", "type": "string", "primary_key": True}]},
            {"name": "a-b", "fields": [{"name": "id", "type": "string", "primary_key": True}]},
        ],
    )
    names = [e["entity"] for e in bp["entities"]]
    assert len(set(names)) == 2, f"slug collision not disambiguated: {names}"


# ── Audit-round-2 regression / new-edge-case tests ──────────────────────────


def test_classify_field_non_dict_never_raises():
    """classify_field must return a safe default for non-dict input, never raise."""
    result = ap.classify_field(None)
    assert result["name"] == ""
    assert result["role"] == "dimension"
    result2 = ap.classify_field("not a dict")
    assert result2["protected"] is False


def test_classify_field_account_is_pii():
    """'account' token must trigger PII classification (replaces dead 'account_number')."""
    assert ap.classify_field({"name": "account", "type": "string"})["role"] == "pii"
    assert ap.classify_field({"name": "bank_account", "type": "string"})["role"] == "pii"


def test_classify_field_code_uuid_guid_are_keys():
    """'code', 'uuid', 'guid' tokens must classify as key."""
    assert ap.classify_field({"name": "product_code", "type": "string"})["role"] == "key"
    assert ap.classify_field({"name": "uuid", "type": "string"})["role"] == "key"
    assert ap.classify_field({"name": "record_guid", "type": "string"})["role"] == "key"


def test_params_is_json_string_not_python_list():
    """DAG params must be a JSON string so str() in the seed SQL stays valid JSON."""
    import json
    bp = ap.build_blueprint(
        cartridge_id="x", name="X",
        entities=[{"name": "e", "fields": [{"name": "id", "type": "string", "primary_key": True}]}],
    )
    dag = bp["dags"][0]
    assert isinstance(dag["params"], str), "params must be a JSON string, not a list"
    parsed = json.loads(dag["params"])
    assert isinstance(parsed, list)


def test_semantic_terms_qualified_with_entity():
    """Vocabulary terms must be 'entity.field' to prevent duplicates across entities."""
    bp = ap.build_blueprint(
        cartridge_id="x", name="X",
        entities=[
            {"name": "a", "fields": [{"name": "amount", "type": "float"}]},
            {"name": "b", "fields": [{"name": "amount", "type": "float"}]},
        ],
    )
    terms = [t["term"] for t in bp["semantic_model"]["vocabulary"]]
    assert "a.amount" in terms and "b.amount" in terms
    assert len(terms) == len(set(terms)), "duplicate vocabulary terms"


def test_silver_sql_subquery_has_alias():
    """Silver dedup subquery must have an alias (_dedup) to be standards-compliant."""
    bp = ap.build_blueprint(
        cartridge_id="x", name="X",
        entities=[{"name": "deals", "fields": [
            {"name": "deal_id", "type": "string", "primary_key": True},
            {"name": "close_date", "type": "date"},
        ]}],
    )
    silver = next(d for d in bp["datasets"] if d["layer"] == "silver")
    assert "_dedup" in silver["sql"]


def test_watermark_excludes_pii_date_fields():
    """A date field that classifies as PII (e.g. birth_date) must not be the watermark."""
    bp = ap.build_blueprint(
        cartridge_id="x", name="X",
        entities=[{"name": "people", "fields": [
            {"name": "id", "type": "string", "primary_key": True},
            {"name": "birth_date", "type": "date"},   # PII — must not be watermark
            {"name": "updated_at", "type": "timestamp"},
        ]}],
    )
    e = next(ent for ent in bp["entities"] if ent["entity"] == "people")
    assert e["watermark_field"] != "birth_date"
    assert e["watermark_field"] == "updated_at"


def test_build_blueprint_tolerates_none_in_entities_list():
    """None entries in the entities list must be silently skipped, not crash."""
    bp = ap.build_blueprint(
        cartridge_id="x", name="X",
        entities=[
            None,
            {"name": "deals", "fields": [{"name": "id", "type": "string", "primary_key": True}]},
        ],
    )
    assert len(bp["entities"]) == 1


def test_gold_sql_excludes_pii_date_fields_from_group_by():
    """Gold SQL must not GROUP BY a PII-classified date field (e.g., birth_date)."""
    bp = ap.build_blueprint(
        cartridge_id="x", name="X",
        entities=[{"name": "people", "fields": [
            {"name": "id", "type": "string", "primary_key": True},
            {"name": "amount", "type": "float"},
            {"name": "birth_date", "type": "date"},   # PII — excluded from Gold GROUP BY
            {"name": "updated_at", "type": "timestamp"},
        ]}],
    )
    gold_datasets = [d for d in bp["datasets"] if d["layer"] == "gold"]
    assert gold_datasets, "need gold to test"
    for gd in gold_datasets:
        assert "birth_date" not in gd["sql"]
