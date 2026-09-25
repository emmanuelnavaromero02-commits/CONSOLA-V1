from __future__ import annotations

import threading
from typing import Any

try:
    from app.duckdb_engine import DuckDBEngine
    from app.publication_contract import PublicationIdentity, canonical_digest
    from app.publication_evidence import (
        PublicationCandidateStore,
        PublicationEvidenceStore,
        PublicationVerifierClient,
    )
    from app.publication_finalize import PublicationFinalizeMixin
    from app.publication_inputs import resolve_input_state
    from app.publication_input_binding import PublicationInputBindingMixin
    from app.publication_objects import PublicationObjectMixin
    from app.publication_read_guard import PublicationReadGuardMixin
    from app.publication_recovery import PublicationRecoveryMixin
    from app.publication_replay import PublicationReplayMixin
    from app.publication_store import PublicationStore
except ModuleNotFoundError:
    from refinement.app.duckdb_engine import DuckDBEngine
    from refinement.app.publication_contract import (
        PublicationIdentity,
        canonical_digest,
    )
    from refinement.app.publication_evidence import (
        PublicationCandidateStore,
        PublicationEvidenceStore,
        PublicationVerifierClient,
    )
    from refinement.app.publication_finalize import PublicationFinalizeMixin
    from refinement.app.publication_inputs import resolve_input_state
    from refinement.app.publication_input_binding import PublicationInputBindingMixin
    from refinement.app.publication_objects import PublicationObjectMixin
    from refinement.app.publication_read_guard import PublicationReadGuardMixin
    from refinement.app.publication_recovery import PublicationRecoveryMixin
    from refinement.app.publication_replay import PublicationReplayMixin
    from refinement.app.publication_store import PublicationStore


class StagedPublicationEngine(
    PublicationFinalizeMixin,
    PublicationReplayMixin,
    PublicationReadGuardMixin,
    PublicationInputBindingMixin,
    PublicationObjectMixin,
    PublicationRecoveryMixin,
    DuckDBEngine,
):

    def __init__(self) -> None:
        super().__init__()
        self._publication_local = threading.local()
        self._publication_replay_local = threading.local()
        self._publication_store_instance: PublicationStore | None = None
        self._evidence_store_instance: PublicationEvidenceStore | None = None
        self._candidate_store_instance: PublicationCandidateStore | None = None
        self._publication_verifier_instance: PublicationVerifierClient | None = None

    @property
    def _publication_store(self) -> PublicationStore:
        if self._publication_store_instance is None:
            self._publication_store_instance = PublicationStore()
        return self._publication_store_instance

    @property
    def _evidence_store(self) -> PublicationEvidenceStore:
        if self._evidence_store_instance is None:
            self._evidence_store_instance = PublicationEvidenceStore()
        return self._evidence_store_instance

    @property
    def _candidate_store(self) -> PublicationCandidateStore:
        if self._candidate_store_instance is None:
            self._candidate_store_instance = PublicationCandidateStore(
                self._publication_store
            )
        return self._candidate_store_instance

    @property
    def _publication_verifier(self) -> PublicationVerifierClient:
        if self._publication_verifier_instance is None:
            self._publication_verifier_instance = PublicationVerifierClient()
        return self._publication_verifier_instance

    def _state(self) -> dict[str, Any] | None:
        return getattr(self._publication_local, "state", None)

    def materialize(
        self, ds: dict[str, Any], user_context: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        if self._state() is not None:
            return super().materialize(ds, user_context)
        self._mark_publication_replayed(False)
        input_state = resolve_input_state(self, ds, user_context)
        candidate = PublicationIdentity.build(
            ds,
            user_context,
            input_state=input_state,
        )
        head = self._publication_store.head(candidate.scope)
        if head and (head.get("input_digest"), head.get("contract_digest")) == (
            candidate.input_digest,
            candidate.contract_digest,
        ):
            self._verify_prepared_object(head)
            self._mark_publication_replayed(True)
            return {
                "name": candidate.scope.dataset,
                "layer": candidate.scope.layer,
                "row_count": int(head.get("row_count") or 0),
            }
        identity = PublicationIdentity.build(
            ds,
            user_context,
            input_state=input_state,
            expected_head_run_id=(head or {}).get("materialization_run_id"),
        )
        with self._publication_store.run_lock(identity):
            return self._materialize_locked(ds, user_context, identity, input_state)

    def _materialize_locked(
        self,
        ds: dict[str, Any],
        user_context: dict[str, Any] | None,
        identity: PublicationIdentity,
        input_state: list[dict[str, Any]],
    ) -> dict[str, Any]:
        status = self._publication_store.reserve(identity)
        current = self._publication_store.run(identity) or {}
        expected_head = current.get("expected_head_run_id")
        if status == "published":
            self._mark_publication_replayed(True)
            return {
                "name": identity.scope.dataset,
                "layer": identity.scope.layer,
                "row_count": int(current.get("row_count") or 0),
            }
        if status == "prepared":
            try:
                self._verify_prepared_object(current)
                self._publication_store.publish(identity, expected_head)
            except Exception as exc:
                if getattr(exc, "pgcode", None) == "40001":
                    self._publication_store.quarantine_prepared(
                        identity, "publication_head_cas_lost"
                    )
                    raise RuntimeError(
                        "publication head conflict; prepared run was quarantined"
                    ) from exc
                self._recover_prepared(identity, exc)
            current = self._publication_store.run(identity) or {}
            return {
                "name": identity.scope.dataset,
                "layer": identity.scope.layer,
                "row_count": int(current.get("row_count") or 0),
            }
        self._publication_local.state = {
            "identity": identity,
            "expected_head": expected_head,
            "dataset": ds,
            "user_context": user_context,
            "input_state": input_state,
            "lineage": {},
            "schema_fields": [],
            "object_uri": "",
            "object_checksum": "",
            "object_version": "",
            "staging_table": None,
            "row_count": 0,
        }
        try:
            result = super().materialize(ds, user_context)
            if not self._state().get("published"):
                raise RuntimeError(
                    "materialization ended without a publication receipt"
                )
            result.pop("storage_uri", None)
            return result
        finally:
            self._publication_local.state = None

    def _ensure_scoped_gold_table(self, con: Any, table: str, sql: str) -> None:
        state = self._state()
        if not state:
            return super()._ensure_scoped_gold_table(con, table, sql)
        state["gold_schema"] = self._gold_query_schema(con, sql)
        state["gold_snapshot_schema"] = {
            str(row[0]): str(row[1])
            for row in con.execute(
                f"DESCRIBE SELECT * FROM ({sql}) _snapshot_schema LIMIT 0"
            ).fetchall()
        }

    def _replace_scoped_gold_rows(
        self, con: Any, table: str, sql: str, tenant: str, workspace: str
    ) -> int:
        state = self._state()
        if not state:
            return super()._replace_scoped_gold_rows(con, table, sql, tenant, workspace)
        result = con.execute(f"SELECT * FROM ({sql}) _q")
        descriptions = result.description or []
        columns = [
            {
                "name": str(item[0]),
                "type": state["gold_schema"][str(item[0]).lower()][1],
            }
            for item in descriptions
        ]
        rows = result.fetchall()
        stage, count, frozen_rows = self._publication_store.stage_gold(
            state["identity"], columns, rows
        )
        state.update(
            staging_table=stage,
            row_count=count,
            gold_columns=columns,
            gold_rows=frozen_rows,
        )
        return count

    def _apply_gold_rls(self, table: str) -> None:
        if not self._state():
            return super()._apply_gold_rls(table)
        return None

    def _copy_scoped_gold_table_snapshot(
        self,
        con: Any,
        table: str,
        storage_path: str,
        tenant: str,
        workspace: str,
        user_context: dict | None,
        partition_by: str | None = None,
    ) -> str:
        state = self._state()
        options = {"partition_by": partition_by} if partition_by else {}
        if not state:
            return super()._copy_scoped_gold_table_snapshot(
                con, table, storage_path, tenant, workspace, user_context, **options
            )
        relation = f"publication_{state['identity'].materialization_run_id.hex}"
        definitions = ",".join(
            f'"{item["name"]}" {state["gold_snapshot_schema"][item["name"]]}'
            for item in state["gold_columns"]
        )
        con.execute(f'CREATE TEMP TABLE "{relation}" ({definitions})')
        try:
            rows = state["gold_rows"]
            if rows:
                marks = ",".join("?" for _ in state["gold_columns"])
                con.executemany(f'INSERT INTO "{relation}" VALUES ({marks})', rows)
            return self._copy_to_parquet(
                con, f'SELECT * FROM "{relation}"', storage_path, **options
            )
        finally:
            con.execute(f'DROP TABLE "{relation}"')

    def _write_lineage(self, **values: Any) -> None:
        state = self._state()
        if not state:
            return super()._write_lineage(**values)
        state["lineage"] = {
            "cartridge_id": str(state["dataset"].get("cartridge") or ""),
            "source_entity": str(values.get("source_entity") or ""),
            "source_load_date": str(values.get("source_load_date") or ""),
            "source_batch_id": str(values.get("source_batch_id") or ""),
            "sql_digest": canonical_digest(str(values.get("sql_def") or "")),
            "column_mapping_digest": canonical_digest(
                values.get("column_mapping") or {}
            ),
            "input_digest": state["identity"].input_digest,
            "contract_digest": state["identity"].contract_digest,
            "public_sources": list(state["dataset"].get("sources") or []),
            "public_metadata": {
                "description": str(state["dataset"].get("description") or ""),
                "relationships": list(state["dataset"].get("relationships") or []),
            },
        }
        state["row_count"] = int(values.get("row_count") or 0)

    def _prune_snapshots(self, *args: Any, **kwargs: Any) -> None:
        if not self._state():
            return super()._prune_snapshots(*args, **kwargs)
        return None
