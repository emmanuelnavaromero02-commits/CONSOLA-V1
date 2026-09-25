from __future__ import annotations

import hashlib
import json
import random
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from decimal import ROUND_FLOOR, ROUND_HALF_UP, Decimal
from typing import Any, Dict, List, Optional, Tuple

from . import schema as b1

Q6 = Decimal("0.000001")
ZERO = Decimal("0")

ACCT_AR = "1200"
ACCT_INVENTORY = "1300"
ACCT_VAT_RECEIVABLE = "1400"
ACCT_AP = "2100"
ACCT_VAT_PAYABLE = "2400"
ACCT_REVENUE = "4100"
ACCT_COGS = "5100"

VAT_RATE = Decimal("0.16")
WHS_MAIN = "01"
WHS_SECOND = "02"

TT_OPENING = 59
TT_GOODS_RECEIPT_PO = 20
TT_DELIVERY = 15
TT_PRODUCTION_ISSUE = 60
TT_PRODUCTION_RECEIPT = 59
TT_TRANSFER = 67
APPL_OBJ_PRODUCTION_ORDER = "202"


def q6(value: Decimal) -> Decimal:
    return value.quantize(Q6, rounding=ROUND_HALF_UP)


def hhmmss(rng: random.Random) -> int:
    return rng.randint(6, 20) * 10000 + rng.randint(0, 59) * 100 + rng.randint(0, 59)


def ts(d: date) -> datetime:
    return datetime(d.year, d.month, d.day)


def min_stock(role: str, item: str) -> Decimal:
    if not item.startswith("FG-"):
        return Decimal("400")
    return Decimal("200") if role == "manufacturer" else Decimal("40")


def month_start(anchor: date, offset: int) -> date:
    year = anchor.year + (anchor.month - 1 + offset) // 12
    month = (anchor.month - 1 + offset) % 12 + 1
    return date(year, month, 1)


@dataclass(frozen=True)
class CompanyProfile:
    alias: str
    schema: str
    name: str
    country: str
    local_currency: str
    sys_currency: str
    role: str


DEFAULT_COMPANIES: Tuple[CompanyProfile, ...] = (
    CompanyProfile("mx_mfg", "SBO_MX_MFG", "Fabricante MX (sintetico)", "MX", "MXN", "MXN", "manufacturer"),
    CompanyProfile("mx_dist_a", "SBO_MX_DIST_A", "Distribuidora A (sintetica)", "MX", "MXN", "USD", "distributor"),
    CompanyProfile("mx_dist_b", "SBO_MX_DIST_B", "Distribuidora B (sintetica)", "MX", "MXN", "MXN", "distributor"),
)

INTERCOMPANY_CUSTOMER = {"mx_dist_a": "C-IC-DIST-A", "mx_dist_b": "C-IC-DIST-B"}
INTERCOMPANY_SUPPLIER = "V-IC-MFG"
GENERIC_RFC = "XAXX010101000"
OPEN_PO_AGE_DAYS = 10
OPEN_SO_AGE_DAYS = 3
COMMITTED_COVER_DAYS = (2, 8)
INVALID_RFC = "RFC-PENDIENTE"
SHARED_CUSTOMERS = {
    ("mx_mfg", "C-0001"): 1, ("mx_dist_a", "C-0011"): 1,
    ("mx_mfg", "C-0002"): 2, ("mx_dist_a", "C-0012"): 2,
    ("mx_mfg", "C-0003"): 3, ("mx_dist_b", "C-0013"): 3,
    ("mx_dist_a", "C-0005"): 4, ("mx_dist_b", "C-0005"): 4,
    ("mx_dist_a", "C-0007"): 5, ("mx_dist_b", "C-0008"): 5,
}
GENERIC_RFC_CUSTOMERS = {("mx_dist_a", "C-0015"), ("mx_dist_b", "C-0015")}
MISSING_RFC_CUSTOMERS = {("mx_mfg", "C-0010")}
INVALID_RFC_CUSTOMERS = {("mx_dist_b", "C-0009")}


def _rfc(prefix: str, number: int) -> str:
    return f"{prefix}{100101 + number:06d}{'ABCDEFGHJK'[number % 10]}{number % 10}{'XYZ'[number % 3]}"


def company_rfc(alias: str) -> str:
    return _rfc("EMP", sum(map(ord, alias)) % 900)


def customer_rfc(alias: str, code: str) -> Optional[str]:
    key = (alias, code)
    if key in GENERIC_RFC_CUSTOMERS:
        return GENERIC_RFC
    if key in MISSING_RFC_CUSTOMERS:
        return None
    if key in INVALID_RFC_CUSTOMERS:
        return INVALID_RFC
    shared = SHARED_CUSTOMERS.get(key)
    return _rfc("CLI", shared if shared else 100 + 20 * (sum(map(ord, alias)) % 40) + int(code[2:]))


def item_barcode(code: str) -> Optional[str]:
    if not code.startswith("FG-"):
        return None
    digits = f"750{int(code[3:]):09d}"
    check = (10 - sum(int(d) * (3 if i % 2 else 1) for i, d in enumerate(digits)) % 10) % 10
    return f"{digits}{check}"


@dataclass
class MonthTruth:
    invoices: int = 0
    invoices_canceled: int = 0
    cancellation_docs: int = 0
    credit_memos: int = 0
    revenue_gross_lc: Decimal = ZERO
    credit_lc: Decimal = ZERO
    revenue_net_lc: Decimal = ZERO
    revenue_net_sc: Decimal = ZERO
    revenue_account_lc: Decimal = ZERO
    revenue_account_sc: Decimal = ZERO
    cogs_lc: Decimal = ZERO
    purchases_lc: Decimal = ZERO
    intercompany_sales_lc: Dict[str, Decimal] = field(default_factory=dict)
    intercompany_purchases_lc: Decimal = ZERO

    def as_dict(self) -> Dict[str, Any]:
        return {
            "invoices": self.invoices,
            "invoices_canceled": self.invoices_canceled,
            "cancellation_docs": self.cancellation_docs,
            "credit_memos": self.credit_memos,
            "revenue_gross_lc": str(self.revenue_gross_lc),
            "credit_lc": str(self.credit_lc),
            "revenue_net_lc": str(self.revenue_net_lc),
            "revenue_net_sc": str(self.revenue_net_sc),
            "revenue_account_lc": str(self.revenue_account_lc),
            "revenue_account_sc": str(self.revenue_account_sc),
            "cogs_lc": str(self.cogs_lc),
            "purchases_lc": str(self.purchases_lc),
            "intercompany_sales_lc": {k: str(v) for k, v in sorted(self.intercompany_sales_lc.items())},
            "intercompany_purchases_lc": str(self.intercompany_purchases_lc),
        }


@dataclass
class Dataset:
    seed: int
    start_month: date
    months: int
    as_of: date
    companies: Tuple[CompanyProfile, ...]
    tables: Dict[str, Dict[str, List[tuple]]]
    truth: Dict[str, Dict[str, MonthTruth]]
    closing_stock: Dict[str, Dict[Tuple[str, str], Decimal]]
    expired_batches: Dict[str, int]

    def row_counts(self) -> Dict[str, Dict[str, int]]:
        return {alias: {t: len(rows) for t, rows in tables.items()} for alias, tables in self.tables.items()}

    def checksum(self) -> str:
        digest = hashlib.sha256()
        for alias in sorted(self.tables):
            for table in sorted(self.tables[alias]):
                for row in self.tables[alias][table]:
                    digest.update(json.dumps([alias, table, [str(v) for v in row]]).encode("utf-8"))
        return digest.hexdigest()

    def truth_as_dict(self) -> Dict[str, Dict[str, Dict[str, Any]]]:
        return {alias: {m: t.as_dict() for m, t in months.items()} for alias, months in self.truth.items()}


JournalLine = Tuple[str, Decimal, Decimal, Optional[str]]


class _Company:

    def __init__(self, profile: CompanyProfile, rng: random.Random, ds: "_Builder") -> None:
        self.p = profile
        self.rng = rng
        self.ds = ds
        self.rows: Dict[str, List[tuple]] = {t: [] for t in b1.TABLES}
        self.next_entry: Dict[str, int] = {}
        self.next_trans = 1
        self.next_inm = 1
        self.inm_group: Optional[Tuple[int, int]] = None
        self.inm_seq = 0
        self.next_ibt = 1
        self.next_batch_abs = 1
        self.next_sysnumber: Dict[str, int] = {}
        self.stock: Dict[Tuple[str, str], Decimal] = {}
        self.batches: Dict[Tuple[str, str], List[List[Any]]] = {}
        self.batch_meta: Dict[Tuple[str, int], tuple] = {}
        self.batch_numbers: set = set()
        self.truth: Dict[str, MonthTruth] = {}
        self.item_cost: Dict[str, Decimal] = {}
        self.item_price: Dict[str, Decimal] = {}
        self.customers: List[str] = []
        self.suppliers: List[str] = []
        self.finished_goods: List[str] = []
        self.raw_materials: List[str] = []
        self.open_production: Dict[str, Decimal] = {}
        self.open_purchases: Dict[str, Decimal] = {}
        self.committed: Dict[str, Decimal] = {}


    def entry(self, table: str) -> int:
        value = self.next_entry.get(table, 1)
        self.next_entry[table] = value + 1
        return value

    def add(self, table: str, **values: Any) -> None:
        cols = b1.columns(table)
        unknown = set(values) - set(cols)
        if unknown:
            raise KeyError(f"{table}: unknown columns {sorted(unknown)}")
        self.rows[table].append(tuple(values.get(c) for c in cols))

    def clamp(self, d: date) -> date:
        return min(d, self.ds.as_of)

    def truth_for(self, d: date) -> MonthTruth:
        key = f"{d.year:04d}-{d.month:02d}"
        return self.truth.setdefault(key, MonthTruth())

    def sys_rate(self, d: date) -> Decimal:
        if self.p.sys_currency == self.p.local_currency:
            return Decimal("1")
        return self.ds.rates[(month_start(d, 0), self.p.sys_currency)]

    def to_sys(self, amount_lc: Decimal, d: date) -> Decimal:
        return q6(amount_lc / self.sys_rate(d))

    def move_stock(self, d: date, item: str, whs: str, qty_in: Decimal, qty_out: Decimal, price: Decimal,
                   trans_type: int, created_by: int, line: int, production_order: Optional[int] = None) -> None:
        key = (item, whs)
        self.stock[key] = self.stock.get(key, ZERO) + qty_in - qty_out
        group = (trans_type, created_by)
        if group != self.inm_group:
            if self.inm_group is not None:
                self.next_inm += 1
            self.inm_group = group
            self.inm_seq = 0
        else:
            self.inm_seq += 1
        self.add(
            "OINM", TransNum=self.next_inm, TransSeq=self.inm_seq, DocDate=ts(d), ItemCode=item, Warehouse=whs,
            InQty=q6(qty_in), OutQty=q6(qty_out), Price=q6(price), TransType=trans_type,
            CreatedBy=created_by, BASE_REF=str(created_by), DocLineNum=line,
            Currency=self.p.local_currency, TransValue=q6((qty_in - qty_out) * price),
            CalcPrice=q6(price), ApplObj=APPL_OBJ_PRODUCTION_ORDER if production_order else None,
            AppObjAbs=production_order, CreateDate=ts(d),
        )

    def receive_batch(self, d: date, item: str, whs: str, qty: Decimal, dist: str, mnf: date, exp: date,
                      base_type: int, base_entry: int, base_line: int) -> None:
        if (item, dist) in self.batch_numbers:
            raise RuntimeError(f"{self.p.alias}: batch number {dist} already exists for {item}")
        self.batch_numbers.add((item, dist))
        sysno = self.next_sysnumber.get(item, 0) + 1
        self.next_sysnumber[item] = sysno
        self.batches.setdefault((item, whs), []).append([sysno, dist, qty])
        self.batch_meta[(item, sysno)] = (dist, mnf, exp)
        self.add("OBTN", AbsEntry=self.next_batch_abs, ItemCode=item, SysNumber=sysno, DistNumber=dist,
                 MnfDate=ts(mnf), ExpDate=ts(exp), InDate=ts(d), Status=0)
        self.next_batch_abs += 1
        self.add("IBT1", LogEntry=self.next_ibt, ItemCode=item, BatchNum=dist, WhsCode=whs, BaseType=base_type,
                 BaseEntry=base_entry, BaseLinNum=base_line, Quantity=q6(qty), Direction=0, DocDate=ts(d))
        self.next_ibt += 1

    def consume_batches(self, d: date, item: str, whs: str, qty: Decimal, base_type: int, base_entry: int, base_line: int) -> None:
        remaining = qty
        for batch in self.batches.get((item, whs), []):
            if remaining <= ZERO:
                break
            if batch[2] <= ZERO:
                continue
            take = min(batch[2], remaining)
            batch[2] -= take
            remaining -= take
            self.add("IBT1", LogEntry=self.next_ibt, ItemCode=item, BatchNum=batch[1], WhsCode=whs, BaseType=base_type,
                     BaseEntry=base_entry, BaseLinNum=base_line, Quantity=q6(take), Direction=1, DocDate=ts(d))
            self.next_ibt += 1
        if remaining > ZERO:
            raise RuntimeError(f"{self.p.alias}: batch stock exhausted for {item}@{whs}")

    def available_batches(self, item: str, whs: str) -> Decimal:
        return sum((b[2] for b in self.batches.get((item, whs), [])), ZERO)

    def journal(self, d: date, memo: str, obj_type: str, base_ref: int, lines: List[JournalLine],
                storno_to: Optional[int] = None) -> int:
        debit = sum((ln[1] for ln in lines), ZERO)
        credit = sum((ln[2] for ln in lines), ZERO)
        if q6(debit) != q6(credit):
            raise RuntimeError(f"{self.p.alias}: unbalanced journal {memo}: {debit} vs {credit}")
        sys_debits = [self.to_sys(ln[1], d) for ln in lines]
        sys_credits = [self.to_sys(ln[2], d) for ln in lines]
        residue = sum(sys_debits, ZERO) - sum(sys_credits, ZERO)
        if residue != ZERO:
            last = len(lines) - 1
            if sys_credits[last] > ZERO:
                sys_credits[last] = q6(sys_credits[last] + residue)
            else:
                sys_debits[last] = q6(sys_debits[last] - residue)
        trans = self.next_trans
        self.next_trans += 1
        create_ts = hhmmss(self.rng)
        self.add("OJDT", TransId=trans, Number=trans, RefDate=ts(d), DueDate=ts(d), TaxDate=ts(d), Memo=memo[:50],
                 TransType=obj_type, BaseRef=str(base_ref), CreatedBy=base_ref, StornoToTr=storno_to,
                 CreateDate=ts(d), CreateTS=create_ts, UpdateDate=ts(d), UpdateTS=create_ts)
        for line_id, ((account, dr, cr, short_name), sys_dr, sys_cr) in enumerate(zip(lines, sys_debits, sys_credits)):
            self.add("JDT1", TransId=trans, Line_ID=line_id, Account=account, ShortName=short_name or account,
                     ContraAct=None, Debit=q6(dr), Credit=q6(cr), FCDebit=ZERO, FCCredit=ZERO, FCCurrency=None,
                     SYSDeb=sys_dr, SYSCred=sys_cr, ProfitCode=None, RefDate=ts(d), DueDate=ts(d), TaxDate=ts(d),
                     BaseRef=str(base_ref), TransType=obj_type, ObjType=obj_type, LineMemo=memo[:50])
        return trans

    def stamp(self, d: date) -> Dict[str, Any]:
        create_ts = hhmmss(self.rng)
        update_d, update_ts = d, create_ts
        if self.rng.random() < 0.10:
            update_d = self.clamp(d + timedelta(days=self.rng.randint(1, 20)))
            update_ts = hhmmss(self.rng)
        return {"CreateDate": ts(d), "CreateTS": create_ts, "UpdateDate": ts(update_d), "UpdateTS": update_ts}

    def marketing_doc(self, header: str, line: str, obj_type: str, d: date, card: str, lines: List[Dict[str, Any]],
                      doc_type: str = "I", base: Optional[Tuple[int, int]] = None, canceled: str = "N",
                      num_at_card: Optional[str] = None, closed: bool = False) -> Tuple[int, Decimal, Decimal]:
        entry = self.entry(header)
        sys_rate = self.sys_rate(d)
        net = ZERO
        vat = ZERO
        profit = ZERO
        for i, ln in enumerate(lines):
            qty = q6(Decimal(ln["qty"]))
            price = q6(Decimal(ln["price"]))
            line_total = q6(qty * price)
            line_vat = q6(line_total * VAT_RATE)
            stock_price = q6(Decimal(ln.get("cost", "0")))
            line_profit = q6(line_total - qty * stock_price) if ln.get("item") else ZERO
            net += line_total
            vat += line_vat
            profit += line_profit
            self.add(
                line, DocEntry=entry, LineNum=i, TargetType=None, TrgetEntry=None,
                BaseType=base[0] if base else -1, BaseEntry=base[1] if base else None, BaseLine=i if base else None,
                LineStatus="C" if (closed or canceled != "N") else "O", ItemCode=ln.get("item"), Dscription=ln.get("name"),
                Quantity=qty, OpenQty=ZERO if closed else qty, Price=price, PriceBefDi=price, Currency=self.p.local_currency,
                Rate=ZERO, DiscPrcnt=ZERO, LineTotal=line_total, TotalFrgn=ZERO,
                TotalSumSy=q6(line_total / sys_rate), GrssProfit=line_profit, GrssProfFC=ZERO,
                GrssProfSC=q6(line_profit / sys_rate), StockPrice=stock_price, WhsCode=ln.get("whs"), ShipDate=ts(d),
                VatPrcnt=q6(VAT_RATE * 100), VatSum=line_vat, AcctCode=ln.get("account"), OcrCode=None,
                LineType=None, TreeType="N", ObjType=obj_type, VisOrder=i,
            )
        total = q6(net + vat)
        self.add(
            header, DocEntry=entry, DocNum=entry, DocType=doc_type, CANCELED=canceled,
            DocStatus="C" if (closed or canceled != "N") else "O",
            ObjType=obj_type, DocDate=ts(d), DocDueDate=ts(d + timedelta(days=30)), TaxDate=ts(d),
            CardCode=card, CardName=self.ds.card_names.get(card, card), NumAtCard=num_at_card,
            DocCur=self.p.local_currency, DocRate=ZERO, DocTotal=total, DocTotalFC=ZERO,
            DocTotalSy=q6(total / sys_rate), VatSum=q6(vat), VatSumFC=ZERO, VatSumSy=q6(vat / sys_rate), DiscSum=ZERO,
            GrosProfit=q6(profit), GrosProfFC=ZERO, GrosProfSy=q6(profit / sys_rate),
            SlpCode=self.rng.randint(1, 5), GroupNum=1, Comments=None, TransId=None, BPLId=1, Series=1,
            UserSign=1, **self.stamp(d),
        )
        return entry, q6(net), q6(vat)

    def set_header(self, header: str, entry: int, **changes: Any) -> None:
        cols = b1.columns(header)
        idx = {c: i for i, c in enumerate(cols)}
        rows = self.rows[header]
        for pos, row in enumerate(rows):
            if row[idx["DocEntry"]] == entry:
                values = list(row)
                for k, v in changes.items():
                    values[idx[k]] = v
                rows[pos] = tuple(values)
                return
        raise KeyError(f"{header} {entry} not found")

    def link_target(self, line_table: str, base_entry: int, target_type: str, target_entry: int) -> None:
        cols = b1.columns(line_table)
        idx = {c: i for i, c in enumerate(cols)}
        rows = self.rows[line_table]
        for pos, row in enumerate(rows):
            if row[idx["DocEntry"]] == base_entry:
                values = list(row)
                values[idx["TargetType"]] = int(target_type)
                values[idx["TrgetEntry"]] = target_entry
                values[idx["LineStatus"]] = "C"
                values[idx["OpenQty"]] = ZERO
                rows[pos] = tuple(values)


class _Builder:
    def __init__(self, seed: int, start_month: date, months: int, companies: Tuple[CompanyProfile, ...]) -> None:
        self.seed = seed
        self.start_month = start_month
        self.months = months
        self.as_of = month_start(start_month, months) - timedelta(days=1)
        self.companies = companies
        self.rng = random.Random(seed)
        self.rates: Dict[Tuple[date, str], Decimal] = {}
        self.card_names: Dict[str, str] = {}
        self.state: Dict[str, _Company] = {}
        self.finished_goods = [f"FG-{i:03d}" for i in range(1, 26)]
        self.raw_materials = [f"RM-{i:03d}" for i in range(1, 16)]
        self.bom: Dict[str, List[Tuple[str, Decimal]]] = {}
        self.rm_price: Dict[str, Decimal] = {}
        self.fg_cost: Dict[str, Decimal] = {}
        self.rm_demand: Dict[str, Decimal] = {}


    def build_shared(self) -> None:
        rng = self.rng
        for rm in self.raw_materials:
            self.rm_price[rm] = q6(Decimal(rng.randint(20, 180)))
        for fg in self.finished_goods:
            comps = rng.sample(self.raw_materials, rng.randint(2, 3))
            self.bom[fg] = [(rm, q6(Decimal(rng.randint(1, 4)))) for rm in comps]
            self.fg_cost[fg] = q6(sum((self.rm_price[rm] * qty for rm, qty in self.bom[fg]), ZERO))
            for rm, qty in self.bom[fg]:
                self.rm_demand[rm] = self.rm_demand.get(rm, ZERO) + qty
        usd = Decimal("17.50")
        for m in range(-3, self.months + 1):
            d = month_start(self.start_month, m)
            usd = q6(usd * (Decimal("1") + Decimal(rng.randint(-25, 25)) / Decimal(1000)))
            self.rates[(d, "USD")] = usd
            self.rates[(d, "MXN")] = Decimal("1")

    def build_company_masters(self, c: _Company) -> None:
        p, rng = c.p, c.rng
        c.add("CINF", Version=1000191, CompnyName=p.name)
        c.add("OADM", Code="1", CompnyName=p.name, MainCurncy=p.local_currency, SysCurrncy=p.sys_currency, Country=p.country)
        c.add("OCRN", CurrCode="MXN", CurrName="Peso mexicano", DocCurrCod="MXN")
        c.add("OCRN", CurrCode="USD", CurrName="US Dollar", DocCurrCod="USD")
        day = self.start_month - timedelta(days=91)
        while day <= self.as_of:
            c.add("ORTT", RateDate=ts(day), Currency="USD", Rate=self.rates[(month_start(day, 0), "USD")])
            day += timedelta(days=1)
        accounts = [
            (ACCT_AR, "Clientes", "N"), (ACCT_INVENTORY, "Inventarios", "N"), (ACCT_VAT_RECEIVABLE, "IVA acreditable", "N"),
            (ACCT_AP, "Proveedores", "N"), (ACCT_VAT_PAYABLE, "IVA trasladado", "N"),
            (ACCT_REVENUE, "Ventas", "I"), (ACCT_COGS, "Costo de ventas", "E"),
        ]
        for code, name, kind in accounts:
            c.add("OACT", AcctCode=code, AcctName=name, Postable="Y", ActType=kind, FatherNum=code[0], Levels=2, GroupMask=int(code[0]))
        for m in range(self.months):
            d = month_start(self.start_month, m)
            end = month_start(self.start_month, m + 1) - timedelta(days=1)
            label = f"{d.year}-{d.month:02d}"
            c.add("OFPR", AbsEntry=m + 1, Code=label, Name=label, F_RefDate=ts(d), T_RefDate=ts(end), Category=str(d.year), Indicator=label)
        c.add("OPRC", PrcCode="CC-VTA", PrcName="Ventas", DimCode=1, Active="Y")
        c.add("OPRC", PrcCode="CC-OPS", PrcName="Operaciones", DimCode=1, Active="Y")
        c.add("OCRG", GroupCode=100, GroupName="Clientes", GroupType="C")
        c.add("OCRG", GroupCode=101, GroupName="Proveedores", GroupType="S")
        c.add("OCRG", GroupCode=102, GroupName="Intercompania", GroupType="C")
        for i in range(1, 6):
            c.add("OSLP", SlpCode=i, SlpName=f"Vendedor {i}", Active="Y")
        c.add("OWHS", WhsCode=WHS_MAIN, WhsName="Almacen principal", Locked="N")
        c.add("OWHS", WhsCode=WHS_SECOND, WhsName="Almacen secundario", Locked="N")
        c.add("OITB", ItmsGrpCod=100, ItmsGrpNam="Producto terminado")
        c.add("OITB", ItmsGrpCod=101, ItmsGrpNam="Materia prima")
        d0 = self.start_month - timedelta(days=90)

        def card(code: str, name: str, kind: str, group: int, rfc: Optional[str]) -> None:
            self.card_names[code] = name
            c.add("OCRD", CardCode=code, CardName=name, CardType=kind, GroupCode=group, Currency=p.local_currency,
                  SlpCode=rng.randint(1, 5), Country="MX", LicTradNum=rfc, validFor="Y", frozenFor="N",
                  CreateDate=ts(d0), UpdateDate=ts(d0), UpdateTS=90000)

        if p.role == "manufacturer":
            c.finished_goods = list(self.finished_goods)
            c.raw_materials = list(self.raw_materials)
            for i in range(1, 11):
                card(f"S-{i:04d}", f"Proveedor sintetico {i}", "S", 101, _rfc("PRV", i))
                c.suppliers.append(f"S-{i:04d}")
            for other in self.companies:
                if other.role == "distributor":
                    card(INTERCOMPANY_CUSTOMER[other.alias], f"Intercompania {other.alias}", "C", 102, company_rfc(other.alias))
            for i in range(1, 11):
                card(f"C-{i:04d}", f"Cliente sintetico {i}", "C", 100, customer_rfc(p.alias, f"C-{i:04d}"))
                c.customers.append(f"C-{i:04d}")
            for fg in c.finished_goods:
                c.item_cost[fg] = self.fg_cost[fg]
                c.item_price[fg] = q6(self.fg_cost[fg] * Decimal("1.60"))
            for rm in c.raw_materials:
                c.item_cost[rm] = self.rm_price[rm]
            for fg in c.finished_goods:
                c.add("OITT", Code=fg, TreeType="P", Qauntity=Decimal("1"), ToWH=WHS_MAIN, PriceList=1, CreateDate=ts(d0), UpdateDate=ts(d0), UpdateTS=90000)
                for n, (rm, qty) in enumerate(self.bom[fg]):
                    c.add("ITT1", Father=fg, ChildNum=n, Code=rm, Quantity=qty, Warehouse=WHS_MAIN, IssueMthd="B", PriceList=1)
        else:
            c.finished_goods = list(self.finished_goods)
            maker = next((other.alias for other in self.companies if other.role == "manufacturer"), p.alias)
            card(INTERCOMPANY_SUPPLIER, "Intercompania fabricante", "S", 102, company_rfc(maker))
            c.suppliers.append(INTERCOMPANY_SUPPLIER)
            for i in range(1, 16):
                card(f"C-{i:04d}", f"Cliente final sintetico {i}", "C", 100, customer_rfc(p.alias, f"C-{i:04d}"))
                c.customers.append(f"C-{i:04d}")
            for fg in c.finished_goods:
                c.item_cost[fg] = q6(self.fg_cost[fg] * Decimal("1.25"))
                c.item_price[fg] = q6(c.item_cost[fg] * Decimal("1.35"))
        for code in c.finished_goods + c.raw_materials:
            is_fg = code.startswith("FG-")
            made = is_fg and p.role == "manufacturer"
            number = int(code[3:])
            c.add("OITM", ItemCode=code, ItemName=f"Articulo {code}", ItmsGrpCod=100 if is_fg else 101, InvntItem="Y",
                  SellItem="Y" if is_fg else "N", PrchseItem="N" if made else "Y",
                  ManBtchNum="Y" if is_fg else "N", DfltWH=WHS_MAIN, AvgPrice=c.item_cost[code], LastPurPrc=c.item_cost[code],
                  CodeBars=item_barcode(code), SuppCatNum=None if made else f"{'FAB' if is_fg else 'PRV'}-{code}",
                  CardCode=None if made else (INTERCOMPANY_SUPPLIER if is_fg else f"S-{number % 10 + 1:04d}"),
                  LeadTime=None if made else (5 if is_fg else 7 * (1 + number % 4)),
                  MinOrdrQty=ZERO if made else Decimal("50" if is_fg else "100"),
                  OrdrMulti=Decimal("1") if made else Decimal("10"), PrcrmntMtd="M" if made else "B",
                  validFor="Y", frozenFor="N", CreateDate=ts(d0), UpdateDate=ts(d0), UpdateTS=90000)

    def opening_stock(self, c: _Company) -> None:
        d = self.start_month - timedelta(days=1)
        entry = 0
        for code in c.raw_materials:
            entry += 1
            c.move_stock(d, code, WHS_MAIN, Decimal("20000"), ZERO, c.item_cost[code], TT_OPENING, entry, 0)
        for code in c.finished_goods:
            entry += 1
            qty = Decimal("5000") if c.p.role == "manufacturer" else Decimal("2000")
            c.move_stock(d, code, WHS_MAIN, qty, ZERO, c.item_cost[code], TT_OPENING, entry, 0)
            mnf = d - timedelta(days=60)
            c.receive_batch(d, code, WHS_MAIN, qty, f"{code}-OPEN", mnf, mnf + timedelta(days=365), TT_OPENING, entry, 0)


    def purchase_chain(self, c: _Company, d: date, supplier: str, lines: List[Dict[str, Any]],
                       batch_prefix: Optional[str] = None, same_day: bool = False) -> None:
        d = c.clamp(d)
        po, _, _ = c.marketing_doc("OPOR", "POR1", "22", d, supplier, lines, closed=True)
        d_receipt = d if same_day else c.clamp(d + timedelta(days=2))
        pdn, _, _ = c.marketing_doc("OPDN", "PDN1", "20", d_receipt, supplier, lines, base=(22, po), closed=True)
        c.link_target("POR1", po, "20", pdn)
        for i, ln in enumerate(lines):
            qty = q6(Decimal(ln["qty"]))
            c.move_stock(d_receipt, ln["item"], ln["whs"], qty, ZERO, Decimal(ln["price"]), TT_GOODS_RECEIPT_PO, pdn, i)
            if batch_prefix:
                mnf = d_receipt - timedelta(days=30)
                c.receive_batch(d_receipt, ln["item"], ln["whs"], qty, f"{ln['item']}-{batch_prefix}-{pdn}-{i}", mnf,
                                mnf + timedelta(days=c.rng.choice([120, 365, 540])), 20, pdn, i)
        d_inv = d_receipt if same_day else c.clamp(d_receipt + timedelta(days=3))
        pch, net, vat = c.marketing_doc("OPCH", "PCH1", "18", d_inv, supplier, lines, base=(20, pdn))
        c.link_target("PDN1", pdn, "18", pch)
        trans = c.journal(d_inv, f"AP {pch}", "18", pch, [
            (ACCT_INVENTORY, net, ZERO, None), (ACCT_VAT_RECEIVABLE, vat, ZERO, None), (ACCT_AP, ZERO, q6(net + vat), supplier)])
        c.set_header("OPCH", pch, TransId=trans)
        truth = c.truth_for(d_inv)
        truth.purchases_lc += net
        if supplier == INTERCOMPANY_SUPPLIER:
            truth.intercompany_purchases_lc += net

    def transfer(self, c: _Company, d: date, item: str, qty: Decimal, from_whs: str, to_whs: str) -> int:
        d = c.clamp(d)
        entry = c.entry("OWTR")
        price = c.item_cost[item]
        total = q6(qty * price)
        c.add("OWTR", DocEntry=entry, DocNum=entry, CANCELED="N", DocStatus="C", ObjType="67", DocDate=ts(d),
              TaxDate=ts(d), CardCode=None, CardName=None, Filler=from_whs, ToWhsCode=to_whs, Comments="Traslado",
              TransId=None, Series=1, CreateDate=ts(d), CreateTS=100000, UpdateDate=ts(d), UpdateTS=100000, UserSign=1)
        c.add("WTR1", DocEntry=entry, LineNum=0, LineStatus="C", ItemCode=item, Dscription=f"Articulo {item}",
              Quantity=q6(qty), Price=q6(price), Currency=c.p.local_currency, Rate=ZERO, LineTotal=total,
              TotalSumSy=c.to_sys(total, d), StockPrice=q6(price), FromWhsCod=from_whs, WhsCode=to_whs, ObjType="67", VisOrder=0)
        c.move_stock(d, item, from_whs, ZERO, qty, price, TT_TRANSFER, entry, 0)
        c.move_stock(d, item, to_whs, qty, ZERO, price, TT_TRANSFER, entry, 0)
        return entry

    def production(self, c: _Company, d0: date, days: int, last_month: bool) -> None:
        for n in range(6):
            fg = c.rng.choice(c.finished_goods)
            planned = Decimal(c.rng.randint(100, 400))
            late = last_month and n < 2
            for rm, per_unit in self.bom[fg]:
                planned = min(planned, (c.stock.get((rm, WHS_MAIN), ZERO) / per_unit).to_integral_value(rounding=ROUND_FLOOR))
            if planned <= ZERO:
                continue
            entry = c.entry("OWOR")
            start = d0 + timedelta(days=(days - c.rng.randint(1, 2)) if late else c.rng.randint(8, 12))
            close = start + timedelta(days=c.rng.randint(3, 8))
            closed = close <= self.as_of
            if not closed:
                c.open_production[fg] = c.open_production.get(fg, ZERO) + planned
            stamp = c.stamp(start)
            c.add("OWOR", DocEntry=entry, DocNum=entry, ItemCode=fg, Status="L" if closed else "R", Type="S",
                  PlannedQty=planned, CmpltQty=planned if closed else ZERO, RjctQty=ZERO, PostDate=ts(start),
                  DueDate=ts(close), StartDate=ts(start), CloseDate=ts(close) if closed else None, Warehouse=WHS_MAIN,
                  **stamp)
            for n, (rm, per_unit) in enumerate(self.bom[fg]):
                issued = q6(per_unit * planned) if closed else ZERO
                c.add("WOR1", DocEntry=entry, LineNum=n, ItemCode=rm, BaseQty=per_unit, PlannedQty=q6(per_unit * planned),
                      IssuedQty=issued, wareHouse=WHS_MAIN, ItemType=4)
                if closed:
                    c.move_stock(close, rm, WHS_MAIN, ZERO, issued, c.item_cost[rm], TT_PRODUCTION_ISSUE, entry, n, production_order=entry)
            if closed:
                c.move_stock(close, fg, WHS_MAIN, planned, ZERO, c.item_cost[fg], TT_PRODUCTION_RECEIPT, entry, 0, production_order=entry)
                c.receive_batch(close, fg, WHS_MAIN, planned, f"{fg}-{close.year}{close.month:02d}-{entry}", close,
                                close + timedelta(days=c.rng.choice([120, 365, 540])), TT_PRODUCTION_RECEIPT, entry, 0)

    def sale_chain(self, c: _Company, d: date, customer: str, lines: List[Dict[str, Any]]) -> Tuple[int, Decimal, Decimal, date]:
        for ln in lines:
            ln["cost"] = c.item_cost[ln["item"]]
        d = c.clamp(d)
        order, _, _ = c.marketing_doc("ORDR", "RDR1", "17", d, customer, lines, closed=True)
        d_del = c.clamp(d + timedelta(days=1))
        dln, _, _ = c.marketing_doc("ODLN", "DLN1", "15", d_del, customer, lines, base=(17, order), closed=True)
        c.link_target("RDR1", order, "15", dln)
        cogs = ZERO
        for i, ln in enumerate(lines):
            qty = q6(Decimal(ln["qty"]))
            c.move_stock(d_del, ln["item"], ln["whs"], ZERO, qty, c.item_cost[ln["item"]], TT_DELIVERY, dln, i)
            c.consume_batches(d_del, ln["item"], ln["whs"], qty, 15, dln, i)
            cogs += q6(qty * c.item_cost[ln["item"]])
        c.journal(d_del, f"COGS {dln}", "15", dln, [(ACCT_COGS, cogs, ZERO, None), (ACCT_INVENTORY, ZERO, cogs, None)])
        d_inv = c.clamp(d_del + timedelta(days=1))
        inv, net, vat = c.marketing_doc("OINV", "INV1", "13", d_inv, customer, lines, base=(15, dln))
        c.link_target("DLN1", dln, "13", inv)
        trans = c.journal(d_inv, f"AR {inv}", "13", inv, [
            (ACCT_AR, q6(net + vat), ZERO, customer), (ACCT_REVENUE, ZERO, net, None), (ACCT_VAT_PAYABLE, ZERO, vat, None)])
        c.set_header("OINV", inv, TransId=trans)
        truth = c.truth_for(d_inv)
        truth.invoices += 1
        truth.revenue_gross_lc += net
        truth.revenue_net_lc += net
        truth.revenue_net_sc += c.to_sys(net, d_inv)
        truth.revenue_account_lc += net
        truth.revenue_account_sc += c.to_sys(net, d_inv)
        c.truth_for(d_del).cogs_lc += cogs
        if customer in INTERCOMPANY_CUSTOMER.values():
            buyer = next(alias for alias, code in INTERCOMPANY_CUSTOMER.items() if code == customer)
            truth.intercompany_sales_lc[buyer] = truth.intercompany_sales_lc.get(buyer, ZERO) + net
        return inv, net, vat, d_inv

    def credit_memo(self, c: _Company, d: date, customer: str, amount: Decimal) -> None:
        d = c.clamp(d)
        lines = [{"item": None, "name": "Ajuste comercial", "qty": "1", "price": str(amount), "whs": None, "account": ACCT_REVENUE}]
        rin, net, vat = c.marketing_doc("ORIN", "RIN1", "14", d, customer, lines, doc_type="S")
        trans = c.journal(d, f"CM {rin}", "14", rin, [
            (ACCT_REVENUE, net, ZERO, None), (ACCT_VAT_PAYABLE, vat, ZERO, None), (ACCT_AR, ZERO, q6(net + vat), customer)])
        c.set_header("ORIN", rin, TransId=trans)
        truth = c.truth_for(d)
        truth.credit_memos += 1
        truth.credit_lc += net
        truth.revenue_net_lc -= net
        truth.revenue_net_sc -= c.to_sys(net, d)
        truth.revenue_account_lc -= net
        truth.revenue_account_sc -= c.to_sys(net, d)

    def cancel_invoice(self, c: _Company, inv: int, net: Decimal, vat: Decimal, customer: str,
                       lines: List[Dict[str, Any]], d_cancel: date, d_inv: date) -> None:
        d_cancel = c.clamp(d_cancel)
        c.set_header("OINV", inv, CANCELED="Y", DocStatus="C", UpdateDate=ts(d_cancel), UpdateTS=hhmmss(c.rng))
        cancel_entry, _, _ = c.marketing_doc("OINV", "INV1", "13", d_cancel, customer, lines, canceled="C", base=(13, inv))
        c.link_target("INV1", inv, "13", cancel_entry)
        original_trans = next(r[b1.columns("OINV").index("TransId")] for r in c.rows["OINV"] if r[0] == inv)
        trans = c.journal(d_cancel, f"STORNO {inv}", "13", cancel_entry, [
            (ACCT_REVENUE, net, ZERO, None), (ACCT_VAT_PAYABLE, vat, ZERO, None), (ACCT_AR, ZERO, q6(net + vat), customer)],
            storno_to=original_trans)
        c.set_header("OINV", cancel_entry, TransId=trans)
        truth = c.truth_for(d_inv)
        truth.invoices_canceled += 1
        truth.revenue_gross_lc -= net
        truth.revenue_net_lc -= net
        truth.revenue_net_sc -= c.to_sys(net, d_inv)
        reversal = c.truth_for(d_cancel)
        reversal.cancellation_docs += 1
        reversal.revenue_account_lc -= net
        reversal.revenue_account_sc -= c.to_sys(net, d_cancel)

    def run_month(self, c: _Company, m: int, mirrored: Dict[str, List[Tuple[date, List[Dict[str, Any]]]]]) -> None:
        rng = c.rng
        d0 = month_start(self.start_month, m)
        days = (month_start(self.start_month, m + 1) - d0).days

        def sale_day() -> date:
            return d0 + timedelta(days=rng.randint(0, days - 4))

        if c.p.role == "manufacturer":
            weights = [max(1, int(self.rm_demand[rm])) for rm in c.raw_materials]
            for _ in range(12):
                items = rng.sample(c.raw_materials, rng.randint(2, 3), counts=weights)
                lines = [{"item": rm, "name": f"Articulo {rm}", "qty": str(rng.randint(150, 900)), "price": str(c.item_cost[rm]), "whs": WHS_MAIN} for rm in items]
                self.purchase_chain(c, d0 + timedelta(days=rng.randint(0, 5)), rng.choice(c.suppliers), lines)
            parked = rng.choice(c.raw_materials)
            parked_qty = min(Decimal(rng.randint(40, 120)), c.stock.get((parked, WHS_MAIN), ZERO))
            if parked_qty > ZERO:
                self.transfer(c, d0 + timedelta(days=7), parked, parked_qty, WHS_MAIN, WHS_SECOND)
            self.production(c, d0, days, last_month=(m == self.months - 1))
            if parked_qty > ZERO:
                self.transfer(c, d0 + timedelta(days=min(20, days - 1)), parked, parked_qty, WHS_SECOND, WHS_MAIN)
            sales: List[Tuple[str, date]] = [(INTERCOMPANY_CUSTOMER[a], sale_day()) for a in sorted(INTERCOMPANY_CUSTOMER) for _ in range(3)]
            sales += [(rng.choice(c.customers), sale_day()) for _ in range(12)]
        else:
            for d_invoice, lines in mirrored.get(c.p.alias, []):
                mirror = [{"item": ln["item"], "name": ln["name"], "qty": ln["qty"], "price": ln["price"], "whs": WHS_MAIN} for ln in lines]
                self.purchase_chain(c, d_invoice, INTERCOMPANY_SUPPLIER, mirror, batch_prefix="IC", same_day=True)
            sales = [(rng.choice(c.customers), sale_day()) for _ in range(12)]

        month_invoices: List[Tuple[int, Decimal, Decimal, str, List[Dict[str, Any]], date]] = []
        for customer, d_sale in sorted(sales, key=lambda s: (s[1], s[0])):
            intercompany = customer in INTERCOMPANY_CUSTOMER.values()
            items = rng.sample(c.finished_goods, rng.randint(2, 3))
            lines = []
            for fg in items:
                qty = rng.randint(10, 60) if c.p.role == "manufacturer" else rng.randint(3, 12)
                price = c.item_cost[fg] * Decimal("1.25") if intercompany else c.item_price[fg]
                qty = min(Decimal(qty), c.available_batches(fg, WHS_MAIN))
                if qty <= ZERO:
                    continue
                lines.append({"item": fg, "name": f"Articulo {fg}", "qty": str(qty), "price": str(q6(price)), "whs": WHS_MAIN})
            if not lines:
                continue
            inv, net, vat, d_inv = self.sale_chain(c, d_sale, customer, lines)
            month_invoices.append((inv, net, vat, customer, lines, d_inv))
            if intercompany:
                buyer = next(a for a, code in INTERCOMPANY_CUSTOMER.items() if code == customer)
                mirrored.setdefault(buyer, []).append((d_inv, [dict(ln) for ln in lines]))

        external = [x for x in month_invoices if x[3] not in INTERCOMPANY_CUSTOMER.values()]
        if external:
            _, net, _, customer, _, d_inv = external[-1]
            self.credit_memo(c, d_inv + timedelta(days=5), customer, q6(net * Decimal("0.05")))
        if m % 3 == 1 and external:
            inv, net, vat, customer, lines, d_inv = rng.choice(external)
            self.cancel_invoice(c, inv, net, vat, customer, lines, d_inv + timedelta(days=rng.randint(1, 7)), d_inv)

    def open_purchase_orders(self, c: _Company) -> None:
        d = self.as_of - timedelta(days=OPEN_PO_AGE_DAYS)
        bought = [code for code in c.raw_materials + c.finished_goods if not (code.startswith("FG-") and c.p.role == "manufacturer")]

        def supplier(code: str) -> str:
            return INTERCOMPANY_SUPPLIER if code.startswith("FG-") else f"S-{int(code[3:]) % 10 + 1:04d}"

        def line(code: str, qty: Decimal) -> Dict[str, Any]:
            return {"item": code, "name": f"Articulo {code}", "qty": str(qty), "price": str(c.item_cost[code]), "whs": WHS_MAIN}

        for n, code in enumerate(bought[::3]):
            qty = Decimal(100 + 50 * n)
            c.marketing_doc("OPOR", "POR1", "22", d, supplier(code), [line(code, qty)])
            c.open_purchases[code] = c.open_purchases.get(code, ZERO) + qty
        if bought:
            c.marketing_doc("OPOR", "POR1", "22", d, supplier(bought[0]), [line(bought[0], Decimal("999"))], canceled="Y")

    def open_sales_orders(self, c: _Company) -> None:
        d = self.as_of - timedelta(days=OPEN_SO_AGE_DAYS)
        cols = b1.columns("OINM")
        start = self.as_of - timedelta(days=90)
        for code, days_left in zip(c.finished_goods[1::3], COMMITTED_COVER_DAYS):
            sold = sum((row[cols.index("OutQty")] for row in c.rows["OINM"]
                        if row[cols.index("ItemCode")] == code and row[cols.index("TransType")] == TT_DELIVERY
                        and start < row[cols.index("DocDate")].date() <= self.as_of), ZERO)
            keep = (sold / 90 * days_left).to_integral_value(rounding=ROUND_FLOOR)
            qty = c.stock.get((code, WHS_MAIN), ZERO) - keep
            if qty <= ZERO or not c.customers:
                continue
            c.marketing_doc("ORDR", "RDR1", "17", d, c.customers[0],
                            [{"item": code, "name": f"Articulo {code}", "qty": str(qty), "price": str(c.item_price[code]), "whs": WHS_MAIN}])
            c.committed[code] = c.committed.get(code, ZERO) + qty

    def finish(self, c: _Company) -> Tuple[Dict[Tuple[str, str], Decimal], int]:
        self.open_purchase_orders(c)
        self.open_sales_orders(c)
        for (item, whs), on_hand in sorted(c.stock.items()):
            main = whs == WHS_MAIN
            on_order = (c.open_purchases.get(item, ZERO) + c.open_production.get(item, ZERO)) if main else ZERO
            minimum = min_stock(c.p.role, item) if main else ZERO
            committed = c.committed.get(item, ZERO) if main else ZERO
            c.add("OITW", ItemCode=item, WhsCode=whs, OnHand=q6(on_hand), IsCommited=q6(committed), OnOrder=q6(on_order),
                  AvgPrice=c.item_cost[item], MinStock=minimum, MaxStock=minimum * 4)
        expired = 0
        for (item, whs), batches in sorted(c.batches.items()):
            for sysno, dist, qty in batches:
                c.add("OBTQ", ItemCode=item, SysNumber=sysno, WhsCode=whs, Quantity=q6(qty))
                c.add("OIBT", ItemCode=item, BatchNum=dist, WhsCode=whs, Quantity=q6(qty))
                if qty > ZERO and c.batch_meta[(item, sysno)][2] < self.as_of:
                    expired += 1
        return dict(c.stock), expired

    def build(self) -> Dataset:
        self.build_shared()
        for profile in self.companies:
            c = _Company(profile, random.Random(f"{self.seed}:{profile.alias}"), self)
            self.state[profile.alias] = c
            self.build_company_masters(c)
            self.opening_stock(c)
        order = sorted(self.companies, key=lambda p: (p.role != "manufacturer", p.alias))
        for m in range(self.months):
            mirrored: Dict[str, List[Tuple[date, List[Dict[str, Any]]]]] = {}
            for profile in order:
                self.run_month(self.state[profile.alias], m, mirrored)
        closing: Dict[str, Dict[Tuple[str, str], Decimal]] = {}
        expired: Dict[str, int] = {}
        for alias, c in self.state.items():
            closing[alias], expired[alias] = self.finish(c)
        return Dataset(
            seed=self.seed, start_month=self.start_month, months=self.months, as_of=self.as_of,
            companies=self.companies, tables={a: c.rows for a, c in self.state.items()},
            truth={a: c.truth for a, c in self.state.items()}, closing_stock=closing, expired_batches=expired,
        )


def generate(seed: int = 7, start_month: date = date(2024, 10, 1), months: int = 24,
             companies: Tuple[CompanyProfile, ...] = DEFAULT_COMPANIES) -> Dataset:
    if months < 1:
        raise ValueError("months must be >= 1")
    roles = {p.role for p in companies}
    if "manufacturer" not in roles:
        raise ValueError("the dataset needs a manufacturer")
    return _Builder(seed, start_month, months, tuple(companies)).build()
