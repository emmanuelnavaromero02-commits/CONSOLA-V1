from __future__ import annotations

from pathlib import Path

import pytest
from fastapi import HTTPException

from app.routers import pages


def test_control_room_file_serves_index_for_directory(monkeypatch, tmp_path: Path):
    root = tmp_path / "control-room"
    (root / "docs").mkdir(parents=True)
    index = root / "docs" / "index.html"
    index.write_text("ok", encoding="utf-8")
    monkeypatch.setattr(pages, "CONTROL_ROOM_STATIC", root)

    assert pages._control_room_file("docs") == index


def test_control_room_file_blocks_path_traversal(monkeypatch, tmp_path: Path):
    root = tmp_path / "control-room"
    root.mkdir()
    outside = tmp_path / "secret.txt"
    outside.write_text("secret", encoding="utf-8")
    monkeypatch.setattr(pages, "CONTROL_ROOM_STATIC", root)

    with pytest.raises(HTTPException) as exc:
        pages._control_room_file("../secret.txt")

    assert exc.value.status_code == 404


def test_control_room_file_reports_unbuilt_frontend(monkeypatch, tmp_path: Path):
    monkeypatch.setattr(pages, "CONTROL_ROOM_STATIC", tmp_path / "missing")

    with pytest.raises(HTTPException) as exc:
        pages._control_room_file()

    assert exc.value.status_code == 503
