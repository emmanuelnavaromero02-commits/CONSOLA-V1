"""Server-owned kill switch for non-contractual mathematical engines.

The deterministic 9-Box classifier is intentionally outside this policy.
Monte Carlo, Bayesian calibration and optimizer execution stay paused unless a
later, separately approved release explicitly enables them.
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
