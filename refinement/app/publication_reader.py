from __future__ import annotations

from typing import Any

try:
    from app.publication_contract import PublicationScope
    from app.publication_store import PublicationStore
except ModuleNotFoundError:
    from refinement.app.publication_contract import PublicationScope
    from refinement.app.publication_store import PublicationStore


class PublicationReader:
    def __init__(self, store: PublicationStore | None = None):
        self.store = store or PublicationStore()

    def published_head(
        self,
        layer: str,
        cartridge: str,
        name: str,
        user_context: dict[str, Any] | None,
    ) -> dict[str, Any] | None:
        del cartridge
        context = user_context or {}
        tenant = str(context.get("tenant_id") or "").strip()
        workspace = str(context.get("workspace_id") or "").strip()
        if not tenant or not workspace:
            return None
        return self.store.head(PublicationScope(tenant, workspace, name, layer))

    def published_object(
        self,
        layer: str,
        cartridge: str,
        name: str,
        user_context: dict[str, Any] | None,
    ) -> str | None:
        head = self.published_head(layer, cartridge, name, user_context)
        return str(head.get("object_uri") or "") if head else None
