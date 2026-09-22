"""One materialization budget, and it has to clear the floor of the work.

The three callers that ask refinement to materialize a dataset used to carry
two different numbers: 300 s on the two silver paths, 600 s on the gold loop --
which also materializes silver datasets. The same work had two budgets
depending on which entry point reached it, and nothing in the code said why.

300 s is below the floor. Measured 2026-09-22 on a host identical to
production, SuccessFactors EmployeeTime is 14.53 GB of raw parquet in the
materialized scope, read from S3 at ~34 MB/s: the read alone cannot finish in
under ~430 s. So the caller gave up on work that was still running and recorded
it as "partial" -- and the server, which shields the worker, went on to finish
and publish it. A budget shorter than the work does not fail the work, it
mislabels it.

These tests hold the budget to one name and one value, and keep that value
above the measured floor.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

MODULE = (
    Path(__file__).resolve().parents[1]
    / "app" / "core" / "refinement_triggers.py"
)
SOURCE = MODULE.read_text(encoding="utf-8")

# The measured read floor for the largest entity, rounded down. A budget at or
# below this cannot complete EmployeeTime however fast the rest of the pipeline
# gets, because it is bounded by S3 throughput, not by CPU or memory.
MEASURED_FLOOR_SECONDS = 430


def _constant() -> int:
    match = re.search(r"^MATERIALIZE_TIMEOUT_SECONDS\s*=\s*(\d+)", SOURCE, re.M)
    assert match, "MATERIALIZE_TIMEOUT_SECONDS is not defined"
    return int(match.group(1))


def test_the_budget_has_one_name() -> None:
    from cartridges.sap_successfactors.app.core import refinement_triggers

    assert refinement_triggers.MATERIALIZE_TIMEOUT_SECONDS == _constant()


def test_no_caller_carries_its_own_number() -> None:
    """A literal here is how the two budgets drifted apart in the first place."""
    literals = re.findall(r"AsyncClient\(\s*timeout\s*=\s*(\d+)", SOURCE)

    assert not literals, (
        f"these callers hardcode a timeout instead of using the shared "
        f"constant: {literals}. One budget, one name."
    )


def test_every_materializing_caller_uses_the_constant() -> None:
    uses = re.findall(
        r"AsyncClient\(\s*timeout\s*=\s*MATERIALIZE_TIMEOUT_SECONDS", SOURCE
    )

    assert len(uses) == 3, (
        f"expected all 3 materializing callers to share the budget, found "
        f"{len(uses)}. A new caller with its own number reintroduces the split."
    )


def test_budget_clears_the_measured_floor() -> None:
    budget = _constant()

    assert budget > MEASURED_FLOOR_SECONDS, (
        f"the budget is {budget}s but the largest entity needs ~"
        f"{MEASURED_FLOOR_SECONDS}s of S3 read alone. Below the floor the "
        "caller abandons work the server completes, and records it as failed."
    )


@pytest.mark.parametrize("name", ["trigger_silver_refresh", "trigger_successfactors_gold_refresh"])
def test_both_layers_are_covered(name: str) -> None:
    """Silver and gold must not drift apart again."""
    assert re.search(rf"^async def {name}\b", SOURCE, re.M), f"{name} moved or was renamed"
