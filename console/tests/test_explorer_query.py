from __future__ import annotations

import ast
import re
from pathlib import Path

import duckdb
import pytest

from app.domains.data_platform import explorer_query
from app.domains.data_platform.explorer_query import (
    BRONZE_LIMIT_CAP,
    DATASET_LIMIT_CAP,
    MAX_FILTERS,
    MAX_IN,
    MAX_SORT,
    MAX_VALUE_LEN,
    ExplorerQueryError,
    bronze_definition_relation,
    bronze_execute_relation,
    compile_explorer_query,
    dataset_relation,
    explorer_column_kind,
    normalize_explorer_schema,
    parse_explorer_source,
    parse_explorer_spec,
    sanitize_explorer_detail,
)


SCHEMA = normalize_explorer_schema(
    [
        {"name": "nombre", "type": "VARCHAR"},
        {"name": "salario", "type": "DECIMAL(12,2)"},
        {"name": "fecha_ingreso", "type": "DATE"},
        {"name": "activo", "type": "BOOLEAN"},
        {"name": "área", "type": "VARCHAR"},
        {"name": 'raro"col', "type": "BIGINT"},
        {"name": "tags", "type": "VARCHAR[]"},
        {"name": "load_date", "type": "DATE"},
    ]
)
RELATION = "read_parquet('raw/acme/Employee')"

INJECTIONS = [
    "x' OR 1=1 --",
    "'; DROP TABLE users; --",
    "a'); ATTACH 'evil.db' (--",
    "') UNION SELECT * FROM read_csv('/etc/passwd') --",
    "\\'; SELECT 1; --",
    "{latest_date}",
    "{bucket}",
    "O'Reilly\nDROP",
    "/* comment */",
]


def _spec(body, cap=BRONZE_LIMIT_CAP):
    return parse_explorer_spec(body, limit_cap=cap)


def _compile(body, mode="execute", cap=BRONZE_LIMIT_CAP, relation=RELATION, **kwargs):
    return compile_explorer_query(
        _spec(body, cap), SCHEMA, relation_sql=relation, limit_cap=cap, mode=mode, **kwargs
    )


def _one_filter(column, op, **values):
    return {"filters": [{"column": column, "op": op, **values}]}


@pytest.mark.parametrize(
    ("raw", "kind"),
    [
        ("VARCHAR", "text"),
        ("STRING", "text"),
        ("UUID", "text"),
        ("BIGINT", "number"),
        ("DECIMAL(18,3)", "number"),
        ("NUMBER", "number"),
        ("double", "number"),
        ("DATE", "temporal"),
        ("Date", "temporal"),
        ("TIMESTAMP WITH TIME ZONE", "temporal"),
        ("DATETIME", "temporal"),
        ("BOOLEAN", "boolean"),
        ("bool", "boolean"),
        ("VARCHAR[]", "other"),
        ("STRUCT(a INTEGER)", "other"),
        ("MAP(VARCHAR, INTEGER)", "other"),
        ("BLOB", "other"),
        ("TIME", "other"),
        ("", "other"),
        (None, "other"),
    ],
)
def test_column_kind_classifies_describe_and_cursor_types(raw, kind):
    assert explorer_column_kind(raw) == kind


def test_schema_normalization_drops_duplicates_and_unsafe_names():
    schema = normalize_explorer_schema(
        [
            {"name": "a", "type": "VARCHAR"},
            {"name": "a", "type": "BIGINT"},
            {"name": "bad\nname", "type": "VARCHAR"},
            {"name": "{latest_date}", "type": "VARCHAR"},
            {"name": "", "type": "VARCHAR"},
            {"name": 5, "type": "VARCHAR"},
            "not-a-field",
        ]
    )
    assert [(column.name, column.kind) for column in schema] == [("a", "text")]
    assert normalize_explorer_schema(None) == ()


@pytest.mark.parametrize(
    ("op", "extra", "expected_sql", "expected_params"),
    [
        ("eq", {"value": "Ana"}, 'CAST("nombre" AS VARCHAR) = ?', ("Ana",)),
        ("neq", {"value": "Ana"}, 'CAST("nombre" AS VARCHAR) IS DISTINCT FROM ?', ("Ana",)),
        (
            "contains",
            {"value": "JOSÉ"},
            'contains(strip_accents(lower(CAST("nombre" AS VARCHAR))), strip_accents(lower(?)))',
            ("JOSÉ",),
        ),
        (
            "not_contains",
            {"value": "x"},
            '("nombre" IS NULL OR NOT contains(strip_accents(lower(CAST("nombre" AS VARCHAR))), strip_accents(lower(?))))',
            ("x",),
        ),
        (
            "starts_with",
            {"value": "jo"},
            'starts_with(strip_accents(lower(CAST("nombre" AS VARCHAR))), strip_accents(lower(?)))',
            ("jo",),
        ),
        ("is_empty", {}, '("nombre" IS NULL OR trim(CAST("nombre" AS VARCHAR)) = \'\')', ()),
        (
            "is_not_empty",
            {},
            '("nombre" IS NOT NULL AND trim(CAST("nombre" AS VARCHAR)) <> \'\')',
            (),
        ),
        ("in", {"values": ["a", "b", "a"]}, 'CAST("nombre" AS VARCHAR) IN (?, ?)', ("a", "b")),
    ],
)
def test_text_operators_are_parameterized(op, extra, expected_sql, expected_params):
    query = _compile(_one_filter("nombre", op, **extra))
    assert query.sql == f"SELECT * FROM {RELATION} WHERE {expected_sql} LIMIT 50"
    assert query.params == expected_params


@pytest.mark.parametrize(
    ("op", "extra", "fragment", "params"),
    [
        ("gt", {"value": 1000}, '"salario" > TRY_CAST(? AS HUGEINT)', ("1000",)),
        ("gte", {"value": "10.50"}, '"salario" >= TRY_CAST(? AS DECIMAL(38,12))', ("10.5",)),
        ("lt", {"value": -3}, '"salario" < TRY_CAST(? AS HUGEINT)', ("-3",)),
        ("lte", {"value": 2.0}, '"salario" <= TRY_CAST(? AS HUGEINT)', ("2",)),
        (
            "between",
            {"values": [1, "2e3"]},
            '"salario" BETWEEN TRY_CAST(? AS HUGEINT) AND TRY_CAST(? AS HUGEINT)',
            ("1", "2000"),
        ),
        ("eq", {"value": 5}, '"salario" = TRY_CAST(? AS HUGEINT)', ("5",)),
        (
            "in",
            {"values": [1, 2.5]},
            '"salario" IN (TRY_CAST(? AS HUGEINT), TRY_CAST(? AS DECIMAL(38,12)))',
            ("1", "2.5"),
        ),
    ],
)
def test_numeric_operators_cast_parameters(op, extra, fragment, params):
    query = _compile(_one_filter("salario", op, **extra))
    assert fragment in query.sql
    assert query.params == params


def test_temporal_and_boolean_operators_cast_parameters():
    query = _compile(
        {
            "filters": [
                {"column": "fecha_ingreso", "op": "between", "values": ["2020-01-01", "2021-12-31T18:30"]},
                {"column": "activo", "op": "eq", "value": "sí"},
            ]
        }
    )
    assert '"fecha_ingreso" BETWEEN TRY_CAST(? AS TIMESTAMP) AND TRY_CAST(? AS TIMESTAMP)' in query.sql
    assert '"activo" = TRY_CAST(? AS BOOLEAN)' in query.sql
    assert query.params == ("2020-01-01", "2021-12-31 18:30:00", "true")


def test_projection_sort_and_quoted_identifiers():
    query = _compile(
        {
            "columns": ["nombre", 'raro"col'],
            "sort": [{"column": "salario", "direction": "desc"}, {"column": "nombre"}],
            "limit": 10,
        }
    )
    assert query.sql == (
        f'SELECT "nombre", "raro""col" FROM {RELATION} '
        'ORDER BY "salario" DESC NULLS LAST, "nombre" ASC NULLS LAST LIMIT 10'
    )
    assert query.limit == 10


def test_latest_only_uses_engine_placeholder_and_requires_load_date():
    query = _compile({"latest_only": True})
    assert "WHERE \"load_date\" = '{latest_date}'" in query.sql
    schema = normalize_explorer_schema([{"name": "a", "type": "VARCHAR"}])
    with pytest.raises(ExplorerQueryError, match="load_date"):
        compile_explorer_query(
            _spec({"latest_only": True}), schema, relation_sql=RELATION, limit_cap=50, mode="execute"
        )
    with pytest.raises(ExplorerQueryError, match="bronze"):
        _compile({"latest_only": True}, allow_latest_only=False)


def test_limits_are_capped_and_never_raised():
    assert _compile({"limit": 999_999}).limit == BRONZE_LIMIT_CAP
    assert _compile({"limit": 999_999}, cap=DATASET_LIMIT_CAP).limit == DATASET_LIMIT_CAP
    assert _compile({}).limit == 50
    assert f"LIMIT {BRONZE_LIMIT_CAP}" in _compile({"limit": 10**9}).sql
    for bad in (0, -1, "10", 1.5, True):
        with pytest.raises(ExplorerQueryError):
            _spec({"limit": bad})


def test_structural_caps_are_enforced():
    with pytest.raises(ExplorerQueryError, match="Máximo"):
        _spec({"filters": [{"column": "nombre", "op": "is_empty"}] * (MAX_FILTERS + 1)})
    with pytest.raises(ExplorerQueryError, match="acepta de 1"):
        _spec(_one_filter("nombre", "in", values=["x"] * (MAX_IN + 1)))
    with pytest.raises(ExplorerQueryError, match="acepta de 1"):
        _spec(_one_filter("nombre", "in", values=[]))
    with pytest.raises(ExplorerQueryError, match="criterios"):
        _spec({"sort": [{"column": f"c{i}"} for i in range(MAX_SORT + 1)]})
    with pytest.raises(ExplorerQueryError, match="demasiado largo"):
        _compile(_one_filter("nombre", "eq", value="x" * (MAX_VALUE_LEN + 1)))
    with pytest.raises(ExplorerQueryError, match="dos valores"):
        _spec(_one_filter("salario", "between", values=[1]))
    with pytest.raises(ExplorerQueryError, match="dos veces"):
        _spec({"sort": [{"column": "a"}, {"column": "a", "direction": "desc"}]})


@pytest.mark.parametrize(
    "body",
    [
        {"columns": ["no_existe"]},
        {"columns": ["nombre; DROP TABLE x"]},
        {"columns": ['nombre" FROM x --']},
        {"sort": [{"column": "1; DROP TABLE x"}]},
        _one_filter("nombre) OR (1=1", "eq", value="a"),
        _one_filter('"nombre"', "eq", value="a"),
        _one_filter("NOMBRE", "eq", value="a"),
    ],
)
def test_identifiers_must_exactly_match_the_real_schema(body):
    with pytest.raises(ExplorerQueryError, match="no existe"):
        _compile(body)


@pytest.mark.parametrize(
    ("column", "op", "extra"),
    [
        ("nombre", "gt", {"value": "a"}),
        ("nombre", "between", {"values": ["a", "b"]}),
        ("salario", "contains", {"value": "1"}),
        ("fecha_ingreso", "starts_with", {"value": "2020"}),
        ("activo", "lt", {"value": "true"}),
        ("tags", "eq", {"value": "x"}),
        ("tags", "contains", {"value": "x"}),
    ],
)
def test_operator_must_match_column_type(column, op, extra):
    with pytest.raises(ExplorerQueryError, match="no aplica"):
        _compile(_one_filter(column, op, **extra))


def test_structured_columns_still_accept_emptiness_checks_but_not_sorting():
    assert "\"tags\" IS NULL" in _compile(_one_filter("tags", "is_empty")).sql
    with pytest.raises(ExplorerQueryError, match="ordenar"):
        _compile({"sort": [{"column": "tags"}]})


@pytest.mark.parametrize(
    ("column", "value", "match"),
    [
        ("salario", "mil", "no es un número"),
        ("salario", "1; DROP TABLE x", "no es un número"),
        ("salario", True, "debe ser un número"),
        ("fecha_ingreso", "05/01/2020", "AAAA-MM-DD"),
        ("fecha_ingreso", "2020-13-45", "AAAA-MM-DD"),
        ("fecha_ingreso", "2020-01-01'; --", "AAAA-MM-DD"),
        ("activo", "quizá", "sí/no"),
    ],
)
def test_typed_values_are_validated(column, value, match):
    with pytest.raises(ExplorerQueryError, match=match):
        _compile(_one_filter(column, "eq", value=value))


def test_between_requires_ordered_bounds():
    with pytest.raises(ExplorerQueryError, match="menor o igual"):
        _compile(_one_filter("salario", "between", values=[10, 1]))
    with pytest.raises(ExplorerQueryError, match="menor o igual"):
        _compile(_one_filter("fecha_ingreso", "between", values=["2021-01-01", "2020-01-01"]))


@pytest.mark.parametrize(
    "raw",
    [
        {"filters": "nombre = 'x'"},
        {"filters": [{"column": "nombre", "op": "raw_sql", "value": "1=1"}]},
        {"filters": [{"column": "nombre", "op": "eq"}]},
        {"filters": [{"column": "nombre", "op": "eq", "value": {"sql": "1=1"}}]},
        {"filters": [{"column": "nombre", "op": "eq", "value": ["a"]}]},
        {"filters": [{"column": "salario", "op": "eq", "value": float("nan")}]},
        {"sort": [{"column": "nombre", "direction": "DESC; DROP TABLE x"}]},
        {"columns": "nombre"},
        {"latest_only": "yes"},
    ],
)
def test_malformed_specs_are_rejected(raw):
    with pytest.raises(ExplorerQueryError):
        _spec(raw)


@pytest.mark.parametrize("payload", INJECTIONS)
def test_execute_mode_never_interpolates_values(payload):
    query = _compile(
        {
            "filters": [
                {"column": "nombre", "op": "eq", "value": payload},
                {"column": "nombre", "op": "contains", "value": payload},
                {"column": "área", "op": "in", "values": [payload, "ok"]},
            ]
        }
    )
    assert payload not in query.sql
    assert query.params.count(payload) == 3
    assert query.sql.count("?") == len(query.params)


@pytest.mark.parametrize(
    "payload",
    [
        "x' OR 1=1 --",
        "O'Reilly",
        "'; DELETE FROM users; --",
        "\\'; SELECT 1; --",
        "/* comment */",
        "comillas \"dobles\"",
    ],
)
def test_definition_mode_encodes_quotes_so_values_stay_literals(payload):
    query = _compile(_one_filter("nombre", "eq", value=payload), mode="definition")
    assert query.params == ()
    assert query.limit is None
    assert "LIMIT" not in query.sql
    assert query.sql_display == query.sql
    literal = "'" + payload.replace("'", "''") + "'"
    assert query.sql.endswith(f'CAST("nombre" AS VARCHAR) = {literal}')
    con = duckdb.connect()
    con.execute("CREATE TABLE t AS SELECT * FROM (VALUES (?), ('other')) v(nombre)", [payload])
    rows = con.execute(query.sql.replace(RELATION, "t")).fetchall()
    assert rows == [(payload,)]


@pytest.mark.parametrize(
    ("payload", "match"),
    [
        ("línea\nnueva", "caracteres de control"),
        ("tab\there", "caracteres de control"),
        ("{latest_date}", "llaves"),
        ("{bucket}", "llaves"),
        ("x'); ATTACH 'evil' (--", "bloquea"),
        ("LOAD httpfs", "bloquea"),
        ("PRAGMA version", "bloquea"),
        ("'; DROP TABLE users; --", "bloquea"),
        ("read_csv('/etc/passwd')", "bloquea"),
        ("s3://other-bucket/raw/x", "ruta de almacenamiento"),
        ("gs://b/raw/x", "ruta de almacenamiento"),
        ("file:///etc/passwd", "ruta de almacenamiento"),
        ("'/etc/passwd", "bloquea"),
    ],
)
def test_definition_mode_rejects_values_the_engine_would_misread(payload, match):
    with pytest.raises(ExplorerQueryError, match=match):
        _compile(_one_filter("nombre", "eq", value=payload), mode="definition")


def test_definition_mode_renders_typed_literals_strictly():
    query = _compile(
        {
            "filters": [
                {"column": "salario", "op": "gte", "value": "1500.5"},
                {"column": "fecha_ingreso", "op": "lt", "value": "2024-02-29"},
                {"column": "activo", "op": "eq", "value": False},
            ],
            "latest_only": True,
        },
        mode="definition",
        relation=bronze_definition_relation(parse_explorer_source({"kind": "bronze", "cartridge": "acme", "entity": "Employee"})),
    )
    assert query.sql == (
        "SELECT * FROM read_parquet('s3://{bucket}/raw/acme/Employee/**/*.parquet', "
        "hive_partitioning=true, union_by_name=true) WHERE \"load_date\" = '{latest_date}' "
        "AND \"salario\" >= 1500.5 AND \"fecha_ingreso\" < TIMESTAMP '2024-02-29' AND \"activo\" = FALSE"
    )


def test_display_sql_inlines_values_safely_without_storage_uris():
    source = parse_explorer_source({"kind": "bronze", "cartridge": "acme", "entity": "Employee"})
    query = compile_explorer_query(
        _spec(_one_filter("nombre", "eq", value="x' OR '1'='1\nnext")),
        SCHEMA,
        relation_sql=bronze_execute_relation(source),
        limit_cap=BRONZE_LIMIT_CAP,
        mode="execute",
    )
    assert query.sql_display == (
        "SELECT * FROM read_parquet('raw/acme/Employee') "
        "WHERE CAST(\"nombre\" AS VARCHAR) = 'x'' OR ''1''=''1 next' LIMIT 50"
    )
    assert "s3://" not in query.sql_display
    assert "tenant_id" not in query.sql_display


def test_compiled_execute_sql_runs_on_duckdb_with_parameters():
    con = duckdb.connect()
    con.execute(
        """
        CREATE TABLE t AS SELECT * FROM (VALUES
          ('José', 3000.0, DATE '2019-03-10', true, 'Ventas'),
          ('Ana', 1200.5, DATE '2020-01-05', true, 'Ingeniería'),
          ('Luis', 800.0, DATE '2023-06-01', false, ''),
          ('O''Reilly', NULL, NULL, NULL, NULL)
        ) v(nombre, salario, fecha_ingreso, activo, "área")
        """
    )
    schema = normalize_explorer_schema(
        [{"name": row[0], "type": row[1]} for row in con.execute("DESCRIBE t").fetchall()]
    )
    spec = _spec(
        {
            "columns": ["nombre"],
            "filters": [
                {"column": "salario", "op": "gt", "value": 1000},
                {"column": "nombre", "op": "not_contains", "value": "jose"},
                {"column": "área", "op": "is_not_empty"},
            ],
            "sort": [{"column": "salario", "direction": "desc"}],
        }
    )
    query = compile_explorer_query(spec, schema, relation_sql="t", limit_cap=10, mode="execute")
    assert con.execute(query.sql, list(query.params)).fetchall() == [("Ana",)]
    injected = _spec(_one_filter("nombre", "eq", value="x' OR 1=1 --"))
    query = compile_explorer_query(injected, schema, relation_sql="t", limit_cap=10, mode="execute")
    assert con.execute(query.sql, list(query.params)).fetchall() == []


@pytest.mark.parametrize(
    "raw",
    [
        None,
        {"kind": "bronze", "cartridge": "acme"},
        {"kind": "bronze", "cartridge": "../acme", "entity": "E"},
        {"kind": "bronze", "cartridge": "acme", "entity": "E/../../x"},
        {"kind": "bronze", "cartridge": "acme", "entity": "E'); DROP"},
        {"kind": "bronze", "cartridge": "acme", "entity": "tenant_id=x"},
        {"kind": "dataset", "name": "gold_x; DROP"},
        {"kind": "dataset", "name": "x.y"},
        {"kind": "silver", "name": "x"},
        {"kind": "sql", "sql": "select 1"},
    ],
)
def test_source_identifiers_are_validated(raw):
    with pytest.raises(ExplorerQueryError):
        parse_explorer_source(raw)


def test_source_relations_use_logical_paths_only():
    bronze = parse_explorer_source({"kind": "bronze", "cartridge": "sap_b1", "entity": "OINV"})
    assert bronze.logical == "raw/sap_b1/OINV"
    assert bronze_execute_relation(bronze) == "read_parquet('raw/sap_b1/OINV')"
    assert "tenant_id" not in bronze_definition_relation(bronze)
    dataset = parse_explorer_source({"kind": "dataset", "name": "ventas"})
    assert dataset.logical == "gold/ventas"
    assert dataset_relation(dataset) == "pggold.gold_ventas"


def test_empty_schema_is_rejected():
    with pytest.raises(ExplorerQueryError) as exc:
        compile_explorer_query(_spec({}), (), relation_sql=RELATION, limit_cap=50, mode="execute")
    assert exc.value.status_code == 422


def test_sanitize_detail_strips_scoped_storage_uris():
    detail = (
        "IO Error: No files found that match the pattern "
        "\"s3://lakehouse/raw/acme/E/tenant_id=t-1/workspace_id=w-9/**/*.parquet\" "
        "in tenant_id=t-1/workspace_id=w-9"
    )
    clean = sanitize_explorer_detail(detail, fallback="x")
    assert "s3://" not in clean
    assert "t-1" not in clean
    assert "w-9" not in clean
    assert sanitize_explorer_detail("", fallback="Sin detalle") == "Sin detalle"
    assert sanitize_explorer_detail("LINE 1: SELECT x", fallback="Sin detalle") == "Sin detalle"
    assert (
        sanitize_explorer_detail("Binder Error: bad column LINE 1: SELECT * FROM t", fallback="x")
        == "Binder Error: bad column"
    )
    assert len(sanitize_explorer_detail("a" * 1000, fallback="x")) == 300


def _regex_flags(node: ast.AST | None) -> int:
    if node is None:
        return 0
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.BitOr):
        return _regex_flags(node.left) | _regex_flags(node.right)
    assert isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name) and node.value.id == "re"
    return int(getattr(re, node.attr))


def _compiled_patterns(path: Path, owner: str | None, names: set[str]) -> dict[str, list[tuple[str, int]]]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    scope = tree.body
    if owner:
        scope = next(node for node in tree.body if isinstance(node, ast.ClassDef) and node.name == owner).body
    found: dict[str, list[tuple[str, int]]] = {}
    for node in scope:
        if not (isinstance(node, ast.Assign) and isinstance(node.targets[0], ast.Name)):
            continue
        name = node.targets[0].id
        if name not in names:
            continue
        calls = node.value.elts if isinstance(node.value, (ast.List, ast.Tuple)) else [node.value]
        found[name] = [
            re.compile(ast.literal_eval(call.args[0]), _regex_flags(call.args[1] if len(call.args) > 1 else None))
            for call in calls
        ]
        found[name] = [(item.pattern, item.flags) for item in found[name]]
    return found


def test_engine_guard_mirrors_match_refinement_sources():
    root = Path(__file__).resolve().parents[2] / "refinement" / "app"
    main = _compiled_patterns(root / "main.py", None, {"_SQL_FORBIDDEN_RE", "_SQL_COMMENT_RE", "_SINGLE_QUOTED_RE"})
    engine = _compiled_patterns(
        root / "duckdb_engine.py", "DuckDBEngine", {"_DANGEROUS_PATTERNS", "_DANGEROUS_PATH_RE"}
    )

    def flat(*patterns):
        return [(item.pattern, item.flags) for item in patterns]

    assert main["_SQL_FORBIDDEN_RE"] == flat(explorer_query.ENGINE_SQL_FORBIDDEN_RE)
    assert main["_SQL_COMMENT_RE"] == flat(explorer_query.ENGINE_SQL_COMMENT_RE)
    assert main["_SINGLE_QUOTED_RE"] == flat(explorer_query.ENGINE_SINGLE_QUOTED_RE)
    assert engine["_DANGEROUS_PATTERNS"] == flat(*explorer_query.ENGINE_DANGEROUS_PATTERNS)
    assert engine["_DANGEROUS_PATH_RE"] == flat(explorer_query.ENGINE_DANGEROUS_PATH_RE)


@pytest.mark.parametrize(
    ("body", "mode"),
    [
        ({"filters": [{"column": "nombre", "op": "eq", "value": "copy x"}, {"column": "área", "op": "eq", "value": "a from"}]}, "definition"),
        ({"columns": ["Copy Number"]}, "execute"),
        ({"columns": ["Copy Number"]}, "definition"),
        ({"columns": ["a;b"]}, "execute"),
        ({"columns": ["x--y"]}, "execute"),
        ({"sort": [{"column": "load"}]}, "execute"),
    ],
)
def test_whole_sql_engine_policy_is_enforced_before_the_engine_sees_it(body, mode):
    schema = normalize_explorer_schema(
        [
            {"name": "nombre", "type": "VARCHAR"},
            {"name": "área", "type": "VARCHAR"},
            {"name": "Copy Number", "type": "VARCHAR"},
            {"name": "a;b", "type": "VARCHAR"},
            {"name": "x--y", "type": "VARCHAR"},
            {"name": "load", "type": "VARCHAR"},
        ]
    )
    with pytest.raises(ExplorerQueryError, match="bloquea por seguridad"):
        compile_explorer_query(_spec(body), schema, relation_sql=RELATION, limit_cap=50, mode=mode)


def test_execute_mode_values_are_not_policy_checked_because_they_are_parameters():
    query = _compile({"filters": [{"column": "nombre", "op": "eq", "value": "copy x"}, {"column": "área", "op": "eq", "value": "a from"}]})
    assert query.params == ("copy x", "a from")


def test_large_integers_compare_exactly_in_execute_mode():
    con = duckdb.connect()
    con.execute("CREATE TABLE t AS SELECT 9007199254740992::BIGINT AS id, 10.10::DECIMAL(18,2) AS m")
    schema = normalize_explorer_schema([{"name": "id", "type": "BIGINT"}, {"name": "m", "type": "DECIMAL(18,2)"}])
    for op, value, expected in (("eq", "9007199254740993", 0), ("eq", "9007199254740992", 1), ("gt", "9007199254740991", 1)):
        query = compile_explorer_query(_spec(_one_filter("id", op, value=value)), schema, relation_sql="t", limit_cap=10, mode="execute")
        assert con.execute(query.sql, list(query.params)).fetchall().__len__() == expected
    query = compile_explorer_query(_spec(_one_filter("m", "eq", value="10.1")), schema, relation_sql="t", limit_cap=10, mode="execute")
    assert len(con.execute(query.sql, list(query.params)).fetchall()) == 1
    definition = compile_explorer_query(_spec(_one_filter("id", "eq", value="9007199254740993")), schema, relation_sql="t", limit_cap=10, mode="definition")
    assert con.execute(definition.sql).fetchall() == []


@pytest.mark.parametrize(
    ("value", "expected"),
    [("1e3", "1000"), ("-0", "0"), ("0012", "12"), ("1.50", "1.5"), (1e20, "100000000000000000000"), ("12345678901234567890123456789012345678", "12345678901234567890123456789012345678")],
)
def test_numbers_are_canonicalized_to_exact_fixed_point(value, expected):
    assert _compile(_one_filter("salario", "eq", value=value)).params == (expected,)


@pytest.mark.parametrize("value", ["1.1234567890123", "1e40", "123456789012345678901234567.5"])
def test_numbers_beyond_supported_precision_are_rejected(value):
    with pytest.raises(ExplorerQueryError):
        _compile(_one_filter("salario", "eq", value=value))


def test_sanitizer_redacts_endpoints_and_encoded_scope_markers():
    detail = (
        "HTTP 403 from https://lakehouse.s3.us-east-1.amazonaws.com/?prefix=raw%2Facme%2FE%2F"
        "tenant_id%3Dt-77%2Fworkspace_id%3Dw-88%2F and http://minio:9000/lakehouse/x"
    )
    clean = sanitize_explorer_detail(detail, fallback="x")
    for secret in ("amazonaws", "minio:9000", "t-77", "w-88", "us-east-1"):
        assert secret not in clean
