from __future__ import annotations

import glob
import re
from pathlib import Path

import duckdb
import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parents[1]


def _dataset_sql(cart: str, name: str, bronze_root: Path) -> str:
    src = (REPO_ROOT / "cartridges" / cart / "datasets" / f"{name}.sql").read_text(
        encoding="utf-8"
    )
    return src.replace("s3://{bucket}/raw/", f"{bronze_root.as_posix()}/raw/")


def _write_parquet(con, rows: list[dict], dest: Path) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    cols = list(rows[0].keys())
    con.execute("DROP TABLE IF EXISTS _t")
    con.execute(
        "CREATE TABLE _t AS SELECT * FROM (VALUES "
        + ", ".join(
            "(" + ", ".join(repr(r[c]) for c in cols) + ")" for r in rows
        )
        + f") AS v({', '.join(cols)})"
    )
    con.execute(f"COPY _t TO '{dest.as_posix()}' (FORMAT PARQUET)")


def _emp(pernr: str, begda: str, kostl: str, aedtm: str) -> dict:
    return {
        "Pernr": pernr, "Begda": begda, "Endda": "9999-12-31",
        "Bukrs": "MX01", "Werks": "PL01", "Persg": "1", "Persk": "AA",
        "Orgeh": "O100", "Plans": "P100", "Kostl": kostl, "AedtmAed": aedtm,
    }


def test_employeemaster_full_plus_delta_keeps_whole_population(tmp_path):
    con = duckdb.connect()
    root = tmp_path / "raw" / "sap_hcm" / "EmployeeMaster"
    full = [_emp(f"E{i}", "2020-01-01", f"CC{i}", "2026-07-01") for i in range(1, 6)]
    _write_parquet(con, full, root / "load_date=2026-08-01" / "data.parquet")
    delta = [
        _emp("E3", "2020-01-01", "CC3-NUEVO", "2026-08-14"),
        _emp("E6", "2026-08-10", "CC6", "2026-08-14"),
    ]
    _write_parquet(con, delta, root / "load_date=2026-08-15" / "data.parquet")

    sql = _dataset_sql("sap_hcm", "sap_hcm_employeemaster_latest", tmp_path)
    rows = con.execute(sql).fetchall()
    cols = [d[0] for d in con.description]
    by_pernr = {r[cols.index("pernr")]: r for r in rows}

    assert len(rows) == 6, "población completa: 5 del full + 1 alta nueva"
    assert by_pernr["E3"][cols.index("cost_center")] == "CC3-NUEVO", (
        "la versión del delta (más nueva) gana"
    )
    assert by_pernr["E1"][cols.index("cost_center")] == "CC1", (
        "los no tocados por el delta sobreviven"
    )

    viejo = con.execute(
        f"""SELECT COUNT(*) FROM read_parquet('{(root / '**/*.parquet').as_posix()}',
            hive_partitioning=true, union_by_name=true)
            WHERE load_date = (SELECT MAX(load_date) FROM read_parquet(
                '{(root / '**/*.parquet').as_posix()}', hive_partitioning=true))"""
    ).fetchone()[0]
    assert viejo == 2, "la evidencia del bug: MAX(load_date) colapsa al delta"


def test_effective_dated_slices_are_distinct_rows(tmp_path):
    con = duckdb.connect()
    root = tmp_path / "raw" / "sap_hcm" / "EmployeeMaster"
    _write_parquet(con, [_emp("E1", "2020-01-01", "CC-A", "2026-07-01")],
                   root / "load_date=2026-08-01" / "data.parquet")
    _write_parquet(con, [_emp("E1", "2026-08-10", "CC-B", "2026-08-14")],
                   root / "load_date=2026-08-15" / "data.parquet")
    sql = _dataset_sql("sap_hcm", "sap_hcm_employeemaster_latest", tmp_path)
    rows = con.execute(sql).fetchall()
    assert len(rows) == 2, "dos vigencias = dos filas (identidad Pernr+Begda)"


def test_salesorderitem_composite_key_dedupe(tmp_path):
    con = duckdb.connect()
    root = tmp_path / "raw" / "sap_s4hana" / "SalesOrderItem"

    def item(so, it, qty, amt):
        return {
            "SalesOrder": so, "SalesOrderItem": it, "Material": f"M-{it}",
            "RequestedQuantity": qty, "NetAmount": amt, "Plant": "PL01",
            "LastChangeDateTime": "2026-08-01",
        }

    _write_parquet(con, [item("SO1", "10", "5", "100"), item("SO1", "20", "2", "40"),
                         item("SO2", "10", "1", "10")],
                   root / "load_date=2026-08-01" / "data.parquet")
    _write_parquet(con, [item("SO1", "20", "9", "180")],
                   root / "load_date=2026-08-15" / "data.parquet")
    sql = _dataset_sql("sap_s4hana", "sap_s4hana_salesorderitem_latest", tmp_path)
    rows = con.execute(sql).fetchall()
    cols = [d[0] for d in con.description]
    assert len(rows) == 3, "los 3 ítems sobreviven"
    updated = next(
        r for r in rows
        if r[cols.index("sales_order")] == "SO1"
        and r[cols.index("sales_order_item")] == "20"
    )
    assert float(updated[cols.index("requested_quantity")]) == 9.0


def test_contract_no_incremental_latest_uses_max_load_date():
    for cart in ("sap_hcm", "sap_s4hana"):
        cfg = yaml.safe_load(
            (REPO_ROOT / "cartridges" / cart / "app" / "config" / "entities.yaml")
            .read_text(encoding="utf-8")
        )
        modes = {e["entity"]: e.get("mode") for e in cfg["entities"]}
        for path in glob.glob(str(REPO_ROOT / "cartridges" / cart / "datasets" / "*_latest.sql")):
            raw = Path(path).read_text(encoding="utf-8")
            m = re.search(r'sources: \["raw/' + cart + r'/([A-Za-z0-9_]+)"\]', raw)
            src = "\n".join(
                line.split("--", 1)[0] for line in raw.splitlines()
            )
            if not m:
                continue
            mode = modes.get(m.group(1))
            if mode == "incremental":
                assert "MAX(load_date)" not in src, (
                    f"{Path(path).name}: entidad incremental con el patrón que pierde datos"
                )
                assert "ROW_NUMBER() OVER" in src and "PARTITION BY" in src
            elif mode == "full":
                assert "MAX(load_date)" in src, (
                    f"{Path(path).name}: full-snapshot debe conservar la última partición"
                )
