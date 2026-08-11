#!/usr/bin/env python3
"""Explicit, audited reconciliation for durable ``pipeline_runs`` rows.

The command is intentionally server-owned and dry-run by default.  It never
discovers or deletes runs: an operator must supply every run id together with
the exact status, start time and fencing token previously observed.  Apply
also requires the server environment to pin the manifest SHA-256.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable
from uuid import UUID


SCHEMA = "omega.pipeline-run-reconciliation/v1"
APPLY_GUARD_ENV = "OMEGA_PIPELINE_RECONCILIATION_ALLOW_APPLY"
ACTOR_ENV = "OMEGA_PIPELINE_RECONCILIATION_ACTOR"
CHANGE_ID_ENV = "OMEGA_PIPELINE_RECONCILIATION_CHANGE_ID"
MANIFEST_SHA_ENV = "OMEGA_PIPELINE_RECONCILIATION_MANIFEST_SHA256"
DATABASE_URL_ENV = "DATABASE_URL"

STATUS_RANKS = {
    "unknown": 0,
    "queued": 10,
    "scheduled": 10,
    "up_for_retry": 15,
    "running": 20,
    "success": 30,
    "noop": 30,
    "skipped": 35,
    "skipped_explicit": 35,
    "partial": 40,
    "blocked": 50,
    "failed": 60,
    "error": 60,
    "upstream_failed": 60,
    "cancelled": 60,
    "removed": 60,
}
ALLOWED_REASONS = {
    "airflow_terminal_failure",
    "airflow_run_missing_after_retention",
    "stale_orphan",
    "aggregate_completed_with_blocks",
}
AIRFLOW_TERMINAL_FAILURE_STATES = {
    "failed",
    "error",
    "upstream_failed",
    "cancelled",
    "removed",
}
ALLOWED_TARGET_STATUSES = {"partial", "blocked", "failed"}
ALLOWED_EVIDENCE_KEYS = {
    "airflow_http_status",
    "airflow_state",
    "blocked_children",
    "failed_children",
    "child_run_ids",
    "observed_at",
    "orphan_confirmed",
    "retention_confirmed",
}
ROOT_KEYS = {"schema", "change_id", "runs"}
RUN_KEYS = {
    "run_id",
    "tenant_id",
    "workspace_id",
    "expected_status",
    "expected_started_at",
    "expected_fencing_token",
    "target_status",
    "reason",
    "evidence",
}
SAFE_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:/@+-]{0,255}$")


class ManifestError(ValueError):
    pass


class ReconciliationConflict(RuntimeError):
    pass


def _normalized_status(value: object | None) -> str:
    return str(value or "unknown").strip().lower() or "unknown"


def _status_rank(value: object | None) -> int:
    return STATUS_RANKS.get(_normalized_status(value), 0)


def _canonical_json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _parse_timestamp(value: object | None, *, field: str) -> datetime | None:
    if value is None:
        return None
    if not isinstance(value, str) or not value.strip():
        raise ManifestError(f"{field} must be an ISO-8601 timestamp or null")
    try:
        parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    except ValueError as exc:
        raise ManifestError(f"{field} must be an ISO-8601 timestamp or null") from exc
    if parsed.tzinfo is None:
        raise ManifestError(f"{field} must include a timezone")
    return parsed.astimezone(timezone.utc)


def _timestamp_text(value: datetime | None) -> str | None:
    if value is None:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc).isoformat()


def _uuid_text(value: object, *, field: str) -> str:
    try:
        return str(UUID(str(value)))
    except (TypeError, ValueError, AttributeError) as exc:
        raise ManifestError(f"{field} must be a UUID") from exc


def _validate_evidence(reason: str, evidence: object) -> dict[str, Any]:
    if not isinstance(evidence, dict):
        raise ManifestError("evidence must be an object")
    unknown = set(evidence) - ALLOWED_EVIDENCE_KEYS
    if unknown:
        raise ManifestError(f"unsupported evidence keys: {sorted(unknown)}")

    clean = dict(evidence)
    for key in ("blocked_children", "failed_children", "airflow_http_status"):
        if key not in clean:
            continue
        value = clean[key]
        if isinstance(value, bool) or not isinstance(value, int) or value < 0:
            raise ManifestError(f"evidence.{key} must be a non-negative integer")
    for key in ("orphan_confirmed", "retention_confirmed"):
        if key in clean and not isinstance(clean[key], bool):
            raise ManifestError(f"evidence.{key} must be a boolean")
    if "airflow_state" in clean:
        state = _normalized_status(clean["airflow_state"])
        if state not in STATUS_RANKS and state != "not_found":
            raise ManifestError("evidence.airflow_state is unsupported")
        clean["airflow_state"] = state
    if reason == "airflow_terminal_failure":
        if clean.get("airflow_state") not in AIRFLOW_TERMINAL_FAILURE_STATES:
            raise ManifestError(
                "Airflow terminal failure reconciliation requires "
                "airflow_state=failed, error, upstream_failed, cancelled or removed"
            )
    elif reason == "airflow_run_missing_after_retention":
        if (
            clean.get("airflow_http_status") != 404
            or clean.get("retention_confirmed") is not True
        ):
            raise ManifestError(
                "retention reconciliation requires airflow_http_status=404 "
                "and retention_confirmed=true"
            )
    elif reason == "stale_orphan":
        if clean.get("orphan_confirmed") is not True:
            raise ManifestError(
                "stale orphan reconciliation requires orphan_confirmed=true"
            )
    elif reason == "aggregate_completed_with_blocks":
        if str(clean.get("airflow_state") or "").lower() != "success":
            raise ManifestError(
                "aggregate block reconciliation requires airflow_state=success"
            )
        if int(clean.get("blocked_children") or 0) < 1:
            raise ManifestError(
                "aggregate block reconciliation requires blocked_children >= 1"
            )

    child_ids = clean.get("child_run_ids")
    if child_ids is not None:
        if not isinstance(child_ids, list) or any(
            not isinstance(item, str) or not SAFE_ID_RE.fullmatch(item)
            for item in child_ids
        ):
            raise ManifestError("evidence.child_run_ids must contain safe run ids")
        if len(child_ids) != len(set(child_ids)):
            raise ManifestError("evidence.child_run_ids contains duplicates")
    if "observed_at" in clean:
        clean["observed_at"] = _timestamp_text(
            _parse_timestamp(clean["observed_at"], field="evidence.observed_at")
        )
    return clean


@dataclass(frozen=True)
class RunExpectation:
    run_id: str
    tenant_id: str
    workspace_id: str
    expected_status: str
    expected_started_at: datetime | None
    expected_fencing_token: int
    target_status: str
    reason: str
    evidence: dict[str, Any]
    fingerprint: str

    def event(self, *, actor: str, change_id: str) -> dict[str, Any]:
        return {
            "fingerprint": self.fingerprint,
            "actor": actor,
            "change_id": change_id,
            "reason": self.reason,
            "previous_status": self.expected_status,
            "target_status": self.target_status,
            "expected_started_at": _timestamp_text(self.expected_started_at),
            "expected_fencing_token": self.expected_fencing_token,
            "evidence": self.evidence,
        }


@dataclass(frozen=True)
class Manifest:
    change_id: str
    runs: tuple[RunExpectation, ...]
    sha256: str


def _expectation_from_dict(raw: object) -> RunExpectation:
    if not isinstance(raw, dict):
        raise ManifestError("each run entry must be an object")
    missing = RUN_KEYS - set(raw)
    unknown = set(raw) - RUN_KEYS
    if missing:
        raise ManifestError(f"run entry is missing keys: {sorted(missing)}")
    if unknown:
        raise ManifestError(f"run entry has unsupported keys: {sorted(unknown)}")

    run_id = str(raw["run_id"] or "").strip()
    if not SAFE_ID_RE.fullmatch(run_id):
        raise ManifestError("run_id is invalid")
    tenant_id = _uuid_text(raw["tenant_id"], field=f"{run_id}.tenant_id")
    workspace_id = _uuid_text(raw["workspace_id"], field=f"{run_id}.workspace_id")
    expected_status = _normalized_status(raw["expected_status"])
    target_status = _normalized_status(raw["target_status"])
    if expected_status not in STATUS_RANKS:
        raise ManifestError(f"{run_id}.expected_status is unsupported")
    if target_status not in ALLOWED_TARGET_STATUSES:
        raise ManifestError(
            f"{run_id}.target_status must be partial, blocked or failed"
        )
    if _status_rank(target_status) <= _status_rank(expected_status):
        raise ManifestError(f"{run_id}.target_status is not a monotonic advance")
    reason = str(raw["reason"] or "").strip()
    if reason not in ALLOWED_REASONS:
        raise ManifestError(f"{run_id}.reason is unsupported")
    evidence = _validate_evidence(reason, raw["evidence"])
    if reason in {
        "airflow_terminal_failure",
        "airflow_run_missing_after_retention",
        "stale_orphan",
    } and target_status != "failed":
        raise ManifestError(f"{run_id}.{reason} must target failed")
    if reason == "aggregate_completed_with_blocks" and target_status not in {
        "partial",
        "blocked",
    }:
        raise ManifestError(
            f"{run_id}.aggregate block target must be partial or blocked"
        )
    expected_started_at = _parse_timestamp(
        raw["expected_started_at"], field=f"{run_id}.expected_started_at"
    )
    fence = raw["expected_fencing_token"]
    if isinstance(fence, bool) or not isinstance(fence, int) or fence < 0:
        raise ManifestError(
            f"{run_id}.expected_fencing_token must be a non-negative integer"
        )

    fingerprint_payload = {
        "run_id": run_id,
        "tenant_id": tenant_id,
        "workspace_id": workspace_id,
        "expected_status": expected_status,
        "expected_started_at": _timestamp_text(expected_started_at),
        "expected_fencing_token": fence,
        "target_status": target_status,
        "reason": reason,
        "evidence": evidence,
    }
    fingerprint = _sha256_bytes(_canonical_json(fingerprint_payload).encode("utf-8"))
    return RunExpectation(
        run_id=run_id,
        tenant_id=tenant_id,
        workspace_id=workspace_id,
        expected_status=expected_status,
        expected_started_at=expected_started_at,
        expected_fencing_token=fence,
        target_status=target_status,
        reason=reason,
        evidence=evidence,
        fingerprint=fingerprint,
    )


def load_manifest(path: Path) -> Manifest:
    raw_bytes = path.read_bytes()
    try:
        payload = json.loads(raw_bytes)
    except json.JSONDecodeError as exc:
        raise ManifestError("manifest is not valid JSON") from exc
    if not isinstance(payload, dict):
        raise ManifestError("manifest root must be an object")
    missing = ROOT_KEYS - set(payload)
    unknown = set(payload) - ROOT_KEYS
    if missing:
        raise ManifestError(f"manifest is missing keys: {sorted(missing)}")
    if unknown:
        raise ManifestError(f"manifest has unsupported keys: {sorted(unknown)}")
    if payload["schema"] != SCHEMA:
        raise ManifestError(f"manifest schema must be {SCHEMA}")
    change_id = str(payload["change_id"] or "").strip()
    if not SAFE_ID_RE.fullmatch(change_id):
        raise ManifestError("change_id is invalid")
    raw_runs = payload["runs"]
    if not isinstance(raw_runs, list) or not raw_runs:
        raise ManifestError("manifest runs must be a non-empty list")
    runs = tuple(_expectation_from_dict(item) for item in raw_runs)
    run_ids = [item.run_id for item in runs]
    if len(run_ids) != len(set(run_ids)):
        raise ManifestError("manifest contains duplicate run ids")
    return Manifest(change_id=change_id, runs=runs, sha256=_sha256_bytes(raw_bytes))


def _row_extra(row: tuple[Any, ...]) -> dict[str, Any]:
    value = row[4] or {}
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except json.JSONDecodeError:
            return {}
    return value if isinstance(value, dict) else {}


def _already_applied(row: tuple[Any, ...], expected: RunExpectation) -> bool:
    if _normalized_status(row[1]) != expected.target_status:
        return False
    reconciliation = _row_extra(row).get("reconciliation")
    return isinstance(reconciliation, list) and any(
        isinstance(item, dict) and item.get("fingerprint") == expected.fingerprint
        for item in reconciliation
    )


def _verify_locked_row(row: tuple[Any, ...] | None, expected: RunExpectation) -> str:
    if row is None:
        raise ReconciliationConflict(f"{expected.run_id}: missing; no row was inserted")
    if _already_applied(row, expected):
        return "already_applied"
    live_status = _normalized_status(row[1])
    live_started_at = _timestamp_text(row[2])
    live_fence = int(row[3] or 0)
    expected_started_at = _timestamp_text(expected.expected_started_at)
    if live_status != expected.expected_status:
        raise ReconciliationConflict(
            f"{expected.run_id}: status changed (expected {expected.expected_status}, found {live_status})"
        )
    if live_started_at != expected_started_at:
        raise ReconciliationConflict(f"{expected.run_id}: started_at changed")
    if live_fence != expected.expected_fencing_token:
        raise ReconciliationConflict(f"{expected.run_id}: fencing token changed")
    return "matched"


def _set_scope(cur: Any, expected: RunExpectation) -> None:
    cur.execute(
        "SELECT set_config('app.tenant_id', %s, true), "
        "set_config('app.workspace_id', %s, true)",
        (expected.tenant_id, expected.workspace_id),
    )


def _select_row(
    cur: Any, expected: RunExpectation, *, lock: bool
) -> tuple[Any, ...] | None:
    suffix = " FOR UPDATE" if lock else ""
    cur.execute(
        """
        SELECT run_id, status, started_at, fencing_token, extra
          FROM pipeline_runs
         WHERE run_id=%s
           AND tenant_id=%s::uuid
           AND workspace_id=%s::uuid
        """
        + suffix,
        (expected.run_id, expected.tenant_id, expected.workspace_id),
    )
    return cur.fetchone()


def _append_audit(cur: Any, expected: RunExpectation, *, event: dict[str, Any]) -> None:
    cur.execute("SELECT to_regclass('public.audit_events')")
    exists = cur.fetchone()
    if not exists or not exists[0]:
        raise ReconciliationConflict(
            "critical audit table public.audit_events is missing"
        )
    metadata = {
        "fingerprint": expected.fingerprint,
        "actor": event["actor"],
        "change_id": event["change_id"],
        "reason": expected.reason,
        "previous_status": expected.expected_status,
        "target_status": expected.target_status,
        "tenant_id": expected.tenant_id,
        "workspace_id": expected.workspace_id,
        "expected_fencing_token": expected.expected_fencing_token,
    }
    cur.execute(
        """
        INSERT INTO audit_events
            (action, resource_type, resource_id, status, metadata)
        VALUES ('pipeline_run.reconcile', 'pipeline_run', %s, 'success', %s::jsonb)
        """,
        (expected.run_id, _canonical_json(metadata)),
    )


def _apply_one(
    cur: Any,
    expected: RunExpectation,
    *,
    actor: str,
    change_id: str,
) -> dict[str, Any]:
    _set_scope(cur, expected)
    row = _select_row(cur, expected, lock=True)
    state = _verify_locked_row(row, expected)
    if state == "already_applied":
        return {
            "run_id": expected.run_id,
            "result": state,
            "status": expected.target_status,
        }

    event = expected.event(actor=actor, change_id=change_id)
    cur.execute(
        """
        UPDATE pipeline_runs
           SET status=%s,
               finished_at=COALESCE(finished_at, NOW()),
               error_message=COALESCE(error_message, %s),
               lease_expires_at=NULL,
               fencing_token=fencing_token + 1,
               extra=jsonb_set(
                   COALESCE(extra, '{}'::jsonb),
                   '{reconciliation}',
                   COALESCE(
                       CASE WHEN jsonb_typeof(extra->'reconciliation')='array'
                            THEN extra->'reconciliation'
                            ELSE '[]'::jsonb END,
                       '[]'::jsonb
                   ) || jsonb_build_array(
                       %s::jsonb || jsonb_build_object('applied_at', NOW())
                   ),
                   true
               )
         WHERE run_id=%s
           AND tenant_id=%s::uuid
           AND workspace_id=%s::uuid
           AND LOWER(COALESCE(status, 'unknown'))=%s
           AND started_at IS NOT DISTINCT FROM %s::timestamptz
           AND fencing_token=%s
         RETURNING status, fencing_token
        """,
        (
            expected.target_status,
            expected.reason,
            _canonical_json(event),
            expected.run_id,
            expected.tenant_id,
            expected.workspace_id,
            expected.expected_status,
            expected.expected_started_at,
            expected.expected_fencing_token,
        ),
    )
    updated = cur.fetchone()
    if updated is None:
        raise ReconciliationConflict(f"{expected.run_id}: CAS update lost its fence")
    _append_audit(cur, expected, event=event)
    return {
        "run_id": expected.run_id,
        "result": "applied",
        "status": str(updated[0]),
        "fencing_token": int(updated[1]),
    }


def _dry_run_one(cur: Any, expected: RunExpectation) -> dict[str, Any]:
    _set_scope(cur, expected)
    row = _select_row(cur, expected, lock=False)
    state = _verify_locked_row(row, expected)
    return {
        "run_id": expected.run_id,
        "result": state if state == "already_applied" else "would_apply",
        "current_status": str(row[1]),
        "target_status": expected.target_status,
        "reason": expected.reason,
    }


def reconcile(
    conn: Any,
    manifest: Manifest,
    *,
    apply: bool,
    actor: str = "dry-run",
) -> list[dict[str, Any]]:
    results: list[dict[str, Any]] = []
    ordered = sorted(manifest.runs, key=lambda item: item.run_id)
    try:
        with conn.cursor() as cur:
            for expected in ordered:
                if apply:
                    results.append(
                        _apply_one(
                            cur,
                            expected,
                            actor=actor,
                            change_id=manifest.change_id,
                        )
                    )
                else:
                    results.append(_dry_run_one(cur, expected))
        if apply:
            conn.commit()
        else:
            conn.rollback()
    except Exception:
        conn.rollback()
        raise
    return results


def _safe_server_value(name: str) -> str:
    value = str(os.environ.get(name) or "").strip()
    if not SAFE_ID_RE.fullmatch(value):
        raise ManifestError(f"{name} is missing or invalid")
    return value


def _apply_authority(manifest: Manifest) -> str:
    if os.environ.get(APPLY_GUARD_ENV) != "1":
        raise ManifestError(f"apply requires {APPLY_GUARD_ENV}=1 on the server")
    actor = _safe_server_value(ACTOR_ENV)
    change_id = _safe_server_value(CHANGE_ID_ENV)
    if change_id != manifest.change_id:
        raise ManifestError(f"{CHANGE_ID_ENV} does not match the manifest")
    pinned_sha = str(os.environ.get(MANIFEST_SHA_ENV) or "").strip().lower()
    if not re.fullmatch(r"[a-f0-9]{64}", pinned_sha) or pinned_sha != manifest.sha256:
        raise ManifestError(
            f"{MANIFEST_SHA_ENV} does not match the exact manifest bytes"
        )
    return actor


def _connect() -> Any:
    dsn = str(os.environ.get(DATABASE_URL_ENV) or "").strip()
    if not dsn:
        raise ManifestError(f"{DATABASE_URL_ENV} is not configured")
    import psycopg2

    return psycopg2.connect(dsn.replace("postgresql+psycopg2://", "postgresql://"))


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", required=True, type=Path)
    parser.add_argument(
        "--apply",
        action="store_true",
        help="apply the exact server-pinned manifest (default is read-only dry-run)",
    )
    return parser


def main(argv: Iterable[str] | None = None) -> int:
    args = _parser().parse_args(list(argv) if argv is not None else None)
    try:
        manifest = load_manifest(args.manifest)
        actor = _apply_authority(manifest) if args.apply else "dry-run"
        conn = _connect()
        try:
            if not args.apply and hasattr(conn, "set_session"):
                conn.set_session(readonly=True, autocommit=False)
            results = reconcile(conn, manifest, apply=args.apply, actor=actor)
        finally:
            conn.close()
    except (OSError, ManifestError, ReconciliationConflict) as exc:
        print(
            json.dumps({"ok": False, "error": str(exc)}, sort_keys=True),
            file=sys.stderr,
        )
        return 2
    except Exception as exc:  # noqa: BLE001 - redact database/transport details
        print(
            json.dumps(
                {
                    "ok": False,
                    "error": "pipeline reconciliation failed",
                    "error_type": type(exc).__name__,
                },
                sort_keys=True,
            ),
            file=sys.stderr,
        )
        return 1

    print(
        json.dumps(
            {
                "ok": True,
                "mode": "apply" if args.apply else "dry-run",
                "manifest_sha256": manifest.sha256,
                "change_id": manifest.change_id,
                "runs": results,
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
