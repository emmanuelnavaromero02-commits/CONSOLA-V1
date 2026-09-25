"""Tables shaped like SAP Business One 10, rendered as Postgres DDL."""

from __future__ import annotations

from typing import Dict, List, Tuple

Column = Tuple[str, str]

_TS = "TIMESTAMP(0)"
_NUM = "NUMERIC(19,6)"

MARKETING_HEADER: List[Column] = [
    ("DocEntry", "INTEGER NOT NULL"),
    ("DocNum", "INTEGER NOT NULL"),
    ("DocType", "CHAR(1) NOT NULL"),
    ("CANCELED", "CHAR(1) NOT NULL"),
    ("DocStatus", "CHAR(1) NOT NULL"),
    ("ObjType", "VARCHAR(20) NOT NULL"),
    ("DocDate", _TS + " NOT NULL"),
    ("DocDueDate", _TS),
    ("TaxDate", _TS),
    ("CardCode", "VARCHAR(15) NOT NULL"),
    ("CardName", "VARCHAR(100)"),
    ("NumAtCard", "VARCHAR(100)"),
    ("DocCur", "VARCHAR(3) NOT NULL"),
    ("DocRate", _NUM + " NOT NULL"),
    ("DocTotal", _NUM + " NOT NULL"),
    ("DocTotalFC", _NUM),
    ("DocTotalSy", _NUM + " NOT NULL"),
    ("VatSum", _NUM),
    ("VatSumFC", _NUM),
    ("VatSumSy", _NUM),
    ("DiscSum", _NUM),
    ("GrosProfit", _NUM),
    ("GrosProfFC", _NUM),
    ("GrosProfSy", _NUM),
    ("SlpCode", "INTEGER"),
    ("GroupNum", "INTEGER"),
    ("Comments", "VARCHAR(254)"),
    ("TransId", "INTEGER"),
    ("BPLId", "INTEGER"),
    ("Series", "INTEGER"),
    ("CreateDate", _TS + " NOT NULL"),
    ("CreateTS", "INTEGER NOT NULL"),
    ("UpdateDate", _TS + " NOT NULL"),
    ("UpdateTS", "INTEGER NOT NULL"),
    ("UserSign", "INTEGER"),
]

MARKETING_LINE: List[Column] = [
    ("DocEntry", "INTEGER NOT NULL"),
    ("LineNum", "INTEGER NOT NULL"),
    ("TargetType", "INTEGER"),
    ("TrgetEntry", "INTEGER"),
    ("BaseType", "INTEGER"),
    ("BaseEntry", "INTEGER"),
    ("BaseLine", "INTEGER"),
    ("LineStatus", "CHAR(1) NOT NULL"),
    ("ItemCode", "VARCHAR(50)"),
    ("Dscription", "VARCHAR(100)"),
    ("Quantity", _NUM + " NOT NULL"),
    ("OpenQty", _NUM),
    ("Price", _NUM + " NOT NULL"),
    ("PriceBefDi", _NUM),
    ("Currency", "VARCHAR(3) NOT NULL"),
    ("Rate", _NUM + " NOT NULL"),
    ("DiscPrcnt", _NUM),
    ("LineTotal", _NUM + " NOT NULL"),
    ("TotalFrgn", _NUM),
    ("TotalSumSy", _NUM + " NOT NULL"),
    ("GrssProfit", _NUM),
    ("GrssProfFC", _NUM),
    ("GrssProfSC", _NUM),
    ("StockPrice", _NUM),
    ("WhsCode", "VARCHAR(8)"),
    ("ShipDate", _TS),
    ("VatPrcnt", _NUM),
    ("VatSum", _NUM),
    ("AcctCode", "VARCHAR(15)"),
    ("OcrCode", "VARCHAR(8)"),
    ("LineType", "CHAR(1)"),
    ("TreeType", "CHAR(1)"),
    ("ObjType", "VARCHAR(20) NOT NULL"),
    ("VisOrder", "INTEGER"),
]

MARKETING_PAIRS: List[Tuple[str, str, str]] = [
    ("OINV", "INV1", "13"),  # A/R invoice
    ("ORIN", "RIN1", "14"),  # A/R credit memo
    ("ODLN", "DLN1", "15"),  # delivery
    ("ORDN", "RDN1", "16"),  # return
    ("ORDR", "RDR1", "17"),  # sales order
    ("OPCH", "PCH1", "18"),  # A/P invoice
    ("ORPC", "RPC1", "19"),  # A/P credit memo
    ("OPDN", "PDN1", "20"),  # goods receipt PO
    ("OPOR", "POR1", "22"),  # purchase order
]

TABLES: Dict[str, List[Column]] = {
    "CINF": [("Version", "INTEGER NOT NULL"), ("CompnyName", "VARCHAR(100)")],
    "OADM": [
        ("Code", "VARCHAR(8) NOT NULL"),
        ("CompnyName", "VARCHAR(100)"),
        ("MainCurncy", "VARCHAR(3) NOT NULL"),
        ("SysCurrncy", "VARCHAR(3) NOT NULL"),
        ("Country", "VARCHAR(3)"),
    ],
    "OCRN": [
        ("CurrCode", "VARCHAR(3) NOT NULL"),
        ("CurrName", "VARCHAR(100)"),
        ("DocCurrCod", "VARCHAR(20)"),
    ],
    "ORTT": [
        ("RateDate", _TS + " NOT NULL"),
        ("Currency", "VARCHAR(3) NOT NULL"),
        ("Rate", _NUM + " NOT NULL"),
    ],
    "OACT": [
        ("AcctCode", "VARCHAR(15) NOT NULL"),
        ("AcctName", "VARCHAR(100)"),
        ("Postable", "CHAR(1)"),
        ("ActType", "CHAR(1)"),
        ("FatherNum", "VARCHAR(15)"),
        ("Levels", "INTEGER"),
        ("GroupMask", "INTEGER"),
    ],
    "OFPR": [
        ("AbsEntry", "INTEGER NOT NULL"),
        ("Code", "VARCHAR(20)"),
        ("Name", "VARCHAR(100)"),
        ("F_RefDate", _TS),
        ("T_RefDate", _TS),
        ("Category", "VARCHAR(4)"),
        ("Indicator", "VARCHAR(20)"),
    ],
    "OPRC": [
        ("PrcCode", "VARCHAR(8) NOT NULL"),
        ("PrcName", "VARCHAR(100)"),
        ("DimCode", "INTEGER"),
        ("Active", "CHAR(1)"),
    ],
    "OCRG": [
        ("GroupCode", "INTEGER NOT NULL"),
        ("GroupName", "VARCHAR(100)"),
        ("GroupType", "CHAR(1)"),
    ],
    "OCRD": [
        ("CardCode", "VARCHAR(15) NOT NULL"),
        ("CardName", "VARCHAR(100)"),
        ("CardType", "CHAR(1) NOT NULL"),
        ("GroupCode", "INTEGER"),
        ("Currency", "VARCHAR(3)"),
        ("SlpCode", "INTEGER"),
        ("Country", "VARCHAR(3)"),
        ("validFor", "CHAR(1)"),
        ("frozenFor", "CHAR(1)"),
        ("CreateDate", _TS),
        ("UpdateDate", _TS),
        ("UpdateTS", "INTEGER"),
    ],
    "OSLP": [
        ("SlpCode", "INTEGER NOT NULL"),
        ("SlpName", "VARCHAR(155)"),
        ("Active", "CHAR(1)"),
    ],
    "OWHS": [
        ("WhsCode", "VARCHAR(8) NOT NULL"),
        ("WhsName", "VARCHAR(100)"),
        ("Locked", "CHAR(1)"),
    ],
    "OITB": [("ItmsGrpCod", "INTEGER NOT NULL"), ("ItmsGrpNam", "VARCHAR(100)")],
    "OITM": [
        ("ItemCode", "VARCHAR(50) NOT NULL"),
        ("ItemName", "VARCHAR(100)"),
        ("ItmsGrpCod", "INTEGER"),
        ("InvntItem", "CHAR(1)"),
        ("SellItem", "CHAR(1)"),
        ("PrchseItem", "CHAR(1)"),
        ("ManBtchNum", "CHAR(1)"),
        ("DfltWH", "VARCHAR(8)"),
        ("AvgPrice", _NUM),
        ("LastPurPrc", _NUM),
        ("validFor", "CHAR(1)"),
        ("frozenFor", "CHAR(1)"),
        ("CreateDate", _TS),
        ("UpdateDate", _TS),
        ("UpdateTS", "INTEGER"),
    ],
    "OITW": [
        ("ItemCode", "VARCHAR(50) NOT NULL"),
        ("WhsCode", "VARCHAR(8) NOT NULL"),
        ("OnHand", _NUM + " NOT NULL"),
        ("IsCommited", _NUM),
        ("OnOrder", _NUM),
        ("AvgPrice", _NUM),
        ("MinStock", _NUM),
        ("MaxStock", _NUM),
    ],
    "OBTN": [
        ("AbsEntry", "INTEGER NOT NULL"),
        ("ItemCode", "VARCHAR(50) NOT NULL"),
        ("SysNumber", "INTEGER NOT NULL"),
        ("DistNumber", "VARCHAR(36) NOT NULL"),
        ("MnfDate", _TS),
        ("ExpDate", _TS),
        ("InDate", _TS),
        ("Status", "INTEGER"),
    ],
    "OBTQ": [
        ("ItemCode", "VARCHAR(50) NOT NULL"),
        ("SysNumber", "INTEGER NOT NULL"),
        ("WhsCode", "VARCHAR(8) NOT NULL"),
        ("Quantity", _NUM + " NOT NULL"),
    ],
    "OIBT": [
        ("ItemCode", "VARCHAR(50) NOT NULL"),
        ("BatchNum", "VARCHAR(36) NOT NULL"),
        ("WhsCode", "VARCHAR(8) NOT NULL"),
        ("Quantity", _NUM + " NOT NULL"),
    ],
    "IBT1": [
        ("LogEntry", "INTEGER NOT NULL"),  # validate in HANA: IBT1's identity column
        ("ItemCode", "VARCHAR(50) NOT NULL"),
        ("BatchNum", "VARCHAR(36) NOT NULL"),
        ("WhsCode", "VARCHAR(8) NOT NULL"),
        ("BaseType", "INTEGER NOT NULL"),
        ("BaseEntry", "INTEGER NOT NULL"),
        ("BaseLinNum", "INTEGER NOT NULL"),
        ("Quantity", _NUM + " NOT NULL"),
        ("Direction", "INTEGER NOT NULL"),  # 0 = in, 1 = out
        ("DocDate", _TS),
    ],
    "OITT": [
        ("Code", "VARCHAR(50) NOT NULL"),
        ("TreeType", "CHAR(1)"),
        ("Qauntity", _NUM),  # sic: B1 spells the BOM quantity column this way
        ("ToWH", "VARCHAR(8)"),  # validate in HANA: the BOM warehouse column name
        ("PriceList", "INTEGER"),
        ("CreateDate", _TS),
        ("UpdateDate", _TS),
        ("UpdateTS", "INTEGER"),
    ],
    "ITT1": [
        ("Father", "VARCHAR(50) NOT NULL"),
        ("ChildNum", "INTEGER NOT NULL"),
        ("Code", "VARCHAR(50) NOT NULL"),
        ("Quantity", _NUM + " NOT NULL"),
        ("Warehouse", "VARCHAR(8)"),
        ("IssueMthd", "CHAR(1)"),
        ("PriceList", "INTEGER"),
    ],
    "OWTR": [
        ("DocEntry", "INTEGER NOT NULL"),
        ("DocNum", "INTEGER NOT NULL"),
        ("CANCELED", "CHAR(1) NOT NULL"),
        ("DocStatus", "CHAR(1) NOT NULL"),
        ("ObjType", "VARCHAR(20) NOT NULL"),
        ("DocDate", _TS + " NOT NULL"),
        ("TaxDate", _TS),
        ("CardCode", "VARCHAR(15)"),
        ("CardName", "VARCHAR(100)"),
        ("Filler", "VARCHAR(8)"),
        ("ToWhsCode", "VARCHAR(8)"),
        ("Comments", "VARCHAR(254)"),
        ("TransId", "INTEGER"),
        ("Series", "INTEGER"),
        ("CreateDate", _TS + " NOT NULL"),
        ("CreateTS", "INTEGER NOT NULL"),
        ("UpdateDate", _TS + " NOT NULL"),
        ("UpdateTS", "INTEGER NOT NULL"),
        ("UserSign", "INTEGER"),
    ],
    "WTR1": [
        ("DocEntry", "INTEGER NOT NULL"),
        ("LineNum", "INTEGER NOT NULL"),
        ("LineStatus", "CHAR(1) NOT NULL"),
        ("ItemCode", "VARCHAR(50)"),
        ("Dscription", "VARCHAR(100)"),
        ("Quantity", _NUM + " NOT NULL"),
        ("Price", _NUM),
        ("Currency", "VARCHAR(3)"),
        ("Rate", _NUM),
        ("LineTotal", _NUM),
        ("TotalSumSy", _NUM),
        ("StockPrice", _NUM),
        ("FromWhsCod", "VARCHAR(8)"),
        ("WhsCode", "VARCHAR(8)"),
        ("ObjType", "VARCHAR(20) NOT NULL"),
        ("VisOrder", "INTEGER"),
    ],
    "OWOR": [
        ("DocEntry", "INTEGER NOT NULL"),
        ("DocNum", "INTEGER NOT NULL"),
        ("ItemCode", "VARCHAR(50) NOT NULL"),
        ("Status", "CHAR(1) NOT NULL"),
        ("Type", "CHAR(1)"),
        ("PlannedQty", _NUM + " NOT NULL"),
        ("CmpltQty", _NUM + " NOT NULL"),
        ("RjctQty", _NUM),
        ("PostDate", _TS),
        ("DueDate", _TS),
        ("StartDate", _TS),
        ("CloseDate", _TS),
        ("Warehouse", "VARCHAR(8)"),
        ("CreateDate", _TS + " NOT NULL"),
        ("CreateTS", "INTEGER"),
        ("UpdateDate", _TS + " NOT NULL"),
        ("UpdateTS", "INTEGER NOT NULL"),
    ],
    "WOR1": [
        ("DocEntry", "INTEGER NOT NULL"),
        ("LineNum", "INTEGER NOT NULL"),
        ("ItemCode", "VARCHAR(50) NOT NULL"),
        ("BaseQty", _NUM),
        ("PlannedQty", _NUM + " NOT NULL"),
        ("IssuedQty", _NUM + " NOT NULL"),
        ("wareHouse", "VARCHAR(8)"),  # sic: B1's casing on WOR1
        ("ItemType", "INTEGER"),
    ],
    "OINM": [
        ("TransNum", "INTEGER NOT NULL"),
        ("TransSeq", "INTEGER NOT NULL"),
        ("DocDate", _TS + " NOT NULL"),
        ("ItemCode", "VARCHAR(50) NOT NULL"),
        ("Warehouse", "VARCHAR(8) NOT NULL"),
        ("InQty", _NUM + " NOT NULL"),
        ("OutQty", _NUM + " NOT NULL"),
        ("Price", _NUM),
        ("TransType", "INTEGER NOT NULL"),
        ("CreatedBy", "INTEGER"),
        ("BASE_REF", "VARCHAR(16)"),
        ("DocLineNum", "INTEGER"),
        ("Currency", "VARCHAR(3)"),
        ("TransValue", _NUM),
        ("CalcPrice", _NUM),
        ("ApplObj", "VARCHAR(20)"),
        ("AppObjAbs", "INTEGER"),
        ("CreateDate", _TS + " NOT NULL"),
    ],
    "OJDT": [
        ("TransId", "INTEGER NOT NULL"),
        ("Number", "INTEGER"),
        ("RefDate", _TS + " NOT NULL"),
        ("DueDate", _TS),
        ("TaxDate", _TS),
        ("Memo", "VARCHAR(50)"),
        ("TransType", "VARCHAR(20)"),
        ("BaseRef", "VARCHAR(16)"),
        ("CreatedBy", "INTEGER"),
        ("StornoToTr", "INTEGER"),
        ("CreateDate", _TS + " NOT NULL"),
        ("CreateTS", "INTEGER"),
        ("UpdateDate", _TS + " NOT NULL"),
        ("UpdateTS", "INTEGER NOT NULL"),
    ],
    "JDT1": [
        ("TransId", "INTEGER NOT NULL"),
        ("Line_ID", "INTEGER NOT NULL"),
        ("Account", "VARCHAR(15) NOT NULL"),
        ("ShortName", "VARCHAR(15)"),
        ("ContraAct", "VARCHAR(15)"),
        ("Debit", _NUM + " NOT NULL"),
        ("Credit", _NUM + " NOT NULL"),
        ("FCDebit", _NUM),
        ("FCCredit", _NUM),
        ("FCCurrency", "VARCHAR(3)"),
        ("SYSDeb", _NUM + " NOT NULL"),
        ("SYSCred", _NUM + " NOT NULL"),
        ("ProfitCode", "VARCHAR(8)"),
        ("RefDate", _TS),
        ("DueDate", _TS),
        ("TaxDate", _TS),
        ("BaseRef", "VARCHAR(16)"),
        ("TransType", "VARCHAR(20)"),
        ("ObjType", "VARCHAR(20)"),
        ("LineMemo", "VARCHAR(50)"),
    ],
}

for _header, _line, _obj in MARKETING_PAIRS:
    TABLES[_header] = list(MARKETING_HEADER)
    TABLES[_line] = list(MARKETING_LINE)

PRIMARY_KEYS: Dict[str, Tuple[str, ...]] = {
    "OADM": ("Code",),
    "OCRN": ("CurrCode",),
    "ORTT": ("RateDate", "Currency"),
    "OACT": ("AcctCode",),
    "OFPR": ("AbsEntry",),
    "OPRC": ("PrcCode",),
    "OCRG": ("GroupCode",),
    "OCRD": ("CardCode",),
    "OSLP": ("SlpCode",),
    "OWHS": ("WhsCode",),
    "OITB": ("ItmsGrpCod",),
    "OITM": ("ItemCode",),
    "OITW": ("ItemCode", "WhsCode"),
    "OBTN": ("AbsEntry",),
    "OBTQ": ("ItemCode", "SysNumber", "WhsCode"),
    "OIBT": ("ItemCode", "BatchNum", "WhsCode"),
    "IBT1": ("LogEntry",),
    "OITT": ("Code",),
    "ITT1": ("Father", "ChildNum"),
    "OWTR": ("DocEntry",),
    "WTR1": ("DocEntry", "LineNum"),
    "OWOR": ("DocEntry",),
    "WOR1": ("DocEntry", "LineNum"),
    "OINM": ("TransNum", "TransSeq"),
    "OJDT": ("TransId",),
    "JDT1": ("TransId", "Line_ID"),
}
for _header, _line, _obj in MARKETING_PAIRS:
    PRIMARY_KEYS[_header] = ("DocEntry",)
    PRIMARY_KEYS[_line] = ("DocEntry", "LineNum")

UPDATE_TS_MAX = 235959


def quote(identifier: str) -> str:
    """Double-quote an identifier the way HANA and Postgres both accept."""
    if '"' in identifier:
        raise ValueError(f"identifier cannot contain a double quote: {identifier!r}")
    return f'"{identifier}"'


def columns(table: str) -> List[str]:
    return [name for name, _type in TABLES[table]]


def render_ddl(schema: str) -> str:
    """CREATE SCHEMA plus one CREATE TABLE per B1 table, in declaration order."""
    statements = [f"CREATE SCHEMA {quote(schema)};"]
    for table, cols in TABLES.items():
        body = [f"    {quote(name)} {ctype}" for name, ctype in cols]
        pk = PRIMARY_KEYS.get(table)
        if pk:
            body.append(
                f"    CONSTRAINT {quote(f'{table}_PRIMARY')} PRIMARY KEY ("
                + ", ".join(quote(c) for c in pk)
                + ")"
            )
        statements.append(
            f"CREATE TABLE {quote(schema)}.{quote(table)} (\n" + ",\n".join(body) + "\n);"
        )
    return "\n".join(statements) + "\n"
