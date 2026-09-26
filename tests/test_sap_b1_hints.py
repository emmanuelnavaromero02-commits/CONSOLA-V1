from __future__ import annotations

import asyncio
import re
import sys
from pathlib import Path

import pytest
import yaml

REPO = Path(__file__).resolve().parents[1]
CARTRIDGE = REPO / "cartridges" / "sap_b1"
HINTS = CARTRIDGE / "hints" / "assistant.md"
CONFIG = CARTRIDGE / "app" / "config"
DATASETS = CARTRIDGE / "datasets"
APPS = CARTRIDGE / "apps"
PARAMETERS = CARTRIDGE / "app" / "services" / "business_parameters_mapping.py"
CONTROL_ROOM_TOOLS = REPO / "mcp-infra" / "app" / "tools" / "control_room.py"
STUDIO_ASSISTANT = REPO / "console" / "app" / "services" / "studio_assistant.py"
WORKSPACE_ASSISTANT = REPO / "workspace" / "app" / "services" / "consumer_assistant.py"
AGENT_RUNTIME = REPO / "console" / "app" / "services" / "agent_runtime.py"
DEPLOY = REPO / "infra" / "terraform" / "deploy"
STUDIO_SECTIONS = REPO / "console-next" / "src" / "lib" / "studio" / "sections.ts"
CONNECTOR = CONFIG / "connector.yaml"
STUDIO_TOOL_STEPS = {
    "query_dataset": {4, 5},
    "get_schema": {4},
    "describe_silver": {4},
    "cartridge_run_kb": {4},
}

MAX_HINTS_CHARS = 8000
REQUIRED_SECTIONS = [
    "Identidad del cartucho",
    "Modelo de datos",
    "Convenciones",
    "Reglas operativas",
    "Apps publicadas",
    "Limitaciones honestas",
]
KPI_TOOL = "control_room__sap_b1_kpis_read"


def _hints() -> str:
    return HINTS.read_text(encoding="utf-8")


def _yaml(name: str) -> dict:
    return yaml.safe_load((CONFIG / name).read_text(encoding="utf-8")) or {}


def _entity_names() -> set[str]:
    return {e["entity"] for e in _yaml("entities.yaml")["entities"]}


def _indicators() -> list[dict]:
    return _yaml("indicators.yaml")["indicators"]


def _catalog() -> dict[str, str | None]:
    pattern = r'ParameterSpec\("([a-z0-9_]+)", "(\w+)", "[^"]*", (None|"[^"]*")'
    found = re.findall(pattern, PARAMETERS.read_text(encoding="utf-8"))
    return {key: (None if default == "None" else default.strip('"')) for key, _kind, default in found}


def _string_set(source: Path, name: str) -> set[str]:
    text = source.read_text(encoding="utf-8")
    start = text.index(f"{name} = {{")
    depth = 0
    for pos in range(text.index("{", start), len(text)):
        depth += {"{": 1, "}": -1}.get(text[pos], 0)
        if depth == 0:
            return set(re.findall(r'"([A-Za-z0-9_]+)"', text[start:pos]))
    raise AssertionError(f"{name} not closed in {source}")


def _code_corpus() -> str:
    parts = [
        path.read_text(encoding="utf-8")
        for pattern in ("app/**/*.py", "app/**/*.yaml", "datasets/*.sql", "apps/*.json")
        for path in CARTRIDGE.glob(pattern)
    ]
    for path in (CONTROL_ROOM_TOOLS, STUDIO_ASSISTANT, WORKSPACE_ASSISTANT):
        parts.append(path.read_text(encoding="utf-8"))
    return "\n".join(parts)


def test_hints_exist_and_markdown():
    assert HINTS.is_file(), "hints/assistant.md missing"
    text = _hints()
    assert text.lstrip().startswith("#")
    assert text.count("##") >= 6


def test_hints_has_six_sections():
    text = _hints()
    for section in REQUIRED_SECTIONS:
        assert section in text, f"hints missing section: {section!r}"


def test_hints_under_injection_cap():
    text = _hints()
    assert len(text) <= MAX_HINTS_CHARS, f"hints {len(text)} chars exceed cap {MAX_HINTS_CHARS}"
    source = AGENT_RUNTIME.read_text(encoding="utf-8")
    assert f"MAX_HINTS_CHARS = {MAX_HINTS_CHARS}" in source
    block = re.search(r"_HINT_INJECTION_TOKENS = \((.*?)\n\)", source, re.S)
    assert block, "agent_runtime no longer declares _HINT_INJECTION_TOKENS"
    tokens = re.findall(r'"([^"]+)"', block.group(1))
    assert len(tokens) == 6
    for token in tokens:
        assert token.lower() not in text.lower(), token


def test_hints_name_no_hosts_or_addresses():
    text = _hints()
    assert re.search(r"https?://", text) is None
    assert re.search(r"\b\d{1,3}(?:\.\d{1,3}){3}\b", text) is None
    assert re.search(r"\b[a-z0-9-]+\.(?:com|net|org|io|mx|local|internal)\b", text, re.I) is None


def test_hints_references_existing_kbs():
    referenced = set(re.findall(r"\bkb_[a-z0-9_]+", _hints()))
    real = {kb["id"] for kb in _yaml("knowledge_bits.yaml")["knowledge_bits"]}
    assert real == {"kb_sales_by_company_month", "kb_stock_on_hand"}
    assert referenced
    assert referenced <= real


def test_hints_cover_every_indicator():
    indicators = _indicators()
    assert len(indicators) == 12
    text = _hints()
    for indicator in indicators:
        assert f"`{indicator['id']}`" in text, indicator["id"]
    datasets = {indicator["dataset"] for indicator in indicators}
    assert len(datasets) == 6
    for dataset in datasets:
        assert f"`{dataset}`" in text, dataset


def test_hints_datasets_and_apps_exist():
    referenced = set(re.findall(r"\bsap_b1_[a-z0-9_]+", _hints()))
    assert referenced
    for name in referenced:
        assert (DATASETS / f"{name}.sql").is_file() or (APPS / f"{name}.html").is_file(), name


def test_hints_list_every_gold_dataset():
    gold = {
        path.stem
        for path in DATASETS.glob("*.sql")
        if "(gold)" in path.read_text(encoding="utf-8").splitlines()[0]
    }
    assert len(gold) == 21
    text = _hints()
    for name in gold:
        assert f"`{name}`" in text, name


def test_hints_references_every_app():
    apps = sorted(path.stem for path in APPS.glob("*.html"))
    assert apps == ["sap_b1_abasto", "sap_b1_margen", "sap_b1_sellout"]
    text = _hints()
    for app in apps:
        assert f"`{app}`" in text, app


def test_hints_parameters_match_the_catalog():
    catalog = _catalog()
    assert len(catalog) == 20
    text = _hints()
    thresholds = {key for ind in _indicators() for key in ind.get("thresholds") or []}
    assert len(thresholds) == 10
    for key in thresholds:
        assert key in catalog, key
        assert f"`{key}`" in text, key
    kinds = dict(re.findall(r'ParameterSpec\("([a-z0-9_]+)", "(\w+)"', PARAMETERS.read_text(encoding="utf-8")))
    no_default = sorted(key for key, default in catalog.items() if default is None and kinds[key] == "threshold")
    assert len(no_default) == 7
    listed = " ".join(text.split())
    block = listed[listed.index("Umbrales sin valor en el catálogo:"):listed.index("Valores por defecto:")]
    for key in no_default:
        assert f"`{key}`" in block, key
    for key, default in catalog.items():
        if default is not None and f"`{key}`" in text:
            assert f"`{key}` = {default}" in text, key
    for key, value in re.findall(r"`([a-z0-9_]+)` = (\d+)", text):
        assert catalog.get(key) == value, (key, value)
    parameters = text[text.index("**Parámetros:**"):text.index("## 4.")]
    for key in re.findall(r"`([a-z0-9_]+)`", parameters):
        if not key.startswith("sap_b1_"):
            assert key in catalog, key


def test_hints_table_count_matches_catalog():
    counts = re.findall(r"(\d+) tablas", _hints())
    assert counts
    assert {int(n) for n in counts} == {len(_entity_names())}
    assert len(_entity_names()) == 48


def test_hints_tables_exist():
    tables = re.findall(r"`(O[A-Z]{3}|[A-Z]{3}1)`", _hints())
    assert len(set(tables)) >= 30
    assert set(tables) <= _entity_names()


def test_every_backticked_identifier_exists_in_code():
    corpus = _code_corpus()
    identifiers = {
        token
        for token in re.findall(r"`([^`]+)`", _hints())
        if re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", token)
    }
    assert identifiers
    missing = sorted(token for token in identifiers if not re.search(rf"\b{token}\b", corpus))
    assert not missing, missing


def test_hints_kpi_tool_and_cases_exist():
    source = CONTROL_ROOM_TOOLS.read_text(encoding="utf-8")
    assert f'name="{KPI_TOOL}"' in source
    cases = set(re.findall(r'^\s+"([a-z]+)": "sap_b1_[a-z_]+_kpis",$', source, re.M))
    assert cases == {"abasto", "aprendizaje", "caducidad", "margen", "semaforo", "ventas"}
    text = _hints()
    assert KPI_TOOL in text
    line = next(line for line in text.splitlines() if KPI_TOOL in line)
    mentioned = set(re.findall(r"[a-z]+", text[text.index(line) : text.index(line) + 200]))
    assert cases <= mentioned


def _studio_module(monkeypatch):
    monkeypatch.setenv("APP_ENV", "test")
    monkeypatch.setenv("INTERNAL_API_KEY", "x" * 64)
    sys.path.insert(0, str(REPO / "console"))
    for name in list(sys.modules):
        if name == "app" or name.startswith("app."):
            del sys.modules[name]
    from app.services import studio_assistant

    return studio_assistant


def _studio_tabs() -> dict[int, str]:
    source = STUDIO_SECTIONS.read_text(encoding="utf-8")
    tabs = re.findall(r'id: "\w+",\s*label: "([^"]+)",\s*step: (\d+)', source)
    return {int(step): label for label, step in tabs}


def test_hints_route_studio_to_the_steps_that_expose_each_tool(monkeypatch):
    studio = _studio_module(monkeypatch)
    tabs = _studio_tabs()
    text = " ".join(_hints().split())
    assert f"en Studio, `query_dataset` está en {tabs[4]} y en {tabs[5]}" in text
    assert f"`get_schema`, `describe_silver` y `cartridge_run_kb` solo en {tabs[4]}" in text
    for tool, claimed in STUDIO_TOOL_STEPS.items():
        assert tool in studio.STUDIO_TOOLS_WHITELIST, tool
        assert f"`{tool}`" in text, tool
        exposed = {
            step
            for step in tabs
            if studio.filter_tools_for_step([{"name": f"server__{tool}"}], step)
        }
        assert exposed == claimed, (tool, exposed)


def test_hints_route_workspace_and_agents_to_tools_they_have():
    studio = _string_set(STUDIO_ASSISTANT, "STUDIO_TOOLS_WHITELIST")
    workspace = _string_set(WORKSPACE_ASSISTANT, "ALLOWED_TOOLS")
    assert KPI_TOOL not in studio
    assert KPI_TOOL not in workspace
    for tool in ("query_dataset", "get_schema", "describe_silver"):
        assert tool in workspace, tool
    text = " ".join(_hints().split())
    assert "En Workspace: `query_dataset`, `get_schema` y `describe_silver`." in text
    assert f"`{KPI_TOOL}` (case" in text and "solo está en los agentes que la tienen asignada" in text


def test_packaged_hints_seed_updates_sap_b1(monkeypatch):
    sys.path.insert(0, str(REPO / "console"))
    from app.services import seed_packaged_hints as seeder

    calls: list[tuple] = []

    class _Conn:
        async def execute(self, sql, *args):
            calls.append(args)
            return "UPDATE 1"

    class _Acquire:
        async def __aenter__(self):
            return _Conn()

        async def __aexit__(self, *exc):
            return False

    class _Pool:
        def acquire(self):
            return _Acquire()

    monkeypatch.setattr(seeder, "_REGISTRY", REPO / "cartridges")
    asyncio.run(seeder.seed_packaged_hints(_Pool()))

    assert ("sap_b1", _hints().strip()) in calls


def test_production_path_creates_and_mounts():
    cartridges = yaml.safe_load((DEPLOY / "docker-compose.cartridges.yml").read_text(encoding="utf-8"))
    assert "sap-b1" in cartridges["services"]
    deploy = (REPO / "scripts" / "deploy_main_aws.py").read_text(encoding="utf-8")
    loop = re.search(r"^for service in ([^;]+); do$", deploy, re.M)
    assert loop and "sap-b1" in loop.group(1).split()
    aws = yaml.safe_load((DEPLOY / "docker-compose.aws.yml").read_text(encoding="utf-8"))
    assert "/opt/modecissions/cartridges:/registry/cartridges:ro" in aws["services"]["console"]["volumes"]
    catalog = (CARTRIDGE / "app" / "services" / "catalog_service.py").read_text(encoding="utf-8")
    assert 'CARTRIDGE_ID = "sap_b1"' in catalog
    assert "INSERT INTO cartridges" in catalog


@pytest.mark.parametrize(
    "phrase",
    [
        "canceled = 'N'",
        "DiscPrcnt",
        "TransSeq",
        "hive_partitioning = true",
        "_source_updated_at DESC NULLS LAST, load_date DESC, _extracted_at DESC",
        "MAX(_source_updated_at) OVER (PARTITION BY _company, DocEntry)",
    ],
)
def test_hints_conventions_match_the_code(phrase):
    assert phrase in _hints()
    assert phrase in _code_corpus()


def test_hints_raw_rule_matches_every_incremental_silver_dataset():
    order = "ORDER BY _source_updated_at DESC NULLS LAST, load_date DESC, _extracted_at DESC"
    stamp = "MAX(_source_updated_at) OVER (PARTITION BY _company, DocEntry)"
    entities = {e["entity"]: e for e in _yaml("entities.yaml")["entities"]}
    for name, entity in entities.items():
        latest = DATASETS / f"sap_b1_{name.lower()}_latest.sql"
        if entity["mode"] != "incremental" or not latest.is_file() or name in {"OINM", "IBT1"}:
            continue
        sql = latest.read_text(encoding="utf-8")
        assert order in sql, name
    for path in DATASETS.glob("*_lines.sql"):
        sql = path.read_text(encoding="utf-8")
        if "DocEntry" in sql:
            assert order in sql and stamp in sql, path.name
    assert "`sap_b1_*_latest`" in _hints() and "`sap_b1_*_lines`" in _hints()


def test_hints_bronze_path_matches_the_connector_layout():
    template = yaml.safe_load(CONNECTOR.read_text(encoding="utf-8"))
    path = _find_key(template, "path_template")
    assert path == "raw/sap_b1/{entity}/tenant_id={tenant_id}/workspace_id={workspace_id}/load_date={load_date}/batch_id={run_id}/"
    hinted = re.sub(r"=\{[a-z_]+\}", "=…", path.replace("{entity}", "<TABLA>"))
    assert f"`{hinted}`" in _hints()


def _find_key(node, key):
    if isinstance(node, dict):
        if key in node:
            return node[key]
        node = list(node.values())
    if isinstance(node, list):
        for child in node:
            found = _find_key(child, key)
            if found is not None:
                return found
    return None


def test_hints_company_format_follows_each_dialect():
    connector = CONNECTOR.read_text(encoding="utf-8")
    assert '"alias=SCHEMA,..." (HANA) or "alias=DATABASE,..." (mssql)' in connector
    dialects = (CARTRIDGE / "app" / "core" / "b1_dialects.py").read_text(encoding="utf-8")
    assert '."dbo"' in dialects
    text = _hints()
    assert "`alias=ESQUEMA` en HANA" in text
    assert "`alias=BASE` en SQL Server" in text and "`dbo`" in text


def test_hints_name_the_silver_columns_in_snake_case():
    sql = "\n".join(path.read_text(encoding="utf-8") for path in DATASETS.glob("*.sql"))
    raw_fields = {f for e in _yaml("entities.yaml")["entities"] for f in e.get("select_fields") or []}
    text = _hints()
    for raw_name, silver_name in (
        ("CardCode", "card_code"),
        ("ItemCode", "item_code"),
        ("DocEntry", "doc_entry"),
        ("WhsCode", "warehouse"),
        ("WhsCode", "whs_code"),
    ):
        assert raw_name in raw_fields
        assert re.search(rf"\b{raw_name} AS [A-Z]+[^)]*\)\s+AS {silver_name}\b", sql), silver_name
        assert f"`{raw_name}`" in text and f"`{silver_name}`" in text
    assert re.search(r"\bAS company\b", sql) and "`company`" in text


def test_hints_commission_statement_matches_the_margin_sql():
    kpis = (DATASETS / "sap_b1_margin_kpis_month.sql").read_text(encoding="utf-8")
    books = kpis[kpis.index("books AS ("):kpis.index("external_customers AS (")]
    group = kpis[kpis.index("group_commission AS ("):kpis.index("rows AS (")]
    assert "SUM(commission_local)" in books and "scope" not in books
    assert "WHERE scope = 'external'" in group
    lines = (DATASETS / "sap_b1_ar_invoice_lines.sql").read_text(encoding="utf-8")
    commission = next(line for line in lines.splitlines() if "AS commission_local" in line)
    assert "intercompany" not in commission
    assert "también las intercompañía; el grupo solo resta la de ventas externas" in _hints()


def test_hints_sell_in_limitation_matches_the_scorecard_sql():
    sql = (DATASETS / "sap_b1_distributor_scorecard_month.sql").read_text(encoding="utf-8")
    assert "CASE WHEN COUNT(DISTINCT local_currency) = 1 THEN SUM(amount) END AS sell_in_amount" in sql
    assert "CASE WHEN COUNT(DISTINCT local_currency) = 1 THEN MAX(local_currency) END AS sell_in_currency" in sql
    assert "AS sell_in_amount_local" in sql and "COALESCE(si.sell_in_qty, 0)" in sql
    text = " ".join(_hints().split())
    assert (
        "si las vendedoras del mes tienen monedas locales distintas, `sell_in_amount_local` y "
        "`sell_in_currency` quedan vacíos (las unidades no)"
    ) in text


def test_hints_data_quality_fallback_matches_the_sql():
    sql = (DATASETS / "sap_b1_data_quality.sql").read_text(encoding="utf-8")
    assert sql.count("), 95)") == 2
    assert "'dq_min_pct_' || q.check_code" in sql
    assert _catalog()["dq_min_pct"] is None
    text = " ".join(_hints().split())
    assert "`dq_min_pct_<control>`; si falta, `sap_b1_data_quality` usa 95" in text
