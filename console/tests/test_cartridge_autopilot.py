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
    for key in ("id", "name", "pattern", "category", "bronze_path", "entities",
                "datasets", "kbs", "agents", "semantic_model", "dags"):
        assert key in bp, f"missing manifest key {key}"
    assert bp["id"] == "demo_crm"
    assert bp["bronze_path"] == "raw/demo_crm"

    layers = [d["layer"] for d in bp["datasets"]]
    assert layers.count("silver") == 2
    assert layers.count("gold") >= 1

    gold = next(d for d in bp["datasets"] if d["layer"] == "gold")
    assert '"total_amount"' in gold["sql"]
    assert '"silver_deals"' in gold["sql"]

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
    bp = ap.build_blueprint(
        cartridge_id="demo_crm", name="Demo CRM", entities=_hubspot_like_schema()
    )
    silver = next(d for d in bp["datasets"] if d["name"] == "silver_deals")
    sql = silver["sql"].lower()
    assert "read_parquet" in sql
    assert "{latest_date}" in silver["sql"]
    assert "row_number()" in sql


def test_gold_sql_not_silver_shaped():
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
    bp = ap.build_blueprint(
        cartridge_id="sf",
        name="SF",
        entities=[{
            "name": "opportunity",
            "fields": [
                {"name": "opp_id", "type": "string", "primary_key": True},
                {"name": "amount", "type": "float"},
                {"name": "close_date", "type": "date"},
            ],
        }],
    )
    ent = bp["entities"][0]
    amount = next(f for f in ent["fields"] if f["name"] == "amount")
    assert amount["nullable"] is True
    assert amount["primary_key"] is False
    assert any(d["layer"] == "gold" for d in bp["datasets"])


def test_pii_no_false_positive_on_metric_names():
    assert ap.classify_field({"name": "dashboard_id", "type": "string"})["role"] == "key"
    assert ap.classify_field({"name": "stage_name", "type": "string"})["role"] == "dimension"
    assert ap.classify_field({"name": "valuestream", "type": "string"})["role"] == "dimension"


def test_money_requires_money_token_not_substring():
    assert ap.classify_field({"name": "valuestream", "type": "int"})["role"] == "metric"
    assert ap.classify_field({"name": "order_value", "type": "float"})["role"] == "money"


def test_classify_pii_beats_pk_for_sensitive_ids():
    assert ap.classify_field({"name": "national_id", "type": "string"})["role"] == "pii"
    assert ap.classify_field({"name": "email_id", "type": "string", "primary_key": True})["role"] == "pii"
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
    assert manifest["knowledge_bits"], "KBs were dropped — kb_id contract broken"
    assert manifest["knowledge_bits"][0]["kb_id"] == "kb_deals"
    assert manifest["semantic_model"]["vocabulary"], "vocabulary dropped"
    assert "INSERT INTO" in seed_sql


def test_blueprint_normalizer_accepts_numeric_source_id():
    from app.services import cartridge_service
    bp = ap.build_blueprint(
        cartridge_id="123erp", name="ERP",
        entities=[{"name": "x", "fields": [{"name": "id", "type": "string", "primary_key": True}]}],
    )
    manifest, _ = cartridge_service._normalize_full_cartridge_manifest(bp)
    assert manifest["id"].startswith("c_")


def test_money_pk_still_drives_gold():
    bp = ap.build_blueprint(
        cartridge_id="x", name="X",
        entities=[{"name": "invoices", "fields": [
            {"name": "total_amount", "type": "float", "primary_key": True},
            {"name": "issued_date", "type": "date"},
        ]}],
    )
    assert any(d["layer"] == "gold" for d in bp["datasets"]), "money PK lost its metric role"


def test_date_pk_still_drives_watermark():
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
    bp = ap.build_blueprint(
        cartridge_id="x", name="X",
        entities=[
            {"name": "a b", "fields": [{"name": "id", "type": "string", "primary_key": True}]},
            {"name": "a-b", "fields": [{"name": "id", "type": "string", "primary_key": True}]},
        ],
    )
    names = [e["entity"] for e in bp["entities"]]
    assert len(set(names)) == 2, f"slug collision not disambiguated: {names}"


def test_classify_field_non_dict_never_raises():
    result = ap.classify_field(None)
    assert result["name"] == ""
    assert result["role"] == "dimension"
    result2 = ap.classify_field("not a dict")
    assert result2["protected"] is False


def test_classify_field_account_is_pii():
    assert ap.classify_field({"name": "account", "type": "string"})["role"] == "pii"
    assert ap.classify_field({"name": "bank_account", "type": "string"})["role"] == "pii"


def test_classify_field_code_uuid_guid_are_keys():
    assert ap.classify_field({"name": "product_code", "type": "string"})["role"] == "key"
    assert ap.classify_field({"name": "uuid", "type": "string"})["role"] == "key"
    assert ap.classify_field({"name": "record_guid", "type": "string"})["role"] == "key"


def test_params_is_json_string_not_python_list():
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
    bp = ap.build_blueprint(
        cartridge_id="x", name="X",
        entities=[{"name": "people", "fields": [
            {"name": "id", "type": "string", "primary_key": True},
            {"name": "birth_date", "type": "date"},
            {"name": "updated_at", "type": "timestamp"},
        ]}],
    )
    e = next(ent for ent in bp["entities"] if ent["entity"] == "people")
    assert e["watermark_field"] != "birth_date"
    assert e["watermark_field"] == "updated_at"


def test_build_blueprint_tolerates_none_in_entities_list():
    bp = ap.build_blueprint(
        cartridge_id="x", name="X",
        entities=[
            None,
            {"name": "deals", "fields": [{"name": "id", "type": "string", "primary_key": True}]},
        ],
    )
    assert len(bp["entities"]) == 1


def test_gold_sql_excludes_pii_date_fields_from_group_by():
    bp = ap.build_blueprint(
        cartridge_id="x", name="X",
        entities=[{"name": "people", "fields": [
            {"name": "id", "type": "string", "primary_key": True},
            {"name": "amount", "type": "float"},
            {"name": "birth_date", "type": "date"},
            {"name": "updated_at", "type": "timestamp"},
        ]}],
    )
    gold_datasets = [d for d in bp["datasets"] if d["layer"] == "gold"]
    assert gold_datasets, "need gold to test"
    for gd in gold_datasets:
        assert "birth_date" not in gd["sql"]


def test_gold_sql_pii_money_fallback_excludes_pii_names():
    bp = ap.build_blueprint(
        cartridge_id="x", name="X",
        entities=[{"name": "payroll", "fields": [
            {"name": "id", "type": "string", "primary_key": True},
            {"name": "salary_amount", "type": "float"},
            {"name": "paid_date", "type": "date"},
        ]}],
    )
    gold_datasets = [d for d in bp["datasets"] if d["layer"] == "gold"]
    for gd in gold_datasets:
        assert "salary_amount" not in gd["sql"], "PII field must not be aggregated in Gold"


def test_gold_sql_all_dates_pii_produces_aggregate_without_group_by():
    bp = ap.build_blueprint(
        cartridge_id="x", name="X",
        entities=[{"name": "payments", "fields": [
            {"name": "id", "type": "string", "primary_key": True},
            {"name": "amount", "type": "float"},
            {"name": "birth_date", "type": "date"},
        ]}],
    )
    gold_datasets = [d for d in bp["datasets"] if d["layer"] == "gold"]
    assert gold_datasets, "need gold"
    sql = gold_datasets[0]["sql"]
    assert "birth_date" not in sql
    assert "group by" not in sql.lower()


def test_classify_field_account_id_is_pii_not_key():
    result = ap.classify_field({"name": "account_id", "type": "string"})
    assert result["role"] == "pii"
    assert result["protected"] is True


def test_silver_sql_no_pk_no_watermark_uses_load_date_fallback():
    bp = ap.build_blueprint(
        cartridge_id="x", name="X",
        entities=[{"name": "log", "fields": []}],
    )
    silver_datasets = [d for d in bp["datasets"] if d["layer"] == "silver"]
    assert silver_datasets, "silver dataset expected even for empty-field entity"
    sql = silver_datasets[0]["sql"]
    assert "load_date" in sql
    assert "{latest_date}" in sql


def test_build_blueprint_agent_watches_money_entity_over_pii_only():
    bp = ap.build_blueprint(
        cartridge_id="x", name="X",
        entities=[
            {"name": "people", "fields": [
                {"name": "email", "type": "string"},
                {"name": "ssn", "type": "string"},
            ]},
            {"name": "invoices", "fields": [
                {"name": "invoice_id", "type": "string", "primary_key": True},
                {"name": "amount", "type": "float"},
                {"name": "invoice_date", "type": "date"},
            ]},
        ],
    )
    agent_instructions = bp["agents"][0]["instructions"]
    assert "invoices" in agent_instructions
