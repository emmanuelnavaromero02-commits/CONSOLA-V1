"""Autonomous Catalog Copilot: profile and link published datasets.

Deterministic pipeline, no LLM:
  published snapshot -> fingerprint -> bounded DuckDB probe (counts only)
  -> rules (semantic type, sensitivity, executive Spanish text)
  -> FK -> PK candidates confirmed by value containment
  -> SQL-guarded writes (manual always wins) -> state row.

Work arrives from two places: right after a publication (post-publish
enqueue, handled by one daemon thread) and when a user opens the catalog
(catch_up, bounded by items and milliseconds). The in-memory queue is lost
on restart by design; catch_up recovers anything left behind.
"""

from __future__ import annotations

import hashlib
import logging
import os
import queue
import threading
import time
from contextlib import nullcontext
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Callable

try:
    from app.catalog_copilot_probe import (
        EXACT_KEY_MAX_ROWS,
        MAX_KEY_CANDIDATES,
        CatalogCopilotProbe,
        probe_error_code,
    )
    from app.catalog_copilot_rules import (
        MAX_PATTERN_COLUMNS,
        RULES_VERSION,
        cardinality,
        classify_column,
        column_description,
        dataset_description,
        dataset_display_name,
        is_scope_column,
        key_class,
        name_hints,
        normalize_name,
        relationship_confidence,
        semantic_type,
        source_display_name,
    )
    from app.catalog_copilot_store import edge_key
    from app.relationship_discovery import (
        _key_evidence,
        _normalize_type,
        discover_relationship_candidates,
    )
except ModuleNotFoundError:
    from refinement.app.catalog_copilot_probe import (
        EXACT_KEY_MAX_ROWS,
        MAX_KEY_CANDIDATES,
        CatalogCopilotProbe,
        probe_error_code,
    )
    from refinement.app.catalog_copilot_rules import (
        MAX_PATTERN_COLUMNS,
        RULES_VERSION,
        cardinality,
        classify_column,
        column_description,
        dataset_description,
        dataset_display_name,
        is_scope_column,
        key_class,
        name_hints,
        normalize_name,
        relationship_confidence,
        semantic_type,
        source_display_name,
    )
    from refinement.app.catalog_copilot_store import edge_key
    from refinement.app.relationship_discovery import (
        _key_evidence,
        _normalize_type,
        discover_relationship_candidates,
    )

logger = logging.getLogger(__name__)

QUEUE_MAX = 256
MAX_CATCH_UP_ITEMS = 8
MAX_CATCH_UP_BUDGET_MS = 1500
MAX_CONTAINMENT_CANDIDATES = 10
RETRY_AFTER_SECONDS = 600
MAX_SOURCE_COLUMNS = 200
_TEXT_TYPES = ("VARCHAR", "TEXT", "STRING", "CHAR", "CHARACTER", "BPCHAR")


def copilot_enabled() -> bool:
    value = os.environ.get("CATALOG_COPILOT_ENABLED", "1").strip().lower()
    return value not in {"0", "false", "off", "no"}


def _digest(parts: list[tuple[str, str]]) -> str:
    raw = "\n".join(f"{name}\t{data_type}" for name, data_type in parts)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:32]


def subject_fingerprint(kind: str, facts: dict[str, Any]) -> str:
    if kind == "dataset":
        schema = str(facts.get("schema_digest") or "") or _digest(
            [(str(f.get("name")), str(f.get("type"))) for f in facts.get("fields") or []]
        )
        return "|".join(
            (
                str(facts.get("materialization_run_id") or ""),
                str(facts.get("generation") or ""),
                schema,
                RULES_VERSION,
            )
        )
    schema = _digest(
        [(str(f.get("name")), str(f.get("type"))) for f in facts.get("fields") or []]
    )
    return "|".join(
        (str(facts.get("load_date") or ""), str(facts.get("num_rows") or 0), schema, RULES_VERSION)
    )


def _snapshot_facts(snapshot: Any) -> dict[str, Any]:
    head = dict(getattr(snapshot, "head", None) or {})
    evidence = getattr(snapshot, "evidence", None) or {}
    fields = [
        dict(item)
        for item in (evidence.get("catalog") or [])
        if isinstance(item, dict) and item.get("name") and item.get("type")
    ]
    schema_digest = head.get("schema_digest")
    if not schema_digest and head.get("status") == "legacy_unverified":
        schema_digest = "legacy:" + _digest(
            [(str(head.get("gold_table") or ""), str(head.get("published_at") or ""))]
        )
    return {
        "materialization_run_id": head.get("materialization_run_id"),
        "generation": head.get("generation"),
        "schema_digest": schema_digest,
        "row_count": head.get("row_count"),
        "published_at": head.get("published_at"),
        "fields": fields,
    }


def _is_text(raw_type: str) -> bool:
    upper = str(raw_type or "").upper()
    return upper.startswith(_TEXT_TYPES) or upper in {"UUID"}


def _is_stale(state: dict[str, Any] | None, fingerprint: str, now: datetime) -> bool:
    if not state:
        return True
    if state.get("fingerprint") != fingerprint:
        return True
    if state.get("status") != "ready":
        profiled_at = state.get("profiled_at")
        if isinstance(profiled_at, datetime):
            if profiled_at.tzinfo is None:
                profiled_at = profiled_at.replace(tzinfo=timezone.utc)
            return (now - profiled_at).total_seconds() >= RETRY_AFTER_SECONDS
    return False


@dataclass
class AutoCatalogStatus:
    status: str
    processed: int
    pending: int
    stale: int
    annotation_epoch: str | None

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "processed": self.processed,
            "pending": self.pending,
            "stale": self.stale,
            "annotation_epoch": self.annotation_epoch,
        }


@dataclass
class CatalogCopilotHost:
    """Scope-aware callables owned by the service entrypoint."""

    list_datasets: Callable[[dict[str, Any]], list[dict[str, Any]]]
    get_dataset: Callable[[dict[str, Any], str], dict[str, Any] | None]
    published_snapshots: Callable[[list[dict[str, Any]], dict[str, Any]], list[Any]]
    list_sources: Callable[[dict[str, Any]], list[str]] = lambda _sec: []
    source_allowed: Callable[[dict[str, Any], str], bool] = lambda _sec, _source: False


class AutonomousCatalogWorker:
    def __init__(
        self,
        host: CatalogCopilotHost,
        store: Any,
        probe: CatalogCopilotProbe,
        *,
        clock: Callable[[], float] = time.monotonic,
        enabled: Callable[[], bool] = copilot_enabled,
    ) -> None:
        self.host = host
        self.store = store
        self.probe = probe
        self.clock = clock
        self.enabled = enabled
        self._queue: queue.Queue = queue.Queue(maxsize=QUEUE_MAX)
        self._pending: set[tuple[str, str, str]] = set()
        self._inflight: set[tuple[str, str, str]] = set()
        self._lock = threading.Lock()
        self._thread: threading.Thread | None = None

    # ------------------------------------------------------------------ queue
    @staticmethod
    def _key(sec: dict[str, Any], subject: dict[str, str]) -> tuple[str, str, str]:
        return (
            str(sec.get("workspace_id") or ""),
            str(subject.get("kind") or ""),
            str(subject.get("name") or ""),
        )

    def enqueue(self, sec: dict[str, Any], subject: dict[str, str]) -> bool:
        if not self.enabled() or not sec.get("trusted"):
            return False
        if not sec.get("tenant_id") or not sec.get("workspace_id"):
            return False
        key = self._key(sec, subject)
        with self._lock:
            if key in self._pending or key in self._inflight:
                return False
            try:
                self._queue.put_nowait((dict(sec), dict(subject)))
            except queue.Full:
                return False
            self._pending.add(key)
            self._ensure_thread()
        return True

    def _ensure_thread(self) -> None:
        if self._thread is not None and self._thread.is_alive():
            return
        self._thread = threading.Thread(
            target=self._drain, name="catalog-copilot", daemon=True
        )
        self._thread.start()

    def _drain(self) -> None:
        while True:
            sec, subject = self._queue.get()
            key = self._key(sec, subject)
            with self._lock:
                self._pending.discard(key)
            try:
                self.profile_and_link(sec, subject)
            except Exception:
                logger.warning(
                    "catalog copilot background profile failed kind=%s",
                    subject.get("kind"),
                    exc_info=True,
                )
            finally:
                self._queue.task_done()

    def wait_idle(self, timeout: float = 10.0) -> bool:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            with self._lock:
                busy = bool(self._pending or self._inflight)
            if not busy and self._queue.unfinished_tasks == 0:
                return True
            time.sleep(0.01)
        return False

    # ---------------------------------------------------------------- catch-up
    def catch_up(
        self,
        sec: dict[str, Any],
        *,
        cartridge: str | None = None,
        include_sources: bool = False,
        max_items: int = MAX_CATCH_UP_ITEMS,
        budget_ms: int = MAX_CATCH_UP_BUDGET_MS,
    ) -> AutoCatalogStatus:
        if not self.enabled():
            return AutoCatalogStatus("idle", 0, 0, 0, None)
        max_items = max(0, min(int(max_items), MAX_CATCH_UP_ITEMS))
        budget = max(0, min(int(budget_ms), MAX_CATCH_UP_BUDGET_MS)) / 1000.0
        started = self.clock()
        now = datetime.now(timezone.utc)
        visible = [
            ds
            for ds in self.host.list_datasets(sec)
            if not cartridge or str(ds.get("cartridge") or "") == cartridge
        ]
        snapshots = self.host.published_snapshots(visible, sec) if visible else []
        states = self.store.load_states(sec)
        stale: list[dict[str, str]] = []
        for ds, snapshot in zip(visible, snapshots, strict=True):
            if snapshot is None:
                continue
            name = str(ds.get("name") or "")
            fingerprint = subject_fingerprint("dataset", _snapshot_facts(snapshot))
            if _is_stale(states.get(("dataset", name)), fingerprint, now):
                stale.append({"kind": "dataset", "name": name})
        if include_sources:
            for source in self.host.list_sources(sec):
                parts = source.split("/")
                if cartridge and (len(parts) < 2 or parts[1] != cartridge):
                    continue
                state = states.get(("bronze_source", source))
                if state is None or _is_stale(state, str(state.get("fingerprint")), now):
                    stale.append({"kind": "bronze_source", "name": source})
        processed = 0
        remaining: list[dict[str, str]] = []
        for index, subject in enumerate(stale):
            key = self._key(sec, subject)
            with self._lock:
                busy = key in self._inflight
            if (
                busy
                or index >= max_items
                or (self.clock() - started) >= budget
            ):
                remaining.append(subject)
                continue
            try:
                outcome = self.profile_and_link(sec, subject)
            except Exception:
                # One broken subject must not hide the rest of the catalog.
                logger.warning(
                    "catalog copilot profile failed kind=%s",
                    subject.get("kind"),
                    exc_info=True,
                )
                continue
            if outcome.get("processed"):
                processed += 1
            elif outcome.get("reason") == "busy":
                remaining.append(subject)
        for subject in remaining:
            self.enqueue(sec, subject)
        pending = len(remaining)
        status = "working" if pending else ("ready" if processed else "idle")
        try:
            epoch = self.store.annotation_epoch(sec)
        except Exception:
            epoch = None
        return AutoCatalogStatus(status, processed, pending, len(stale), epoch)

    # ----------------------------------------------------------------- profile
    def profile_and_link(
        self, sec: dict[str, Any], subject: dict[str, str]
    ) -> dict[str, Any]:
        if not self.enabled():
            return {"processed": False, "reason": "disabled"}
        if not sec.get("trusted") or not sec.get("tenant_id") or not sec.get("workspace_id"):
            return {"processed": False, "reason": "scope"}
        key = self._key(sec, subject)
        with self._lock:
            if key in self._inflight:
                return {"processed": False, "reason": "busy"}
            self._inflight.add(key)
        session = getattr(self.store, "session", None)
        try:
            with session(sec) if callable(session) else nullcontext():
                if subject.get("kind") == "bronze_source":
                    return self._profile_source(sec, str(subject.get("name") or ""))
                return self._profile_dataset(sec, str(subject.get("name") or ""))
        finally:
            with self._lock:
                self._inflight.discard(key)

    def _elapsed_ms(self, started: float) -> int:
        return max(0, int((self.clock() - started) * 1000))

    def _profile_dataset(self, sec: dict[str, Any], name: str) -> dict[str, Any]:
        started = self.clock()
        dataset = self.host.get_dataset(sec, name)
        if not dataset:
            return {"processed": False, "reason": "not_visible"}
        snapshot = (self.host.published_snapshots([dataset], sec) or [None])[0]
        if snapshot is None:
            return {"processed": False, "reason": "unpublished"}
        facts = _snapshot_facts(snapshot)
        fingerprint = subject_fingerprint("dataset", facts)
        state = self.store.load_states(sec, [("dataset", name)]).get(("dataset", name))
        if not _is_stale(state, fingerprint, datetime.now(timezone.utc)):
            return {"processed": False, "reason": "fresh"}

        layer = str(dataset.get("layer") or "silver")
        cartridge = str(dataset.get("cartridge") or "")
        visible_meta = self.host.list_datasets(sec)
        visible = {str(ds.get("name") or "") for ds in visible_meta}
        visible.add(name)
        row_counts = {
            str(ds.get("name") or ""): ds.get("row_count") for ds in visible_meta
        }
        row_count = facts.get("row_count")
        if isinstance(row_count, int):
            row_counts[name] = row_count
        live = self.store.load_catalog_columns(sec, visible)
        own_live = live.get(name, {})
        fields = facts["fields"] or [
            {
                "name": column,
                "type": str(row.get("data_type") or ""),
                "null_rate": row.get("null_rate"),
                "distinct_count": row.get("distinct_count"),
            }
            for column, row in own_live.items()
        ]
        protection = self.store.load_protection(sec, dataset.get("sources") or [])

        status = "ready"
        error_code: str | None = None
        text_cols = [
            str(f["name"])
            for f in fields
            if _is_text(str(f.get("type"))) and not is_scope_column(str(f["name"]))
        ][:MAX_PATTERN_COLUMNS]
        key_cols = self._key_candidates(fields, row_count)
        relation = None
        probe_result = None
        try:
            relation = self.probe.published_relation(dataset, sec)
            probe_result = self.probe.column_probe(
                relation, sec, text_cols=text_cols, key_cols=key_cols, row_count=row_count
            )
        except Exception as exc:
            code = probe_error_code(exc)
            if code is None:
                raise
            status, error_code = "partial", code
        exact_keys = dict(probe_result.exact_keys) if probe_result else {}

        # Profiler distinct counts are HyperLogLog estimates (40 rows can read
        # as 48), so they neither prove nor refute a key. Keys are either
        # confirmed by an exact count or hypothesised from a key-like, null-free
        # column and confirmed exactly before any edge is persisted; children
        # carry no distinct count so value containment alone decides.
        states_all = self.store.load_states(sec)
        own_rows = [
            _candidate_row(
                name,
                str(f["name"]),
                str(f.get("type") or ""),
                row_count,
                f.get("null_rate"),
                confirmed=bool(exact_keys.get(str(f["name"]))),
                refuted=str(f["name"]) in exact_keys and not exact_keys[str(f["name"])],
            )
            for f in fields
            if not is_scope_column(str(f["name"]))
        ]
        other_rows = []
        for dataset_name, columns in live.items():
            if dataset_name == name or dataset_name not in visible:
                continue
            summary = (states_all.get(("dataset", dataset_name)) or {}).get("summary") or {}
            confirmed_keys = set(summary.get("exact_keys") or [])
            for column, row in columns.items():
                if is_scope_column(column):
                    continue
                other_rows.append(
                    _candidate_row(
                        dataset_name,
                        column,
                        str(row.get("data_type") or ""),
                        row_counts.get(dataset_name),
                        row.get("null_rate"),
                        confirmed=column in confirmed_keys,
                        refuted=False,
                    )
                )
        confirmed_other = {
            (dataset_name, column)
            for dataset_name, state in (
                (key[1], value) for key, value in states_all.items() if key[0] == "dataset"
            )
            for column in ((state.get("summary") or {}).get("exact_keys") or [])
        }
        edges: list[dict[str, Any]] = []
        if relation is not None and status == "ready":
            try:
                edges = self._link(
                    sec,
                    name,
                    relation,
                    own_rows,
                    other_rows,
                    row_counts,
                    exact_keys,
                    live,
                    visible_meta,
                    confirmed_other,
                )
            except Exception as exc:
                code = probe_error_code(exc)
                if code is None:
                    raise
                status, error_code = "partial", code

        links_to: dict[str, str] = {}
        display_by_name = {
            str(ds.get("name") or ""): dataset_display_name(
                str(ds.get("name") or ""), str(ds.get("cartridge") or "")
            )
            for ds in visible_meta
        }
        for edge in edges:
            if edge["from_dataset"] == name:
                links_to[edge["from_column"]] = display_by_name.get(
                    edge["to_dataset"], edge["to_dataset"]
                )

        annotations: list[dict[str, Any]] = []
        type_counts: dict[str, int] = {}
        pii_columns: list[str] = []
        pii_kinds: list[str] = []
        financial_columns: list[str] = []
        for f in fields:
            column = str(f["name"])
            row = own_live.get(column) or {}
            exact = bool(exact_keys.get(column))
            semantic = semantic_type(
                str(f.get("type") or ""), column, is_key=exact or bool(row.get("is_key"))
            )
            type_counts[semantic] = type_counts.get(semantic, 0) + 1
            hits = (probe_result.pattern_hits.get(column) if probe_result else None) or {}
            sampled = (probe_result.non_null.get(column, 0) if probe_result else 0) or 0
            classification = classify_column(
                column,
                semantic,
                declared_protection=protection.get(normalize_name(column)),
                pattern_hits=hits,
                sampled_non_null=sampled,
            )
            classes = classification.classifications
            if "pii" in classes:
                pii_columns.append(column)
                if classification.pii_kind:
                    pii_kinds.append(classification.pii_kind)
            if "financial" in classes:
                financial_columns.append(column)
            evidence = dict(classification.evidence) if classes else {}
            basis = list(evidence.get("basis") or [])
            key_note = _key_evidence(
                {"distinct_count": f.get("distinct_count"), "null_rate": f.get("null_rate")},
                row_count if isinstance(row_count, int) else None,
            )
            if exact:
                basis.append("key:exact")
            elif key_note == "approximate" and semantic == "identifier":
                basis.append("key:approximate")
            if basis:
                evidence["basis"] = list(dict.fromkeys(basis))
            description = column_description(
                {
                    "column": column,
                    "semantic_type": semantic,
                    "classifications": classes,
                    "pii_kind": classification.pii_kind,
                    "is_key": exact,
                    "links_to": links_to.get(column),
                    "null_rate": f.get("null_rate"),
                    "distinct_count": f.get("distinct_count"),
                    "row_count": row_count,
                    "min_value": f.get("min_value"),
                    "max_value": f.get("max_value"),
                }
            )
            annotations.append(
                {
                    "column": column,
                    "data_type": str(f.get("type") or ""),
                    "semantic_type": semantic,
                    "description": description,
                    "classifications": list(classes),
                    "classification_origin": classification.origin,
                    "confidence": classification.confidence,
                    "evidence": evidence,
                    "is_key": exact,
                }
            )
        self.store.upsert_column_annotations(
            sec, dataset=name, layer=layer, cartridge=cartridge, annotations=annotations
        )
        if edges:
            self.store.upsert_copilot_edges(sec, edges)
        if status == "ready":
            self.store.retire_copilot_edges(
                sec, dataset=name, keep={edge_key(edge) for edge in edges}, visible=visible
            )
        active = [
            row
            for row in self.store.load_edges(sec, [name])
            if row.get("status") == "active"
            and str(row.get("from_dataset")) in visible
            and str(row.get("to_dataset")) in visible
        ]
        related = []
        for row in active:
            other = row["to_dataset"] if row["from_dataset"] == name else row["from_dataset"]
            if other != name:
                related.append(display_by_name.get(str(other), str(other)))
        display_name = dataset_display_name(name, cartridge)
        published_at = facts.get("published_at")
        description = dataset_description(
            {
                "display_name": display_name,
                "cartridge": cartridge,
                "row_count": row_count if isinstance(row_count, int) else None,
                "last_refresh": published_at,
                "type_counts": type_counts,
                "pii_columns": pii_columns,
                "pii_kinds": pii_kinds,
                "financial_columns": financial_columns,
                "related": related,
            }
        )
        self.store.save_state(
            sec,
            {
                "subject_kind": "dataset",
                "subject": name,
                "layer": layer,
                "cartridge": cartridge,
                "fingerprint": fingerprint,
                "rules_version": RULES_VERSION,
                "status": status,
                "display_name": display_name,
                "description": description,
                "summary": {
                    "columns": len(fields),
                    "rows": row_count if isinstance(row_count, int) else None,
                    "type_counts": type_counts,
                    "pii_columns": pii_columns,
                    "financial_columns": financial_columns,
                    "exact_keys": sorted(c for c, ok in exact_keys.items() if ok),
                    "relations": len(active),
                    "copilot_relations": sum(
                        1 for row in active if row.get("origin") == "copilot"
                    ),
                    "probe": {
                        "sampled_rows": probe_result.sampled_rows if probe_result else 0,
                        "pattern_columns": len(text_cols),
                        "keys_checked": bool(probe_result and probe_result.keys_checked),
                    },
                },
                "error_code": error_code,
                "duration_ms": self._elapsed_ms(started),
            },
        )
        return {
            "processed": True,
            "status": status,
            "edges": len(edges),
            "duration_ms": self._elapsed_ms(started),
        }

    @staticmethod
    def _key_candidates(fields: list[dict[str, Any]], row_count: Any) -> list[str]:
        ranked: list[tuple[int, str]] = []
        for f in fields:
            column = str(f["name"])
            if is_scope_column(column):
                continue
            null_rate = f.get("null_rate")
            if null_rate not in (None, 0, 0.0):
                continue
            evidence = _key_evidence(
                {"distinct_count": f.get("distinct_count"), "null_rate": null_rate or 0.0},
                row_count if isinstance(row_count, int) else None,
            )
            key_like = semantic_type(str(f.get("type") or ""), column) == "identifier"
            if key_like and evidence is not None:
                ranked.append((0, column))
            elif key_like:
                ranked.append((1, column))
            elif evidence is not None:
                ranked.append((2, column))
        ranked.sort()
        return [column for _rank, column in ranked][:MAX_KEY_CANDIDATES]

    def _link(
        self,
        sec: dict[str, Any],
        name: str,
        relation: Any,
        own_rows: list[dict[str, Any]],
        other_rows: list[dict[str, Any]],
        row_counts: dict[str, Any],
        exact_keys: dict[str, bool],
        live: dict[str, dict[str, dict[str, Any]]],
        visible_meta: list[dict[str, Any]],
        confirmed_other: set[tuple[str, str]] | None = None,
    ) -> list[dict[str, Any]]:
        candidates = [
            candidate
            for candidate in discover_relationship_candidates(
                own_rows + other_rows, row_counts
            )
            if name in {candidate["from_dataset"], candidate["to_dataset"]}
            and not is_scope_column(candidate["from_column"])
        ]
        candidates = candidates[:MAX_CONTAINMENT_CANDIDATES]
        relations: dict[str, Any] = {name: relation}
        other_exact: dict[tuple[str, str], bool] = {
            key: True for key in (confirmed_other or set())
        }
        by_name = {str(ds.get("name") or ""): ds for ds in visible_meta}
        edges: dict[tuple[str, str, str, str], dict[str, Any]] = {}
        for candidate in candidates:
            from_ds, from_col = candidate["from_dataset"], candidate["from_column"]
            to_ds, to_col = candidate["to_dataset"], candidate["to_column"]
            for other in (from_ds, to_ds):
                if other in relations:
                    continue
                meta = self.host.get_dataset(sec, other) or by_name.get(other)
                if not meta:
                    relations[other] = None
                    continue
                try:
                    relations[other] = self.probe.published_relation(meta, sec)
                except Exception as exc:
                    if probe_error_code(exc) is None:
                        raise
                    relations[other] = None
            child, parent = relations.get(from_ds), relations.get(to_ds)
            if child is None or parent is None:
                continue
            if to_ds == name:
                target_checked = to_col in exact_keys
                target_exact = bool(exact_keys.get(to_col))
            else:
                cache_key = (to_ds, to_col)
                if cache_key not in other_exact:
                    rows = row_counts.get(to_ds)
                    if isinstance(rows, int) and 0 < rows <= EXACT_KEY_MAX_ROWS:
                        result = self.probe.column_probe(
                            parent, sec, text_cols=[], key_cols=[to_col], row_count=rows
                        )
                        if result.keys_checked:
                            other_exact[cache_key] = bool(result.exact_keys.get(to_col))
                target_checked = cache_key in other_exact
                target_exact = bool(other_exact.get(cache_key))
            if target_checked and not target_exact:
                # An exact count proved the target is not unique: not a PK.
                continue
            from_unique = from_ds == name and bool(exact_keys.get(from_col))
            containment = self.probe.containment(child, from_col, parent, to_col, sec)
            ratio = containment.ratio
            from_type = _column_type(own_rows, live, from_ds, from_col)
            to_type = _column_type(own_rows, live, to_ds, to_col)
            from_class = key_class(from_col)
            to_class = key_class(to_col)
            match = {
                "name_exact": normalize_name(from_col) == normalize_name(to_col),
                "same_class": bool(
                    from_class and to_class and from_class[0] == to_class[0]
                ),
                "types_compatible": bool(from_type)
                and _normalize_type(from_type) == _normalize_type(to_type),
                "containment": ratio,
                "target_key_exact": target_exact,
            }
            confidence = relationship_confidence(match)
            if confidence is None:
                continue
            shape = cardinality(from_unique, True)
            if shape not in {"N:1", "1:1"}:
                continue
            key = (from_ds, from_col, to_ds, to_col)
            reverse = (to_ds, to_col, from_ds, from_col)
            if shape == "1:1" and reverse in edges:
                if reverse < key:
                    continue
                edges.pop(reverse, None)
            codes = ["name:exact" if match["name_exact"] else "name:class"]
            if match["types_compatible"]:
                codes.append("types:compatible")
            codes.append(f"containment:{ratio:.2f}")
            codes.append("key:exact" if target_exact else "key:approximate")
            child_label = dataset_display_name(from_ds, str((by_name.get(from_ds) or {}).get("cartridge") or ""))
            parent_label = dataset_display_name(to_ds, str((by_name.get(to_ds) or {}).get("cartridge") or ""))
            verb = "corresponde a un" if shape == "1:1" else "se vincula con un"
            edges[key] = {
                "from_dataset": from_ds,
                "from_column": from_col,
                "to_dataset": to_ds,
                "to_column": to_col,
                "cardinality": shape,
                "confidence": confidence,
                "rules_version": RULES_VERSION,
                "description": (
                    f"Cada registro de {child_label} {verb} registro de {parent_label} "
                    f"por {from_col}. Relación detectada por Copiloto."
                ),
                "basis": {
                    "codes": codes,
                    "containment": ratio,
                    "child_distinct": containment.child_distinct,
                    "orphan_values": containment.orphan_values,
                    "containment_sampled": containment.sampled,
                },
            }
        return list(edges.values())

    # ----------------------------------------------------------------- sources
    def _profile_source(self, sec: dict[str, Any], source: str) -> dict[str, Any]:
        started = self.clock()
        if not source or not self.host.source_allowed(sec, source):
            return {"processed": False, "reason": "not_visible"}
        parts = source.split("/")
        cartridge = parts[1] if len(parts) >= 3 else ""
        protection = self.store.load_protection(sec, [source])

        def allow_range(column: str, data_type: str) -> bool:
            semantic = semantic_type(data_type, column)
            return semantic in {"date", "datetime"} and not name_hints(column).any and (
                normalize_name(column) not in protection
                or protection[normalize_name(column)] == "plain"
            )

        try:
            footer = self.probe.bronze_footer(source, sec, allow_range=allow_range)
        except Exception as exc:
            code = probe_error_code(exc)
            if code is None:
                raise
            return {"processed": False, "reason": code}
        fingerprint = subject_fingerprint(
            "bronze_source",
            {"load_date": footer.load_date, "num_rows": footer.num_rows, "fields": footer.fields},
        )
        state = self.store.load_states(sec, [("bronze_source", source)]).get(
            ("bronze_source", source)
        )
        if not _is_stale(state, fingerprint, datetime.now(timezone.utc)):
            return {"processed": False, "reason": "fresh"}
        columns: list[dict[str, Any]] = []
        type_counts: dict[str, int] = {}
        pii_columns: list[str] = []
        pii_kinds: list[str] = []
        financial_columns: list[str] = []
        for f in footer.fields[:MAX_SOURCE_COLUMNS]:
            column, data_type = str(f["name"]), str(f["type"])
            semantic = semantic_type(data_type, column)
            type_counts[semantic] = type_counts.get(semantic, 0) + 1
            classification = classify_column(
                column, semantic, declared_protection=protection.get(normalize_name(column))
            )
            classes = classification.classifications
            if "pii" in classes:
                pii_columns.append(column)
                if classification.pii_kind:
                    pii_kinds.append(classification.pii_kind)
            if "financial" in classes:
                financial_columns.append(column)
            num_values = int(f.get("num_values") or 0)
            nulls = f.get("null_count")
            null_rate = (
                round(nulls / num_values, 6)
                if isinstance(nulls, int) and num_values > 0
                else None
            )
            low, high = f.get("range") or (None, None)
            columns.append(
                {
                    "name": column,
                    "type": data_type,
                    "semantic_type": semantic,
                    "classifications": list(classes),
                    "classification_origin": classification.origin,
                    "confidence": classification.confidence,
                    "basis": list(classification.basis),
                    "null_rate": null_rate,
                    "description": column_description(
                        {
                            "column": column,
                            "semantic_type": semantic,
                            "classifications": classes,
                            "pii_kind": classification.pii_kind,
                            "null_rate": null_rate,
                            "min_value": low,
                            "max_value": high,
                        }
                    ),
                }
            )
        display_name = source_display_name(source)
        description = dataset_description(
            {
                "display_name": display_name,
                "cartridge": cartridge,
                "row_count": footer.num_rows,
                "last_refresh": footer.load_date,
                "type_counts": type_counts,
                "pii_columns": pii_columns,
                "pii_kinds": pii_kinds,
                "financial_columns": financial_columns,
                "related": [],
            }
        )
        if footer.truncated:
            description = description[:520] + " Perfil parcial: se leyeron 50 archivos."
        self.store.save_state(
            sec,
            {
                "subject_kind": "bronze_source",
                "subject": source,
                "layer": "bronze",
                "cartridge": cartridge,
                "fingerprint": fingerprint,
                "rules_version": RULES_VERSION,
                "status": "partial" if footer.truncated else "ready",
                "display_name": display_name,
                "description": description,
                "summary": {
                    "columns": columns,
                    "rows": footer.num_rows,
                    "files": footer.files,
                    "truncated": footer.truncated,
                    "load_date": footer.load_date,
                    "type_counts": type_counts,
                    "pii_columns": pii_columns,
                    "financial_columns": financial_columns,
                    "relations": 0,
                },
                "error_code": None,
                "duration_ms": self._elapsed_ms(started),
            },
        )
        return {"processed": True, "status": "ready", "edges": 0}


def _candidate_row(
    dataset: str,
    column: str,
    data_type: str,
    rows: Any,
    null_rate: Any,
    *,
    confirmed: bool,
    refuted: bool,
) -> dict[str, Any]:
    key_like = semantic_type(data_type, column) == "identifier"
    hypothesis = (
        not refuted
        and key_like
        and null_rate in (0, 0.0)
        and isinstance(rows, int)
        and rows > 0
    )
    is_key = confirmed or hypothesis
    return {
        "dataset": dataset,
        "column_name": column,
        "data_type": data_type,
        "distinct_count": rows if is_key and isinstance(rows, int) else None,
        "null_rate": 0.0 if is_key else null_rate,
    }


def _column_type(
    own_rows: list[dict[str, Any]],
    live: dict[str, dict[str, dict[str, Any]]],
    dataset: str,
    column: str,
) -> str:
    for row in own_rows:
        if row["dataset"] == dataset and row["column_name"] == column:
            return str(row.get("data_type") or "")
    return str(((live.get(dataset) or {}).get(column) or {}).get("data_type") or "")


__all__ = [
    "AutoCatalogStatus",
    "AutonomousCatalogWorker",
    "CatalogCopilotHost",
    "copilot_enabled",
    "subject_fingerprint",
]
