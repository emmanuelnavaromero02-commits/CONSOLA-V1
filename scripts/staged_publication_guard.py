from __future__ import annotations

from typing import Any


def require_legacy_gold_writer(cur: Any) -> None:
    """Disable direct Gold seeders once the staged authority is installed."""
    cur.execute("SELECT to_regnamespace('omega_publication')")
    if cur.fetchone()[0] is not None:
        raise RuntimeError(
            "direct Gold writer disabled; materialize through staged publication"
        )
