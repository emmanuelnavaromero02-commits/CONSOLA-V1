from __future__ import annotations

import re
from pathlib import Path

from app.services.business_parameters_mapping import CATALOG_BY_KEY
from app.services.indicators import indicators

DATASETS = Path(__file__).resolve().parents[1] / "datasets"


def test_the_twelve_indicators_of_the_three_cases():
    items = indicators()
    cases = [item["case"] for item in items]
    assert (cases.count("finanzas"), cases.count("ventas"), cases.count("compras")) == (5, 4, 3)
    assert len({item["id"] for item in items}) == 12
    for item in items:
        assert item["formula"] and item["unit"] and item["dimensions"] and item["granularity"], item["id"]
        sql = (DATASETS / f"{item['dataset']}.sql").read_text(encoding="utf-8")
        assert "(gold)" in sql.splitlines()[0], item["id"]
        column = item["filter"].split(" ")[0]
        if column and not column.startswith("indicator"):
            assert re.search(rf"\bAS {column}\b", sql), (item["id"], column)
        if column.startswith("indicator"):
            assert f"'{item['id']}'" in sql, item["id"]
        for key in item["thresholds"]:
            assert key in CATALOG_BY_KEY, (item["id"], key)
