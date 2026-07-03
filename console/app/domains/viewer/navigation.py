"""Navigation helpers for legacy viewer entrypoints."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any
from urllib.parse import urlencode


def viewer_redirect_url(
    query_params: Mapping[str, Any],
    viewer_type: str,
    params: Mapping[str, Any] | None = None,
) -> str:
    query = dict(query_params)
    query["type"] = viewer_type
    for key, value in (params or {}).items():
        if value:
            query[key] = value
    return f"/viewer?{urlencode(query)}"
