"""Bounded word-only SQL statement productions inside public copy."""

from __future__ import annotations


_NO_ARGUMENT_STATEMENTS = frozenset(
    {
        "abort",
        "analyze",
        "analyse",
        "begin",
        "checkpoint",
        "cluster",
        "commit",
        "describe",
        "end",
        "rollback",
        "show",
        "start",
        "vacuum",
    }
)
_ONE_ARGUMENT_STATEMENTS = frozenset(
    {
        "attach",
        "call",
        "close",
        "deallocate",
        "detach",
        "discard",
        "exec",
        "execute",
        "install",
        "listen",
        "load",
        "lock",
        "move",
        "notify",
        "pivot",
        "pragma",
        "release",
        "reset",
        "savepoint",
        "summarize",
        "table",
        "truncate",
        "unlisten",
        "unpivot",
        "use",
        "values",
    }
)
_OBJECT_STATEMENTS = frozenset(
    {
        "alter",
        "create",
        "delete",
        "drop",
        "explain",
        "export",
        "fetch",
        "force",
        "grant",
        "import",
        "insert",
        "merge",
        "prepare",
        "reassign",
        "refresh",
        "reindex",
        "revoke",
        "security",
        "set",
        "update",
    }
)
_TWO_WORD_STATEMENTS = frozenset(
    {
        ("force", "checkpoint"),
    }
)


def contains_embedded_word_statement(words: tuple[str, ...]) -> bool:
    """Detect a complete word-only statement at any non-initial position."""

    for index in range(1, len(words)):
        tail = words[index:]
        starter = tail[0]
        if starter == "select":
            return True
        if starter in _NO_ARGUMENT_STATEMENTS:
            return True
        if starter in _ONE_ARGUMENT_STATEMENTS and len(tail) >= 2:
            return True
        if starter in _OBJECT_STATEMENTS and len(tail) >= 3:
            return True
        if len(tail) >= 2 and tail[:2] in _TWO_WORD_STATEMENTS:
            return True
    return False


__all__ = ("contains_embedded_word_statement",)
