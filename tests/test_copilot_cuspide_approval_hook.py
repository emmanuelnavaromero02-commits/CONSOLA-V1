from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock

import pytest


_SIBLINGS = ("/cartridges/", "/refinement", "/vault", "/workspace", "/mcp-infra")


@pytest.fixture
def lessons_mod():
    repo = Path(__file__).resolve().parents[1]
    sys.path[:] = [p for p in sys.path if not any(s in p for s in _SIBLINGS)]
    sys.path.insert(0, str(repo / "console"))
    for name in list(sys.modules):
        if name == "app" or name.startswith("app."):
            del sys.modules[name]
    from app.services import lessons_service as mod
    from app.services._copilot_helpers import reset_table_cache
    reset_table_cache()
    return mod


class FakeRecord(dict):
    pass


class FakePool:
    def __init__(self):
        self._fetchval_queue: list[Any] = []
        self._fetchrow_queue: list[Any] = []
        self.execs: list[tuple[str, tuple]] = []
        self.fetched_rows: list[FakeRecord] = []

    async def fetchval(self, sql, *args):
        return self._fetchval_queue.pop(0) if self._fetchval_queue else None

    async def fetchrow(self, sql, *args):
        return self._fetchrow_queue.pop(0) if self._fetchrow_queue else None

    async def fetch(self, sql, *args):
        return self.fetched_rows

    async def execute(self, sql, *args):
        self.execs.append((sql, args))
        return "UPDATE 1"


def test_record_lesson_from_approval_writes_expected_row(lessons_mod, monkeypatch):
    fake = FakePool()
    fake._fetchval_queue = [
        "copilot_lessons",
        None,
    ]
    fake._fetchrow_queue = [FakeRecord(id="lesson-from-approval")]
    monkeypatch.setattr(lessons_mod.auth, "pool", AsyncMock(return_value=fake))

    out = asyncio.run(
        lessons_mod.record_lesson_from_approval(
            user_id=42,
            workspace_id="b1f1c1b0-1111-2222-3333-444444444444",
            tool_name="replicon.create_dag",
            tool_args={"name": "monthly_close", "schedule": "0 0 1 * *"},
            conversation_id="c1c1c1c1-1111-2222-3333-444444444444",
        )
    )
    assert out == "lesson-from-approval"


def test_record_lesson_from_decline_writes_expected_row(lessons_mod, monkeypatch):
    fake = FakePool()
    fake._fetchval_queue = ["copilot_lessons", None]
    fake._fetchrow_queue = [FakeRecord(id="lesson-from-decline")]
    monkeypatch.setattr(lessons_mod.auth, "pool", AsyncMock(return_value=fake))

    out = asyncio.run(
        lessons_mod.record_lesson_from_decline(
            user_id=42,
            workspace_id=None,
            tool_name="replicon.delete_dag",
            tool_args={"name": "monthly_close"},
            reason="No quiero borrar todavía",
        )
    )
    assert out == "lesson-from-decline"


def test_record_lesson_from_approval_idempotent_within_dedupe_window(
    lessons_mod, monkeypatch,
):
    fake = FakePool()
    fake._fetchval_queue = [
        "copilot_lessons",
        "previous-lesson-id",
    ]
    monkeypatch.setattr(lessons_mod.auth, "pool", AsyncMock(return_value=fake))

    out = asyncio.run(
        lessons_mod.record_lesson_from_approval(
            user_id=42,
            workspace_id=None,
            tool_name="replicon.create_dag",
            tool_args={"name": "monthly_close"},
        )
    )
    assert out == "previous-lesson-id"
