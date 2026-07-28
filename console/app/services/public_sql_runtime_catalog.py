"""Versioned runtime catalogue derived from the test-only SQL grammars."""

from __future__ import annotations

from enum import Enum


DUCKDB_GRAMMAR_VERSION = "1.2.2"
POSTGRESQL_GRAMMAR_VERSION = "15.18"
SQL_RUNTIME_CATALOG_VERSION = "duckdb-1.2.2_postgresql-15.18_v2"


class HeadPolicy(str, Enum):
    """Catalogue grouping; every head still requires a recognized production."""

    STRUCTURAL = "structural"
    FAIL_CLOSED = "fail_closed"


# These heads overlap common business words and use positional production shapes.
STRUCTURAL_HEADS = frozenset(
    {
        "call",
        "copy",
        "desc",
        "describe",
        "grant",
        "set",
        "show",
        "use",
    }
)
STRUCTURAL_PRODUCTIONS = {
    "call": "qualified_name(parenthesized_arguments)",
    "copy": "target TO|FROM transport",
    "desc": "qualified_relation",
    "describe": "qualified_relation",
    "grant": "privileges_or_roles TO principal",
    "set": "name TO|= value or special SET form",
    "show": "qualified_name or fixed grammar production",
    "use": "qualified_catalog_or_schema",
}

# These heads have grammar-specific evidence in the prefix recognizer. Membership
# alone never blocks an isolated word; valid no-argument productions are explicit.
FAIL_CLOSED_HEADS = frozenset(
    {
        "abort",
        "alter",
        "analyse",
        "analyze",
        "attach",
        "begin",
        "checkpoint",
        "close",
        "cluster",
        "comment",
        "commit",
        "create",
        "deallocate",
        "declare",
        "delete",
        "detach",
        "discard",
        "do",
        "drop",
        "end",
        "exec",
        "execute",
        "explain",
        "export",
        "fetch",
        "force",
        "from",
        "import",
        "insert",
        "install",
        "listen",
        "load",
        "lock",
        "merge",
        "move",
        "notify",
        "pivot",
        "pivot_wider",
        "pragma",
        "prepare",
        "reassign",
        "refresh",
        "reindex",
        "release",
        "reset",
        "revoke",
        "rollback",
        "savepoint",
        "security",
        "select",
        "start",
        "summarize",
        "table",
        "truncate",
        "unlisten",
        "unpivot",
        "update",
        "vacuum",
        "values",
        "with",
    }
)

STATEMENT_HEAD_POLICIES = {
    **{head: HeadPolicy.STRUCTURAL for head in STRUCTURAL_HEADS},
    **{head: HeadPolicy.FAIL_CLOSED for head in FAIL_CLOSED_HEADS},
}
STATEMENT_HEADS = frozenset(STATEMENT_HEAD_POLICIES)


__all__ = (
    "DUCKDB_GRAMMAR_VERSION",
    "FAIL_CLOSED_HEADS",
    "HeadPolicy",
    "POSTGRESQL_GRAMMAR_VERSION",
    "SQL_RUNTIME_CATALOG_VERSION",
    "STATEMENT_HEAD_POLICIES",
    "STATEMENT_HEADS",
    "STRUCTURAL_HEADS",
    "STRUCTURAL_PRODUCTIONS",
)
