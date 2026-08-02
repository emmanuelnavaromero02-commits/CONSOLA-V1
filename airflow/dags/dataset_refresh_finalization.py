"""Fail-closed ordering for pipeline registry and downstream intelligence."""

from __future__ import annotations

from collections.abc import Callable


def finalize_pipeline_status(
    *,
    final_status: str,
    should_trigger_intelligence: bool,
    save_status: Callable[[str], None],
    trigger_intelligence: Callable[[], None],
) -> None:
    """Never expose a green pipeline before downstream work succeeds."""
    if should_trigger_intelligence:
        save_status("running")
        try:
            trigger_intelligence()
        except Exception as exc:
            save_status("failed")
            raise RuntimeError("Gold intelligence trigger unavailable") from exc
    save_status(final_status)
