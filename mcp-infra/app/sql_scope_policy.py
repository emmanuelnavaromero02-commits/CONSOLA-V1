from __future__ import annotations

from sqlglot import exp
from sqlglot.optimizer.scope import Scope, ScopeType, traverse_scope
from sqlglot.tokens import TokenType


_ASCII_FOLD = str.maketrans("ABCDEFGHIJKLMNOPQRSTUVWXYZ", "abcdefghijklmnopqrstuvwxyz")


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


def has_adjacent_relation_string_scan(tree: exp.Expression, tokens: list) -> bool:
    ambiguous: set[tuple[str, str]] = set()
    for table in tree.find_all(exp.Table):
        if table.args.get("db") is not None or table.args.get("catalog") is not None:
            continue
        relation = table.this
        alias = table.args.get("alias")
        alias_id = alias.this if isinstance(alias, exp.TableAlias) else None
        if (
            isinstance(relation, exp.Identifier)
            and not relation.args.get("quoted")
            and isinstance(alias_id, exp.Identifier)
            and alias_id.args.get("quoted")
        ):
            ambiguous.add(
                (_duckdb_identifier_key(relation.this or ""), str(alias_id.this or ""))
            )
    return any(
        left.token_type is TokenType.VAR
        and right.token_type is TokenType.STRING
        and left.end + 1 == right.start
        and (_duckdb_identifier_key(left.text), right.text) in ambiguous
        for left, right in zip(tokens, tokens[1:])
    )
