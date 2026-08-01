from __future__ import annotations

import json
import os
import re
from collections.abc import Iterable
from contextlib import contextmanager
from typing import Any

import psycopg2
from psycopg2.extras import execute_values

try:
    from app.publication_contract import PublicationIdentity, PublicationScope
except ModuleNotFoundError:
    from refinement.app.publication_contract import (
        PublicationIdentity,
        PublicationScope,
    )


_IDENTIFIER = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,127}$")


def _dsn(raw: str) -> str:
    return (raw or "").replace("postgresql+psycopg2://", "postgresql://")


def _ident(value: str) -> str:
    if not _IDENTIFIER.fullmatch(value or ""):
        raise ValueError("invalid publication identifier")
    return '"' + value.replace('"', '""') + '"'


class PublicationStore:
    def __init__(self, publisher_dsn: str | None = None, reader_dsn: str | None = None):
        self.publisher_dsn = _dsn(
            publisher_dsn or os.environ.get("GOLD_PUBLISHER_DATABASE_URL", "")
        )
        self.reader_dsn = _dsn(
            reader_dsn
            or os.environ.get("GOLD_DATABASE_URL", "")
            or os.environ.get("DATABASE_URL", "")
        )

    def _publisher_url(self) -> str:
        if not self.publisher_dsn:
            raise RuntimeError("GOLD_PUBLISHER_DATABASE_URL is required")
        return self.publisher_dsn

    @staticmethod
    def _scope(cur: Any, scope: PublicationScope) -> None:
        cur.execute("SELECT set_config('app.tenant_id', %s, true)", (scope.tenant_id,))
        cur.execute(
            "SELECT set_config('app.workspace_id', %s, true)", (scope.workspace_id,)
        )

    @contextmanager
    def run_lock(self, identity: PublicationIdentity):
        """Serialize one server-owned run across its complete preparation."""
        conn = psycopg2.connect(self._publisher_url())
        try:
            with conn.cursor() as cur:
                self._scope(cur, identity.scope)
                cur.execute(
                    "SELECT pg_advisory_lock(hashtextextended(%s, 0))",
                    (str(identity.materialization_run_id),),
                )
            conn.commit()
            try:
                yield
            except Exception:
                current = self.run(identity) or {}
                status = str(current.get("status") or "")
                if status == "prepared" or status == "published":
                    raise
                self._abandon_unprepared(identity)
                raise
        finally:
            try:
                with conn.cursor() as cur:
                    cur.execute(
                        "SELECT pg_advisory_unlock(hashtextextended(%s, 0))",
                        (str(identity.materialization_run_id),),
                    )
                conn.commit()
            finally:
                conn.close()

    def _abandon_unprepared(self, identity: PublicationIdentity) -> None:
        self.abandon(identity)

    def reserve(self, identity: PublicationIdentity) -> str:
        with psycopg2.connect(self._publisher_url()) as conn, conn.cursor() as cur:
            self._scope(cur, identity.scope)
            cur.execute(
                "SELECT status FROM omega_publication.reserve_materialization(%s,%s,%s,%s,%s,%s,%s,%s)",
                (
                    str(identity.materialization_run_id),
                    identity.scope.tenant_id,
                    identity.scope.workspace_id,
                    identity.scope.dataset,
                    identity.scope.layer,
                    identity.input_digest,
                    identity.contract_digest,
                    identity.expected_head_run_id,
                ),
            )
            return str(cur.fetchone()[0])

    def head(self, scope: PublicationScope) -> dict[str, Any] | None:
        dsn = self.reader_dsn or self._publisher_url()
        with psycopg2.connect(dsn) as conn, conn.cursor() as cur:
            self._scope(cur, scope)
            cur.execute(
                """
                SELECT h.materialization_run_id, h.generation, r.object_uri,
                       r.object_checksum, r.row_count, r.status, h.published_at,
                       r.input_digest, r.contract_digest, r.gold_table
                  FROM omega_publication.dataset_publication_heads h
                  JOIN omega_publication.materialization_runs r
                    ON r.materialization_run_id=h.materialization_run_id
                 WHERE h.tenant_id=%s AND h.workspace_id=%s
                   AND h.dataset=%s AND h.layer=%s
                """,
                (scope.tenant_id, scope.workspace_id, scope.dataset, scope.layer),
            )
            row = cur.fetchone()
            if not row:
                return None
            return {
                "materialization_run_id": str(row[0]),
                "generation": int(row[1]),
                "object_uri": row[2],
                "object_checksum": row[3],
                "row_count": row[4],
                "status": row[5],
                "published_at": row[6],
                "input_digest": row[7],
                "contract_digest": row[8],
                "gold_table": row[9],
            }

    def run(self, identity: PublicationIdentity) -> dict[str, Any] | None:
        with psycopg2.connect(self._publisher_url()) as conn, conn.cursor() as cur:
            self._scope(cur, identity.scope)
            cur.execute(
                """SELECT status, object_uri, object_checksum, row_count,
                          expected_head_run_id
                     FROM omega_publication.materialization_runs
                    WHERE materialization_run_id=%s""",
                (str(identity.materialization_run_id),),
            )
            row = cur.fetchone()
            return (
                {
                    "status": row[0],
                    "object_uri": row[1],
                    "object_checksum": row[2],
                    "row_count": row[3],
                    "expected_head_run_id": str(row[4]) if row[4] else None,
                }
                if row
                else None
            )

    def stage_gold(
        self,
        identity: PublicationIdentity,
        columns: list[dict[str, str]],
        rows: Iterable[tuple[Any, ...]],
    ) -> tuple[str, int, list[tuple[Any, ...]]]:
        values = list(rows)
        with psycopg2.connect(self._publisher_url()) as conn, conn.cursor() as cur:
            self._scope(cur, identity.scope)
            cur.execute(
                "SELECT omega_publication.create_gold_stage(%s,%s::jsonb)",
                (str(identity.materialization_run_id), json.dumps(columns)),
            )
            stage = str(cur.fetchone()[0])
            if values:
                names = ", ".join(_ident(item["name"]) for item in columns)
                execute_values(
                    cur,
                    f"INSERT INTO omega_publication_stage.{_ident(stage)} ({names}) VALUES %s",
                    values,
                    page_size=1000,
                )
            names = ", ".join(_ident(item["name"]) for item in columns)
            cur.execute(f"SELECT {names} FROM omega_publication_stage.{_ident(stage)}")
            frozen = cur.fetchall()
            return stage, len(frozen), frozen

    def mark_prepared(
        self,
        identity: PublicationIdentity,
        *,
        object_uri: str,
        object_checksum: str,
        row_count: int,
        staging_table: str | None,
        lineage: dict[str, Any],
        catalog: list[dict[str, str]],
    ) -> dict[str, str]:
        gold_table = (
            f"run_{identity.materialization_run_id.hex}"
            if identity.scope.layer == "gold"
            else None
        )
        with psycopg2.connect(self._publisher_url()) as conn, conn.cursor() as cur:
            self._scope(cur, identity.scope)
            cur.execute(
                "SELECT schema_digest,evidence_digest FROM omega_publication.mark_prepared(%s,%s,%s,%s,%s,%s,%s::jsonb,%s::jsonb)",
                (
                    str(identity.materialization_run_id),
                    object_uri,
                    object_checksum,
                    row_count,
                    gold_table,
                    staging_table,
                    json.dumps(lineage, sort_keys=True, default=str),
                    json.dumps(catalog, sort_keys=True, default=str),
                ),
            )
            row = cur.fetchone()
            if not row:
                raise RuntimeError("publication evidence was not prepared")
            return {"schema_digest": str(row[0]), "evidence_digest": str(row[1])}

    def publish(
        self, identity: PublicationIdentity, expected_head: str | None
    ) -> dict[str, Any]:
        with psycopg2.connect(self._publisher_url()) as conn, conn.cursor() as cur:
            self._scope(cur, identity.scope)
            cur.execute(
                "SELECT receipt_id, generation, replayed FROM omega_publication.publish_materialization(%s,%s)",
                (str(identity.materialization_run_id), expected_head),
            )
            receipt, generation, replayed = cur.fetchone()
            return {
                "receipt_id": str(receipt),
                "generation": int(generation),
                "replayed": bool(replayed),
            }

    def abandon(self, identity: PublicationIdentity) -> None:
        with psycopg2.connect(self._publisher_url()) as conn, conn.cursor() as cur:
            self._scope(cur, identity.scope)
            cur.execute(
                "SELECT omega_publication.abandon_materialization(%s)",
                (str(identity.materialization_run_id),),
            )
