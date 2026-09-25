from __future__ import annotations


DDL_NAMES = (
    "Comercio",
    '"Comercio"',
    "hr.Comercio",
    '"hr"."Comercio"',
)

_NAMED_TEMPLATES = (
    "CREATE OR REPLACE SCHEMA {name}",
    "CREATE TEMP SEQUENCE {name}",
    "CREATE TEMPORARY SEQUENCE {name}",
    "CREATE LOCAL TEMP SEQUENCE {name}",
    "CREATE GLOBAL TEMPORARY SEQUENCE {name}",
    "CREATE OR REPLACE TEMP SEQUENCE {name}",
    "CREATE TEMP MACRO {name}() AS 1",
    "CREATE TEMPORARY MACRO {name}() AS 1",
    "CREATE LOCAL TEMPORARY MACRO {name}() AS 1",
)

_NAMED_STATEMENTS = tuple(
    template.format(name=name) for template in _NAMED_TEMPLATES for name in DDL_NAMES
)

_OBJECT_BODIES = {
    "aggregate": "AGGREGATE Comercio (integer) (SFUNC = int4pl, STYPE = integer)",
    "conversion": "CONVERSION Comercio FOR 'UTF8' TO 'LATIN1' FROM convert_fn",
    "function": "FUNCTION Comercio() RETURNS integer LANGUAGE SQL AS $$SELECT 1$$",
    "index": "INDEX Comercio ON payroll(id)",
    "language": "LANGUAGE Comercio HANDLER handler_fn",
    "macro": "MACRO Comercio() AS 1",
    "materialized": "MATERIALIZED VIEW Comercio AS SELECT 1",
    "procedure": "PROCEDURE Comercio() LANGUAGE SQL AS $$SELECT 1$$",
    "rule": "RULE Comercio AS ON SELECT TO payroll DO INSTEAD NOTHING",
    "schema": "SCHEMA Comercio",
    "secret": "SECRET Comercio (TYPE S3)",
    "sequence": "SEQUENCE Comercio",
    "table": "TABLE Comercio(id INTEGER)",
    "transform": (
        "TRANSFORM FOR integer LANGUAGE SQL "
        "(FROM SQL WITH FUNCTION from_sql(integer), "
        "TO SQL WITH FUNCTION to_sql(internal))"
    ),
    "trigger": "TRIGGER Comercio BEFORE INSERT ON payroll EXECUTE FUNCTION handler_fn()",
    "view": "VIEW Comercio AS SELECT 1",
}

_MODIFIER_OBJECTS = {
    "OR REPLACE": (
        "aggregate",
        "function",
        "language",
        "macro",
        "procedure",
        "rule",
        "schema",
        "secret",
        "sequence",
        "table",
        "transform",
        "trigger",
        "view",
    ),
    "TEMP": ("macro", "sequence", "table", "view"),
    "TEMPORARY": ("macro", "secret", "sequence", "table", "view"),
    "GLOBAL TEMP": ("sequence", "table", "view"),
    "GLOBAL TEMPORARY": ("sequence", "table", "view"),
    "LOCAL TEMP": ("macro", "sequence", "table", "view"),
    "LOCAL TEMPORARY": ("macro", "sequence", "table", "view"),
    "PERSISTENT": ("secret",),
    "UNLOGGED": ("materialized", "sequence", "table", "view"),
    "UNIQUE": ("index",),
    "DEFAULT": ("conversion",),
    "TRUSTED": ("language",),
    "PROCEDURAL": ("language",),
    "TRUSTED PROCEDURAL": ("language",),
    "OR REPLACE TEMP": ("macro", "sequence", "table", "view"),
    "OR REPLACE TEMPORARY": ("macro", "secret", "sequence", "table", "view"),
    "OR REPLACE GLOBAL TEMP": ("view",),
    "OR REPLACE GLOBAL TEMPORARY": ("view",),
    "OR REPLACE LOCAL TEMP": ("macro", "sequence", "table", "view"),
    "OR REPLACE LOCAL TEMPORARY": ("macro", "sequence", "table", "view"),
    "OR REPLACE PERSISTENT": ("secret",),
    "OR REPLACE TRUSTED": ("language",),
    "OR REPLACE PROCEDURAL": ("language",),
    "OR REPLACE TRUSTED PROCEDURAL": ("language",),
}

_GENERATED_MODIFIER_STATEMENTS = tuple(
    f"CREATE {modifier} {_OBJECT_BODIES[object_name]}"
    for modifier, object_names in _MODIFIER_OBJECTS.items()
    for object_name in object_names
) + (
    "CREATE CONSTRAINT TRIGGER Comercio AFTER INSERT ON payroll "
    "DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION handler_fn()",
    "DROP PERSISTENT SECRET Comercio",
    "DROP TEMPORARY SECRET Comercio",
)

_POSITIONAL_STATEMENTS = (
    "CREATE TEMP SEQUENCE IF NOT EXISTS Comercio",
    "CREATE TEMP MACRO IF NOT EXISTS Comercio() AS 1",
    "CREATE TEMPORARY SECRET Comercio (TYPE S3)",
    "CREATE PERSISTENT SECRET IF NOT EXISTS Comercio (TYPE S3, KEY_ID 'x')",
    "CREATE OR REPLACE SECRET Comercio (TYPE S3)",
    "CREATE OR REPLACE TEMPORARY SECRET Comercio (TYPE S3, KEY_ID 'x')",
    "CREATE OR REPLACE PERSISTENT SECRET Comercio (TYPE S3)",
    "CREATE GLOBAL TEMP SEQUENCE IF NOT EXISTS Comercio",
    "CREATE LOCAL TEMP MACRO Comercio() AS 1",
    "CREATE OR REPLACE LOCAL TEMP SEQUENCE Comercio",
    "CREATE OR REPLACE LOCAL TEMP MACRO Comercio() AS 1",
    "CREATE UNLOGGED SEQUENCE IF NOT EXISTS Comercio",
    "CREATE PROCEDURAL LANGUAGE Comercio HANDLER handler_fn",
    "CREATE TRUSTED PROCEDURAL LANGUAGE Comercio HANDLER handler_fn",
    "CREATE OR REPLACE LANGUAGE Comercio HANDLER handler_fn",
    "CREATE OR REPLACE PROCEDURAL LANGUAGE Comercio HANDLER handler_fn",
    "DROP PERSISTENT SECRET IF EXISTS Comercio",
    "DROP PERSISTENT SECRET IF",
    "DROP TEMPORARY SECRET IF EXISTS Comercio",
    "CREATE UNLOGGED TABLE IF NOT EXISTS Comercio(id INTEGER)",
    "CREATE UNIQUE INDEX IF NOT EXISTS Comercio ON payroll(id)",
    "CREATE DEFAULT CONVERSION Comercio FOR 'UTF8' TO 'LATIN1' FROM convert_fn",
    "CREATE OR REPLACE TRUSTED PROCEDURAL LANGUAGE Comercio HANDLER handler_fn",
)

DDL_STATEMENTS = tuple(
    dict.fromkeys(
        _NAMED_STATEMENTS + _GENERATED_MODIFIER_STATEMENTS + _POSITIONAL_STATEMENTS
    )
)

DDL_LAYOUT_VARIANTS = (
    "create\tor\nreplace schema Comercio",
    "CrEaTe Or RePlAcE ScHeMa Comercio",
    "CREATE/* head */OR/* mode */REPLACE/* object */SCHEMA Comercio",
    "CREATE\nTEMP\nSEQUENCE\nComercio",
    "CREATE/* scope */LOCAL/* kind */TEMP/* object */MACRO Comercio() AS 1",
    "DROP/* lifetime */PERSISTENT/* object */SECRET IF EXISTS Comercio",
)

BUSINESS_COPY_CONTROLS = (
    "Create better outcomes for employees.",
    "Create temporary project teams.",
    "Create or replace the onboarding guide.",
    "Create global talent programs.",
    "Temporary sequence planning.",
    "Schema planning workshop.",
    "Macro economic review.",
    "Replace the temporary equipment.",
    "Comercio.",
    "Región Norte.",
    "Sales Receipts.",
    "State: California.",
    "Status: Won.",
    "Call center roster.",
    "Set of core values.",
    "Grant Portfolio Review.",
    "Copy of the signed contract.",
    "employees TABLE.",
    "round table.",
    "water table.",
    "conference table.",
    "periodic table.",
    "the negotiating table.",
)

TRUNCATED_DDL_COPY = (
    "Create or",
    "Create or replace",
    "Create temporary",
    "Create global temporary",
    "Drop persistent",
)


__all__ = (
    "BUSINESS_COPY_CONTROLS",
    "DDL_LAYOUT_VARIANTS",
    "DDL_NAMES",
    "DDL_STATEMENTS",
    "TRUNCATED_DDL_COPY",
)
