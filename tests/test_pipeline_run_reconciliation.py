from __future__ import annotations

import importlib.util
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
TENANT = "11111111-1111-1111-1111-111111111111"
WORKSPACE = "22222222-2222-2222-2222-222222222222"
STARTED = datetime(2026, 8, 8, 12, 0, tzinfo=timezone.utc)


def _load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


RECONCILE = _load(
    "omega_reconcile_pipeline_runs",
    ROOT / "scripts" / "reconcile_pipeline_runs.py",
)


def _run(
    run_id: str = "sync_now:sap_successfactors:orphan-1",
    *,
    reason: str = "stale_orphan",
    target_status: str = "failed",
    evidence: dict | None = None,
) -> dict:
    if evidence is None:
        evidence = {"orphan_confirmed": True, "child_run_ids": []}
    return {
        "run_id": run_id,
        "tenant_id": TENANT,
        "workspace_id": WORKSPACE,
        "expected_status": "running",
        "expected_started_at": STARTED.isoformat(),
        "expected_fencing_token": 7,
        "target_status": target_status,
        "reason": reason,
        "evidence": evidence,
    }


def _manifest(tmp_path: Path, runs: list[dict]) -> object:
    path = tmp_path / "pipeline-reconciliation.json"
    path.write_text(
        json.dumps(
            {
                "schema": RECONCILE.SCHEMA,
                "change_id": "checkpoint-5.5-stale-runs",
                "runs": runs,
            },
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )
    return RECONCILE.load_manifest(path)


class _Cursor:
    def __init__(self, rows: dict[str, tuple | None], *, cas_lost: bool = False):
        self.rows = dict(rows)
        self.cas_lost = cas_lost
        self.executed: list[tuple[str, tuple]] = []
        self.pending = None

    def __enter__(self):
        return self

    def __exit__(self, *_exc):
        return False

    def execute(self, sql: str, args: tuple | None = None):
        args = args or ()
        self.executed.append((sql, args))
        normalized = " ".join(sql.split())
        if normalized.startswith("SELECT run_id, status, started_at"):
            self.pending = self.rows.get(str(args[0]))
        elif normalized.startswith("UPDATE pipeline_runs"):
            run_id = str(args[3])
            current = self.rows.get(run_id)
            assert current is not None
            self.pending = (
                None if self.cas_lost else (str(args[0]), int(current[3]) + 1)
            )
        elif normalized.startswith("SELECT to_regclass"):
            self.pending = ("audit_events",)
        else:
            self.pending = None

    def fetchone(self):
        value = self.pending
        self.pending = None
        return value


class _Connection:
    def __init__(self, rows: dict[str, tuple | None], *, cas_lost: bool = False):
        self.cursor_obj = _Cursor(rows, cas_lost=cas_lost)
        self.commits = 0
        self.rollbacks = 0

    def cursor(self):
        return self.cursor_obj

    def commit(self):
        self.commits += 1

    def rollback(self):
        self.rollbacks += 1


def _row(*, status: str = "running", fence: int = 7, extra: dict | None = None):
    return (
        "sync_now:sap_successfactors:orphan-1",
        status,
        STARTED,
        fence,
        extra or {},
    )


def _mutating_sql(cursor: _Cursor) -> list[str]:
    return [
        sql
        for sql, _args in cursor.executed
        if "UPDATE pipeline_runs" in sql
        or "INSERT INTO audit_events" in sql
        or "DELETE " in sql.upper()
    ]


def test_console_and_mcp_infra_share_the_same_status_order() -> None:
    console_status = _load(
        "omega_console_pipeline_status",
        ROOT / "console" / "app" / "domains" / "pipeline" / "status_transitions.py",
    )
    mcp_status = _load(
        "omega_mcp_pipeline_status",
        ROOT / "mcp-infra" / "app" / "pipeline_run_transitions.py",
    )
    successfactors_status = _load(
        "omega_successfactors_pipeline_status",
        ROOT
        / "cartridges"
        / "sap_successfactors"
        / "app"
        / "services"
        / "pipeline_run_transitions.py",
    )

    assert console_status.PIPELINE_STATUS_RANKS == mcp_status.PIPELINE_STATUS_RANKS
    assert (
        console_status.PIPELINE_STATUS_RANKS
        == successfactors_status.PIPELINE_STATUS_RANKS
    )
    assert console_status.PIPELINE_STATUS_RANKS == RECONCILE.STATUS_RANKS


def test_only_fenced_materialization_slots_may_reopen_for_a_new_generation() -> None:
    source = (
        ROOT / "airflow" / "dags" / "dataset_refresh_idempotency.py"
    ).read_text(encoding="utf-8")
    reclaim = source.split("if existing:", 1)[1].split("else:", 1)[0]

    assert "FOR UPDATE" in source.split("if existing:", 1)[0]
    assert "SET status = 'running'" in reclaim
    assert "fencing_token = fencing_token + 1" in reclaim
    assert "tenant_id = %s::uuid" in reclaim
    assert "workspace_id = %s::uuid" in reclaim


def test_dry_run_is_read_only_and_uses_the_exact_scoped_run_id(tmp_path: Path) -> None:
    manifest = _manifest(tmp_path, [_run()])
    conn = _Connection({manifest.runs[0].run_id: _row()})

    result = RECONCILE.reconcile(conn, manifest, apply=False)

    assert result == [
        {
            "run_id": manifest.runs[0].run_id,
            "result": "would_apply",
            "current_status": "running",
            "target_status": "failed",
            "reason": "stale_orphan",
        }
    ]
    assert _mutating_sql(conn.cursor_obj) == []
    assert conn.commits == 0
    assert conn.rollbacks == 1
    select_sql, select_args = next(
        (sql, args)
        for sql, args in conn.cursor_obj.executed
        if "SELECT run_id, status" in sql
    )
    assert "tenant_id=%s::uuid" in select_sql
    assert "workspace_id=%s::uuid" in select_sql
    assert "FOR UPDATE" not in select_sql
    assert select_args == (manifest.runs[0].run_id, TENANT, WORKSPACE)


def test_apply_locks_rechecks_cas_fences_and_audits_without_delete(
    tmp_path: Path,
) -> None:
    manifest = _manifest(tmp_path, [_run()])
    conn = _Connection({manifest.runs[0].run_id: _row()})

    result = RECONCILE.reconcile(
        conn,
        manifest,
        apply=True,
        actor="gcp-release-operator",
    )

    assert result == [
        {
            "run_id": manifest.runs[0].run_id,
            "result": "applied",
            "status": "failed",
            "fencing_token": 8,
        }
    ]
    assert conn.commits == 1
    assert conn.rollbacks == 0
    sql_text = "\n".join(sql for sql, _args in conn.cursor_obj.executed)
    assert "FOR UPDATE" in sql_text
    assert "started_at IS NOT DISTINCT FROM %s::timestamptz" in sql_text
    assert "fencing_token=%s" in sql_text
    assert "fencing_token=fencing_token + 1" in sql_text
    assert "'{reconciliation}'" in sql_text
    assert "INSERT INTO audit_events" in sql_text
    assert "DELETE " not in sql_text.upper()


def test_apply_is_idempotent_for_the_same_reconciliation_fingerprint(
    tmp_path: Path,
) -> None:
    manifest = _manifest(tmp_path, [_run()])
    expected = manifest.runs[0]
    already = _row(
        status="failed",
        fence=8,
        extra={"reconciliation": [{"fingerprint": expected.fingerprint}]},
    )
    conn = _Connection({expected.run_id: already})

    result = RECONCILE.reconcile(
        conn,
        manifest,
        apply=True,
        actor="gcp-release-operator",
    )

    assert result == [
        {"run_id": expected.run_id, "result": "already_applied", "status": "failed"}
    ]
    assert _mutating_sql(conn.cursor_obj) == []
    assert conn.commits == 1


def test_airflow_404_requires_retention_evidence_and_retains_the_row(
    tmp_path: Path,
) -> None:
    manifest = _manifest(
        tmp_path,
        [
            _run(
                reason="airflow_run_missing_after_retention",
                evidence={
                    "airflow_http_status": 404,
                    "retention_confirmed": True,
                    "observed_at": "2026-08-11T12:00:00Z",
                },
            )
        ],
    )
    conn = _Connection({manifest.runs[0].run_id: _row()})

    RECONCILE.reconcile(conn, manifest, apply=True, actor="gcp-release-operator")

    sql_text = "\n".join(sql for sql, _args in conn.cursor_obj.executed)
    assert "UPDATE pipeline_runs" in sql_text
    assert "DELETE " not in sql_text.upper()


def test_airflow_404_without_confirmed_retention_is_rejected(tmp_path: Path) -> None:
    with pytest.raises(RECONCILE.ManifestError, match="retention_confirmed"):
        _manifest(
            tmp_path,
            [
                _run(
                    reason="airflow_run_missing_after_retention",
                    evidence={"airflow_http_status": 404, "retention_confirmed": False},
                )
            ],
        )


@pytest.mark.parametrize(
    "airflow_state",
    sorted(RECONCILE.AIRFLOW_TERMINAL_FAILURE_STATES),
)
def test_airflow_terminal_failure_requires_an_exact_failure_state(
    tmp_path: Path, airflow_state: str
) -> None:
    manifest = _manifest(
        tmp_path,
        [
            _run(
                reason="airflow_terminal_failure",
                evidence={
                    "airflow_state": airflow_state,
                    "observed_at": "2026-08-11T12:00:00Z",
                },
            )
        ],
    )
    conn = _Connection({manifest.runs[0].run_id: _row()})

    result = RECONCILE.reconcile(
        conn,
        manifest,
        apply=True,
        actor="gcp-release-operator",
    )

    assert result[0]["status"] == "failed"


@pytest.mark.parametrize("airflow_state", ["success", "running", "not_found", None])
def test_airflow_terminal_failure_rejects_non_failure_or_missing_state(
    tmp_path: Path, airflow_state: str | None
) -> None:
    evidence = (
        {"airflow_state": airflow_state} if airflow_state is not None else {}
    )
    with pytest.raises(RECONCILE.ManifestError, match="terminal failure"):
        _manifest(
            tmp_path,
            [_run(reason="airflow_terminal_failure", evidence=evidence)],
        )


def test_airflow_terminal_failure_must_target_failed(tmp_path: Path) -> None:
    with pytest.raises(RECONCILE.ManifestError, match="must target failed"):
        _manifest(
            tmp_path,
            [
                _run(
                    reason="airflow_terminal_failure",
                    target_status="blocked",
                    evidence={"airflow_state": "failed"},
                )
            ],
        )


def test_run_id_accepts_an_iso_timezone_offset(tmp_path: Path) -> None:
    run_id = "manual__2026-06-12T17:04:23.825231+00:00"

    manifest = _manifest(tmp_path, [_run(run_id=run_id)])

    assert manifest.runs[0].run_id == run_id


@pytest.mark.parametrize(
    "run_id",
    [
        "manual run",
        "manual\nrun",
        "manual;run",
        "manual$(run)",
    ],
)
def test_run_id_still_rejects_unsafe_characters(tmp_path: Path, run_id: str) -> None:
    with pytest.raises(RECONCILE.ManifestError, match="run_id is invalid"):
        _manifest(tmp_path, [_run(run_id=run_id)])


def test_missing_run_fails_closed_without_insert_or_commit(tmp_path: Path) -> None:
    manifest = _manifest(tmp_path, [_run()])
    conn = _Connection({manifest.runs[0].run_id: None})

    with pytest.raises(RECONCILE.ReconciliationConflict, match="missing"):
        RECONCILE.reconcile(
            conn,
            manifest,
            apply=True,
            actor="gcp-release-operator",
        )

    assert conn.commits == 0
    assert conn.rollbacks == 1
    sql_text = "\n".join(sql for sql, _args in conn.cursor_obj.executed)
    assert "INSERT INTO pipeline_runs" not in sql_text
    assert "DELETE " not in sql_text.upper()


@pytest.mark.parametrize(
    ("row", "message"),
    [
        (_row(status="queued"), "status changed"),
        (_row(fence=9), "fencing token changed"),
        (
            (
                "sync_now:sap_successfactors:orphan-1",
                "running",
                datetime(2026, 8, 8, 12, 1, tzinfo=timezone.utc),
                7,
                {},
            ),
            "started_at changed",
        ),
    ],
)
def test_recheck_rejects_race_on_status_started_at_or_fence(
    tmp_path: Path, row: tuple, message: str
) -> None:
    manifest = _manifest(tmp_path, [_run()])
    conn = _Connection({manifest.runs[0].run_id: row})

    with pytest.raises(RECONCILE.ReconciliationConflict, match=message):
        RECONCILE.reconcile(
            conn,
            manifest,
            apply=True,
            actor="gcp-release-operator",
        )

    assert _mutating_sql(conn.cursor_obj) == []
    assert conn.rollbacks == 1


def test_apply_fails_closed_if_cas_loses_after_the_locked_read(tmp_path: Path) -> None:
    manifest = _manifest(tmp_path, [_run()])
    conn = _Connection({manifest.runs[0].run_id: _row()}, cas_lost=True)

    with pytest.raises(RECONCILE.ReconciliationConflict, match="lost its fence"):
        RECONCILE.reconcile(
            conn,
            manifest,
            apply=True,
            actor="gcp-release-operator",
        )

    assert conn.commits == 0
    assert conn.rollbacks == 1
    assert not any(
        "INSERT INTO audit_events" in sql for sql, _args in conn.cursor_obj.executed
    )


def test_aggregate_airflow_success_with_blocked_children_targets_partial(
    tmp_path: Path,
) -> None:
    aggregate = _run(
        run_id="manual__aggregate-with-blocks",
        reason="aggregate_completed_with_blocks",
        target_status="partial",
        evidence={
            "airflow_state": "success",
            "blocked_children": 2,
            "failed_children": 0,
            "child_run_ids": ["manual__child-1", "manual__child-2"],
        },
    )
    aggregate["expected_status"] = "success"
    manifest = _manifest(tmp_path, [aggregate])
    row = (
        aggregate["run_id"],
        "success",
        STARTED,
        7,
        {},
    )
    conn = _Connection({aggregate["run_id"]: row})

    result = RECONCILE.reconcile(
        conn, manifest, apply=True, actor="gcp-release-operator"
    )

    assert result[0]["status"] == "partial"


def test_apply_authority_pins_exact_manifest_bytes(tmp_path: Path, monkeypatch) -> None:
    manifest = _manifest(tmp_path, [_run()])
    monkeypatch.setenv(RECONCILE.APPLY_GUARD_ENV, "1")
    monkeypatch.setenv(RECONCILE.ACTOR_ENV, "gcp-release-operator")
    monkeypatch.setenv(RECONCILE.CHANGE_ID_ENV, manifest.change_id)
    monkeypatch.setenv(RECONCILE.MANIFEST_SHA_ENV, "0" * 64)

    with pytest.raises(RECONCILE.ManifestError, match="exact manifest bytes"):
        RECONCILE._apply_authority(manifest)

    monkeypatch.setenv(RECONCILE.MANIFEST_SHA_ENV, manifest.sha256)
    assert RECONCILE._apply_authority(manifest) == "gcp-release-operator"
