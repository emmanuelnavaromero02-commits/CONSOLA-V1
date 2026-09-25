from __future__ import annotations

from app.services.public_sql_select_lexer import SelectToken


DDL_OBJECT_WORDS = frozenset(
    {
        "access",
        "aggregate",
        "cast",
        "collation",
        "conversion",
        "database",
        "domain",
        "event",
        "extension",
        "foreign",
        "function",
        "group",
        "index",
        "language",
        "large",
        "macro",
        "materialized",
        "operator",
        "policy",
        "procedure",
        "publication",
        "role",
        "routine",
        "rule",
        "schema",
        "secret",
        "sequence",
        "server",
        "statistics",
        "subscription",
        "table",
        "tablespace",
        "text",
        "transform",
        "trigger",
        "type",
        "user",
        "view",
    }
)

_REPLACE_OBJECTS = frozenset(
    {
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
    }
)
_TEMP_OBJECTS = frozenset({"macro", "sequence", "table", "view"})
_TEMPORARY_OBJECTS = _TEMP_OBJECTS | {"secret"}
_GLOBAL_TEMP_OBJECTS = frozenset({"sequence", "table", "view"})
_LOCAL_TEMP_OBJECTS = _TEMP_OBJECTS
_REPLACE_GLOBAL_TEMP_OBJECTS = frozenset({"view"})

_CREATE_MODIFIER_OBJECTS = {
    ("or", "replace"): _REPLACE_OBJECTS,
    ("temp",): _TEMP_OBJECTS,
    ("temporary",): _TEMPORARY_OBJECTS,
    ("global", "temp"): _GLOBAL_TEMP_OBJECTS,
    ("global", "temporary"): _GLOBAL_TEMP_OBJECTS,
    ("local", "temp"): _LOCAL_TEMP_OBJECTS,
    ("local", "temporary"): _LOCAL_TEMP_OBJECTS,
    ("persistent",): frozenset({"secret"}),
    ("unlogged",): frozenset({"materialized", "sequence", "table", "view"}),
    ("unique",): frozenset({"index"}),
    ("default",): frozenset({"conversion"}),
    ("constraint",): frozenset({"trigger"}),
    ("trusted",): frozenset({"language"}),
    ("procedural",): frozenset({"language"}),
    ("trusted", "procedural"): frozenset({"language"}),
    ("or", "replace", "temp"): _TEMP_OBJECTS,
    ("or", "replace", "temporary"): _TEMPORARY_OBJECTS,
    ("or", "replace", "global", "temp"): _REPLACE_GLOBAL_TEMP_OBJECTS,
    ("or", "replace", "global", "temporary"): _REPLACE_GLOBAL_TEMP_OBJECTS,
    ("or", "replace", "local", "temp"): _LOCAL_TEMP_OBJECTS,
    ("or", "replace", "local", "temporary"): _LOCAL_TEMP_OBJECTS,
    ("or", "replace", "persistent"): frozenset({"secret"}),
    ("or", "replace", "trusted"): frozenset({"language"}),
    ("or", "replace", "procedural"): frozenset({"language"}),
    ("or", "replace", "trusted", "procedural"): frozenset({"language"}),
}
_DROP_MODIFIER_OBJECTS = {
    ("persistent",): frozenset({"secret"}),
    ("procedural",): frozenset({"language"}),
    ("temporary",): frozenset({"secret"}),
}
_ALTER_MODIFIER_OBJECTS = {
    ("procedural",): frozenset({"language"}),
}


def _word(token: SelectToken) -> str | None:
    return token.value.casefold() if token.kind == "word" else None


def _matches_modifier_object(
    tail: tuple[SelectToken, ...],
    modifiers: tuple[str, ...],
    objects: frozenset[str],
) -> bool:
    object_index = len(modifiers)
    return bool(
        len(tail) > object_index
        and all(_word(tail[index]) == word for index, word in enumerate(modifiers))
        and _word(tail[object_index]) in objects
    )


def ddl_prefix(head: str, tail: tuple[SelectToken, ...]) -> bool:

    if tail and _word(tail[0]) in DDL_OBJECT_WORDS:
        return True
    productions = (
        _CREATE_MODIFIER_OBJECTS
        if head == "create"
        else _ALTER_MODIFIER_OBJECTS
        if head == "alter"
        else _DROP_MODIFIER_OBJECTS
        if head == "drop"
        else {}
    )
    return any(
        _matches_modifier_object(tail, modifiers, objects)
        for modifiers, objects in productions.items()
    )


__all__ = ("ddl_prefix",)
