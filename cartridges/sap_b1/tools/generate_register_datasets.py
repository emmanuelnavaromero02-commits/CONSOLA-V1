"""Generate config/register_datasets.sql from datasets/*.sql.

The platform registers a cartridge's datasets in the ``datasets`` catalogue
per workspace. Other cartridges do it with an infra migration that targets
the first workspace; this cartridge belongs to one customer group whose
workspace is created when the connection is set up, so registration is a
script run at that moment with two psql variables:

    psql -v workspace_id='<uuid>' -v tenant_id='<uuid>' -f config/register_datasets.sql

Idempotent: ON CONFLICT (name) DO UPDATE keeps the catalogue equal to the
files. Run: python cartridges/sap_b1/tools/generate_register_datasets.py
"""
from __future__ import annotations

import json
import pathlib
import re
import sys

CART = pathlib.Path(__file__).resolve().parents[1]
DATASETS = CART / "datasets"
OUT = pathlib.Path(sys.argv[1]) if len(sys.argv) > 1 else CART / "config" / "register_datasets.sql"

HEADER_RE = re.compile(r"^--\s+(\S+)\s+\((silver|gold)\)\s+cartridge:\s+sap_b1\s*$")


def parse(path: pathlib.Path) -> tuple[str, str, list[str], str, str]:
    lines = path.read_text(encoding="utf-8").splitlines()
    m = HEADER_RE.match(lines[0])
    if not m or m.group(1) != path.stem:
        raise SystemExit(f"{path.name}: bad header")
    sources = json.loads(re.match(r"^--\s+sources:\s+(\[.*\])\s*$", lines[1]).group(1))
    description = re.match(r"^--\s+description:\s+(.*\S)\s*$", lines[2]).group(1)
    return path.stem, m.group(2), sources, description, path.read_text(encoding="utf-8")


def render() -> str:
    rows = []
    for path in sorted(DATASETS.glob("*.sql")):
        name, layer, sources, description, sql = parse(path)
        if "$seed$" in sql:
            raise SystemExit(f"{path.name}: dollar-quote tag collision")
        rows.append(
            "($seed$%s$seed$, $seed$%s$seed$, $seed$sap_b1$seed$, $seed$%s$seed$::jsonb, $seed$\n%s$seed$, $seed$%s$seed$, $seed${}$seed$::jsonb, $seed$$seed$, NOW(), :'workspace_id'::uuid)"
            % (name, layer, json.dumps(sources), sql, description.replace("$seed$", ""))
        )
    silver = sum(1 for p in DATASETS.glob("*.sql") if parse(p)[1] == "silver")
    gold = sum(1 for p in DATASETS.glob("*.sql") if parse(p)[1] == "gold")
    return (
        "-- register_datasets.sql — GENERATED from cartridges/sap_b1/datasets/*.sql by\n"
        "-- tools/generate_register_datasets.py; a test keeps them equal. Do not edit.\n"
        "--\n"
        "-- Registers the SAP Business One datasets in the workspace of the customer\n"
        "-- group. Run once the workspace exists, from infra/terraform/deploy on the host:\n"
        "--   docker compose -f docker-compose.aws.yml exec -T postgres psql -U postgres -d modecissions \\\n"
        "--     -v workspace_id='<uuid>' -v tenant_id='<uuid>' -f /docker-entrypoint-initdb.d/../cartridges/sap_b1/config/register_datasets.sql\n"
        "-- (or copy the file in). Idempotent: re-running updates the SQL to match the files.\n"
        f"-- {silver} silver + {gold} gold datasets.\n\n"
        "\\set ON_ERROR_STOP on\n"
        "SELECT :'workspace_id'::uuid AS workspace_id, :'tenant_id'::uuid AS tenant_id;\n\n"
        "INSERT INTO datasets (name, layer, cartridge, sources, sql_def, description, column_mapping, schedule, updated_at, workspace_id)\nVALUES\n"
        + ",\n".join(rows)
        + "\nON CONFLICT (name) DO UPDATE\n    SET layer = EXCLUDED.layer,\n        sources = EXCLUDED.sources,\n        sql_def = EXCLUDED.sql_def,\n        description = EXCLUDED.description,\n        updated_at = NOW();\n"
    )


if __name__ == "__main__":
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(render(), encoding="utf-8")
    print(OUT.name, "written")
