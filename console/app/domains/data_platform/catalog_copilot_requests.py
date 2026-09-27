"""Console side of the Catalog Copilot: debounced auto-profile and rejections.

The console never profiles anything itself. It forwards a bounded catch-up
request to refinement (which re-checks permissions and visibility with the
signed security context), remembers the last answer per identity so page
opens do not hammer refinement, and invalidates the scoped catalog cache
when new annotations landed.
"""

from __future__ import annotations

import re
import threading
import time
from collections import OrderedDict
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any, Literal

from fastapi import HTTPException
from pydantic import BaseModel, ConfigDict, Field

from app.domains.data_platform.refinement_errors import (
    raise_for_refinement_payload_error,
)
from app.domains.data_platform.scoped_reads import scoped_cache_identity

AUTO_PROFILE_FRESH_SECONDS = 30.0
AUTO_PROFILE_MIN_INTERVAL_SECONDS = 1.5
AUTO_PROFILE_TIMEOUT_SECONDS = 10
AUTO_PROFILE_MEMO_LIMIT = 2048
_CARTRIDGE = re.compile(r"^[a-z0-9_]{1,64}$")
_DATASET = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,127}$")
_COLUMN = re.compile(r'^[^\x00-\x1f\x7f"]{1,128}$')


class AutoProfileResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: Literal["idle", "ready", "working"]
    processed: int = Field(ge=0)
    pending: int = Field(ge=0)
    stale: int = Field(ge=0)
    annotation_epoch: str | None = Field(default=None, max_length=64)
    cached: bool = False


class RejectRelationshipResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    rejected: bool
    relation: str = Field(max_length=600)


@dataclass
class _Memo:
    at: float
    result: AutoProfileResponse


class AutoProfileMemo:
    """Last auto-profile answer per (identity, cartridge, sources) and identity."""

    def __init__(self, limit: int = AUTO_PROFILE_MEMO_LIMIT) -> None:
        self.limit = limit
        self._results: OrderedDict[tuple[Any, ...], _Memo] = OrderedDict()
        self._last_call: OrderedDict[tuple[Any, ...], float] = OrderedDict()
        self._lock = threading.Lock()

    def get(self, key: tuple[Any, ...]) -> _Memo | None:
        with self._lock:
            return self._results.get(key)

    def last_call(self, identity: tuple[Any, ...]) -> float | None:
        with self._lock:
            return self._last_call.get(identity)

    def touch(self, identity: tuple[Any, ...], at: float) -> None:
        with self._lock:
            self._last_call[identity] = at
            self._last_call.move_to_end(identity)
            while len(self._last_call) > self.limit:
                self._last_call.popitem(last=False)

    def put(self, key: tuple[Any, ...], memo: _Memo) -> None:
        with self._lock:
            self._results[key] = memo
            self._results.move_to_end(key)
            while len(self._results) > self.limit:
                self._results.popitem(last=False)

    def clear(self) -> None:
        with self._lock:
            self._results.clear()
            self._last_call.clear()


AUTO_PROFILE_MEMO = AutoProfileMemo()


def _count(value: Any) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        return 0
    return max(0, value)


def _auto_profile_result(payload: Any) -> AutoProfileResponse:
    data = payload if isinstance(payload, dict) else {}
    status = data.get("status")
    epoch = data.get("annotation_epoch")
    return AutoProfileResponse(
        status=status if status in {"idle", "ready", "working"} else "idle",
        processed=_count(data.get("processed")),
        pending=_count(data.get("pending")),
        stale=_count(data.get("stale")),
        annotation_epoch=str(epoch)[:64] if epoch else None,
    )


async def auto_profile_payload(
    *,
    body: dict[str, Any],
    user: dict[str, Any],
    refinement_invoke: Callable[..., Awaitable[Any]],
    scoped_read_cache_invalidate: Callable[[str, dict[str, Any]], None],
    memo: AutoProfileMemo = AUTO_PROFILE_MEMO,
    clock: Callable[[], float] = time.monotonic,
) -> dict[str, Any]:
    cartridge = str(body.get("cartridge") or "").strip()
    if cartridge and not _CARTRIDGE.fullmatch(cartridge):
        raise HTTPException(400, "Fuente de datos inválida")
    include_sources = body.get("include_sources") is True
    identity = scoped_cache_identity(user)
    key = (identity, cartridge, include_sources)
    now = clock()
    previous = memo.get(key)
    if previous is not None:
        age = now - previous.at
        if age < AUTO_PROFILE_MIN_INTERVAL_SECONDS or (
            previous.result.pending == 0 and age < AUTO_PROFILE_FRESH_SECONDS
        ):
            return previous.result.model_copy(update={"cached": True}).model_dump()
    last_call = memo.last_call(identity)
    if last_call is not None and now - last_call < AUTO_PROFILE_MIN_INTERVAL_SECONDS:
        raise HTTPException(429, "Autocatalogado en curso; intenta de nuevo en un momento")
    memo.touch(identity, now)
    args: dict[str, Any] = {"include_sources": include_sources}
    if cartridge:
        args["cartridge"] = cartridge
    payload = await refinement_invoke(
        "auto_catalog", args, timeout=AUTO_PROFILE_TIMEOUT_SECONDS, user=user
    )
    raise_for_refinement_payload_error(payload, "Catalog auto-profile failed")
    result = _auto_profile_result(payload)
    if result.processed > 0:
        scoped_read_cache_invalidate("catalog", user)
    memo.put(key, _Memo(at=clock(), result=result))
    return result.model_dump()


async def catalog_annotation_epoch(
    user: dict[str, Any],
    *,
    pool_factory: Callable[[], Awaitable[Any]] | None = None,
    scoped_db: Callable[..., Any] | None = None,
) -> str | None:
    """Latest Copilot profile time of the caller's workspace, for cache keys."""
    try:
        if pool_factory is None:
            from app.services import auth

            pool_factory = auth.pool
        if scoped_db is None:
            from app.services.db_scope import scoped_db_for_user

            scoped_db = scoped_db_for_user
        pool = await pool_factory()
        async with scoped_db(pool, user) as (conn, _tenant_id, workspace_id):
            value = await conn.fetchval(
                "SELECT max(profiled_at)::text FROM catalog_copilot_state "
                "WHERE workspace_id = $1::uuid",
                workspace_id,
            )
    except Exception:
        return None
    return str(value)[:64] if value else None


def _edge(body: dict[str, Any]) -> dict[str, str]:
    edge = {
        key: str(body.get(key) or "").strip()
        for key in ("from_dataset", "from_column", "to_dataset", "to_column")
    }
    for key in ("from_dataset", "to_dataset"):
        if not _DATASET.fullmatch(edge[key]):
            raise HTTPException(400, f"{key} inválido")
    for key in ("from_column", "to_column"):
        if not _COLUMN.fullmatch(edge[key]):
            raise HTTPException(400, f"{key} inválido")
    return edge


async def reject_relationship_payload(
    *,
    body: dict[str, Any],
    user: dict[str, Any],
    refinement_invoke: Callable[..., Awaitable[Any]],
    scoped_read_cache_invalidate: Callable[[str, dict[str, Any]], None],
) -> dict[str, Any]:
    edge = _edge(body if isinstance(body, dict) else {})
    payload = await refinement_invoke("reject_relationship", edge, timeout=15, user=user)
    raise_for_refinement_payload_error(payload, "Catalog relationship rejection failed")
    if not isinstance(payload, dict) or payload.get("rejected") is not True:
        raise HTTPException(404, "La relación no pertenece al espacio de trabajo activo")
    scoped_read_cache_invalidate("catalog", user)
    return RejectRelationshipResponse(
        rejected=True,
        relation=(
            f"{edge['from_dataset']}.{edge['from_column']} → "
            f"{edge['to_dataset']}.{edge['to_column']}"
        ),
    ).model_dump()


__all__ = [
    "AUTO_PROFILE_MEMO",
    "AutoProfileMemo",
    "AutoProfileResponse",
    "RejectRelationshipResponse",
    "auto_profile_payload",
    "catalog_annotation_epoch",
    "reject_relationship_payload",
]
