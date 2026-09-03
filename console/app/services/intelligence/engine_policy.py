"""Server-owned kill switch for non-contractual mathematical engines.

The 9-Box classifier is deliberately not covered: it is a deterministic data
contract. Monte Carlo, Bayesian calibration and optimization remain paused
until an operator explicitly enables a later, separately approved release.
"""

from __future__ import annotations

import os


ENV_NAME = "INTELLIGENCE_MATH_ENGINES_ENABLED"
PAUSED_REASON = "paused_by_server_policy"


def math_engines_enabled() -> bool:
    return str(os.environ.get(ENV_NAME) or "").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }
