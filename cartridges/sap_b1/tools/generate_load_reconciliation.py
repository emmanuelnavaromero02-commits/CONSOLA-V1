from __future__ import annotations

import pathlib
import re
import sys

import yaml

CART = pathlib.Path(__file__).resolve().parents[1]
OUT = pathlib.Path(sys.argv[1]) if len(sys.argv) > 1 else CART / "datasets"
entities = yaml.safe_load((CART / "app/config/entities.yaml").read_text())["entities"]
by_name = {e["entity"]: e for e in entities}


def snake(name: str) -> str:
    s = re.sub(r"([a-z0-9])([A-Z])", r"\1_\2", name)
    s = re.sub(r"([A-Z]+)([A-Z][a-z])", r"\1_\2", s)
    return re.sub(r"__+", "_", s).lower()


def silver(entity: str) -> str:
    return f"sap_b1_{entity.lower()}_latest"


def platform_rows(e: dict) -> str:
    entity = e["entity"]
    source = f"read_parquet('s3://{{bucket}}/silver/sap_b1/{silver(entity)}/**/*.parquet')"
    if not e.get("date_field"):
        return (
            f"    SELECT '{entity}' AS entity, s.company, COUNT(*) AS platform_rows\n"
            f"    FROM {source} s\n"
            f"    GROUP BY 1, 2"
        )
    parent = e.get("parent")
    if parent:
        head = by_name[parent]
        date_col = snake(head["date_field"])
        join_key, parent_key = snake(e["join_key"]), snake(e["parent_key"])
        parent_source = f"read_parquet('s3://{{bucket}}/silver/sap_b1/{silver(parent)}/**/*.parquet')"
        return (
            f"    SELECT '{entity}' AS entity, s.company, COUNT(*) AS platform_rows\n"
            f"    FROM {source} s\n"
            f"    JOIN {parent_source} h ON h.company = s.company AND h.{parent_key} = s.{join_key}\n"
            f"    JOIN counts c ON c.company = s.company AND c.entity = '{entity}'\n"
            f"    WHERE CAST(h.{date_col} AS DATE) >= CAST(c.window_start AS DATE) AND CAST(h.{date_col} AS DATE) < CAST(c.window_end AS DATE)\n"
            f"    GROUP BY 1, 2"
        )
    date_col = snake(e["date_field"])
    return (
        f"    SELECT '{entity}' AS entity, s.company, COUNT(*) AS platform_rows\n"
        f"    FROM {source} s\n"
        f"    JOIN counts c ON c.company = s.company AND c.entity = '{entity}'\n"
        f"    WHERE CAST(s.{date_col} AS DATE) >= CAST(c.window_start AS DATE) AND CAST(s.{date_col} AS DATE) < CAST(c.window_end AS DATE)\n"
        f"    GROUP BY 1, 2"
    )


def render() -> str:
    sources = ["silver/sap_b1/sap_b1_source_counts"] + [f"silver/sap_b1/{silver(e['entity'])}" for e in entities]
    names = ",\n".join(
        f"        ('{e['entity']}', '{e['business_name'].replace(chr(39), chr(39) * 2)}', '{e['mode']}', {'TRUE' if e.get('date_field') else 'FALSE'})"
        for e in entities
    )
    unions = "\n    UNION ALL\n".join(platform_rows(e) for e in entities)
    header = (
        "-- sap_b1_load_reconciliation  (gold)  cartridge: sap_b1\n"
        f"-- sources: [{', '.join(chr(34) + s + chr(34) for s in sources)}]\n"
        "-- description: What arrived against what Business One holds, per company and table: the newest row count the agent took at the source "
        "(inside the 24-month window for dated tables, the whole table otherwise) against the rows the platform keeps in silver for the same window, "
        "the share loaded and a status (ok when they match, faltan when the platform has fewer rows, sobran when it has more, sin_conteo when the count failed), "
        "with the table's business name and extraction mode. This is the progress of the initial load and the evidence behind the signed mapping.\n"
    )
    return (
        header
        + "\n"
        + "WITH counts AS (\n"
        + "    SELECT * FROM read_parquet('s3://{bucket}/silver/sap_b1/sap_b1_source_counts/**/*.parquet')\n"
        + "),\n"
        + "catalog AS (\n"
        + "    SELECT * FROM (VALUES\n"
        + names
        + "\n    ) t(entity, business_name, mode, dated)\n"
        + "),\n"
        + "platform AS (\n"
        + unions
        + "\n)\n"
        + "SELECT\n"
        + "    c.company,\n"
        + "    c.entity,\n"
        + "    k.business_name,\n"
        + "    k.mode,\n"
        + "    k.dated,\n"
        + "    c.window_start,\n"
        + "    c.window_end,\n"
        + "    c.counted_at,\n"
        + "    c.source_rows,\n"
        + "    COALESCE(p.platform_rows, 0)                                            AS platform_rows,\n"
        + "    COALESCE(p.platform_rows, 0) - c.source_rows                            AS difference,\n"
        + "    CASE WHEN c.source_rows > 0 THEN ROUND(100.0 * LEAST(COALESCE(p.platform_rows, 0), c.source_rows) / c.source_rows, 2)\n"
        + "         WHEN c.error IS NULL THEN 100 END                                   AS loaded_pct,\n"
        + "    CASE WHEN c.error IS NOT NULL THEN 'sin_conteo'\n"
        + "         WHEN COALESCE(p.platform_rows, 0) = c.source_rows THEN 'ok'\n"
        + "         WHEN COALESCE(p.platform_rows, 0) < c.source_rows THEN 'faltan'\n"
        + "         ELSE 'sobran' END                                                  AS status,\n"
        + "    c.error\n"
        + "FROM counts c\n"
        + "LEFT JOIN catalog k ON k.entity = c.entity\n"
        + "LEFT JOIN platform p ON p.company = c.company AND p.entity = c.entity\n"
        + "ORDER BY c.company, c.entity\n"
    )


if __name__ == "__main__":
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "sap_b1_load_reconciliation.sql").write_text(render())
    print("load reconciliation written for", len(entities), "entities")
