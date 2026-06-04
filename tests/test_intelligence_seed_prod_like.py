from scripts import seed_intelligence_gold_prod_like as seed


def test_prod_like_seed_includes_pipeline_salud_for_stress() -> None:
    payload = seed.DATASETS["pipeline_salud"]

    columns = {name for name, _kind in payload["columns"]}

    assert {"deal_id", "vendedor", "estado", "monto_usd", "monto_ponderado_usd"} <= columns
    assert payload["rows"]
