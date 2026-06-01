from __future__ import annotations

from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
READYZ_FILES = (
    REPO_ROOT / "console" / "app" / "main.py",
    REPO_ROOT / "console" / "app" / "routers" / "v1" / "system.py",
)


def _readyz_section(path: Path) -> str:
    src = path.read_text(encoding="utf-8")
    start = src.index("async def readyz(")
    end = src.index("async def api_config", start)
    return src[start:end]


def test_readyz_keeps_public_probe_but_redacts_dependency_topology():
    for path in READYZ_FILES:
        section = _readyz_section(path)
        assert 'body = {"ok": ok, "service": "console"}' in section
        assert 'getattr(request.state, "user", None)' in section
        assert 'body["checks"] = checks' in section
        assert '{"ok": ok, "service": "console", "checks": checks}' not in section
