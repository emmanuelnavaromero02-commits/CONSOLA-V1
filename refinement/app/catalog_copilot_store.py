"""Postgres persistence of the Catalog Copilot.

Every statement runs inside one transaction scoped with
set_config('app.tenant_id'/'app.workspace_id', ..., true) so FORCE RLS
applies. The provenance guards are SQL, not Python: a Copilot write never
replaces a manual or packaged description, classification or edge, and a
rejected edge is never re-activated. The Copilot never writes is_key,
tags, is_metric or example_values: those columns feed attested publication
evidence, so key detection lives in copilot_evidence ("key:exact") instead.
"""

from __future__ import annotations

import json
import os
from collections.abc import Iterable, Iterator
from contextlib import contextmanager
from typing import Any, Callable

import psycopg2

try:
    from app.catalog_copilot_rules import RULES_VERSION, evidence_is_counts_only
except ModuleNotFoundError:
    from refinement.app.catalog_copilot_rules import (
        RULES_VERSION,
        evidence_is_counts_only,
    )

_PROTECTION_RANK = {"plain": 0, "shadowed": 1, "masked": 2, "encrypted": 3}
_EDGE_KEYS = ("from_dataset", "from_column", "to_dataset", "to_column")


def _dsn(raw: str) -> str:
    return (raw or "").replace("postgresql+psycopg2://", "postgresql://")


def _scope(sec: dict[str, Any] | None) -> tuple[str, str]:
    tenant_id = str((sec or {}).get("tenant_id") or "").strip()
    workspace_id = str((sec or {}).get("workspace_id") or "").strip()
    if not tenant_id or not workspace_id:
        raise ValueError("catalog copilot requires tenant and workspace scope")
    return tenant_id, workspace_id


def edge_key(edge: dict[str, Any]) -> tuple[str, str, str, str]:
    return tuple(str(edge.get(key) or "") for key in _EDGE_KEYS)  # type: ignore[return-value]


def _normalized(name: str) -> str:
    return "".join(ch for ch in str(name or "").lower() if ch.isalnum())


class CatalogCopilotStore:
    def __init__(self, dsn: str | Callable[[], str] | None = None) -> None:
        self._dsn = dsn

    def _database_url(self) -> str:
        raw = self._dsn() if callable(self._dsn) else self._dsn
        return _dsn(raw or os.environ.get("DATABASE_URL", ""))

    @contextmanager
    def _cursor(self, sec: dict[str, Any] | None) -> Iterator[Any]:
        tenant_id, workspace_id = _scope(sec)
        conn = psycopg2.connect(self._database_url(), connect_timeout=3)
        try:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT set_config('app.tenant_id', %s, true),"
                    " set_config('app.workspace_id', %s, true)",
                    (tenant_id, workspace_id),
                )
                yield cur
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    @staticmethod
    def _rows(cur: Any) -> list[dict[str, Any]]:
        names = [item[0] for item in cur.description]
        return [dict(zip(names, row)) for row in cur.fetchall()]

    def load_states(
        self,
        sec: dict[str, Any],
        subjects: Iterable[tuple[str, str]] | None = None,
    ) -> dict[tuple[str, str], dict[str, Any]]:
        _tenant_id, workspace_id = _scope(sec)
        wanted = list(subjects) if subjects is not None else None
        with self._cursor(sec) as cur:
            if wanted is None:
                cur.execute(
                    """SELECT subject_kind, subject, layer, cartridge, fingerprint,
                              rules_version, status, display_name, description,
                              summary, error_code, duration_ms, profiled_at
                         FROM catalog_copilot_state
                        WHERE workspace_id = %s::uuid""",
                    (workspace_id,),
                )
            else:
                if not wanted:
                    return {}
                cur.execute(
                    """SELECT subject_kind, subject, layer, cartridge, fingerprint,
                              rules_version, status, display_name, description,
                              summary, error_code, duration_ms, profiled_at
                         FROM catalog_copilot_state
                        WHERE workspace_id = %s::uuid
                          AND (subject_kind, subject) IN (
                              SELECT * FROM unnest(%s::text[], %s::text[]))""",
                    (
                        workspace_id,
                        [kind for kind, _subject in wanted],
                        [subject for _kind, subject in wanted],
                    ),
                )
            return {
                (row["subject_kind"], row["subject"]): row for row in self._rows(cur)
            }

    def load_catalog_columns(
        self, sec: dict[str, Any], datasets: Iterable[str]
    ) -> dict[str, dict[str, dict[str, Any]]]:
        _tenant_id, workspace_id = _scope(sec)
        names = sorted({str(name) for name in datasets if name})
        if not names:
            return {}
        with self._cursor(sec) as cur:
            cur.execute(
                """SELECT dataset, column_name, data_type, description,
                          description_origin, tags, is_key, is_metric,
                          null_rate, distinct_count, semantic_type,
                          classifications, classification_origin,
                          copilot_confidence
                     FROM data_catalog
                    WHERE workspace_id = %s::uuid
                      AND scope_status = 'scoped'
                      AND dataset = ANY(%s::text[])""",
                (workspace_id, names),
            )
            out: dict[str, dict[str, dict[str, Any]]] = {}
            for row in self._rows(cur):
                out.setdefault(str(row["dataset"]), {})[str(row["column_name"])] = row
            return out

    def load_edges(
        self, sec: dict[str, Any], datasets: Iterable[str]
    ) -> list[dict[str, Any]]:
        _tenant_id, workspace_id = _scope(sec)
        names = sorted({str(name) for name in datasets if name})
        if not names:
            return []
        with self._cursor(sec) as cur:
            cur.execute(
                """SELECT from_dataset, from_column, to_dataset, to_column,
                          join_hint, cardinality, origin, status, confidence,
                          basis, description
                     FROM data_relationships
                    WHERE workspace_id = %s::uuid
                      AND scope_status = 'scoped'
                      AND (from_dataset = ANY(%s::text[]) OR to_dataset = ANY(%s::text[]))""",
                (workspace_id, names, names),
            )
            return self._rows(cur)

    def load_protection(
        self, sec: dict[str, Any], sources: Iterable[str]
    ) -> dict[str, str]:
        pairs: list[tuple[str, str]] = []
        for source in sources or []:
            parts = [part for part in str(source or "").split("/") if part]
            if len(parts) >= 3 and parts[0] == "raw":
                pairs.append((parts[1], parts[2]))
        if not pairs:
            return {}
        with self._cursor(sec) as cur:
            cur.execute(
                """SELECT protection
                     FROM entity_config
                    WHERE (cartridge_id, entity) IN (
                        SELECT * FROM unnest(%s::text[], %s::text[]))""",
                ([cartridge for cartridge, _entity in pairs], [entity for _c, entity in pairs]),
            )
            protection: dict[str, str] = {}
            for (value,) in cur.fetchall():
                if isinstance(value, str):
                    try:
                        value = json.loads(value)
                    except json.JSONDecodeError:
                        continue
                if not isinstance(value, dict):
                    continue
                for field_name, level in value.items():
                    level_text = str(level or "").strip().lower()
                    if level_text not in _PROTECTION_RANK:
                        continue
                    key = _normalized(field_name)
                    current = protection.get(key, "plain")
                    if _PROTECTION_RANK[level_text] > _PROTECTION_RANK[current]:
                        protection[key] = level_text
            return protection

    def upsert_column_annotations(
        self,
        sec: dict[str, Any],
        *,
        dataset: str,
        layer: str,
        cartridge: str,
        annotations: list[dict[str, Any]],
    ) -> int:
        tenant_id, workspace_id = _scope(sec)
        written = 0
        with self._cursor(sec) as cur:
            for item in annotations:
                evidence = dict(item.get("evidence") or {})
                if not evidence_is_counts_only(evidence):
                    raise ValueError("copilot evidence must hold counts only")
                cur.execute(
                    """
                    INSERT INTO data_catalog
                        (dataset, layer, cartridge, column_name, data_type,
                         description, description_origin, semantic_type,
                         classifications, classification_origin,
                         copilot_evidence, copilot_confidence, copilot_at,
                         is_key, is_metric, tags,
                         tenant_id, workspace_id, scope_status, updated_at)
                    VALUES (%s, %s, %s, %s, %s,
                            %s, 'copilot', %s,
                            %s::text[], %s,
                            %s::jsonb, %s, clock_timestamp(),
                            FALSE, FALSE, '{}'::text[],
                            %s::uuid, %s::uuid, 'scoped', NOW())
                    ON CONFLICT (workspace_id, dataset, column_name)
                    WHERE workspace_id IS NOT NULL
                    DO UPDATE SET
                        description = CASE
                            WHEN COALESCE(btrim(data_catalog.description), '') = ''
                              OR data_catalog.description_origin = 'copilot'
                            THEN EXCLUDED.description
                            ELSE data_catalog.description END,
                        description_origin = CASE
                            WHEN COALESCE(btrim(data_catalog.description), '') = ''
                              OR data_catalog.description_origin = 'copilot'
                            THEN 'copilot'
                            ELSE data_catalog.description_origin END,
                        semantic_type = EXCLUDED.semantic_type,
                        classifications = CASE
                            WHEN data_catalog.classification_origin IS NULL
                              OR data_catalog.classification_origin = 'copilot'
                              OR (data_catalog.classification_origin = 'packaged'
                                  AND EXCLUDED.classification_origin = 'packaged')
                            THEN EXCLUDED.classifications
                            ELSE data_catalog.classifications END,
                        classification_origin = CASE
                            WHEN data_catalog.classification_origin IS NULL
                              OR data_catalog.classification_origin = 'copilot'
                              OR (data_catalog.classification_origin = 'packaged'
                                  AND EXCLUDED.classification_origin = 'packaged')
                            THEN EXCLUDED.classification_origin
                            ELSE data_catalog.classification_origin END,
                        copilot_evidence = EXCLUDED.copilot_evidence,
                        copilot_confidence = EXCLUDED.copilot_confidence,
                        copilot_at = EXCLUDED.copilot_at,
                        tenant_id = EXCLUDED.tenant_id,
                        scope_status = 'scoped',
                        updated_at = NOW()
                    """,
                    (
                        dataset,
                        layer,
                        cartridge,
                        str(item["column"]),
                        str(item.get("data_type") or ""),
                        str(item.get("description") or "")[:600],
                        item.get("semantic_type"),
                        list(item.get("classifications") or []),
                        item.get("classification_origin")
                        if item.get("classifications")
                        else None,
                        json.dumps(evidence, sort_keys=True),
                        item.get("confidence"),
                        tenant_id,
                        workspace_id,
                    ),
                )
                written += max(cur.rowcount, 0)
        return written

    def upsert_copilot_edges(
        self, sec: dict[str, Any], edges: list[dict[str, Any]]
    ) -> int:
        tenant_id, workspace_id = _scope(sec)
        if not edges:
            return 0
        datasets = {edge["from_dataset"] for edge in edges} | {
            edge["to_dataset"] for edge in edges
        }
        existing = {edge_key(row): row for row in self.load_edges(sec, datasets)}
        written = 0
        with self._cursor(sec) as cur:
            for edge in edges:
                key = edge_key(edge)
                reverse = (key[2], key[3], key[0], key[1])
                blocked = False
                for candidate in (key, reverse):
                    row = existing.get(candidate)
                    if row is None:
                        continue
                    if row.get("status") == "rejected" or row.get("origin") in {
                        "manual",
                        "packaged",
                    }:
                        blocked = True
                if blocked:
                    continue
                basis = dict(edge.get("basis") or {})
                if {"values", "examples", "sample", "samples", "min", "max"} & set(basis):
                    raise ValueError("relationship basis must hold counts only")
                cur.execute(
                    """
                    INSERT INTO data_relationships
                        (from_dataset, from_column, to_dataset, to_column,
                         join_hint, description, origin, status, cardinality,
                         confidence, basis, detected_at, rules_version,
                         tenant_id, workspace_id, scope_status)
                    SELECT %s, %s, %s, %s, 'LEFT', %s, 'copilot', 'active', %s,
                           %s, %s::jsonb, clock_timestamp(), %s,
                           %s::uuid, %s::uuid, 'scoped'
                      FROM datasets from_ds
                      JOIN datasets to_ds
                        ON to_ds.name = %s AND to_ds.workspace_id = %s::uuid
                     WHERE from_ds.name = %s AND from_ds.workspace_id = %s::uuid
                    ON CONFLICT (workspace_id, from_dataset, from_column, to_dataset, to_column)
                    WHERE workspace_id IS NOT NULL
                    DO UPDATE SET
                        cardinality = EXCLUDED.cardinality,
                        confidence = EXCLUDED.confidence,
                        basis = EXCLUDED.basis,
                        description = EXCLUDED.description,
                        detected_at = EXCLUDED.detected_at,
                        rules_version = EXCLUDED.rules_version,
                        join_hint = 'LEFT',
                        status = 'active',
                        updated_at = NOW()
                    WHERE data_relationships.origin = 'copilot'
                      AND data_relationships.status IN ('active', 'retired')
                    """,
                    (
                        key[0],
                        key[1],
                        key[2],
                        key[3],
                        str(edge.get("description") or "")[:500],
                        edge.get("cardinality"),
                        edge.get("confidence"),
                        json.dumps(basis, sort_keys=True),
                        edge.get("rules_version") or RULES_VERSION,
                        tenant_id,
                        workspace_id,
                        key[2],
                        workspace_id,
                        key[0],
                        workspace_id,
                    ),
                )
                written += max(cur.rowcount, 0)
        return written

    def retire_copilot_edges(
        self,
        sec: dict[str, Any],
        *,
        dataset: str,
        keep: set[tuple[str, str, str, str]],
        visible: set[str],
    ) -> int:
        _tenant_id, workspace_id = _scope(sec)
        stale = [
            edge_key(row)
            for row in self.load_edges(sec, [dataset])
            if row.get("origin") == "copilot"
            and row.get("status") == "active"
            and edge_key(row) not in keep
            and str(row.get("from_dataset")) in visible
            and str(row.get("to_dataset")) in visible
        ]
        if not stale:
            return 0
        retired = 0
        with self._cursor(sec) as cur:
            for key in stale:
                cur.execute(
                    """UPDATE data_relationships
                          SET status = 'retired', updated_at = NOW()
                        WHERE workspace_id = %s::uuid
                          AND from_dataset = %s AND from_column = %s
                          AND to_dataset = %s AND to_column = %s
                          AND origin = 'copilot' AND status = 'active'""",
                    (workspace_id, *key),
                )
                retired += max(cur.rowcount, 0)
        return retired

    def reject_edge(self, sec: dict[str, Any], edge: dict[str, Any]) -> bool:
        tenant_id, workspace_id = _scope(sec)
        key = edge_key(edge)
        with self._cursor(sec) as cur:
            cur.execute(
                """
                INSERT INTO data_relationships
                    (from_dataset, from_column, to_dataset, to_column, join_hint,
                     description, origin, status, tenant_id, workspace_id, scope_status)
                SELECT %s, %s, %s, %s, NULL, '', 'manual', 'rejected',
                       %s::uuid, %s::uuid, 'scoped'
                  FROM datasets from_ds
                  JOIN datasets to_ds
                    ON to_ds.name = %s AND to_ds.workspace_id = %s::uuid
                 WHERE from_ds.name = %s AND from_ds.workspace_id = %s::uuid
                ON CONFLICT (workspace_id, from_dataset, from_column, to_dataset, to_column)
                WHERE workspace_id IS NOT NULL
                DO UPDATE SET status = 'rejected', origin = 'manual', updated_at = NOW()
                RETURNING 1
                """,
                (
                    *key,
                    tenant_id,
                    workspace_id,
                    key[2],
                    workspace_id,
                    key[0],
                    workspace_id,
                ),
            )
            return cur.fetchone() is not None

    def save_state(self, sec: dict[str, Any], state: dict[str, Any]) -> None:
        tenant_id, workspace_id = _scope(sec)
        summary = dict(state.get("summary") or {})
        if {"values", "examples", "sample", "samples", "min", "max"} & set(summary):
            raise ValueError("copilot summary must hold counts only")
        with self._cursor(sec) as cur:
            cur.execute(
                """
                INSERT INTO catalog_copilot_state
                    (tenant_id, workspace_id, subject_kind, subject, layer,
                     cartridge, fingerprint, rules_version, status, display_name,
                     description, summary, error_code, duration_ms, profiled_at)
                VALUES (%s::uuid, %s::uuid, %s, %s, %s, %s, %s, %s, %s, %s, %s,
                        %s::jsonb, %s, %s, clock_timestamp())
                ON CONFLICT (workspace_id, subject_kind, subject) DO UPDATE SET
                    layer = EXCLUDED.layer,
                    cartridge = EXCLUDED.cartridge,
                    fingerprint = EXCLUDED.fingerprint,
                    rules_version = EXCLUDED.rules_version,
                    status = EXCLUDED.status,
                    display_name = EXCLUDED.display_name,
                    description = EXCLUDED.description,
                    summary = EXCLUDED.summary,
                    error_code = EXCLUDED.error_code,
                    duration_ms = EXCLUDED.duration_ms,
                    profiled_at = clock_timestamp()
                """,
                (
                    tenant_id,
                    workspace_id,
                    state["subject_kind"],
                    state["subject"],
                    state.get("layer"),
                    state.get("cartridge"),
                    state["fingerprint"],
                    state.get("rules_version") or RULES_VERSION,
                    state["status"],
                    (state.get("display_name") or None) and str(state["display_name"])[:200],
                    (state.get("description") or None) and str(state["description"])[:600],
                    json.dumps(summary, sort_keys=True, default=str),
                    state.get("error_code"),
                    state.get("duration_ms"),
                ),
            )

    def load_annotations(
        self,
        sec: dict[str, Any],
        datasets: Iterable[str],
        *,
        include_sources: bool = False,
    ) -> dict[str, Any]:
        """Live catalog annotations for the overlay: three scoped reads."""
        _tenant_id, workspace_id = _scope(sec)
        names = sorted({str(name) for name in datasets if name})
        columns: dict[str, dict[str, dict[str, Any]]] = {}
        relationships: list[dict[str, Any]] = []
        subjects: dict[tuple[str, str], dict[str, Any]] = {}
        sources: list[dict[str, Any]] = []
        with self._cursor(sec) as cur:
            if names:
                cur.execute(
                    """SELECT dataset, column_name, description, description_origin,
                              tags, is_key, is_metric, semantic_type, classifications,
                              classification_origin, copilot_confidence,
                              copilot_evidence
                         FROM data_catalog
                        WHERE workspace_id = %s::uuid
                          AND scope_status = 'scoped'
                          AND dataset = ANY(%s::text[])""",
                    (workspace_id, names),
                )
                for row in self._rows(cur):
                    columns.setdefault(str(row["dataset"]), {})[
                        str(row["column_name"])
                    ] = row
                cur.execute(
                    """SELECT from_dataset, from_column, to_dataset, to_column,
                              join_hint, cardinality, origin, status, confidence,
                              basis, description
                         FROM data_relationships
                        WHERE workspace_id = %s::uuid
                          AND scope_status = 'scoped'
                          AND (from_dataset = ANY(%s::text[])
                               OR to_dataset = ANY(%s::text[]))""",
                    (workspace_id, names, names),
                )
                relationships = self._rows(cur)
            cur.execute(
                """SELECT subject_kind, subject, layer, cartridge, status,
                          display_name, description, summary, rules_version,
                          profiled_at
                     FROM catalog_copilot_state
                    WHERE workspace_id = %s::uuid
                      AND ((subject_kind = 'dataset' AND subject = ANY(%s::text[]))
                           OR (%s AND subject_kind = 'bronze_source'))""",
                (workspace_id, names, bool(include_sources)),
            )
            for row in self._rows(cur):
                if row["subject_kind"] == "bronze_source":
                    sources.append(row)
                else:
                    subjects[("dataset", str(row["subject"]))] = row
        return {
            "columns": columns,
            "relationships": relationships,
            "subjects": subjects,
            "sources": sources,
        }

    def annotation_epoch(self, sec: dict[str, Any]) -> str | None:
        _tenant_id, workspace_id = _scope(sec)
        with self._cursor(sec) as cur:
            cur.execute(
                "SELECT max(profiled_at)::text FROM catalog_copilot_state "
                "WHERE workspace_id = %s::uuid",
                (workspace_id,),
            )
            row = cur.fetchone()
            return str(row[0]) if row and row[0] else None


__all__ = ["CatalogCopilotStore", "edge_key"]
