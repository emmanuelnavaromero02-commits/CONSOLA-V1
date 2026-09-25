from __future__ import annotations

from app.services.public_sql_select_lexer import SelectToken


_PRIVILEGES = frozenset(
    {
        "all",
        "connect",
        "create",
        "delete",
        "execute",
        "insert",
        "privileges",
        "references",
        "select",
        "temporary",
        "trigger",
        "truncate",
        "update",
        "usage",
    }
)
_GRANT_OBJECTS = frozenset(
    {
        "database",
        "domain",
        "foreign",
        "function",
        "language",
        "large",
        "procedure",
        "routine",
        "schema",
        "sequence",
        "server",
        "table",
        "tablespace",
        "type",
    }
)


def _word(token: SelectToken) -> str | None:
    return token.value.casefold() if token.kind == "word" else None


def _name_end(tokens: tuple[SelectToken, ...], start: int = 0) -> int | None:
    if start >= len(tokens) or tokens[start].kind not in {"identifier", "word"}:
        return None
    index = start + 1
    while (
        index + 1 < len(tokens)
        and tokens[index] == SelectToken("symbol", ".")
        and tokens[index + 1].kind in {"identifier", "word"}
    ):
        index += 2
    return index


def _group_end(tokens: tuple[SelectToken, ...], start: int) -> int | None:
    if start >= len(tokens) or tokens[start].value != "(":
        return None
    depth = 0
    for index in range(start, len(tokens)):
        if tokens[index] == SelectToken("symbol", "("):
            depth += 1
        elif tokens[index] == SelectToken("symbol", ")"):
            depth -= 1
            if depth == 0:
                return index + 1
    return None


def _target_end(tokens: tuple[SelectToken, ...], start: int = 0) -> int | None:
    if start >= len(tokens):
        return None
    if tokens[start].kind == "string":
        return start + 1
    if tokens[start] == SelectToken("symbol", "("):
        return _group_end(tokens, start)
    end = _name_end(tokens, start)
    if end is not None and end < len(tokens) and tokens[end].value == "(":
        return _group_end(tokens, end)
    return end


def _assignment_after_name(tokens: tuple[SelectToken, ...], start: int) -> bool:
    end = _name_end(tokens, start)
    return bool(
        end is not None
        and end < len(tokens)
        and (tokens[end] == SelectToken("symbol", "=") or _word(tokens[end]) == "to")
    )


def call_prefix(tail: tuple[SelectToken, ...]) -> bool:
    end = _name_end(tail)
    return bool(end is not None and end < len(tail) and tail[end].value == "(")


def copy_prefix(tail: tuple[SelectToken, ...]) -> bool:
    start = 1 if tail and _word(tail[0]) == "binary" else 0
    end = _target_end(tail, start)
    return bool(
        end is not None and end < len(tail) and _word(tail[end]) in {"from", "to"}
    )


def grant_or_revoke_prefix(
    tail: tuple[SelectToken, ...],
    *,
    direction: str,
) -> bool:
    first_end = _name_end(tail)
    if first_end is None:
        return False
    while first_end + 1 < len(tail) and tail[first_end].value == ",":
        next_end = _name_end(tail, first_end + 1)
        if next_end is None:
            return False
        first_end = next_end
    if first_end < len(tail) and _word(tail[first_end]) == direction:
        return True
    on_index = next(
        (index for index, token in enumerate(tail) if _word(token) == "on"),
        None,
    )
    if on_index is None or not any(
        _word(token) in _PRIVILEGES for token in tail[:on_index]
    ):
        return False
    target_start = on_index + 1
    if target_start < len(tail) and _word(tail[target_start]) in _GRANT_OBJECTS:
        target_start += 1
    target_end = _target_end(tail, target_start)
    while target_end is not None and target_end + 1 < len(tail):
        if tail[target_end].value != ",":
            break
        target_end = _target_end(tail, target_end + 1)
    return bool(
        target_end is not None
        and target_end < len(tail)
        and _word(tail[target_end]) == direction
    )


def pivot_prefix(tail: tuple[SelectToken, ...]) -> bool:
    end = _target_end(tail)
    if end is None:
        return False
    if end + 1 < len(tail) and _word(tail[end]) == "as":
        end = _name_end(tail, end + 1) or end
    if end >= len(tail):
        return False
    clause = _word(tail[end])
    return clause in {"on", "using"} or (
        clause == "group" and end + 1 < len(tail) and _word(tail[end + 1]) == "by"
    )


def set_prefix(tail: tuple[SelectToken, ...]) -> bool:
    if not tail:
        return False
    first = _word(tail[0])
    if first == "variable":
        return _assignment_after_name(tail, 1)
    if first in {"names", "role", "schema"}:
        return len(tail) >= 2
    if first == "time":
        return len(tail) >= 3 and _word(tail[1]) == "zone"
    if first == "transaction":
        return len(tail) >= 2
    if first == "constraints":
        return len(tail) >= 3 and _word(tail[2]) in {"deferred", "immediate"}
    if first in {"local", "session"}:
        if (
            first == "session"
            and len(tail) >= 4
            and _word(tail[1]) == "characteristics"
            and _word(tail[2]) == "as"
            and _word(tail[3]) == "transaction"
        ):
            return True
        if len(tail) >= 3 and _word(tail[1]) in {"authorization", "transaction"}:
            return True
        return set_prefix(tail[1:])
    if first == "xml":
        return (
            len(tail) >= 3
            and _word(tail[1]) == "option"
            and _word(tail[2]) in {"content", "document"}
        )
    return _assignment_after_name(tail, 0)


def update_prefix(tail: tuple[SelectToken, ...]) -> bool:
    end = _name_end(tail)
    if end is None:
        return False
    if end + 1 < len(tail) and _word(tail[end]) == "as":
        end = _name_end(tail, end + 1) or end
    elif end + 1 < len(tail) and _word(tail[end]) != "set":
        end = _name_end(tail, end) or end
    return end < len(tail) and _word(tail[end]) == "set"


def with_prefix(tail: tuple[SelectToken, ...]) -> bool:
    start = 1 if tail and _word(tail[0]) == "recursive" else 0
    end = _name_end(tail, start)
    if end is None:
        return False
    if end < len(tail) and tail[end].value == "(":
        end = _group_end(tail, end) or end
    if end >= len(tail) or _word(tail[end]) != "as":
        return False
    end += 1
    if end < len(tail) and _word(tail[end]) == "not":
        if end + 1 >= len(tail) or _word(tail[end + 1]) != "materialized":
            return False
        end += 2
    elif end < len(tail) and _word(tail[end]) == "materialized":
        end += 1
    return end < len(tail) and tail[end].value == "("


__all__ = (
    "call_prefix",
    "copy_prefix",
    "grant_or_revoke_prefix",
    "pivot_prefix",
    "set_prefix",
    "update_prefix",
    "with_prefix",
)
