from __future__ import annotations

from typing import Any


def matches_shape(payload: Any, shape: dict[str, Any]) -> bool:
    return isinstance(payload, dict) and all(
        isinstance(payload.get(key), expected)
        if isinstance(expected, type)
        else payload.get(key) == expected
        for key, expected in shape.items()
    )


def stable_response(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            key: stable_response(item)
            for key, item in value.items()
            if key
            not in {
                "checked_at",
                "due_at",
                "generated_at",
                "latency_ms",
                "updated_at",
            }
        }
    if isinstance(value, list):
        return [stable_response(item) for item in value]
    return value


def assert_route_scope(
    sentinel: Any,
    *,
    scope_start: int,
    query_start: int,
    request_ids: set[str],
    expected_scope: tuple[str, str],
) -> None:
    scope_calls = sentinel.scope_calls[scope_start:]
    query_calls = sentinel.query_calls[query_start:]
    scoped_ids = {request_id for request_id, *_scope in scope_calls}

    missing_scope = request_ids - scoped_ids
    assert not missing_scope, {
        "missing_scope": sorted(missing_scope),
        "scope_calls": scope_calls,
        "query_calls": query_calls,
    }
    assert query_calls, sorted(request_ids)
    assert all(call[1:] == expected_scope for call in scope_calls)
    for request_id, statement, query_scope in query_calls:
        assert request_id in request_ids, statement
        assert request_id in scoped_ids, statement
        assert query_scope == expected_scope, (request_id, statement)
