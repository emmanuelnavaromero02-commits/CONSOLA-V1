"""Production composition root for fail-closed materialization evidence."""

from __future__ import annotations

from app import main as service
from app.operational_engine import OperationalDuckDBEngine


service.engine = OperationalDuckDBEngine()
app = service.app


__all__ = ["app"]
