"""Scope-aware helpers shared by public and pre-execution SQL guards."""

from __future__ import annotations

from sqlglot import exp
from sqlglot.optimizer.scope import Scope, traverse_scope


_ASCII_UPPER = "ABCDEFGHIJKLMNOPQRSTUVWXYZ"
_ASCII_LOWER = "abcdefghijklmnopqrstuvwxyz"
_ASCII_FOLD = str.maketrans(_ASCII_UPPER, _ASCII_LOWER)


def _duckdb_identifier_key(value: object) -> str:
    """Match DuckDB's ASCII-only identifier folding without folding Unicode."""
    return str(value).translate(_ASCII_FOLD)


def resolved_cte_table_ids(tree: exp.Expression) -> frozenset[int]:
    """Return table nodes that SQLGlot resolves to a visible CTE scope."""
    resolved: set[int] = set()
    for scope in traverse_scope(tree):
        folded_sources: dict[str, Scope] = {}
        ambiguous: set[str] = set()
        for name, source in scope.cte_sources.items():
            folded = _duckdb_identifier_key(name)
            if folded in folded_sources and folded_sources[folded] is not source:
                ambiguous.add(folded)
                continue
            folded_sources[folded] = source
        for table in scope.tables:
            folded = _duckdb_identifier_key(table.name)
            source = None if folded in ambiguous else folded_sources.get(folded)
            if isinstance(source, Scope):
                resolved.add(id(table))
    return frozenset(resolved)
