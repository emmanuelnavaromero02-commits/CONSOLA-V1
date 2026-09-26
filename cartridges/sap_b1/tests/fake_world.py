from __future__ import annotations

import re
import shutil
from datetime import date, datetime, time, timedelta, timezone
from itertools import count
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path

CARTRIDGE = Path(__file__).resolve().parents[1]
DATASETS = CARTRIDGE / "datasets"
HEADER_RE = re.compile(r"^--\s+(\S+)\s+\((silver|gold)\)\s+cartridge:\s+sap_b1\s*$")


class Bronze:
    def __init__(self, root: Path, today: date | None = None) -> None:
        self.root = root
        self.today = today
        self.watermarks: dict[str, str] = {}
        self.failed: list[dict] = []
        self.runs = 0

    def install(self, monkeypatch) -> None:
        from app.core import b1_source
        from app.services import business_parameters as bp
        from app.services import extraction_service as es
        from app.services import finance_runs as fr
        from app.services import intercompany as ic
        from app.services import parquet_service
        from app.services.bronze_parquet import stamp_now

        def _copy(*, local_path: str, object_name: str) -> None:
            target = self.root / object_name
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy(local_path, target)

        def _create_run(**kw):
            self.runs += 1
            return f"run-{self.runs}"

        monkeypatch.setattr(parquet_service, "upload_file_to_minio", _copy)
        monkeypatch.setattr(fr, "object_exists_with_prefix",
                            lambda prefix: any(p.is_file() for p in (self.root / prefix).rglob("*")) if (self.root / prefix).exists() else False)
        for module in (es, ic, bp, fr):
            monkeypatch.setattr(module, "create_run", _create_run)
            monkeypatch.setattr(module, "finish_run", lambda **kw: None)
            monkeypatch.setattr(module, "fail_run", lambda **kw: self.failed.append(kw))
        monkeypatch.setattr(es, "get_watermark", lambda key: self.watermarks.get(key))
        monkeypatch.setattr(es, "update_watermark", lambda **kw: self.watermarks.__setitem__(kw["entity_name"], kw["last_watermark_value"]))
        monkeypatch.setattr(b1_source.Connection, "source_now", lambda self: datetime(2099, 1, 1))
        if self.today is not None:
            start = datetime.combine(self.today, time(12, 0), tzinfo=timezone.utc)
            ticks = count()
            monkeypatch.setattr(parquet_service, "stamp_now",
                                lambda now=None: stamp_now(now or start + timedelta(seconds=next(ticks))))


def _configs() -> list[dict]:
    from app.services.catalog_service import _yaml_entities

    return [dict(e) for e in _yaml_entities()]


def _extract(entities: list[str] | None = None, mode: str | None = None) -> None:
    from app.services import business_parameters as bp
    from app.services import extraction_service as es
    from app.services import intercompany as ic

    for config in _configs():
        if entities is not None and config["entity"] not in entities:
            continue
        if mode:
            config["mode"] = mode
        es.run_entity(config)
    if entities is None:
        ic.refresh_intercompany_partners()
        bp.refresh_business_parameters()


def _dataset_files() -> list[Path]:
    return sorted(DATASETS.glob("*.sql"))


def _layer(path: Path) -> str:
    return HEADER_RE.match(path.read_text(encoding="utf-8").splitlines()[0]).group(2)


SOURCES_RE = re.compile(r"^-- sources:\s*(\[.*\])\s*$", re.M)


def _ordered(files: list[Path]) -> list[Path]:
    import json

    by_name = {p.stem: p for p in files}
    needs = {
        p.stem: {s.rsplit("/", 1)[-1] for s in json.loads(SOURCES_RE.search(p.read_text(encoding="utf-8")).group(1))} & set(by_name)
        for p in files
    }
    ordered: list[Path] = []
    done: set[str] = set()
    while len(ordered) < len(files):
        ready = sorted(n for n in by_name if n not in done and needs[n] <= done)
        if not ready:
            raise RuntimeError(f"dataset dependency cycle among {sorted(set(by_name) - done)}")
        for name in ready:
            ordered.append(by_name[name])
            done.add(name)
    return ordered


def _materialise(bronze: Path):
    import duckdb

    con = duckdb.connect()
    files = _dataset_files()
    silver = [p for p in files if _layer(p) == "silver"]
    for path in _ordered(silver) + _ordered([p for p in files if _layer(p) == "gold"]):
        sql = path.read_text(encoding="utf-8").replace("s3://{bucket}/", bronze.as_posix() + "/")
        out = bronze / _layer(path) / "sap_b1" / path.stem
        out.mkdir(parents=True, exist_ok=True)
        con.execute(f"CREATE OR REPLACE TABLE \"{path.stem}\" AS {sql}")
        con.execute(f"COPY \"{path.stem}\" TO '{(out / 'data.parquet').as_posix()}' (FORMAT PARQUET)")
    return con


def _rows(con, sql: str) -> list[tuple]:
    return con.execute(sql).fetchall()


def _month_map(rows) -> dict[tuple, Decimal]:
    return {tuple(r[:-1]): Decimal(str(r[-1])) for r in rows}


def _cents(value: Decimal) -> Decimal:
    return value.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def _pg(dsn: str, sql: str, params=None):
    import psycopg2

    with psycopg2.connect(dsn) as conn:
        with conn.cursor() as cur:
            cur.execute(sql, params)
            return cur.fetchall()


def _pg_exec(dsn: str, sql: str, params=None) -> None:
    import psycopg2

    with psycopg2.connect(dsn) as conn:
        with conn.cursor() as cur:
            cur.execute(sql, params)
        conn.commit()
