from __future__ import annotations

from pathlib import Path

_CANDIDATES = (
    Path("/app/VERSION"),
    Path(__file__).resolve().parent.parent.parent / "VERSION",
)


def app_version() -> str:
    for p in _CANDIDATES:
        try:
            if p.exists():
                return p.read_text().strip() or "unknown"
        except Exception:
            continue
    return "unknown"
