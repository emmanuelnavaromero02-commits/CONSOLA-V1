"""The AWS probes write to a real deployment.

In September 2026 an earlier run left rows behind in production that had to be
removed by hand, table by table, under individual confirmation. The invariant
that prevents a repeat is simple and checkable from the source: every table a
probe writes to, the same probe deletes from.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]

PROBES = (
    "scripts/aws_decision_orchestrator_probe.py",
    "scripts/aws_decision_orchestrator_execution_probe.py",
)

INSERTED = re.compile(r"INSERT\s+INTO\s+([a-z_][a-z0-9_]*)", re.IGNORECASE)
DELETED = re.compile(r"DELETE\s+FROM\s+([a-z_][a-z0-9_]*)", re.IGNORECASE)


def _read(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


@pytest.mark.parametrize("probe", PROBES)
def test_every_table_written_is_also_cleaned(probe: str):
    src = _read(probe)
    written = set(INSERTED.findall(src))
    cleaned = set(DELETED.findall(src))
    assert written, f"{probe} no longer writes anything; update this contract"
    orphans = sorted(written - cleaned)
    assert not orphans, (
        f"{probe} writes to {orphans} without deleting from them. A probe that "
        "cannot undo its own writes leaves residue in a real deployment."
    )


@pytest.mark.parametrize("probe", PROBES)
def test_cleanup_is_reachable_and_reports_its_outcome(probe: str):
    src = _read(probe)
    assert "async def _cleanup()" in src
    # Silence is the failure mode that hid the last residue: the probe must say
    # whether the cleanup finished, so an operator can tell without querying.
    assert "probe_cleanup=" in src
    assert "INCOMPLETE" in src
