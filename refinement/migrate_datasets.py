import os, json, yaml, psycopg2
from pathlib import Path

dsn  = os.environ["DATABASE_URL"].replace("postgresql+psycopg2://", "postgresql://")
conn = psycopg2.connect(dsn)
cur  = conn.cursor()
migrated = skipped = 0

for f in sorted(Path("/app/datasets").glob("*.yaml")):
    d    = yaml.safe_load(f.read_text())
    name = d.get("name", f.stem)
    cur.execute("SELECT 1 FROM datasets WHERE name=%s", (name,))
    if cur.fetchone():
        print("SKIP", name); skipped += 1; continue
    cur.execute(
        """INSERT INTO datasets
             (name,layer,cartridge,sources,sql_def,description,
              column_mapping,schedule,last_refresh,row_count)
           VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
        (name, d.get("layer","silver"), d.get("cartridge",""),
         json.dumps(d.get("sources",[])), d.get("sql",""), d.get("description",""),
         json.dumps(d.get("column_mapping",{})), d.get("schedule"),
         d.get("last_refresh"), d.get("row_count"))
    )
    print("MIGRATED", name); migrated += 1

conn.commit(); conn.close()
print(f"Done: {migrated} migrados, {skipped} saltados")
