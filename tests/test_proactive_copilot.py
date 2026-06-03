"""Sprint v1.44.2 (Tarea F) — proactive copilot briefing static
verification.

Live-DB exercise of the 4 analyzers requires a running Postgres
with extraction_runs / jobs / users tables populated. The CI
sandbox can't run docker, so this file:

  * Asserts the service module exports the 4 analyzers + the
    briefing_for_user aggregator + dismiss_highlight.
  * Asserts each analyzer's SQL references the right table /
    column (no typo'd column names like the v1.44.1 R1 P0).
  * Verifies the highlight shape contract (id / severity /
    title / body / category).
  * Verifies migration 50 declares the table + index + self-
    registers via the v1.43+ filename pattern.
  * Verifies the two new routes exist on the copilot router and
    are CSRF-gated where they mutate state.
"""
from __future__ import annotations

import re
from pathlib import Path


REPO        = Path(__file__).resolve().parents[1]
SERVICE     = REPO / "console/app/services/proactive_service.py"
COPILOT_R   = REPO / "console/app/routers/copilot.py"
MIGRATION50 = REPO / "infra/init/50_copilot_briefing_dismissed.sql"


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


# ── Service module ───────────────────────────────────────────────────────


def test_proactive_service_exists():
    assert SERVICE.exists()


def test_proactive_service_exports_four_analyzers_plus_aggregator():
    src = _read(SERVICE)
    for fn in (
        "analyze_freshness",
        "analyze_volume_anomaly",
        "analyze_pending_actions",
        "analyze_extraction_failures",
        "briefing_for_user",
        "dismiss_highlight",
    ):
        assert f"async def {fn}" in src, (
            f"proactive_service must export {fn}"
        )


def test_freshness_analyzer_uses_correct_column():
    """v1.44.1 R1 caught a wrong-column-name bug in the dashboard.
    Lock the freshness analyzer to ``finished_at`` + ``status='success'``
    so a future copy-paste doesn't repeat history."""
    src = _read(SERVICE)
    block = re.search(
        r"async def analyze_freshness.*?(?=^async def|\Z)",
        src, re.DOTALL | re.MULTILINE,
    )
    assert block, "analyze_freshness body not found"
    body = block.group(0)
    assert "MAX(finished_at)" in body
    assert "status = 'success'" in body
    assert "FROM extraction_runs" in body


def test_volume_analyzer_uses_records_extracted():
    """The volume heuristic compares records_extracted (sum per day)
    not row count of extraction_runs — a single huge run shouldn't
    be diluted by a same-day tiny run."""
    src = _read(SERVICE)
    block = re.search(
        r"async def analyze_volume_anomaly.*?(?=^async def|\Z)",
        src, re.DOTALL | re.MULTILINE,
    )
    body = block.group(0) if block else ""
    assert "records_extracted" in body
    # The ±30% threshold the brief documents.
    assert "_VOLUME_DELTA_PCT" in src
    assert "30" in src


def test_pending_actions_uses_jobs_table_running_state():
    """Heuristic: jobs in 'running' for > 24h. Lock the table +
    state literal so the analyzer can't drift away from the v1.43.3
    schema (migration 46 made jobs cartridge-shared)."""
    src = _read(SERVICE)
    block = re.search(
        r"async def analyze_pending_actions.*?(?=^async def|\Z)",
        src, re.DOTALL | re.MULTILINE,
    )
    body = block.group(0) if block else ""
    assert "FROM jobs" in body
    assert "status = 'running'" in body
    assert "INTERVAL '24 hours'" in body


def test_failures_analyzer_buckets_by_count():
    """≥3 failures → critical; 1-2 → warning. The threshold lives
    in the analyzer body — lock the literals so the briefing severity
    spectrum stays stable."""
    src = _read(SERVICE)
    block = re.search(
        r"async def analyze_extraction_failures.*?(?=^async def|\Z)",
        src, re.DOTALL | re.MULTILINE,
    )
    body = block.group(0) if block else ""
    assert "status = 'failed'" in body
    assert "failures >= 3" in body
    assert '"critical"' in body and '"warning"' in body


def test_briefing_for_user_dedupes_dismissed():
    """The aggregator must filter out highlight_ids the user already
    dismissed. Static check on the SQL query — must reference
    copilot_briefing_dismissed + user_id parameter."""
    src = _read(SERVICE)
    block = re.search(
        r"async def briefing_for_user.*?(?=^async def|\Z)",
        src, re.DOTALL | re.MULTILINE,
    )
    body = block.group(0) if block else ""
    assert "copilot_briefing_dismissed" in body
    assert "WHERE user_id = $1" in body


def test_briefing_filters_cartridges_by_user_context():
    src = _read(SERVICE)
    assert "allowed_cartridges" in src
    assert "_filter_visible_highlights" in src
    assert "user_context: dict | None = None" in src


def test_briefing_for_user_caps_results():
    """Brief documents up to 6 highlights. Lock the default."""
    src = _read(SERVICE)
    assert "limit: int = 6" in src
    block = re.search(
        r"async def briefing_for_user.*?(?=^async def|\Z)",
        src, re.DOTALL | re.MULTILINE,
    )
    body = block.group(0) if block else ""
    assert "[:limit]" in body, (
        "briefing_for_user must truncate to ``limit``"
    )


def test_briefing_severity_sort_order():
    """Critical first, then warning, then info. Lock the constant."""
    src = _read(SERVICE)
    assert "_SEVERITY_ORDER" in src
    assert re.search(
        r'"critical":\s*0.*"warning":\s*1.*"info":\s*2',
        src, re.DOTALL,
    ), "severity order must be critical < warning < info"


def test_dismiss_highlight_uses_on_conflict():
    """Idempotent dismissal — repeated POSTs are no-ops."""
    src = _read(SERVICE)
    block = re.search(
        r"async def dismiss_highlight.*?(?=^async def|\Z)",
        src, re.DOTALL | re.MULTILINE,
    )
    body = block.group(0) if block else ""
    assert "ON CONFLICT (user_id, highlight_id) DO NOTHING" in body


def test_highlight_shape_includes_all_documented_keys():
    """A highlight is rendered as a card; missing any key makes the
    UI render an empty slot. Lock the documented shape."""
    src = _read(SERVICE)
    for key in ('"id"', '"severity"', '"title"', '"body"', '"category"',
                '"cartridge"', '"action_label"', '"action_href"'):
        assert key in src, f"_make_highlight must include {key}"


def test_cartridge_actions_use_next_viewer_route():
    src = _read(SERVICE)
    assert "def _cartridge_href" in src
    assert '"/cartridges/viewer?id=' in src
    assert 'action_href=f"/cartridges/{cart}"' not in src


# ── Router ──────────────────────────────────────────────────────────────


def test_copilot_router_declares_get_briefing():
    src = _read(COPILOT_R)
    assert '@router.get("/briefing")' in src
    assert "async def get_briefing" in src


def test_copilot_router_declares_post_dismiss_briefing():
    src = _read(COPILOT_R)
    assert '@router.post(\n    "/briefing/{highlight_id}/dismiss"' in src
    # The dismiss handler must be CSRF-gated AND audit-logged.
    block_start = src.find('"/briefing/{highlight_id}/dismiss"')
    nxt = src.find("async def", block_start)
    decorator = src[block_start:nxt]
    assert "require_csrf" in decorator
    handler_block = src[nxt:nxt + 2000]
    assert "audit_service.record_event" in handler_block
    assert "copilot.briefing.dismiss" in handler_block


def test_dismiss_endpoint_validates_highlight_id():
    """Empty / too-long IDs would let a malformed POST insert a row
    that mass-dismisses everything when iterated later. Reject at
    the boundary."""
    src = _read(COPILOT_R)
    handler_start = src.find("async def dismiss_briefing")
    handler = src[handler_start:handler_start + 1200]
    assert "if not highlight_id" in handler
    assert "200" in handler  # max-length cap


# ── Migration 50 ────────────────────────────────────────────────────────


def test_migration_50_exists():
    assert MIGRATION50.exists()


def test_migration_50_creates_table_with_composite_pk():
    src = _read(MIGRATION50)
    assert "CREATE TABLE IF NOT EXISTS copilot_briefing_dismissed" in src
    assert "PRIMARY KEY (user_id, highlight_id)" in src


def test_migration_50_creates_reverse_index():
    src = _read(MIGRATION50)
    assert "CREATE INDEX IF NOT EXISTS idx_copilot_briefing_dismissed_highlight" in src


def test_migration_50_self_registers_with_filename_column():
    src = _read(MIGRATION50)
    assert "INSERT INTO schema_migrations" in src
    assert "'50_copilot_briefing_dismissed.sql'" in src
    # v1.43.3 lesson: column is ``filename``, not ``migration_name``.
    insert = re.search(r"INSERT INTO schema_migrations.*?;", src, re.DOTALL)
    assert insert and "filename" in insert.group(0)
    assert insert and "migration_name" not in insert.group(0)


def test_migration_50_cascades_on_user_delete():
    """If a user is GDPR-deleted, their dismissals should go with
    them — no orphans clinging to a non-existent user_id."""
    src = _read(MIGRATION50)
    assert "REFERENCES users(id) ON DELETE CASCADE" in src


def test_migration_50_ordering():
    init = REPO / "infra/init"
    names = sorted(p.name for p in init.glob("*.sql"))
    assert names.index("49_dashboard_kpi_indexes.sql") < names.index(
        "50_copilot_briefing_dismissed.sql"
    )
