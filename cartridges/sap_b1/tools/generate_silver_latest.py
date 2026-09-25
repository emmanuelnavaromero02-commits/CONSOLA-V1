"""Generate datasets/sap_b1_<entity>_latest.sql from entities.yaml (run."""
from __future__ import annotations

import pathlib
import re
import sys

import yaml

CART = pathlib.Path(__file__).resolve().parents[1]
OUT = pathlib.Path(sys.argv[1]) if len(sys.argv) > 1 else CART / "datasets"
entities = yaml.safe_load((CART / "app/config/entities.yaml").read_text())["entities"]

TYPES = {"int64": "BIGINT", "decimal(19,6)": "DECIMAL(19,6)", "timestamp": "TIMESTAMP", "string": "VARCHAR"}

def snake(name: str) -> str:
    s = re.sub(r"([a-z0-9])([A-Z])", r"\1_\2", name)
    s = re.sub(r"([A-Z]+)([A-Z][a-z])", r"\1_\2", s)
    return re.sub(r"__+", "_", s).lower()

def render(e: dict) -> str:
    entity = e["entity"]
    cols = e["select_fields"]
    types = e["column_types"]
    pk = [c.strip() for c in (e.get("primary_key") or "").split(",") if c.strip()]
    parent = e.get("parent")
    kind = e.get("watermark_format")
    stamped = kind == "b1_update_ts"
    log = kind == "integer"
    snapshot = not stamped and not log
    src = f"read_parquet('s3://{{bucket}}/raw/sap_b1/{entity}/**/*.parquet',\n                      hive_partitioning = true,\n                      union_by_name   = true)"
    aliases = sorted({snake(c) for c in cols})
    assert len(aliases) == len(cols), (entity, "snake_case collision")
    select_cols = "\n".join(f"    CAST({c} AS {TYPES[types[c]]}){' ' * max(1, 34 - len(c) - len(TYPES[types[c]]))}AS {snake(c)}," for c in cols)
    key_sql = ", ".join(pk) if pk else None
    name = f"sap_b1_{entity.lower()}_latest"
    desc = e["description"].replace("\n", " ")
    header = f"-- {name}  (silver)  cartridge: sap_b1\n-- sources: [\"raw/sap_b1/{entity}\"]\n-- description: {desc}\n"
    if stamped and parent:
        rule = ""
        body = (
            "WITH versions AS (\n"
            f"{rule}"
            "    SELECT *\n"
            "    FROM (\n"
            "        SELECT *,\n"
            "               ROW_NUMBER() OVER (\n"
            f"                   PARTITION BY _company, {key_sql}\n"
            "                   ORDER BY _source_updated_at DESC NULLS LAST, load_date DESC, _extracted_at DESC\n"
            "               ) AS _rn\n"
            f"        FROM {src}\n"
            "    )\n"
            "    WHERE _rn = 1\n"
            "),\n"
            "current_lines AS (\n"
            "    SELECT *\n"
            "    FROM (\n"
            "        SELECT *,\n"
            f"               MAX(_source_updated_at) OVER (PARTITION BY _company, {e['join_key']}) AS _header_stamp\n"
            "        FROM versions\n"
            "    )\n"
            "    WHERE _source_updated_at IS NULL OR _source_updated_at = _header_stamp\n"
            ")\n"
            "SELECT\n"
            f"{select_cols}\n"
            "    _company                              AS company,\n"
            "    _source_updated_at                    AS source_updated_at,\n"
            "    load_date\n"
            "FROM current_lines\n"
            f"ORDER BY company, {', '.join(snake(c) for c in pk)}\n"
        )
    elif stamped or log:
        rule = ""
        order = "_source_updated_at DESC NULLS LAST, load_date DESC, _extracted_at DESC" if stamped else "load_date DESC, _extracted_at DESC"
        body = (
            "WITH latest AS (\n"
            f"{rule}"
            "    SELECT *\n"
            "    FROM (\n"
            "        SELECT *,\n"
            "               ROW_NUMBER() OVER (\n"
            f"                   PARTITION BY _company, {key_sql}\n"
            f"                   ORDER BY {order}\n"
            "               ) AS _rn\n"
            f"        FROM {src}\n"
            "    )\n"
            "    WHERE _rn = 1\n"
            ")\n"
            "SELECT\n"
            f"{select_cols}\n"
            "    _company                              AS company,\n"
            "    _source_updated_at                    AS source_updated_at,\n"
            "    load_date\n"
            "FROM latest\n"
            f"ORDER BY company, {', '.join(snake(c) for c in pk)}\n"
        )
    else:
        rule = ""
        dedupe = (
            f"               ROW_NUMBER() OVER (PARTITION BY s._company, {', '.join('s.' + c for c in pk)} ORDER BY s._extracted_at DESC) AS _rn\n"
            if pk else
            "               1 AS _rn\n"
        )
        body = (
            "WITH newest_run AS (\n"
            f"{rule}"
            "    SELECT _company,\n"
            "           arg_max(regexp_replace(_run_id, '-b[0-9]+$', ''), _extracted_at) AS _run_key\n"
            f"    FROM {src}\n"
            "    GROUP BY _company\n"
            "),\n"
            "latest AS (\n"
            "    SELECT *\n"
            "    FROM (\n"
            "        SELECT s.*,\n"
            f"{dedupe}"
            f"        FROM {src} s\n"
            "        JOIN newest_run n\n"
            "          ON n._company = s._company\n"
            "         AND regexp_replace(s._run_id, '-b[0-9]+$', '') = n._run_key\n"
            "    )\n"
            "    WHERE _rn = 1\n"
            ")\n"
            "SELECT\n"
            f"{select_cols}\n"
            "    _company                              AS company,\n"
            "    load_date\n"
            "FROM latest\n"
            f"ORDER BY company{(', ' + ', '.join(snake(c) for c in pk)) if pk else ''}\n"
        )
    return header + "\n" + body

OUT.mkdir(exist_ok=True)
written = []
for e in entities:
    text = render(e)
    path = OUT / f"sap_b1_{e['entity'].lower()}_latest.sql"
    path.write_text(text)
    written.append(path.name)
print(len(written), "silver datasets written")
