from __future__ import annotations

import json
import time
import uuid
from pathlib import Path

import psycopg2
import pytest
from psycopg2 import errors

from refinement.app.catalog_copilot import AutonomousCatalogWorker, CatalogCopilotHost
from refinement.app.catalog_copilot_probe import CatalogCopilotProbe
from refinement.app.catalog_copilot_store import CatalogCopilotStore
from refinement.app.catalog_overlay import CatalogAnnotations
from refinement.app.publication_public import published_catalog
from refinement.app.publication_semantics import snapshot_public_semantics
from refinement.tests.catalog_copilot_fakes import (
    DEPARTMENTS_SQL,
    EMPLOYEES_SQL,
    FakeHost,
    LocalEngine,
)
from tests.staged_publication_canaries import TENANT_A, WORKSPACE_A
from tests.test_operational_rls_console_refinement import (
    OMEGA_CONSOLE_PASSWORD,
    OMEGA_REFINEMENT_PASSWORD,
    POSTGRES_PASSWORD,
    POSTGRES_USER,
    postgres_with_real_init_schema,  # noqa: F401 - module fixture
)
from tests.test_staged_publication_integrity_live import _engine, _scope
from tests.test_staged_publication_live import (  # noqa: F401 - module fixture
    staged_publication_live_stack,
)


def _dsn(admin_dsn: str, role: str, password: str) -> str:
    return admin_dsn.replace(f"{POSTGRES_USER}:{POSTGRES_PASSWORD}", f"{role}:{password}")


def _refinement(admin_dsn: str) -> str:
    return _dsn(admin_dsn, "omega_refinement", OMEGA_REFINEMENT_PASSWORD)


def _workspace(admin_dsn: str, *datasets: str) -> dict:
    tenant, workspace = str(uuid.uuid4()), str(uuid.uuid4())
    slug = f"copilot-{uuid.uuid4().hex[:10]}"
    with psycopg2.connect(admin_dsn) as conn, conn.cursor() as cur:
        cur.execute(
            "INSERT INTO tenants(id,name,slug,status) VALUES(%s,%s,%s,'active')",
            (tenant, slug, slug),
        )
        cur.execute(
            "INSERT INTO workspaces(id,tenant_id,name) VALUES(%s,%s,%s)",
            (workspace, tenant, slug),
        )
        for name in datasets:
            cur.execute(
                "INSERT INTO datasets(name,layer,cartridge,tenant_id,workspace_id)"
                " VALUES(%s,'silver','sap_successfactors',%s,%s)",
                (name, tenant, workspace),
            )
    return {
        "trusted": True,
        "tenant_id": tenant,
        "workspace_id": workspace,
        "role": "admin",
        "workspace_role": "workspace_admin",
        "allowed_cartridges": ["*"],
    }


def _scoped(dsn: str, sec: dict, sql: str, params=(), fetch: bool = False):
    with psycopg2.connect(dsn) as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT set_config('app.tenant_id',%s,true),"
            "set_config('app.workspace_id',%s,true)",
            (sec["tenant_id"], sec["workspace_id"]),
        )
        cur.execute(sql, params)
        return cur.fetchall() if fetch else None


def test_migration_shape_on_the_real_schema(postgres_with_real_init_schema: str) -> None:
    admin = postgres_with_real_init_schema
    with psycopg2.connect(admin) as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT relrowsecurity, relforcerowsecurity FROM pg_class "
            "WHERE relname='catalog_copilot_state'"
        )
        assert cur.fetchone() == (True, True)
        cur.execute(
            """SELECT conname, convalidated FROM pg_constraint
                WHERE conrelid IN ('data_catalog'::regclass, 'data_relationships'::regclass)
                  AND conname LIKE ANY(ARRAY['data_catalog_%%','data_relationships_%%'])
                  AND contype='c'"""
        )
        checks = dict(cur.fetchall())
        for name in (
            "data_catalog_copilot_evidence_counts_only",
            "data_catalog_classifications_check",
            "data_relationships_origin_check",
            "data_relationships_join_hint_check",
            "data_relationships_cardinality_check",
        ):
            assert checks[name] is True, name
        cur.execute(
            "SELECT has_table_privilege('omega_refinement','catalog_copilot_state','INSERT'),"
            " has_table_privilege('omega_refinement','catalog_copilot_state','DELETE'),"
            " has_table_privilege('omega_console','catalog_copilot_state','SELECT'),"
            " has_table_privilege('omega_console','catalog_copilot_state','UPDATE'),"
            " has_table_privilege('omega_mcp_infra','catalog_copilot_state','SELECT')"
        )
        assert cur.fetchone() == (True, False, True, False, True)
        cur.execute(
            "SELECT count(*) FROM schema_migrations WHERE filename='99zzzzu_catalog_copilot.sql'"
        )
        assert cur.fetchone()[0] == 1


MIGRATION = Path(__file__).resolve().parents[1] / "infra/init/99zzzzu_catalog_copilot.sql"


def test_migration_reruns_and_backfills_legacy_rows(
    postgres_with_real_init_schema: str,
) -> None:
    admin = postgres_with_real_init_schema
    sec = _workspace(admin, "legacy_a", "legacy_b")
    with psycopg2.connect(admin) as conn, conn.cursor() as cur:
        cur.execute(
            "UPDATE datasets SET column_mapping = %s::jsonb "
            "WHERE workspace_id = %s AND name = 'legacy_a'",
            (
                json.dumps({"mapped": "Texto del paquete", "mapped_rewritten": "Original"}),
                sec["workspace_id"],
            ),
        )
    with psycopg2.connect(admin) as conn, conn.cursor() as cur:
        # What real databases still carry: the truncated-name UNIQUE of
        # 13_data_catalog.sql, 67's global index, and on beta databases the
        # composite primary key of 19_operational_stability_hotfix.sql.
        cur.execute(
            "CREATE UNIQUE INDEX IF NOT EXISTS data_relationships_edge_uq ON "
            "data_relationships (from_dataset, from_column, to_dataset, to_column)"
        )
        cur.execute(
            "ALTER TABLE data_relationships ADD CONSTRAINT "
            "data_relationships_from_dataset_from_column_to_dataset_to_c_key "
            "UNIQUE (from_dataset, from_column, to_dataset, to_column)"
        )
        cur.execute("ALTER TABLE data_relationships DROP CONSTRAINT data_relationships_pkey")
        cur.execute(
            "ALTER TABLE data_relationships ADD CONSTRAINT data_relationships_pkey "
            "PRIMARY KEY (from_dataset, from_column, to_dataset, to_column)"
        )
        cur.execute(
            "ALTER TABLE data_relationships DROP CONSTRAINT data_relationships_join_hint_check"
        )
        cur.execute(
            "ALTER TABLE data_catalog DROP CONSTRAINT data_catalog_description_origin_check"
        )
        for column, hint in (
            ("c1", "many_to_one"),
            ("c2", "one_to_many"),
            ("c3", " inner "),
            ("c4", "COALESCE"),
            ("c5", "LEFT"),
        ):
            cur.execute(
                """INSERT INTO data_relationships(from_dataset,from_column,to_dataset,
                       to_column,join_hint,tenant_id,workspace_id,scope_status)
                   VALUES('legacy_a',%s,'legacy_b',%s,%s,%s,%s,'scoped')""",
                (column, column, hint, sec["tenant_id"], sec["workspace_id"]),
            )
        for column, description, tags in (
            (
                "template",
                "Atributo descriptivo de estado proveniente de legacy a; aporta contexto "
                "de negocio para analisis y busqueda semantica.",
                ["semantic_enrichment", "auto_described"],
            ),
            (
                "rewritten",
                "Texto reescrito por RH",
                ["semantic_enrichment", "auto_described"],
            ),
            ("authored", "Texto autorizado", ["finance"]),
            ("empty", "", []),
            ("mapped", "Texto del paquete", []),
            ("mapped_rewritten", "Texto que una persona reescribió", []),
        ):
            cur.execute(
                """INSERT INTO data_catalog(dataset,layer,cartridge,column_name,data_type,
                       description,tags,tenant_id,workspace_id,scope_status)
                   VALUES('legacy_a','silver','x',%s,'VARCHAR',%s,%s,%s,%s,'scoped')""",
                (column, description, tags, sec["tenant_id"], sec["workspace_id"]),
            )
        cur.execute("UPDATE data_catalog SET description_origin=NULL WHERE dataset='legacy_a'")
    migration = MIGRATION.read_text(encoding="utf-8")
    for _attempt in range(2):
        with psycopg2.connect(admin) as conn, conn.cursor() as cur:
            cur.execute(migration)
    rows = _scoped(
        admin,
        sec,
        """SELECT from_column, join_hint, cardinality, origin, status
             FROM data_relationships WHERE workspace_id=%s ORDER BY from_column""",
        (sec["workspace_id"],),
        fetch=True,
    )
    assert rows == [
        ("c1", None, "N:1", "manual", "active"),
        ("c2", None, "1:N", "manual", "active"),
        ("c3", "INNER", None, "manual", "active"),
        ("c4", None, None, "manual", "active"),
        ("c5", "LEFT", None, "manual", "active"),
    ]
    origins = dict(
        _scoped(
            admin,
            sec,
            "SELECT column_name, description_origin FROM data_catalog WHERE workspace_id=%s",
            (sec["workspace_id"],),
            fetch=True,
        )
    )
    assert origins == {
        "template": "copilot",
        "rewritten": "manual",
        "authored": "manual",
        "empty": None,
        "mapped": "packaged",
        "mapped_rewritten": "manual",
    }
    with psycopg2.connect(admin) as conn, conn.cursor() as cur:
        cur.execute(
            """SELECT count(*) FROM pg_constraint
                WHERE conname IN ('data_relationships_join_hint_check',
                                  'data_catalog_description_origin_check')
                  AND convalidated"""
        )
        assert cur.fetchone()[0] == 2
        cur.execute(
            """SELECT indexname FROM pg_indexes
                WHERE tablename = 'data_relationships' AND indexdef LIKE 'CREATE UNIQUE%%'
                ORDER BY indexname"""
        )
        assert [row[0] for row in cur.fetchall()] == [
            "data_relationships_legacy_key",
            "data_relationships_pkey",
            "data_relationships_scoped_key",
        ]
        cur.execute(
            """SELECT array_agg(a.attname::text)
                 FROM pg_constraint c
                 JOIN pg_attribute a ON a.attrelid = c.conrelid AND a.attnum = ANY (c.conkey)
                WHERE c.conrelid = 'data_relationships'::regclass AND c.contype = 'p'"""
        )
        assert cur.fetchone()[0] == ["id"]


def test_two_workspaces_with_the_same_datasets_keep_their_own_edges(
    postgres_with_real_init_schema: str,
) -> None:
    """Reviewer reproduction: a global edge key broke every workspace but the first."""
    admin = postgres_with_real_init_schema
    first = _workspace(admin, "shared_employees", "shared_departments")
    second = _workspace(admin, "shared_employees", "shared_departments")
    store = CatalogCopilotStore(_refinement(admin))
    edge = {
        "from_dataset": "shared_employees",
        "from_column": "department_id",
        "to_dataset": "shared_departments",
        "to_column": "department_id",
        "cardinality": "N:1",
        "confidence": 0.9,
        "description": "Detectada",
        "basis": {"codes": ["name:exact"]},
    }
    for sec in (first, second):
        assert store.upsert_copilot_edges(sec, [edge]) == (1, 0)
    manual = {**edge, "from_column": "company", "to_column": "company"}
    for sec in (first, second):
        _scoped(
            _refinement(admin),
            sec,
            """INSERT INTO data_relationships(from_dataset,from_column,to_dataset,to_column,
                   join_hint,cardinality,description,transform,origin,status,
                   tenant_id,workspace_id,scope_status)
               SELECT %s,%s,%s,%s,'LEFT','N:1','',NULL,'manual','active',%s::uuid,%s::uuid,'scoped'
                 FROM datasets from_ds
                 JOIN datasets to_ds ON to_ds.name = %s
                WHERE from_ds.name = %s AND from_ds.workspace_id = %s::uuid
                  AND to_ds.workspace_id = %s::uuid
               ON CONFLICT (workspace_id, from_dataset, from_column, to_dataset, to_column)
               WHERE workspace_id IS NOT NULL
               DO UPDATE SET origin = 'manual', status = 'active'""",
            (
                manual["from_dataset"],
                manual["from_column"],
                manual["to_dataset"],
                manual["to_column"],
                sec["tenant_id"],
                sec["workspace_id"],
                manual["to_dataset"],
                manual["from_dataset"],
                sec["workspace_id"],
                sec["workspace_id"],
            ),
        )
        assert store.reject_edge(sec, edge) is True
    for sec in (first, second):
        rows = _scoped(
            admin,
            sec,
            "SELECT from_column, origin, status FROM data_relationships "
            "WHERE workspace_id=%s ORDER BY from_column",
            (sec["workspace_id"],),
            fetch=True,
        )
        assert rows == [("company", "manual", "active"), ("department_id", "manual", "rejected")]


def test_mapping_text_is_packaged_and_never_replaces_manual_text(
    postgres_with_real_init_schema: str,
) -> None:
    from refinement.app.duckdb_engine import DuckDBEngine

    admin = postgres_with_real_init_schema
    sec = _workspace(admin, "mapped")
    for column, description, origin in (
        ("manual_col", "Texto de una persona", "manual"),
        ("packaged_col", "Texto viejo del paquete", "packaged"),
        ("copilot_col", "Texto inferido", "copilot"),
    ):
        _scoped(
            admin,
            sec,
            """INSERT INTO data_catalog(dataset,layer,cartridge,column_name,data_type,
                   description,description_origin,tenant_id,workspace_id,scope_status)
               VALUES('mapped','silver','x',%s,'VARCHAR',%s,%s,%s,%s,'scoped')""",
            (column, description, origin, sec["tenant_id"], sec["workspace_id"]),
        )
    engine = DuckDBEngine()
    engine.pg_url = _refinement(admin)
    columns = ("manual_col", "packaged_col", "copilot_col", "new_col")
    engine._update_catalog(
        "mapped",
        "silver",
        "x",
        [{"name": column, "type": "VARCHAR"} for column in columns],
        {column: f"Texto nuevo de {column}" for column in columns},
        user_context={"tenant_id": sec["tenant_id"], "workspace_id": sec["workspace_id"]},
    )
    rows = dict(
        (row[0], row[1:])
        for row in _scoped(
            admin,
            sec,
            "SELECT column_name, description, description_origin FROM data_catalog "
            "WHERE workspace_id=%s",
            (sec["workspace_id"],),
            fetch=True,
        )
    )
    assert rows["manual_col"] == ("Texto de una persona", "manual")
    assert rows["packaged_col"] == ("Texto nuevo de packaged_col", "packaged")
    assert rows["copilot_col"] == ("Texto nuevo de copilot_col", "packaged")
    assert rows["new_col"] == ("Texto nuevo de new_col", "packaged")


def test_database_refuses_value_bearing_evidence_and_legacy_tokens(
    postgres_with_real_init_schema: str,
) -> None:
    admin = postgres_with_real_init_schema
    sec = _workspace(admin, "copilot_checks")
    refinement = _refinement(admin)
    with pytest.raises(errors.CheckViolation):
        _scoped(
            refinement,
            sec,
            """INSERT INTO data_catalog(dataset,layer,cartridge,column_name,data_type,
                   copilot_evidence,tenant_id,workspace_id,scope_status)
               VALUES('copilot_checks','silver','x','rfc','VARCHAR',
                   '{"basis":["name:rfc"],"values":["GODE800101AB1"]}'::jsonb,
                   %s,%s,'scoped')""",
            (sec["tenant_id"], sec["workspace_id"]),
        )
    with pytest.raises(errors.CheckViolation):
        _scoped(
            refinement,
            sec,
            """INSERT INTO data_catalog(dataset,layer,cartridge,column_name,data_type,
                   classifications,tenant_id,workspace_id,scope_status)
               VALUES('copilot_checks','silver','x','a','VARCHAR',ARRAY['secret'],
                   %s,%s,'scoped')""",
            (sec["tenant_id"], sec["workspace_id"]),
        )
    with pytest.raises(errors.CheckViolation):
        _scoped(
            refinement,
            sec,
            """INSERT INTO data_relationships(from_dataset,from_column,to_dataset,to_column,
                   join_hint,tenant_id,workspace_id,scope_status)
               VALUES('a','b','c','d','many_to_one',%s,%s,'scoped')""",
            (sec["tenant_id"], sec["workspace_id"]),
        )
    store = CatalogCopilotStore(refinement)
    with pytest.raises(errors.CheckViolation):
        with store._cursor(sec) as cur:
            cur.execute(
                """INSERT INTO catalog_copilot_state(tenant_id,workspace_id,subject_kind,
                       subject,fingerprint,rules_version,status,summary)
                   VALUES(%s,%s,'dataset','copilot_checks','f','r','ready',
                       '{"sample":["x"]}'::jsonb)""",
                (sec["tenant_id"], sec["workspace_id"]),
            )


def test_store_guards_on_the_real_schema(postgres_with_real_init_schema: str) -> None:
    admin = postgres_with_real_init_schema
    sec = _workspace(admin, "employees", "departments")
    store = CatalogCopilotStore(_refinement(admin))
    _scoped(
        admin,
        sec,
        """INSERT INTO data_catalog(dataset,layer,cartridge,column_name,data_type,
               description,description_origin,is_key,tenant_id,workspace_id,scope_status)
           VALUES('employees','silver','sap_successfactors','email','VARCHAR',
               'Correo validado por RH','manual',TRUE,%s,%s,'scoped')""",
        (sec["tenant_id"], sec["workspace_id"]),
    )
    annotation = {
        "data_type": "VARCHAR",
        "semantic_type": "text",
        "classifications": ["pii", "confidential"],
        "classification_origin": "copilot",
        "confidence": 0.97,
        "evidence": {
            "basis": ["name:email", "pattern:email"],
            "pattern_hits": {"email": 998},
            "sampled_non_null": 1000,
            "sample_method": "first_rows",
        },
    }
    store.upsert_column_annotations(
        sec,
        dataset="employees",
        layer="silver",
        cartridge="sap_successfactors",
        annotations=[
            {**annotation, "column": "email", "description": "Plantilla del Copiloto"},
            {**annotation, "column": "rfc", "description": "RFC de la persona."},
        ],
    )
    rows = {
        row[0]: row[1:]
        for row in _scoped(
            admin,
            sec,
            """SELECT column_name, description, description_origin, classifications,
                      copilot_evidence, is_key, tags
                 FROM data_catalog WHERE workspace_id=%s ORDER BY column_name""",
            (sec["workspace_id"],),
            fetch=True,
        )
    }
    assert rows["email"][:2] == ("Correo validado por RH", "manual")
    assert rows["email"][2] == ["pii", "confidential"]
    assert rows["email"][4] is True, "the Copilot never flips is_key"
    assert rows["rfc"][:2] == ("RFC de la persona.", "copilot")
    assert rows["rfc"][3]["pattern_hits"] == {"email": 998}
    assert rows["rfc"][4] is False and rows["rfc"][5] == []

    edge = {
        "from_dataset": "employees",
        "from_column": "department_id",
        "to_dataset": "departments",
        "to_column": "department_id",
        "cardinality": "N:1",
        "confidence": 0.95,
        "description": "Detectada",
        "basis": {"codes": ["name:exact"], "containment": 1.0},
    }
    assert store.upsert_copilot_edges(sec, [edge]).written == 1
    assert store.reject_edge(sec, edge) is True
    assert store.upsert_copilot_edges(sec, [edge]).written == 0
    reverse = {
        **edge,
        "from_dataset": "departments",
        "to_dataset": "employees",
    }
    assert store.upsert_copilot_edges(sec, [reverse]).written == 0, "rejected in either direction"
    stale = {**edge, "from_column": "location_id", "to_column": "location_id"}
    assert store.upsert_copilot_edges(sec, [stale]).written == 1
    stale_key = ("employees", "location_id", "departments", "location_id")
    assert store.retire_copilot_edges(sec, {stale_key}) == 1
    assert store.upsert_copilot_edges(sec, [stale]).written == 1, "retired edges come back"
    _scoped(
        admin,
        sec,
        """INSERT INTO data_relationships(from_dataset,from_column,to_dataset,to_column,
               join_hint,origin,status,tenant_id,workspace_id,scope_status)
           VALUES('employees','company','departments','company','LEFT','manual','active',
               %s,%s,'scoped')""",
        (sec["tenant_id"], sec["workspace_id"]),
    )
    manual = {**edge, "from_column": "company", "to_column": "company"}
    assert store.upsert_copilot_edges(sec, [manual]).written == 0
    statuses = {
        (row[0], row[1]): row[2:]
        for row in _scoped(
            admin,
            sec,
            """SELECT from_column, from_dataset, origin, status, cardinality
                 FROM data_relationships WHERE workspace_id=%s""",
            (sec["workspace_id"],),
            fetch=True,
        )
    }
    assert statuses[("department_id", "employees")] == ("manual", "rejected", "N:1")
    assert statuses[("location_id", "employees")] == ("copilot", "active", "N:1")
    assert statuses[("company", "employees")] == ("manual", "active", None)


def test_state_is_workspace_scoped_and_append_only_for_services(
    postgres_with_real_init_schema: str,
) -> None:
    admin = postgres_with_real_init_schema
    first = _workspace(admin, "alpha")
    second = _workspace(admin, "alpha")
    store = CatalogCopilotStore(_refinement(admin))
    state = {
        "subject_kind": "dataset",
        "subject": "alpha",
        "layer": "silver",
        "cartridge": "sap_successfactors",
        "fingerprint": "run|1|digest|catalog-copilot/1",
        "status": "ready",
        "display_name": "Alpha",
        "description": "Alpha de SAP SuccessFactors.",
        "summary": {"columns": 2, "pii_columns": ["email"]},
        "duration_ms": 12,
    }
    store.save_state(first, state)
    assert ("dataset", "alpha") in store.load_states(first)
    assert store.load_states(second) == {}
    assert store.annotation_epoch(second) is None
    assert store.annotation_epoch(first) is not None
    with pytest.raises(errors.InsufficientPrivilege):
        with store._cursor(first) as cur:
            cur.execute("DELETE FROM catalog_copilot_state")
    with pytest.raises(psycopg2.Error):
        with store._cursor(first) as cur:
            cur.execute(
                """INSERT INTO catalog_copilot_state(tenant_id,workspace_id,subject_kind,
                       subject,fingerprint,rules_version,status)
                   VALUES(%s,%s,'dataset','forged','f','r','ready')""",
                (second["tenant_id"], second["workspace_id"]),
            )
    console = _dsn(admin, "omega_console", OMEGA_CONSOLE_PASSWORD)
    visible = _scoped(
        console,
        first,
        "SELECT subject FROM catalog_copilot_state",
        fetch=True,
    )
    assert visible == [("alpha",)]
    assert _scoped(console, second, "SELECT subject FROM catalog_copilot_state", fetch=True) == []


def test_zero_click_profile_with_the_real_store_under_two_seconds(
    postgres_with_real_init_schema: str, tmp_path
) -> None:
    admin = postgres_with_real_init_schema
    employees, departments = "sap_successfactors_employees", "sap_successfactors_departments"
    sec = _workspace(admin, employees, departments)
    local = LocalEngine(tmp_path)
    host = FakeHost(local)
    for name, sql in ((departments, DEPARTMENTS_SQL), (employees, EMPLOYEES_SQL)):
        snapshot = host.add(name, sql)
        for field in snapshot.evidence["catalog"]:
            _scoped(
                admin,
                sec,
                """INSERT INTO data_catalog(dataset,layer,cartridge,column_name,data_type,
                       null_rate,distinct_count,tenant_id,workspace_id,scope_status)
                   VALUES(%s,'silver','sap_successfactors',%s,%s,%s,%s,%s,%s,'scoped')""",
                (
                    name,
                    field["name"],
                    field["type"],
                    field.get("null_rate"),
                    field.get("distinct_count"),
                    sec["tenant_id"],
                    sec["workspace_id"],
                ),
            )
    store = CatalogCopilotStore(_refinement(admin))
    worker = AutonomousCatalogWorker(
        CatalogCopilotHost(
            list_datasets=host.list_datasets,
            get_dataset=host.get_dataset,
            published_snapshots=host.published_snapshots,
        ),
        store,
        CatalogCopilotProbe(local.engine),
    )
    started = time.perf_counter()
    assert worker.enqueue(sec, {"kind": "dataset", "name": employees})
    assert worker.wait_idle(timeout=10)
    raw = store.load_annotations(sec, [employees, departments])
    elapsed = time.perf_counter() - started
    assert elapsed < 2.0, f"autocatalog with the real store took {elapsed:.3f}s"
    columns = raw["columns"][employees]
    assert columns["email"]["classifications"] == ["pii", "confidential"]
    assert columns["email"]["description_origin"] == "copilot"
    assert raw["subjects"][("dataset", employees)]["description"].startswith(
        "Empleados de SAP SuccessFactors: 10 000 registros"
    )
    edges = [row for row in raw["relationships"] if row["origin"] == "copilot"]
    assert [(row["from_column"], row["to_dataset"], row["cardinality"]) for row in edges] == [
        ("department_id", departments, "N:1")
    ]
    dumped = json.dumps(
        [row["copilot_evidence"] for row in columns.values()]
        + [raw["subjects"][("dataset", employees)]["summary"]],
        default=str,
    )
    assert "@empresa.com.mx" not in dumped and "GODE" not in dumped


def test_copilot_inference_stays_out_of_the_evidence_snapshot(
    postgres_with_real_init_schema: str,
) -> None:
    admin = postgres_with_real_init_schema
    sec = _workspace(admin, "semantic_copilot", "employees")
    _scoped(
        admin,
        sec,
        """INSERT INTO data_catalog(dataset,layer,cartridge,column_name,data_type,
               description,description_origin,tags,is_key,is_metric,copilot_evidence,
               classifications,classification_origin,tenant_id,workspace_id,scope_status)
           VALUES
           ('semantic_copilot','gold','acceptance','value','INTEGER','Texto inferido',
            'copilot',ARRAY['auto_described','semantic_enrichment'],TRUE,TRUE,
            '{"basis":["name:salary"]}'::jsonb,ARRAY['financial','confidential'],
            'copilot',%s,%s,'scoped'),
           ('semantic_copilot','gold','acceptance','label','VARCHAR','Etiqueta autorizada',
            'manual',ARRAY['finance'],FALSE,FALSE,'{}'::jsonb,'{}','manual',%s,%s,'scoped')""",
        (sec["tenant_id"], sec["workspace_id"]) * 2,
    )
    _scoped(
        admin,
        sec,
        """INSERT INTO data_relationships(from_dataset,from_column,to_dataset,to_column,
               join_hint,cardinality,description,origin,status,
               tenant_id,workspace_id,scope_status)
           VALUES
           ('semantic_copilot','value','employees','employee_id','LEFT','N:1',
            'Inferida','copilot','active',%s,%s,'scoped'),
           ('semantic_copilot','label','employees','label','LEFT','N:1',
            'Autorizada','manual','active',%s,%s,'scoped'),
           ('semantic_copilot','other','employees','other','LEFT',NULL,
            'Rechazada','manual','rejected',%s,%s,'scoped')""",
        (sec["tenant_id"], sec["workspace_id"]) * 3,
    )
    refinement = _refinement(admin)
    semantics = snapshot_public_semantics(
        lambda: psycopg2.connect(refinement),
        tenant_id=sec["tenant_id"],
        workspace_id=sec["workspace_id"],
        dataset={"name": "semantic_copilot"},
    )
    assert semantics["columns"]["value"]["description"] == ""
    assert semantics["columns"]["value"]["tags"] == []
    assert semantics["columns"]["value"]["is_key"] is False
    assert semantics["columns"]["value"]["is_metric"] is False
    assert "classifications" not in semantics["columns"]["value"]
    assert semantics["columns"]["label"]["description"] == "Etiqueta autorizada"
    assert semantics["columns"]["label"]["tags"] == ["finance"]
    assert [
        (relation["from_column"], relation.get("cardinality"))
        for relation in semantics["relationships"]
    ] == [("label", "N:1")]
    live = CatalogCopilotStore(refinement).load_annotations(
        sec, ["semantic_copilot", "employees"]
    )
    assert live["columns"]["semantic_copilot"]["value"]["description"] == "Texto inferido"
    assert {row["origin"] for row in live["relationships"]} == {"copilot", "manual"}


def test_end_to_end_evidence_excludes_copilot_while_overlay_shows_it(
    postgres_with_real_init_schema: str,
    staged_publication_live_stack,  # noqa: F811 - pytest fixture
    monkeypatch,
) -> None:
    admin = postgres_with_real_init_schema
    with psycopg2.connect(admin) as conn, conn.cursor() as cur:
        cur.execute(
            "INSERT INTO tenants(id,name,slug,status) "
            "VALUES(%s,'Copilot Tenant','copilot-tenant','active') ON CONFLICT DO NOTHING",
            (TENANT_A,),
        )
        cur.execute(
            "INSERT INTO workspaces(id,tenant_id,name) "
            "VALUES(%s,%s,'Copilot Workspace') ON CONFLICT DO NOTHING",
            (WORKSPACE_A, TENANT_A),
        )
        cur.execute(
            """INSERT INTO data_catalog(dataset,layer,cartridge,column_name,data_type,
                   description,description_origin,tenant_id,workspace_id,scope_status)
               VALUES('copilot_evidence','gold','acceptance','value','INTEGER',
                   'Monto inferido por Copiloto','copilot',%s,%s,'scoped')""",
            (TENANT_A, WORKSPACE_A),
        )
        cur.execute(
            """INSERT INTO data_relationships(from_dataset,from_column,to_dataset,to_column,
                   join_hint,cardinality,description,origin,status,
                   tenant_id,workspace_id,scope_status)
               VALUES('copilot_evidence','value','employees','employee_id','LEFT','N:1',
                   'Inferida','copilot','active',%s,%s,'scoped')""",
            (TENANT_A, WORKSPACE_A),
        )
    engine = _engine(
        staged_publication_live_stack, monkeypatch, database_url=_refinement(admin)
    )
    engine.materialize(
        {
            "name": "copilot_evidence",
            "layer": "gold",
            "cartridge": "acceptance",
            "description": "",
            "sources": [],
            "sql_def": "SELECT 1::INTEGER AS value",
        },
        _scope(),
    )
    evidence = staged_publication_live_stack.evidence("copilot_evidence")[0]
    lineage, catalog = evidence[2], evidence[3]
    value = next(item for item in catalog if item["name"] == "value")
    assert value.get("description", "") == ""
    assert lineage["public_metadata"]["relationships"] == []
    live = CatalogCopilotStore(_refinement(admin)).load_annotations(
        {"tenant_id": TENANT_A, "workspace_id": WORKSPACE_A},
        ["copilot_evidence", "employees"],
    )
    public = published_catalog(
        [
            {"name": "copilot_evidence", "layer": "gold", "cartridge": "acceptance"},
            {"name": "employees", "layer": "gold", "cartridge": "acceptance"},
        ],
        _scope(),
        annotations=CatalogAnnotations(
            columns=live["columns"],
            relationships=live["relationships"],
            subjects=live["subjects"],
        ),
    )
    column = public["datasets"]["copilot_evidence"]["columns"][0]
    assert column["description"] == "Monto inferido por Copiloto"
    assert column["description_origin"] == "copilot"
    assert [relation["origin"] for relation in public["relationships"]] == ["copilot"]
