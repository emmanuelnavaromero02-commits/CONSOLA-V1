from __future__ import annotations

from pathlib import Path


REPO = Path(__file__).resolve().parents[1]


def test_refresh_route_has_no_non_atomic_fallback() -> None:
    for path in (
        REPO / "console" / "app" / "main.py",
        REPO / "console" / "app" / "routers" / "v1" / "auth.py",
    ):
        source = path.read_text(encoding="utf-8")
        start = source.index("async def auth_refresh")
        end = source.index("async def", start + len("async def"))
        body = source[start:end]
        assert "rotate_refresh_token" in body
        assert '_auth.hash_refresh_token(refresh_token or "")[:16]' in body
        assert 'subject = (refresh_token or "")[:16]' not in body
        assert "get_refresh_token_user" not in body
        assert "create_refresh_token" not in body
        assert "revoke_refresh_token" not in body
