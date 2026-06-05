from scripts import seed_intelligence_gold_prod_like as seed
import inspect


def test_prod_like_seed_includes_pipeline_salud_for_stress() -> None:
    payload = seed.DATASETS["pipeline_salud"]

    columns = {name for name, _kind in payload["columns"]}

    assert {"deal_id", "vendedor", "estado", "monto_usd", "monto_ponderado_usd"} <= columns
    assert payload["rows"]


def test_prod_like_seed_handles_legacy_text_scoped_gold_tables() -> None:
    source = inspect.getsource(seed._seed_dataset)

    assert "workspace_id::text = %s" in source
    assert "tenant_id::text = %s" in source
    assert "::uuid" not in source


def test_prod_like_seed_applies_gold_rls_under_request_scope() -> None:
    seed_dataset_source = inspect.getsource(seed._seed_dataset)
    main_source = inspect.getsource(seed.main)

    assert "omega_apply_gold_rls_for_table" in seed_dataset_source
    assert "set_config('app.tenant_id'" in main_source
    assert "set_config('app.workspace_id'" in main_source
