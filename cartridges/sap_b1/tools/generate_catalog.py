"""Generate app/config/entities.yaml and config/seed.sql from the Business One-shaped fake schema (tests/fixtures/sap_b1/schema.py), the single source of truth for column names and types."""
from __future__ import annotations

import importlib
import importlib.util
import pathlib
import sys

import yaml

CARTRIDGE = pathlib.Path(__file__).resolve().parents[1]
REPO = CARTRIDGE.parents[1]
FIX = REPO / "tests" / "fixtures" / "sap_b1"
OUT = pathlib.Path(sys.argv[1]) if len(sys.argv) > 1 else CARTRIDGE
if "sap_b1_fake" not in sys.modules:
    spec = importlib.util.spec_from_file_location("sap_b1_fake", FIX / "__init__.py", submodule_search_locations=[str(FIX)])
    pkg = importlib.util.module_from_spec(spec)
    sys.modules["sap_b1_fake"] = pkg
    spec.loader.exec_module(pkg)
b1 = importlib.import_module("sap_b1_fake.schema")

MASTERS = {
    "CINF": ("Company info", "Company database version and name (one row per company).", 100),
    "OADM": ("Company settings", "Company code, local currency (MainCurncy) and system currency (SysCurrncy).", 100),
    "OCRN": ("Currencies", "Currency codes configured in the company.", 500),
    "ORTT": ("Exchange rates", "Daily exchange rates per currency (RateDate, Currency).", 5000),
    "OACT": ("Chart of accounts", "G/L accounts: ActType N (P&L), I (income), E (expense); Postable flag.", 5000),
    "OFPR": ("Posting periods", "Financial posting periods with their date ranges.", 1000),
    "OPRC": ("Cost centres", "Profit centres / cost centres (OcrCode dimension).", 1000),
    "OCRG": ("Business partner groups", "Customer and supplier groups.", 1000),
    "OSLP": ("Sales employees", "Sales employees / buyers referenced by SlpCode.", 1000),
    "OWHS": ("Warehouses", "Warehouse codes and names.", 1000),
    "OITB": ("Item groups", "Item groups.", 1000),
    "OITW": ("Stock by warehouse", "On-hand, committed and on-order quantities per item and warehouse (snapshot).", 5000),
    "OBTN": ("Batches", "Batch master (DistNumber, manufacture and expiry dates).", 5000),
    "OBTQ": ("Batch quantities", "Batch quantity per item, batch (SysNumber) and warehouse (snapshot).", 5000),
    "OIBT": ("Batch quantities (legacy)", "Batch quantity per item, BatchNum and warehouse (compatibility table).", 5000),
}
STAMPED_MASTERS = {
    "OCRD": ("Business partners", "Customers (CardType C) and suppliers (CardType S) with their currency and group.", "CardCode", 2000),
    "OITM": ("Items", "Item master: inventory / sales / purchase flags, batch management, default warehouse.", "ItemCode", 2000),
    "OITT": ("Bills of materials", "Bill of materials headers (production BOMs).", "Code", 1000),
}
DOCS = {
    "OINV": ("A/R invoices", "INV1", "13"), "ORIN": ("A/R credit memos", "RIN1", "14"), "ODLN": ("Deliveries", "DLN1", "15"),
    "ORDN": ("Returns", "RDN1", "16"), "ORDR": ("Sales orders", "RDR1", "17"), "OPCH": ("A/P invoices", "PCH1", "18"),
    "ORPC": ("A/P credit memos", "RPC1", "19"), "OPDN": ("Goods receipt POs", "PDN1", "20"), "OPOR": ("Purchase orders", "POR1", "22"),
}
entities = []
def arrow_kind(ddl: str) -> str:
    t = ddl.upper()
    if t.startswith("INTEGER"):
        return "int64"
    if t.startswith("NUMERIC") or t.startswith("DECIMAL"):
        return "decimal(19,6)"
    if t.startswith("TIMESTAMP") or t.startswith("DATE") or t.startswith("SECONDDATE"):
        return "timestamp"
    return "string"
def entry(**kw):
    kw.setdefault("protection", {})
    kw["column_types"] = {c: arrow_kind(t) for c, t in b1.TABLES[kw["entity"]]}
    entities.append(kw)
for table, (name, desc, page) in MASTERS.items():
    pk = list(b1.PRIMARY_KEYS.get(table, ()))
    entry(entity=table, display_name=f"{name} ({table})", description=desc, mode="full", page_size=page,
          primary_key=",".join(pk) if pk else None, select_fields=b1.columns(table))
for table, (name, desc, pk, page) in STAMPED_MASTERS.items():
    entry(entity=table, display_name=f"{name} ({table})", description=desc, mode="incremental",
          watermark_field="UpdateDate", watermark_ts_field="UpdateTS", watermark_format="b1_update_ts",
          primary_key=pk, page_size=page, select_fields=b1.columns(table))
entry(entity="ITT1", display_name="Bill of materials lines (ITT1)", description="Components per BOM; read through OITT so an edited BOM brings all its lines.",
      mode="incremental", watermark_field="UpdateDate", watermark_ts_field="UpdateTS", watermark_format="b1_update_ts",
      parent="OITT", parent_key="Code", join_key="Father", primary_key="Father,ChildNum", page_size=5000, select_fields=b1.columns("ITT1"))
for header, (name, line, obj) in DOCS.items():
    entry(entity=header, display_name=f"{name} ({header})", description=f"{name} headers, ObjType {obj}: totals in document, local and system currency; CANCELED N/Y/C.",
          mode="incremental", watermark_field="UpdateDate", watermark_ts_field="UpdateTS", watermark_format="b1_update_ts",
          primary_key="DocEntry", date_field="DocDate", page_size=2000, select_fields=b1.columns(header))
    entry(entity=line, display_name=f"{name} lines ({line})", description=f"{name} lines, read through {header}: quantities, prices, line totals in three currencies, base/target links.",
          mode="incremental", watermark_field="UpdateDate", watermark_ts_field="UpdateTS", watermark_format="b1_update_ts",
          parent=header, parent_key="DocEntry", join_key="DocEntry", primary_key="DocEntry,LineNum", date_field="DocDate", page_size=5000, select_fields=b1.columns(line))
entry(entity="OJDT", display_name="Journal entries (OJDT)", description="Journal entry headers: RefDate, TransType (originating object), StornoToTr for reversals.",
      mode="incremental", watermark_field="UpdateDate", watermark_ts_field="UpdateTS", watermark_format="b1_update_ts",
      primary_key="TransId", date_field="RefDate", page_size=2000, select_fields=b1.columns("OJDT"))
entry(entity="JDT1", display_name="Journal entry lines (JDT1)", description="Journal lines in local, foreign and system currency; ShortName carries the CardCode on control-account lines.",
      mode="incremental", watermark_field="UpdateDate", watermark_ts_field="UpdateTS", watermark_format="b1_update_ts",
      parent="OJDT", parent_key="TransId", join_key="TransId", primary_key="TransId,Line_ID", date_field="RefDate", page_size=5000, select_fields=b1.columns("JDT1"))
entry(entity="OWTR", display_name="Inventory transfers (OWTR)", description="Inventory transfer headers, ObjType 67: source warehouse (Filler) and target warehouse (ToWhsCode); stock moves, money does not.",
      mode="incremental", watermark_field="UpdateDate", watermark_ts_field="UpdateTS", watermark_format="b1_update_ts",
      primary_key="DocEntry", date_field="DocDate", page_size=2000, select_fields=b1.columns("OWTR"))
entry(entity="WTR1", display_name="Inventory transfer lines (WTR1)", description="Inventory transfer lines, read through OWTR: item, quantity, from and to warehouse.",
      mode="incremental", watermark_field="UpdateDate", watermark_ts_field="UpdateTS", watermark_format="b1_update_ts",
      parent="OWTR", parent_key="DocEntry", join_key="DocEntry", primary_key="DocEntry,LineNum", date_field="DocDate", page_size=5000, select_fields=b1.columns("WTR1"))
entry(entity="OWOR", display_name="Production orders (OWOR)", description="Production order headers: planned, completed and rejected quantities, status and dates.",
      mode="incremental", watermark_field="UpdateDate", watermark_ts_field="UpdateTS", watermark_format="b1_update_ts",
      primary_key="DocEntry", date_field="PostDate", page_size=2000, select_fields=b1.columns("OWOR"))
entry(entity="WOR1", display_name="Production order components (WOR1)", description="Components per production order, read through OWOR.",
      mode="incremental", watermark_field="UpdateDate", watermark_ts_field="UpdateTS", watermark_format="b1_update_ts",
      parent="OWOR", parent_key="DocEntry", join_key="DocEntry", primary_key="DocEntry,LineNum", date_field="PostDate", page_size=5000, select_fields=b1.columns("WOR1"))
entry(entity="OINM", display_name="Inventory movements (OINM)", description="Inventory transaction log (a view over OIVL/IVL1 in B1 >= 8.8); rows never change. One TransNum per document with one row per line (TransSeq): TransNum is the watermark, (TransNum, TransSeq) the page key.",
      mode="incremental", watermark_field="TransNum", watermark_format="integer", primary_key="TransNum,TransSeq", date_field="DocDate", page_size=5000, select_fields=b1.columns("OINM"))
entry(entity="IBT1", display_name="Batch transactions (IBT1)", description="Batch quantities per document line, Direction 0 in / 1 out; rows never change, LogEntry (identity, validate in HANA) is the watermark and page key.",
      mode="incremental", watermark_field="LogEntry", watermark_format="integer", primary_key="LogEntry", date_field="DocDate", page_size=5000, select_fields=b1.columns("IBT1"))

covered = {e["entity"] for e in entities}
assert covered == set(b1.TABLES), (set(b1.TABLES) ^ covered)
assert len(entities) == len(b1.TABLES), len(entities)
header = ""
text = yaml.safe_dump({"entities": entities}, sort_keys=False, allow_unicode=True, width=100)
(OUT / "app" / "config").mkdir(parents=True, exist_ok=True)
(OUT / "app" / "config" / "entities.yaml").write_text(header + text)
print("entities:", len(entities))

def q(v):
    if v is None: return "NULL"
    if isinstance(v, bool): return "TRUE" if v else "FALSE"
    if isinstance(v, int): return str(v)
    return "'" + str(v).replace("'", "''") + "'"
rows = []
for e in entities:
    rows.append("    (" + ", ".join([
        q("sap_b1"), q(e["entity"]), q(e["display_name"]), q(e["description"]), q(e["mode"]),
        q(e.get("watermark_field")), q(e.get("watermark_format")), q(e["page_size"]), q(e.get("primary_key")),
        q(e.get("date_field")), q("sap_b1_extract"), "TRUE", q("manual"),
    ]) + ")")
seed = """INSERT INTO cartridges (id, name, version, description, pattern, category, bronze_path)
VALUES (
    'sap_b1',
    'SAP Business One',
    '0.1.0',
    'SAP Business One 10 (HANA), multi-company: maestros, documentos de venta y compra, asientos, inventario, lotes y producción.',
    'dag-based',
    'cartridge',
    'raw/sap_b1/{entity}/load_date={date}/'
)
ON CONFLICT (id) DO UPDATE
    SET name        = EXCLUDED.name,
        version     = EXCLUDED.version,
        description = EXCLUDED.description,
        updated_at  = NOW();

INSERT INTO cartridge_dags (cartridge_id, dag_id, file, description, trigger, params)
VALUES
    ('sap_b1', 'sap_b1_extract',     'sap_b1_extract.py',     'Extrae una tabla de Business One en Bronze (full, incremental o histórico), todas las empresas configuradas', 'on-demand', '["entity","mode","from_date","to_date"]'),
    ('sap_b1', 'sap_b1_extract_all', 'sap_b1_extract_all.py', 'Extrae todas las tablas habilitadas',                                                                        'on-demand', '["mode","entities"]')
ON CONFLICT (cartridge_id, dag_id) DO NOTHING;

INSERT INTO entity_config
    (cartridge_id, entity, display_name, description, mode,
     watermark_field, watermark_format, page_size, primary_key, date_field,
     dag_id, enabled, trigger_type)
VALUES
""" + ",\n".join(rows) + "\nON CONFLICT (cartridge_id, entity) DO NOTHING;\n"
(OUT / "config").mkdir(parents=True, exist_ok=True)
(OUT / "config" / "seed.sql").write_text(seed)
print("seed rows:", len(rows))
