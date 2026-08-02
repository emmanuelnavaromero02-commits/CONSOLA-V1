from __future__ import annotations

from typing import Any

import sqlglot
from sqlglot import exp

try:
    from app.publication_contract import canonical_digest
    from app.publication_inputs import resolve_input_state
    from app.publication_snapshot import PublicationSnapshotResolver
except ModuleNotFoundError:
    from refinement.app.publication_contract import canonical_digest
    from refinement.app.publication_inputs import resolve_input_state
    from refinement.app.publication_snapshot import PublicationSnapshotResolver


def _quote(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


class PublicationInputBindingMixin:
    """Pins execution to the exact dependency vector used by the run digest."""

    def _input_items(self) -> dict[str, dict[str, Any]]:
        state = self._state() or {}
        return {
            str(item.get("source") or ""): item
            for item in state.get("input_state") or []
        }

    def _latest_materialized_uri(
        self,
        layer: str,
        cartridge: str,
        name: str,
        user_context: dict | None = None,
    ) -> str | None:
        source = f"{layer}/{cartridge}/{name}"
        item = self._input_items().get(source)
        if item is not None:
            published = item.get("published")
            return str((published or {}).get("uri") or "") or None
        context = user_context or {}
        snapshot = PublicationSnapshotResolver(self.storage).published_snapshot(
            {"name": name, "layer": layer, "cartridge": cartridge}, context
        )
        return str((snapshot.head if snapshot else {}).get("object_uri") or "") or None

    def get_rls_filters(self, sql: str, user_context: dict) -> tuple[str, list]:
        context = user_context or {}
        tenant = str(context.get("tenant_id") or "").strip()
        workspace = str(context.get("workspace_id") or "").strip()
        items: dict[str, dict[str, Any]] = {}
        if tenant and workspace:
            tree = sqlglot.parse_one(sql, read="duckdb")
            for table in tree.find_all(exp.Table):
                if table.catalog or (table.db or "").lower() != "pggold":
                    continue
                name = table.name.lower()
                if not name.startswith("gold_"):
                    continue
                dataset = name.removeprefix("gold_")
                snapshot = PublicationSnapshotResolver(self.storage).published_snapshot(
                    {"name": dataset, "layer": "gold", "cartridge": "registered"},
                    {"tenant_id": tenant, "workspace_id": workspace},
                )
                head = snapshot.head if snapshot else None
                if not head:
                    raise ValueError("Gold dataset is not published")
                items[f"gold/registered/{dataset}"] = {"published": head}
        return super().get_rls_filters(
            self._bind_gold_relations(sql, items), user_context
        )

    def _scope_storage_sql(
        self, sql: str, sources: list[str], user_context: dict | None
    ) -> str:
        scoped = super()._scope_storage_sql(sql, sources, user_context)
        items = self._input_items()
        for source in sources or []:
            source = str(source or "").strip().strip("/")
            item = items.get(source)
            if not source.startswith("raw/") or item is None:
                continue
            wildcard = super()._bronze_path(source, user_context)
            uris = [self._storage_uri(obj["key"]) for obj in item.get("objects") or []]
            exact = "[" + ",".join(_quote(uri) for uri in uris) + "]"
            scoped = scoped.replace(_quote(wildcard), exact)
            scoped = scoped.replace('"' + wildcard + '"', exact)
        return self._bind_gold_relations(scoped, items)

    @staticmethod
    def _bind_gold_relations(sql: str, items: dict[str, dict[str, Any]]) -> str:
        relations: dict[str, tuple[str, str]] = {}
        for source, item in items.items():
            parts = source.split("/")
            published = item.get("published") or {}
            table = str(published.get("gold_table") or "")
            if len(parts) != 3 or parts[0] != "gold" or not table:
                continue
            schema = (
                "public"
                if published.get("status") == "legacy_unverified"
                else "omega_publication_gold"
            )
            relations[f"gold_{parts[2]}".lower()] = (schema, table)
        if not relations:
            return sql
        tree = sqlglot.parse_one(sql, read="duckdb")

        def replace(node):
            if not isinstance(node, exp.Table) or (node.db or "").lower() != "pggold":
                return node
            target = relations.get(node.name.lower())
            if not target:
                return node
            schema, table = target
            return exp.Table(
                this=exp.to_identifier(table),
                db=exp.to_identifier(schema),
                catalog=exp.to_identifier("pggold"),
                alias=node.args.get("alias"),
            )

        return tree.transform(replace).sql(dialect="duckdb")

    def _assert_inputs_unchanged(
        self, dataset: dict[str, Any], user_context: dict[str, Any] | None
    ) -> None:
        state = self._state() or {}
        captured = state.get("input_state") or []
        current = resolve_input_state(self, dataset, user_context)
        if canonical_digest(current) != canonical_digest(captured):
            raise RuntimeError(
                "materialization dependencies changed before publication"
            )
