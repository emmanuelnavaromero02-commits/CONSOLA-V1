from __future__ import annotations

from sqlglot import exp
from sqlglot.optimizer.scope import Scope, ScopeType, traverse_scope


_ASCII_UPPER = "ABCDEFGHIJKLMNOPQRSTUVWXYZ"
_ASCII_LOWER = "abcdefghijklmnopqrstuvwxyz"
_ASCII_FOLD = str.maketrans(_ASCII_UPPER, _ASCII_LOWER)


def _duckdb_identifier_key(value: object) -> str:
    return str(value).translate(_ASCII_FOLD)


def resolved_cte_table_ids(tree: exp.Expression) -> frozenset[int]:
    resolved: set[int] = set()
    for scope in traverse_scope(tree):
        folded_sources: dict[str, Scope] = {}
        ambiguous: set[str] = set()
        visible_sources = list(scope.cte_sources.items())
        if scope.scope_type is ScopeType.CTE and scope.parent is not None:
            visible_sources.extend(
                (name, source)
                for name, source in scope.parent.cte_sources.items()
                if source is not scope
            )
        for name, source in visible_sources:
            folded = _duckdb_identifier_key(name)
            if folded in folded_sources and folded_sources[folded] is not source:
                ambiguous.add(folded)
                continue
            folded_sources[folded] = source
        for table in scope.tables:
            if table.db or table.catalog:
                continue
            folded = _duckdb_identifier_key(table.name)
            source = None if folded in ambiguous else folded_sources.get(folded)
            if isinstance(source, Scope):
                resolved.add(id(table))
    return frozenset(resolved)
