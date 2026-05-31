"""Microsoft Teams channel for the Copiloto Empresarial.

Teams is a transport-only I/O channel: it receives Bot Framework activities,
authorizes them, normalizes to the internal copilot contract, drives the
EXISTING copilot, and renders the reply back to Teams. No business logic
lives in this package. See README.md for capability levels and setup.
"""
from .config import active_level, load_config
from .service import handle_activity, status_snapshot

__all__ = ["load_config", "active_level", "handle_activity", "status_snapshot"]
