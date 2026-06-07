from __future__ import annotations

import os


os.environ.setdefault(
    "INTERNAL_API_KEY",
    "refinement-tests-internal-api-key-with-more-than-32-chars",
)
os.environ.setdefault(
    "SECURITY_CONTEXT_SIGNING_KEY",
    "refinement-tests-security-context-signing-key-distinct-64-chars",
)
