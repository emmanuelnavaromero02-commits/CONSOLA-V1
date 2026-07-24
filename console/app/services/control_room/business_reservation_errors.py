from __future__ import annotations

from typing import Any


class ReservationUnavailable(RuntimeError):
    pass


async def reservation_fetchrow(conn: Any, query: str, *args: Any) -> Any:
    try:
        return await conn.fetchrow(query, *args)
    except Exception as exc:
        raise ReservationUnavailable("action reservation storage unavailable") from exc


__all__ = ("ReservationUnavailable", "reservation_fetchrow")
