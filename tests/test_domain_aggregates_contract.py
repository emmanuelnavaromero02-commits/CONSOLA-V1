from __future__ import annotations

import inspect
import re
import typing
from pathlib import Path

from app.services.intelligence import (
    domain_aggregate_support as support,
    finance_aggregates,
    operations_aggregates,
    risk_aggregates,
)

REPO_ROOT = Path(__file__).resolve().parents[1]
INTELLIGENCE = REPO_ROOT / "console" / "app" / "services" / "intelligence"
MODULES = {
    "finance": INTELLIGENCE / "finance_aggregates.py",
    "operations": INTELLIGENCE / "operations_aggregates.py",
    "risk": INTELLIGENCE / "risk_aggregates.py",
}
SUPPORT = INTELLIGENCE / "domain_aggregate_support.py"
PY_MODULES = {
    "finance": finance_aggregates,
    "operations": operations_aggregates,
    "risk": risk_aggregates,
}

FORBIDDEN_FUNCTIONS = (
    "query_budget_vs_actual",
    "query_cost_center_overrun",
    "query_shift_coverage",
    "query_unbilled_validated_hours",
    "query_payroll_cost",
    "query_contract_expiry",
)
EXPECTED_FUNCTIONS = {
    "finance": {
        "query_billable_hours_logged",
        "query_labor_cost_by_department",
        "query_project_margin",
    },
    "operations": {
        "query_pipeline_health",
        "query_data_freshness_by_cartridge",
        "query_absence_rate_company_by_type",
    },
    "risk": {
        "query_attrition_risk_population",
        "query_employment_end_expiry",
        "query_deal_slippage",
    },
}
PROXY_FUNCTIONS = {
    "query_billable_hours_logged",
    "query_labor_cost_by_department",
    "query_project_margin",
    "query_data_freshness_by_cartridge",
    "query_absence_rate_company_by_type",
    "query_employment_end_expiry",
}


def _source(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def test_modules_never_read_rows():
    for name, path in MODULES.items():
        src = _source(path)
        assert "fetchall" not in src, name
        assert ".format(" not in src, name
        assert "SELECT *" not in src, name
        assert not re.search(r"\bLIMIT\s+\d", src), name
        assert "clamp_top_n" in src, name
        for match in re.finditer(r"\bLIMIT\s+(\S+)", src):
            assert match.group(1).startswith("$"), (name, match.group(0))
    support_src = _source(SUPPORT)
    assert "fetchall" not in support_src
    assert re.search(r"\bLIMIT\b", support_src) is None


def test_gold_modules_follow_the_talent_pattern():
    support_src = _source(SUPPORT)
    assert 'conn.transaction(isolation="repeatable_read", readonly=True)' in support_src
    assert "set_config('app.tenant_id', $1, true)" in support_src
    assert "resolve_published_gold_relation(" in support_src
    assert "SELECT to_regclass($1)" in support_src
    assert "asyncpg.connect(dsn, command_timeout=" in support_src
    assert (
        'GOLD_SCOPE_PREDICATE = "workspace_id::text = $1 AND tenant_id::text = $2"'
        in support_src
    )
    for name, path in list(MODULES.items()) + [("support", SUPPORT)]:
        src = _source(path)
        assert (
            'f"gold_' not in src and "'gold_' +" not in src and '"gold_" +' not in src
        ), name
    for name in ("finance", "risk"):
        src = _source(MODULES[name])
        assert "run_gold_aggregate(" in src, name
        assert "resolve_relation(" in src, name
        assert "GOLD_SCOPE_PREDICATE" in src, name


def test_operations_uses_console_scope_with_rls_gucs():
    src = _source(MODULES["operations"])
    assert (
        "from app.services.db_scope import scoped_db, workspace_scope_from_user" in src
    )
    assert 'conn.transaction(isolation="repeatable_read", readonly=True)' in src
    assert "scoped_db(conn, tenant_id, workspace_id)" in src
    assert "CONSOLE_SCOPE_PREDICATE = (" in src
    assert (
        '"workspace_id = $1::uuid AND ($2::uuid IS NULL OR tenant_id = $2::uuid)"'
        in src
    )
    assert operations_aggregates.CONSOLE_SCOPE_PREDICATE == (
        "workspace_id = $1::uuid AND ($2::uuid IS NULL OR tenant_id = $2::uuid)"
    )
    assert "WHY TWO DSNs" in src
    assert "OPERATIONS_FRESHNESS_SLA_HOURS" in src


def test_unsupported_metrics_have_no_stub():
    for name, module in PY_MODULES.items():
        public = {
            attr
            for attr in dir(module)
            if attr.startswith("query_") and not attr.startswith("_")
        }
        assert public == EXPECTED_FUNCTIONS[name], (name, public)
        for forbidden in FORBIDDEN_FUNCTIONS:
            assert not hasattr(module, forbidden), (name, forbidden)
    for path in MODULES.values():
        src = _source(path)
        for forbidden in FORBIDDEN_FUNCTIONS:
            assert f"def {forbidden}" not in src


def test_every_query_returns_the_shared_result_contract():
    for name, module in PY_MODULES.items():
        for attr in EXPECTED_FUNCTIONS[name]:
            fn = getattr(module, attr)
            assert inspect.iscoroutinefunction(fn), attr
            hints = typing.get_type_hints(fn)
            result_type = hints["return"]
            assert issubclass(result_type, support.AggregateResult), attr
            default = result_type()
            assert default.status == support.STATUS_UNAVAILABLE
            assert default.supported is True
            assert hasattr(default, "proxy_note")
            assert hasattr(default, "evidence_refs")
            assert callable(default.to_dict)
            assert "supported" in default.to_dict()


def test_proxy_notes_say_what_is_not_measured():
    notes = {
        "query_billable_hours_logged": finance_aggregates.BILLABLE_HOURS_PROXY_NOTE,
        "query_labor_cost_by_department": finance_aggregates.LABOR_COST_PROXY_NOTE,
        "query_project_margin": finance_aggregates.PROJECT_MARGIN_PROXY_NOTE,
        "query_data_freshness_by_cartridge": operations_aggregates.FRESHNESS_PROXY_NOTE,
        "query_absence_rate_company_by_type": operations_aggregates.ABSENCE_RATE_PROXY_NOTE,
        "query_employment_end_expiry": risk_aggregates.EMPLOYMENT_END_PROXY_NOTE,
    }
    assert set(notes) == PROXY_FUNCTIONS
    for attr, note in notes.items():
        assert len(note) > 80, attr
        assert "NO " in note, attr


def test_dataset_names_match_cartridge_definitions():
    cartridges = REPO_ROOT / "cartridges"
    expected = {
        finance_aggregates.CONSULTOR_MENSUAL_DATASET: "replicon",
        finance_aggregates.COSTO_CONSULTOR_DATASET: "replicon",
        finance_aggregates.PNL_MENSUAL_DATASET: "replicon",
        operations_aggregates.ABSENCE_DATASET: "sap_hcm",
        operations_aggregates.HEADCOUNT_DATASET: "sap_hcm",
        risk_aggregates.RETENTION_RISK_DATASET: "sap_successfactors",
        risk_aggregates.ACTION_CANDIDATES_DATASET: "sap_successfactors",
        risk_aggregates.EMPLOYEE_360_DATASET: "sap_successfactors",
        risk_aggregates.DEALS_AT_RISK_DATASET: "salesforce",
    }
    for dataset, cartridge in expected.items():
        path = cartridges / cartridge / "datasets" / f"{dataset}.sql"
        assert path.exists(), path
        header = path.read_text(encoding="utf-8").splitlines()[0]
        assert "(gold)" in header, (dataset, header)


def test_required_columns_exist_in_dataset_definitions():
    cartridges = REPO_ROOT / "cartridges"
    checks = [
        (
            "replicon",
            "consultor_mensual",
            finance_aggregates._BILLABLE_REQUIRED
            | finance_aggregates._BILLABLE_OPTIONAL,
        ),
        (
            "replicon",
            "costo_consultor_mensual",
            finance_aggregates._LABOR_REQUIRED | finance_aggregates._LABOR_OPTIONAL,
        ),
        (
            "replicon",
            "pnl_mensual",
            finance_aggregates._MARGIN_REQUIRED | finance_aggregates._MARGIN_OPTIONAL,
        ),
        (
            "sap_hcm",
            "absence_by_type_and_month",
            operations_aggregates._ABSENCE_REQUIRED
            | operations_aggregates._ABSENCE_OPTIONAL,
        ),
        (
            "sap_hcm",
            "headcount_by_department",
            operations_aggregates._HEADCOUNT_REQUIRED,
        ),
        (
            "sap_successfactors",
            "sap_successfactors_talent_retention_risk",
            risk_aggregates._RETENTION_REQUIRED | risk_aggregates._RETENTION_OPTIONAL,
        ),
        (
            "sap_successfactors",
            "sap_successfactors_talent_action_candidates",
            risk_aggregates._ACTION_REQUIRED | risk_aggregates._ACTION_OPTIONAL,
        ),
        (
            "sap_successfactors",
            "sap_successfactors_employee_360",
            risk_aggregates._EMPLOYEE_REQUIRED | risk_aggregates._EMPLOYEE_OPTIONAL,
        ),
        (
            "salesforce",
            "salesforce_deals_en_riesgo",
            risk_aggregates._DEALS_REQUIRED | risk_aggregates._DEALS_OPTIONAL,
        ),
    ]
    for cartridge, dataset, columns in checks:
        sql = (cartridges / cartridge / "datasets" / f"{dataset}.sql").read_text(
            encoding="utf-8"
        )
        for column in columns:
            pattern = rf"(AS\s+{re.escape(column)}\b|\b{re.escape(column)}\s*,|\b{re.escape(column)}\s*$|\.{re.escape(column)}\b)"
            assert re.search(pattern, sql, re.MULTILINE), (dataset, column)
