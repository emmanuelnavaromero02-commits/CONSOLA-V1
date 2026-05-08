"""
sync_dag_sources.py
===================
Sincroniza el source_code de los DAGs en cartridge_dags con
los archivos .py actuales en airflow/dags/.

Uso:
    python infra/sync_dag_sources.py

Requiere psycopg2 y acceso directo al PostgreSQL (localhost:5432).
Ejecutar desde la raíz del repo.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

try:
    import psycopg2
except ImportError:
    print("Instala psycopg2:  pip install psycopg2-binary")
    sys.exit(1)

# ── Config ────────────────────────────────────────────────────────────────────
PG_DSN = os.getenv("PG_DSN")
if not PG_DSN:
    raise RuntimeError("PG_DSN is required. Example: host=localhost port=5432 dbname=modecissions user=postgres password=...")
DAGS_DIR = Path(__file__).parent.parent / "airflow" / "dags"

# DAGs a sincronizar: (cartridge_id, dag_id, archivo)
DAGS = [
    ("replicon", "replicon_extract",          "replicon_extract.py"),
    ("replicon", "replicon_extract_all",      "replicon_extract_all.py"),
    ("replicon", "replicon_projects_detail",  "replicon_projects_detail.py"),
    ("generic",  "minio_file_convert",        "minio_file_convert.py"),
]

# ── Sync ──────────────────────────────────────────────────────────────────────

def main():
    conn = psycopg2.connect(PG_DSN)
    updated = []
    skipped = []

    with conn.cursor() as cur:
        for cartridge_id, dag_id, filename in DAGS:
            path = DAGS_DIR / filename
            if not path.exists():
                print(f"  [SKIP] {filename} — archivo no encontrado en {DAGS_DIR}")
                skipped.append(dag_id)
                continue

            source = path.read_text(encoding="utf-8")

            cur.execute(
                """INSERT INTO cartridge_dags
                       (cartridge_id, dag_id, file, source_code, updated_at)
                   VALUES (%s, %s, %s, %s, NOW())
                   ON CONFLICT (cartridge_id, dag_id) DO UPDATE
                   SET source_code = EXCLUDED.source_code,
                       file        = EXCLUDED.file,
                       updated_at  = NOW()""",
                (cartridge_id, dag_id, filename, source),
            )
            print(f"  [OK]   {dag_id}  ({len(source):,} chars)")
            updated.append(dag_id)

    conn.commit()
    conn.close()

    print(f"\nActualizados: {len(updated)}  |  Omitidos: {len(skipped)}")
    if skipped:
        print("Omitidos:", skipped)


if __name__ == "__main__":
    print(f"Sincronizando DAGs -> PostgreSQL ({PG_DSN})\n")
    main()
