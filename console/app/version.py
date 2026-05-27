"""Single source of truth for the console's release version.

Every surface that reports a version (``/healthz``, ``/api/system/info``,
the Control Room dashboard ``meta``, ``/api/control-room/ops/summary``)
MUST read it from here so they never drift — a stale or contradictory
version is a credibility bug, not a cosmetic one.
"""
from __future__ import annotations

from pathlib import Path

_CANDIDATES = (
    Path("/app/VERSION"),
    Path(__file__).resolve().parent.parent.parent / "VERSION",
)


def app_version() -> str:
    """Read the repo VERSION file. Returns ``unknown`` if unreadable —
    never raises (a health probe must not 500 over a missing file)."""
    for p in _CANDIDATES:
        try:
            if p.exists():
                return p.read_text().strip() or "unknown"
        except Exception:
            continue
    return "unknown"
