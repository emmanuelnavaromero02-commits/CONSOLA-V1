from __future__ import annotations

from . import (
    admin_decisions,
    agents,
    auth,
    data,
    jobs,
    marketplace_apps,
    misc,
    monitoring,
    pipeline_studio,
    rag,
    system,
    vault,
)

ROUTERS = (
    auth.router,
    system.router,
    jobs.router,
    data.router,
    pipeline_studio.router,
    marketplace_apps.router,
    misc.router,
    rag.router,
    agents.router,
    vault.router,
    monitoring.router,
    admin_decisions.router,
)
