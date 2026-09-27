"""Autonomous Catalog Copilot: profile and link published datasets.

Deterministic pipeline, no LLM:
  published snapshot -> fingerprint -> bounded DuckDB probe (counts only)
  -> rules (semantic type, sensitivity, executive Spanish text)
  -> FK -> PK candidates confirmed by value containment
  -> SQL-guarded writes (manual always wins) -> state row.

All profiling happens on one daemon thread, never in a request: a
publication or an extraction enqueues its subject, and opening the catalog
(catch_up) only compares fingerprints with stored state and enqueues what is
stale, so no user request ever waits for a probe. Each profile runs under a
time budget that also clamps every DuckDB query. The in-memory queue is lost
on restart by design; catch_up recovers anything left behind.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import queue
import threading
import time
from contextlib import nullcontext
from dataclasses import dataclass, field
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
MAX_CONTAINMENT_CANDIDATES = 10
RETRY_AFTER_SECONDS = 600
SOURCE_SCAN_INTERVAL_SECONDS = 600
MAX_SOURCE_COLUMNS = 200
SUBJECT_BUDGET_SECONDS = 30.0
SUMMARY_MAX_BYTES = 60_000
MAX_SUMMARY_NAMES = 200
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


@dataclass
class LinkResult:
    edges: list[dict[str, Any]] = field(default_factory=list)
    # Candidates whose containment (or target key) was checked in this run.
    evaluated: set[tuple[str, str, str, str]] = field(default_factory=set)
    # Every edge the current rules propose for the dataset, before the cap.
    proposed: set[tuple[str, str, str, str]] = field(default_factory=set)
    errors: list[str] = field(default_factory=list)


class AutonomousCatalogWorker:
    def __init__(
        self,
        host: CatalogCopilotHost,
        store: Any,
        probe: CatalogCopilotProbe,
        *,
        clock: Callable[[], float] = time.monotonic,
        enabled: Callable[[], bool] = copilot_enabled,
        subject_budget: float = SUBJECT_BUDGET_SECONDS,
    ) -> None:
        self.host = host
        self.store = store
        self.probe = probe
        self.clock = clock
        self.enabled = enabled
        self.subject_budget = subject_budget
        self._queue: queue.Queue = queue.Queue(maxsize=QUEUE_MAX)
        self._pending: set[tuple[str, str, str]] = set()
        self._inflight: set[tuple[str, str, str]] = set()
        self._last_scan: dict[tuple[str, str], float] = {}
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
        # A plain daemon thread: it never holds the service's request
        # admission gate, only the DuckDB lock one bounded query at a time.
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

    def _busy_keys(self, workspace_id: str) -> set[tuple[str, str, str]]:
        with self._lock:
            return {
                key
                for key in self._pending | self._inflight
                if key[0] == workspace_id and key[1] != "source_scan"
            }

    # ---------------------------------------------------------------- catch-up
    def catch_up(
        self,
        sec: dict[str, Any],
        *,
        cartridge: str | None = None,
        include_sources: bool = False,
    ) -> AutoCatalogStatus:
        """Enqueue what is stale and report; never profiles in the caller."""
        if not self.enabled():
            return AutoCatalogStatus("idle", 0, 0, 0, None)
        if not sec.get("trusted") or not sec.get("tenant_id") or not sec.get("workspace_id"):
            return AutoCatalogStatus("idle", 0, 0, 0, None)
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
            for (kind, subject), state in sorted(states.items()):
                if kind != "bronze_source":
                    continue
                if cartridge and str(state.get("cartridge") or "") != cartridge:
                    continue
                if _is_stale(state, str(state.get("fingerprint") or ""), now):
                    stale.append({"kind": "bronze_source", "name": subject})
            self._schedule_source_scan(sec, cartridge or "")
        for subject in stale:
            self.enqueue(sec, subject)
        workspace_id = str(sec.get("workspace_id") or "")
        pending = self._busy_keys(workspace_id) | {self._key(sec, subject) for subject in stale}
        try:
            epoch = self.store.annotation_epoch(sec)
        except Exception:
            epoch = None
        return AutoCatalogStatus(
            "working" if pending else "idle", 0, len(pending), len(stale), epoch
        )

    def _schedule_source_scan(self, sec: dict[str, Any], cartridge: str) -> None:
        key = (str(sec.get("workspace_id") or ""), cartridge)
        now = self.clock()
        with self._lock:
            last = self._last_scan.get(key)
            if last is not None and now - last < SOURCE_SCAN_INTERVAL_SECONDS:
                return
            self._last_scan[key] = now
        self.enqueue(sec, {"kind": "source_scan", "name": cartridge})

    def _scan_sources(self, sec: dict[str, Any], cartridge: str) -> dict[str, Any]:
        states = self.store.load_states(sec)
        now = datetime.now(timezone.utc)
        queued = 0
        for source in sorted(self.host.list_sources(sec)):
            parts = source.split("/")
            if cartridge and (len(parts) < 2 or parts[1] != cartridge):
                continue
            state = states.get(("bronze_source", source))
            if state is None or _is_stale(state, str(state.get("fingerprint") or ""), now):
                if self.enqueue(sec, {"kind": "bronze_source", "name": source}):
                    queued += 1
        return {"processed": True, "queued": queued}

    # ----------------------------------------------------------------- profile
    def profile_and_link(
        self,
        sec: dict[str, Any],
        subject: dict[str, str],
        *,
        deadline: float | None = None,
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
        if deadline is None:
            deadline = time.monotonic() + self.subject_budget
        session = getattr(self.store, "session", None)
        try:
            with session(sec) if callable(session) else nullcontext():
                kind = subject.get("kind")
                name = str(subject.get("name") or "")
                if kind == "source_scan":
                    return self._scan_sources(sec, name)
                if kind == "bronze_source":
                    return self._profile_source(sec, name, deadline)
                return self._profile_dataset(sec, name, deadline)
        finally:
            with self._lock:
                self._inflight.discard(key)

    def _elapsed_ms(self, started: float) -> int:
        return max(0, int((self.clock() - started) * 1000))

    def _fresh_knowledge(
        self,
        sec: dict[str, Any],
        visible_meta: list[dict[str, Any]],
        states: dict[tuple[str, str], dict[str, Any]],
    ) -> dict[tuple[str, str], bool]:
        """Exact key checks recorded by earlier profiles of unchanged datasets."""
        knowledge: dict[tuple[str, str], bool] = {}
        snapshots = self.host.published_snapshots(visible_meta, sec) if visible_meta else []
        for ds, snapshot in zip(visible_meta, snapshots, strict=True):
            name = str(ds.get("name") or "")
            state = states.get(("dataset", name))
            if snapshot is None or not state:
                continue
            if state.get("fingerprint") != subject_fingerprint(
                "dataset", _snapshot_facts(snapshot)
            ):
                continue
            summary = state.get("summary") if isinstance(state.get("summary"), dict) else {}
            for column in summary.get("exact_keys") or []:
                knowledge[(name, str(column))] = True
            for column in summary.get("refuted_keys") or []:
                knowledge[(name, str(column))] = False
        return knowledge

    def _profile_dataset(
        self, sec: dict[str, Any], name: str, deadline: float
    ) -> dict[str, Any]:
        started = self.clock()
        dataset = self.host.get_dataset(sec, name)
        if not dataset:
            return {"processed": False, "reason": "not_visible"}
        snapshot = (self.host.published_snapshots([dataset], sec) or [None])[0]
        if snapshot is None:
            return {"processed": False, "reason": "unpublished"}
        facts = _snapshot_facts(snapshot)
        fingerprint = subject_fingerprint("dataset", facts)
        states = self.store.load_states(sec)
        if not _is_stale(states.get(("dataset", name)), fingerprint, datetime.now(timezone.utc)):
            return {"processed": False, "reason": "fresh"}

        layer = str(dataset.get("layer") or "silver")
        cartridge = str(dataset.get("cartridge") or "")
        visible_meta = [ds for ds in self.host.list_datasets(sec) if ds.get("name")]
        visible = {str(ds["name"]) for ds in visible_meta} | {name}
        row_counts: dict[str, Any] = {
            str(ds["name"]): ds.get("row_count") for ds in visible_meta
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
        own_columns = {str(f["name"]) for f in fields}
        protection = self.store.load_protection(sec, dataset.get("sources") or [])
        knowledge = self._fresh_knowledge(
            sec, [ds for ds in visible_meta if str(ds["name"]) != name], states
        )

        errors: list[str] = []
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
                relation,
                sec,
                text_cols=text_cols,
                key_cols=key_cols,
                row_count=row_count,
                deadline=deadline,
            )
        except Exception as exc:
            code = probe_error_code(exc)
            if code is None:
                raise
            errors.append(code)
        exact_keys = dict(probe_result.exact_keys) if probe_result else {}
        for column, unique in exact_keys.items():
            knowledge[(name, column)] = unique

        # Profiler distinct counts are HyperLogLog estimates (40 rows can read
        # as 48), so they neither prove nor refute a key. A key is known from
        # an exact count (this run or a fresh earlier profile) or hypothesised
        # from a key-like, null-free column and confirmed exactly before any
        # edge is saved; value containment decides every candidate.
        rows: list[dict[str, Any]] = []
        for dataset_name in sorted(visible):
            if dataset_name == name:
                source_columns = [
                    (str(f["name"]), str(f.get("type") or ""), f.get("null_rate"))
                    for f in fields
                ]
            else:
                source_columns = [
                    (column, str(row.get("data_type") or ""), row.get("null_rate"))
                    for column, row in live.get(dataset_name, {}).items()
                ]
            for column, data_type, null_rate in source_columns:
                if is_scope_column(column):
                    continue
                rows.append(
                    _candidate_row(
                        dataset_name,
                        column,
                        data_type,
                        row_counts.get(dataset_name),
                        null_rate,
                        known=knowledge.get((dataset_name, column)),
                    )
                )
        link = LinkResult()
        if relation is not None:
            link = self._link(
                sec,
                name,
                relation,
                rows,
                row_counts,
                knowledge,
                live,
                visible_meta,
                deadline,
            )
            errors.extend(link.errors)

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
            elif key_note == "approximate" and semantic == "identifier" and column not in exact_keys:
                basis.append("key:approximate")
            if basis:
                evidence["basis"] = list(dict.fromkeys(basis))
            # Links to other tables are rendered per viewer by the overlay, so
            # stored text never names a dataset the reader may not see.
            description = column_description(
                {
                    "column": column,
                    "semantic_type": semantic,
                    "classifications": classes,
                    "pii_kind": classification.pii_kind,
                    "is_key": exact,
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
                }
            )
        try:
            written = self.store.upsert_column_annotations(
                sec, dataset=name, layer=layer, cartridge=cartridge, annotations=annotations
            )
            if getattr(written, "failed", 0):
                errors.append("annotation_write_failed")
        except Exception:
            logger.warning("catalog copilot annotations not written", exc_info=True)
            errors.append("annotation_write_failed")
        kept = {edge_key(edge) for edge in link.edges}
        if link.edges:
            try:
                result = self.store.upsert_copilot_edges(sec, link.edges)
                if getattr(result, "failed", 0):
                    errors.append("edge_write_failed")
            except Exception:
                logger.warning("catalog copilot edges not written", exc_info=True)
                errors.append("edge_write_failed")
        # Retire only what this run can vouch for: edges it re-checked and
        # dropped, edges the current rules no longer propose at all, and edges
        # whose column here vanished or whose key here was refuted. An edge
        # beyond the candidate cap, or towards a dataset this reader cannot
        # see, is left untouched.
        retire = set(link.evaluated) - kept
        try:
            for row in self.store.load_edges(sec, [name]):
                if row.get("origin") != "copilot" or row.get("status") != "active":
                    continue
                key = edge_key(row)
                if key[0] not in visible or key[2] not in visible:
                    continue
                reverse = (key[2], key[3], key[0], key[1])
                if key[0] == name and key[1] not in own_columns:
                    retire.add(key)
                elif key[2] == name and (
                    key[3] not in own_columns or knowledge.get((name, key[3])) is False
                ):
                    retire.add(key)
                elif (
                    relation is not None
                    and key not in link.proposed
                    and reverse not in link.proposed
                    and key not in kept
                ):
                    retire.add(key)
            self.store.retire_copilot_edges(sec, retire - kept)
        except Exception:
            logger.warning("catalog copilot edges not retired", exc_info=True)
            errors.append("retire_failed")

        errors = list(dict.fromkeys(errors))
        display_name = dataset_display_name(name, cartridge)
        description = dataset_description(
            {
                "display_name": display_name,
                "cartridge": cartridge,
                "row_count": row_count if isinstance(row_count, int) else None,
                "last_refresh": facts.get("published_at"),
                "type_counts": type_counts,
                "pii_columns": pii_columns,
                "pii_kinds": pii_kinds,
                "financial_columns": financial_columns,
            }
        )
        status = "partial" if errors else "ready"
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
                    "pii_columns": pii_columns[:MAX_SUMMARY_NAMES],
                    "financial_columns": financial_columns[:MAX_SUMMARY_NAMES],
                    "exact_keys": sorted(c for c, ok in exact_keys.items() if ok),
                    "refuted_keys": sorted(c for c, ok in exact_keys.items() if not ok),
                    "copilot_relations": len(link.edges),
                    "probe": {
                        "sampled_rows": probe_result.sampled_rows if probe_result else 0,
                        "pattern_columns": len(text_cols),
                        "keys_checked": bool(probe_result and probe_result.keys_checked),
                    },
                },
                "error_code": errors[0] if errors else None,
                "duration_ms": self._elapsed_ms(started),
            },
        )
        return {
            "processed": True,
            "status": status,
            "edges": len(link.edges),
            "errors": errors,
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

    def _exact(
        self,
        sec: dict[str, Any],
        dataset: str,
        column: str,
        relation: Any,
        knowledge: dict[tuple[str, str], bool],
        row_counts: dict[str, Any],
        deadline: float,
    ) -> bool | None:
        """Whether a column is unique and null-free; None when it cannot be checked."""
        known = knowledge.get((dataset, column))
        if known is not None:
            return known
        rows = row_counts.get(dataset)
        if not isinstance(rows, int) or rows <= 0 or rows > EXACT_KEY_MAX_ROWS:
            return None
        result = self.probe.column_probe(
            relation, sec, text_cols=[], key_cols=[column], row_count=rows, deadline=deadline
        )
        if not result.keys_checked:
            return None
        unique = bool(result.exact_keys.get(column))
        knowledge[(dataset, column)] = unique
        return unique

    def _link(
        self,
        sec: dict[str, Any],
        name: str,
        relation: Any,
        rows: list[dict[str, Any]],
        row_counts: dict[str, Any],
        knowledge: dict[tuple[str, str], bool],
        live: dict[str, dict[str, dict[str, Any]]],
        visible_meta: list[dict[str, Any]],
        deadline: float,
    ) -> LinkResult:
        result = LinkResult()
        by_name = {str(ds.get("name") or ""): ds for ds in visible_meta}
        hypotheses = {
            (row["dataset"], row["column_name"])
            for row in rows
            if row.get("distinct_count") is not None
        }

        def snapshot_unique(dataset: str, column: str) -> bool:
            stats = (live.get(dataset) or {}).get(column) or {}
            rows_known = row_counts.get(dataset)
            return (
                isinstance(rows_known, int)
                and stats.get("distinct_count") == rows_known
                and stats.get("null_rate") in (0, 0.0)
            )

        def rank(candidate: dict[str, Any]) -> tuple:
            target = (candidate["to_dataset"], candidate["to_column"])
            tier = 0 if knowledge.get(target) else (1 if snapshot_unique(*target) else 2)
            return (
                tier,
                0 if candidate["match"] == "name" else 1,
                -float(candidate["confidence"]),
                candidate["from_dataset"],
                candidate["from_column"],
                candidate["to_dataset"],
                candidate["to_column"],
            )

        proposed = sorted(
            (
                candidate
                for candidate in discover_relationship_candidates(
                    rows, row_counts, prune_by_distinct=False
                )
                if name in {candidate["from_dataset"], candidate["to_dataset"]}
                and not is_scope_column(candidate["from_column"])
                and knowledge.get((candidate["to_dataset"], candidate["to_column"]))
                is not False
            ),
            key=rank,
        )
        result.proposed = {
            (c["from_dataset"], c["from_column"], c["to_dataset"], c["to_column"])
            for c in proposed
        }
        candidates = proposed[:MAX_CONTAINMENT_CANDIDATES]

        relations: dict[str, Any] = {name: relation}

        def relation_of(dataset: str) -> Any:
            if dataset not in relations:
                meta = self.host.get_dataset(sec, dataset)
                try:
                    relations[dataset] = (
                        self.probe.published_relation(meta, sec) if meta else None
                    )
                except Exception as exc:
                    if probe_error_code(exc) is None:
                        raise
                    relations[dataset] = None
            return relations[dataset]

        edges: dict[tuple[str, str, str, str], dict[str, Any]] = {}
        for candidate in candidates:
            from_ds, from_col = candidate["from_dataset"], candidate["from_column"]
            to_ds, to_col = candidate["to_dataset"], candidate["to_column"]
            key = (from_ds, from_col, to_ds, to_col)
            reverse = (to_ds, to_col, from_ds, from_col)
            if key in edges or reverse in edges:
                continue
            try:
                child, parent = relation_of(from_ds), relation_of(to_ds)
                if child is None or parent is None:
                    continue
                target_exact = self._exact(
                    sec, to_ds, to_col, parent, knowledge, row_counts, deadline
                )
                if target_exact is False:
                    # An exact count proved the target is not unique: not a PK.
                    result.evaluated.add(key)
                    continue
                containment = self.probe.containment(
                    child, from_col, parent, to_col, sec, deadline=deadline
                )
                result.evaluated.add(key)
                confidence = relationship_confidence(
                    _match(rows, live, key, containment.ratio, target_exact is True)
                )
                if confidence is None:
                    continue
                from_unique = False
                if (from_ds, from_col) in hypotheses:
                    from_unique = bool(
                        self._exact(
                            sec, from_ds, from_col, child, knowledge, row_counts, deadline
                        )
                    )
                if from_unique:
                    # 1:1 is stored once, in one canonical orientation.
                    result.evaluated.add(reverse)
                    if (from_ds, from_col) > (to_ds, to_col):
                        containment = self.probe.containment(
                            parent, to_col, child, from_col, sec, deadline=deadline
                        )
                        confidence = relationship_confidence(
                            _match(rows, live, reverse, containment.ratio, True)
                        )
                        target_exact = True
                        if confidence is None:
                            continue
                        key = reverse
                    shape = cardinality(True, True)
                else:
                    shape = cardinality(False, True)
            except Exception as exc:
                code = probe_error_code(exc)
                if code is None:
                    raise
                result.errors.append(code)
                continue
            edges[key] = self._edge(
                key, shape, confidence, containment, target_exact is True, rows, live, by_name
            )
        result.edges = list(edges.values())
        return result

    @staticmethod
    def _edge(
        key: tuple[str, str, str, str],
        shape: str,
        confidence: float,
        containment: Any,
        target_exact: bool,
        rows: list[dict[str, Any]],
        live: dict[str, dict[str, dict[str, Any]]],
        by_name: dict[str, dict[str, Any]],
    ) -> dict[str, Any]:
        from_ds, from_col, to_ds, to_col = key
        match = _match(rows, live, key, containment.ratio, target_exact)
        ratio = containment.ratio or 0.0
        codes = ["name:exact" if match["name_exact"] else "name:class"]
        if match["types_compatible"]:
            codes.append("types:compatible")
        codes.append(f"containment:{ratio:.2f}")
        codes.append("key:exact" if target_exact else "key:approximate")
        child_label = dataset_display_name(
            from_ds, str((by_name.get(from_ds) or {}).get("cartridge") or "")
        )
        parent_label = dataset_display_name(
            to_ds, str((by_name.get(to_ds) or {}).get("cartridge") or "")
        )
        verb = "corresponde a un" if shape == "1:1" else "se vincula con un"
        return {
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

    # ----------------------------------------------------------------- sources
    def _profile_source(
        self, sec: dict[str, Any], source: str, deadline: float
    ) -> dict[str, Any]:
        started = self.clock()
        if not source or not self.host.source_allowed(sec, source):
            return {"processed": False, "reason": "not_visible"}
        parts = source.split("/")
        cartridge = parts[1] if len(parts) >= 3 else ""
        protection = self.store.load_protection(sec, [source])
        try:
            footer = self.probe.bronze_footer(source, sec, deadline=deadline)
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
            # Compact facts only: descriptions are rendered at read time, so a
            # wide source still fits the state row.
            item: dict[str, Any] = {
                "name": column,
                "type": data_type[:64],
                "semantic_type": semantic,
                "null_rate": null_rate,
            }
            if classes:
                item.update(
                    classifications=list(classes),
                    classification_origin=classification.origin,
                    confidence=classification.confidence,
                    basis=list(classification.basis)[:6],
                    pii_kind=classification.pii_kind,
                )
            columns.append(item)
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
            }
        )
        if footer.truncated:
            description = description[:520] + " Perfil parcial: se leyeron 50 archivos."
        summary, trimmed = _fit_summary(
            {
                "columns": columns,
                "rows": footer.num_rows,
                "files": footer.files,
                "truncated": footer.truncated,
                "load_date": footer.load_date,
                "type_counts": type_counts,
                "pii_columns": pii_columns[:MAX_SUMMARY_NAMES],
                "financial_columns": financial_columns[:MAX_SUMMARY_NAMES],
            }
        )
        status = "partial" if footer.truncated or trimmed else "ready"
        state = {
            "subject_kind": "bronze_source",
            "subject": source,
            "layer": "bronze",
            "cartridge": cartridge,
            "fingerprint": fingerprint,
            "rules_version": RULES_VERSION,
            "status": status,
            "display_name": display_name,
            "description": description,
            "summary": summary,
            "error_code": "summary_trimmed" if trimmed else None,
            "duration_ms": self._elapsed_ms(started),
        }
        try:
            self.store.save_state(sec, state)
        except Exception:
            # A summary the database still refuses is dropped, never retried
            # forever: the source is recorded as partial without columns.
            logger.warning("catalog copilot source summary refused", exc_info=True)
            self.store.save_state(
                sec,
                {
                    **state,
                    "status": "partial",
                    "error_code": "summary_too_large",
                    "summary": {**summary, "columns": [], "columns_dropped": True},
                },
            )
            status = "partial"
        return {"processed": True, "status": status, "edges": 0}


def _fit_summary(summary: dict[str, Any]) -> tuple[dict[str, Any], bool]:
    """Shrink a Bronze summary under the database size bound."""

    def size(value: dict[str, Any]) -> int:
        return len(json.dumps(value, sort_keys=True, default=str).encode("utf-8"))

    if size(summary) <= SUMMARY_MAX_BYTES:
        return summary, False
    fitted = {**summary, "columns": [dict(column) for column in summary.get("columns") or []]}
    for column in fitted["columns"]:
        column.pop("basis", None)
        column.pop("type", None)
    while fitted["columns"] and size(fitted) > SUMMARY_MAX_BYTES:
        fitted["columns"] = fitted["columns"][: max(0, len(fitted["columns"]) * 3 // 4)]
    fitted["columns_trimmed"] = True
    return fitted, True


def _candidate_row(
    dataset: str,
    column: str,
    data_type: str,
    rows: Any,
    null_rate: Any,
    *,
    known: bool | None,
) -> dict[str, Any]:
    if known is None:
        is_key = (
            semantic_type(data_type, column) == "identifier"
            and null_rate in (0, 0.0)
            and isinstance(rows, int)
            and rows > 0
        )
    else:
        is_key = known and isinstance(rows, int) and rows > 0
    return {
        "dataset": dataset,
        "column_name": column,
        "data_type": data_type,
        "distinct_count": rows if is_key else None,
        "null_rate": 0.0 if is_key else null_rate,
    }


def _column_type(
    rows: list[dict[str, Any]],
    live: dict[str, dict[str, dict[str, Any]]],
    dataset: str,
    column: str,
) -> str:
    for row in rows:
        if row["dataset"] == dataset and row["column_name"] == column:
            return str(row.get("data_type") or "")
    return str(((live.get(dataset) or {}).get(column) or {}).get("data_type") or "")


def _match(
    rows: list[dict[str, Any]],
    live: dict[str, dict[str, dict[str, Any]]],
    key: tuple[str, str, str, str],
    ratio: float | None,
    target_exact: bool,
) -> dict[str, Any]:
    from_ds, from_col, to_ds, to_col = key
    from_type = _column_type(rows, live, from_ds, from_col)
    to_type = _column_type(rows, live, to_ds, to_col)
    from_class = key_class(from_col)
    to_class = key_class(to_col)
    return {
        "name_exact": normalize_name(from_col) == normalize_name(to_col),
        "same_class": bool(from_class and to_class and from_class[0] == to_class[0]),
        "types_compatible": bool(from_type)
        and _normalize_type(from_type) == _normalize_type(to_type),
        "containment": ratio,
        "target_key_exact": target_exact,
    }


__all__ = [
    "AutoCatalogStatus",
    "AutonomousCatalogWorker",
    "CatalogCopilotHost",
    "LinkResult",
    "copilot_enabled",
    "subject_fingerprint",
]
