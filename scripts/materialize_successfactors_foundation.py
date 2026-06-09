#!/usr/bin/env python3
"""Local wrapper for the Refinement SuccessFactors foundation materializer."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path


def _load_refinement_runner():
    repo = Path(__file__).resolve().parents[1]
    runner = repo / "refinement" / "scripts" / "materialize_successfactors_foundation.py"
    spec = importlib.util.spec_from_file_location("refinement_sf_foundation_materializer", runner)
    if not spec or not spec.loader:
        raise RuntimeError(f"cannot load {runner}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


_runner = _load_refinement_runner()

SUCCESSFACTORS_GOLD_FOUNDATION_ORDER = _runner.SUCCESSFACTORS_GOLD_FOUNDATION_ORDER
MaterializationContractError = _runner.MaterializationContractError
materialize_foundation = _runner.materialize_foundation


if __name__ == "__main__":
    raise SystemExit(_runner.main(sys.argv[1:]))
