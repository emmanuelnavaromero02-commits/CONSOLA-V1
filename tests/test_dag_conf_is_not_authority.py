from __future__ import annotations

import ast
import importlib.util
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
AIRFLOW_DAGS = ROOT / "airflow" / "dags"
DAG_SOURCES = sorted(
    [*ROOT.glob("cartridges/*/dags/*.py"), *AIRFLOW_DAGS.glob("*.py")]
)
# DAGs that act only on platform state: they never run a tenant cartridge.
PLATFORM_DAGS = {
    "airflow/dags/agent_runner.py": "agent schedules are read and scoped from the database",
    "airflow/dags/entity_scheduler.py": "issues run authority; never consumes it",
    "airflow/dags/dataset_refresh_chain.py": "admitted by its own conf-bound, single-use envelope",
}
ADMISSION_CALLS = {"admit_run", "service_run"}
# Wrappers a DAG may call instead of admit_run/service_run, with the module that owns them.
ADMISSION_WRAPPERS = {
    "security_context_from_conf": "market_security_context.py",
    "sap_b1_security_context": "b1_runtime_context.py",
}
CONTEXT_READERS = {"airflow/dags/cartridge_run_admission.py", "airflow/dags/dataset_refresh_chain.py"}
# The only modules allowed to produce signatures: purpose-built builders, the admission
# minting path and the verified-upstream refresh envelope.
SIGNERS = {
    "airflow/dags/runtime_security_context.py",
    "airflow/dags/cartridge_run_admission.py",
    "airflow/dags/dataset_refresh_admission.py",
}


def _rel(path: Path) -> str:
    return path.relative_to(ROOT).as_posix()


def _tree(path: Path) -> ast.AST:
    return ast.parse(path.read_text(encoding="utf-8"), filename=str(path))


def _called_names(tree: ast.AST) -> set[str]:
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            func = node.func
            if isinstance(func, ast.Name):
                names.add(func.id)
            elif isinstance(func, ast.Attribute):
                names.add(func.attr)
    return names


def _defines_dag(tree: ast.AST) -> bool:
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and any(
            (isinstance(d, ast.Call) and getattr(d.func, "id", None) == "dag")
            or getattr(d, "id", None) == "dag"
            for d in node.decorator_list
        ):
            return True
        if isinstance(node, ast.Call) and getattr(node.func, "id", None) == "DAG":
            return True
    return False


def _mentions_conf(node: ast.AST) -> bool:
    for child in ast.walk(node):
        if isinstance(child, ast.Name) and child.id in {"conf", "run_conf"}:
            return True
        if isinstance(child, ast.Attribute) and child.attr == "conf":
            return True
    return False


def test_every_dag_file_is_classified():
    dag_files = {_rel(path) for path in DAG_SOURCES if _defines_dag(_tree(path))}
    assert "cartridges/sap_successfactors/dags/sap_successfactors_extract_all.py" in dag_files
    assert "airflow/dags/file_ingest.py" in dag_files
    assert set(PLATFORM_DAGS) <= dag_files, "stale platform exemption"


def test_every_tenant_dag_admits_its_run_before_acting():
    wrappers_ok = {
        name: ADMISSION_CALLS & _called_names(_tree(AIRFLOW_DAGS / module))
        for name, module in ADMISSION_WRAPPERS.items()
    }
    assert all(wrappers_ok.values()), wrappers_ok
    missing = []
    for path in DAG_SOURCES:
        rel = _rel(path)
        tree = _tree(path)
        if rel in PLATFORM_DAGS or not _defines_dag(tree):
            continue
        called = _called_names(tree)
        if not (called & ADMISSION_CALLS or called & set(ADMISSION_WRAPPERS)):
            missing.append(rel)
    assert missing == [], f"tenant DAGs without run admission: {missing}"


def _signing_sites(tree: ast.AST) -> list[int]:
    sites = []
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name.lstrip("_").startswith("sign"):
            sites.append(node.lineno)
        if isinstance(node, ast.Call):
            func = node.func
            name = func.id if isinstance(func, ast.Name) else getattr(func, "attr", "")
            owner = getattr(getattr(func, "value", None), "id", "")
            if name.lstrip("_").startswith("sign") or (owner == "hmac" and name == "new"):
                sites.append(node.lineno)
    return sites


def test_no_dag_signs_its_own_context():
    offenders = [
        f"{_rel(path)}:{line}"
        for path in DAG_SOURCES
        if _rel(path) not in SIGNERS
        for line in _signing_sites(_tree(path))
    ]
    assert offenders == [], "DAGs mint authority only through AdmittedRun.context()"


def test_signing_modules_never_sign_conf_values():
    for rel in SIGNERS:
        for node in ast.walk(_tree(ROOT / rel)):
            if isinstance(node, ast.Call) and getattr(node.func, "id", "").lstrip("_").startswith("sign"):
                assert not any(
                    _mentions_conf(arg) for arg in [*node.args, *(kw.value for kw in node.keywords)]
                ), f"{rel}:{node.lineno}"


def test_conf_security_context_is_only_read_by_the_admission_code():
    offenders = []
    for path in DAG_SOURCES:
        rel = _rel(path)
        if rel in CONTEXT_READERS:
            continue
        for node in ast.walk(_tree(path)):
            read = (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and node.func.attr in {"get", "pop"}
                and node.args
                and isinstance(node.args[0], ast.Constant)
                and node.args[0].value == "security_context"
            ) or (
                isinstance(node, ast.Subscript)
                and isinstance(node.ctx, ast.Load)
                and isinstance(node.slice, ast.Constant)
                and node.slice.value == "security_context"
            )
            if read:
                offenders.append(f"{rel}:{node.lineno}")
    assert offenders == [], "conf security_context must reach cartridges only through admit_run"


def test_cartridge_dags_never_read_scope_from_conf():
    offenders = []
    for path in sorted(ROOT.glob("cartridges/*/dags/*.py")):
        for node in ast.walk(_tree(path)):
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and node.func.attr == "get"
                and _mentions_conf(node.func.value)
                and node.args
                and isinstance(node.args[0], ast.Constant)
                and node.args[0].value in {"tenant_id", "workspace_id"}
            ):
                offenders.append(f"{_rel(path)}:{node.lineno}")
    assert offenders == []


def test_the_sweep_catches_the_patterns_it_forbids(tmp_path):
    sample = tmp_path / "sample.py"
    sample.write_text(
        "from airflow.decorators import dag\n"
        "@dag()\n"
        "def d():\n"
        "    ctx = conf.get('security_context')\n"
        "    tenant = conf.get('tenant_id')\n"
        "    return _sign_security_context({'tenant_id': conf['tenant_id']})\n",
        encoding="utf-8",
    )
    tree = _tree(sample)
    assert _defines_dag(tree)
    assert not (_called_names(tree) & ADMISSION_CALLS)
    assert _signing_sites(tree) == [6]


def _load_console_service(name: str):
    path = ROOT / "console" / "app" / "services" / f"{name}.py"
    spec = importlib.util.spec_from_file_location(f"_sweep_{name}", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _generated_dag_sources() -> list[tuple[str, str]]:
    templates = _load_console_service("dag_templates")
    generator = _load_console_service("dag_code_generator")
    sources = [
        (f"template:{item['id']}", templates.get_code(item["id"], cartridge="hubspot", entity="deals"))
        for item in templates.get_all()
    ]
    for label, schema in (
        ("odata", {"connector": {"protocol": "odata", "api": {}, "auth": {"type": "basic"}}}),
        ("rest", {"connector": {"api": {}, "auth": {"type": "bearer_token"}}}),
    ):
        sources.append((f"generator:{label}", generator.generate_dag_code("synthetic", "Orders", schema)["code"]))
    return sources


GENERATED_DAGS = _generated_dag_sources()


@pytest.mark.parametrize("label,source", GENERATED_DAGS, ids=[label for label, _ in GENERATED_DAGS])
def test_generated_dags_take_scope_only_from_admitted_authority(label, source):
    tree = ast.parse(source)
    assert _defines_dag(tree), label
    assert "admit_run" in _called_names(tree), f"{label} never admits its run"
    assert _signing_sites(tree) == [], label
    extracts = [node for node in ast.walk(tree) if isinstance(node, ast.FunctionDef) and node.name == "extract"]
    assert extracts, label
    for extract in extracts:
        assert "_admitted_run" in _called_names(extract), f"{label}: extract runs without admission"
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        constant = node.args[0].value if node.args and isinstance(node.args[0], ast.Constant) else None
        if isinstance(func, ast.Attribute) and func.attr in {"get", "pop"} and constant == "security_context":
            pytest.fail(f"{label}:{node.lineno} reads conf security_context")
        if (
            isinstance(func, ast.Attribute)
            and func.attr == "get"
            and _mentions_conf(func.value)
            and constant in {"tenant_id", "workspace_id"}
        ):
            pytest.fail(f"{label}:{node.lineno} reads scope from conf")
