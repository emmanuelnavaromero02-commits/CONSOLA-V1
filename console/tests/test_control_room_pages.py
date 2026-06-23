from __future__ import annotations

import base64
import hashlib
from pathlib import Path

import pytest
from fastapi import HTTPException
from starlette.requests import Request

from app.routers import pages


def _request(path: str = "/control-room") -> Request:
    return Request({"type": "http", "method": "GET", "path": path, "headers": []})


def test_control_room_uses_console_next_index_for_directory(monkeypatch, tmp_path: Path):
    root = tmp_path / "console-next"
    (root / "docs").mkdir(parents=True)
    index = root / "docs" / "index.html"
    index.write_text("ok", encoding="utf-8")
    monkeypatch.setattr(pages, "CONSOLE_NEXT_STATIC", root)

    assert pages._console_next_file("docs") == index


def test_control_room_console_next_file_blocks_path_traversal(monkeypatch, tmp_path: Path):
    root = tmp_path / "console-next"
    root.mkdir()
    outside = tmp_path / "secret.txt"
    outside.write_text("secret", encoding="utf-8")
    monkeypatch.setattr(pages, "CONSOLE_NEXT_STATIC", root)

    with pytest.raises(HTTPException) as exc:
        pages._console_next_file("../secret.txt")

    assert exc.value.status_code == 404


def test_control_room_console_next_file_reports_unbuilt_frontend(monkeypatch, tmp_path: Path):
    monkeypatch.setattr(pages, "CONSOLE_NEXT_STATIC", tmp_path / "missing")

    with pytest.raises(HTTPException) as exc:
        pages._console_next_file()

    assert exc.value.status_code == 503


def _sha256_csp_hash(body: str) -> str:
    digest = hashlib.sha256(body.encode("utf-8")).digest()
    return f"'sha256-{base64.b64encode(digest).decode('ascii')}'"


def _script_src_segment(csp: str) -> str:
    return csp.split("style-src", 1)[0]


def test_native_control_room_page_hashes_inline_scripts(monkeypatch, tmp_path: Path):
    root = tmp_path / "console-next"
    control_room = root / "control-room"
    control_room.mkdir(parents=True)
    script_a = "self.__next_f=self.__next_f||[]"
    script_b = "self.__next_f.push([1,\"payload\"])"
    (control_room / "index.html").write_text(
        f"<html><body><script>{script_a}</script><script src=\"/control-room/app.js\"></script>"
        f"<script>{script_b}</script></body></html>",
        encoding="utf-8",
    )
    monkeypatch.setattr(pages, "CONSOLE_NEXT_STATIC", root)

    response = pages._console_next_response(_request(), "control-room/index.html")
    csp = response.headers["content-security-policy"]
    script_seg = _script_src_segment(csp)

    assert "script-src 'self'" in script_seg
    assert "'unsafe-inline'" not in script_seg
    assert _sha256_csp_hash(script_a) in script_seg
    assert _sha256_csp_hash(script_b) in script_seg
    assert "frame-ancestors 'none'" in csp


def test_native_control_room_talent_page_hashes_inline_scripts(monkeypatch, tmp_path: Path):
    root = tmp_path / "console-next"
    control_room_talent = root / "control-room" / "talent"
    control_room_talent.mkdir(parents=True)
    script_a = "self.__next_f=self.__next_f||[]"
    script_b = "self.__next_f.push([1,\"talent\"])"
    (control_room_talent / "index.html").write_text(
        f"<html><body><script>{script_a}</script><script src=\"/control-room/talent/app.js\"></script>"
        f"<script>{script_b}</script></body></html>",
        encoding="utf-8",
    )
    monkeypatch.setattr(pages, "CONSOLE_NEXT_STATIC", root)

    response = pages._console_next_response(
        _request("/control-room/talent"), "control-room/talent/index.html"
    )
    csp = response.headers["content-security-policy"]
    script_seg = _script_src_segment(csp)

    assert "script-src 'self'" in script_seg
    assert "'unsafe-inline'" not in script_seg
    assert _sha256_csp_hash(script_a) in script_seg
    assert _sha256_csp_hash(script_b) in script_seg
    assert "frame-ancestors 'none'" in csp


def test_control_room_export_hashes_every_inline_script():
    page = pages.CONSOLE_NEXT_STATIC / "control-room" / "index.html"
    html = page.read_text(encoding="utf-8")
    inline_scripts = [body for body in pages._INLINE_SCRIPT_RE.findall(html) if body.strip()]

    assert inline_scripts, "Control Room Next export should expose hashable hydration scripts"

    csp = pages._console_next_csp(str(page))
    script_seg = _script_src_segment(csp)

    assert "'unsafe-inline'" not in script_seg
    for body in inline_scripts:
        assert _sha256_csp_hash(body) in script_seg


def test_control_room_talent_export_hashes_every_inline_script():
    page = pages.CONSOLE_NEXT_STATIC / "control-room" / "talent" / "index.html"
    html = page.read_text(encoding="utf-8")
    inline_scripts = [body for body in pages._INLINE_SCRIPT_RE.findall(html) if body.strip()]

    assert inline_scripts, "Control Room Talent Next export should expose hashable hydration scripts"

    csp = pages._console_next_csp(str(page))
    script_seg = _script_src_segment(csp)

    assert "'unsafe-inline'" not in script_seg
    for body in inline_scripts:
        assert _sha256_csp_hash(body) in script_seg
