from __future__ import annotations

from typing import Any


class PublicationReadGuardMixin:

    def _published_dataset_head(
        self, dataset: dict[str, Any], user_context: dict[str, Any] | None
    ) -> dict[str, Any]:
        head = self._publication_store.head(
            self._publication_scope(dataset, user_context)
        )
        if not head:
            raise RuntimeError("dataset is not published")
        if head.get("status") != "legacy_unverified":
            self._verify_prepared_object(head)
        return head

    @staticmethod
    def _published_sql(dataset: dict[str, Any], head: dict[str, Any]) -> str:
        layer = str(dataset.get("layer") or "silver").lower()
        if layer == "gold":
            table = str(head.get("gold_table") or "")
            if head.get("status") == "legacy_unverified" and table.startswith("gold_"):
                return f'SELECT * FROM pggold."{table}"'
            if table.startswith("run_"):
                return f'SELECT * FROM pggold.omega_publication_gold."{table}"'
            raise RuntimeError("published Gold relation is unavailable")
        uri = str(head.get("object_uri") or "")
        if not uri.startswith("s3://"):
            raise RuntimeError("published Silver object is unavailable")
        return "SELECT * FROM read_parquet('" + uri.replace("'", "''") + "')"

    def _published_dataset(self, dataset: dict[str, Any], user_context):
        head = self._published_dataset_head(dataset, user_context)
        return {**dataset, "sql_def": self._published_sql(dataset, head), "sources": []}

    @staticmethod
    def _publication_scope(
        dataset: dict[str, Any], user_context: dict[str, Any] | None
    ):
        try:
            from app.publication_contract import PublicationScope
        except ModuleNotFoundError:
            from refinement.app.publication_contract import PublicationScope

        return PublicationScope.from_dataset(dataset, user_context)

    def query_dataset(
        self,
        dataset: dict[str, Any],
        filters: dict,
        limit: int = 100,
        user_context: dict | None = None,
    ) -> dict:
        published = self._published_dataset(dataset, user_context)
        return super().query_dataset(published, filters, limit, user_context)

    def get_dataset_schema(
        self, dataset: dict[str, Any], user_context: dict | None = None
    ) -> dict:
        published = self._published_dataset(dataset, user_context)
        return super().get_dataset_schema(published, user_context)
