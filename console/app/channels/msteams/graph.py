"""Microsoft Graph access — minimal, app-only, file-download surface.

This is the ONLY place the Teams channel talks to Graph. It is deliberately
small: app-only token acquisition + a size-capped download. No SDK is pulled
in — ``httpx`` is already a console dependency and the surface is too narrow
to warrant ``msgraph-sdk-python``.

Tokens
------
We use the OAuth 2.0 client_credentials flow against the tenant-specific
token endpoint, with the channel's existing ``MSTEAMS_APP_ID`` /
``MSTEAMS_APP_PASSWORD`` / ``MSTEAMS_TENANT_ID``. Tokens are cached
in-process keyed by tenant; the cache is purely a perf optimisation — every
call still re-validates the expiry with a 60 s safety window.

Failure model
-------------
Every public function returns a ``GraphResult`` rather than raising — the
webhook path must never 500 on a Graph hiccup. Specific failure reasons
(``token_unavailable``, ``download_too_large``, ``download_http_<code>``,
``download_failed``) are surfaced for audit clarity.
"""
from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass
from typing import Any

import httpx

from .config import MsTeamsConfig

logger = logging.getLogger("msteams.graph")

_TOKEN_ENDPOINT_TMPL = "https://login.microsoftonline.com/{tenant}/oauth2/v2.0/token"
_GRAPH_DEFAULT_SCOPE = "https://graph.microsoft.com/.default"

# In-process token cache. Keyed by (tenant_id, app_id) — a redeploy or restart
# wipes it, which is fine: token TTL is ~1h and re-acquisition is a single
# POST. Concurrency is guarded by an asyncio lock per cache entry so a burst
# of webhook requests acquires the token ONCE, not N times.
_TokenCacheEntry = tuple[str, int]  # (token, expires_at_unix)
_token_cache: dict[tuple[str, str], _TokenCacheEntry] = {}
_token_locks: dict[tuple[str, str], asyncio.Lock] = {}
_TOKEN_EXPIRY_SKEW_SEC = 60  # refresh slightly before real expiry


@dataclass(frozen=True)
class GraphResult:
    """Outcome envelope for Graph operations.

    ``ok=True`` means the call succeeded and ``content`` carries the payload.
    ``ok=False`` carries a stable ``reason`` string for audit (never an
    exception message — those can leak internals).
    """
    ok: bool
    reason: str = ""
    content: bytes | None = None
    content_type: str | None = None


async def get_app_token(cfg: MsTeamsConfig) -> str | None:
    """Return a cached app-only Graph token, fetching one if needed.

    Returns ``None`` (not an exception) when the token cannot be obtained
    — callers are expected to degrade gracefully (skip the file, audit the
    reason). Misconfiguration (missing app id / password / tenant) is the
    most common cause; Graph errors are the second.
    """
    if not (cfg.app_id and cfg.app_password and cfg.tenant_id):
        return None
    cache_key = (cfg.tenant_id, cfg.app_id)
    cached = _token_cache.get(cache_key)
    now = int(time.time())
    if cached and cached[1] - _TOKEN_EXPIRY_SKEW_SEC > now:
        return cached[0]

    lock = _token_locks.setdefault(cache_key, asyncio.Lock())
    async with lock:
        # Re-check inside the lock — another coroutine may have refreshed
        # while we were waiting (thundering-herd avoidance).
        cached = _token_cache.get(cache_key)
        now = int(time.time())
        if cached and cached[1] - _TOKEN_EXPIRY_SKEW_SEC > now:
            return cached[0]

        url = _TOKEN_ENDPOINT_TMPL.format(tenant=cfg.tenant_id)
        data = {
            "grant_type": "client_credentials",
            "client_id": cfg.app_id,
            "client_secret": cfg.app_password,
            "scope": _GRAPH_DEFAULT_SCOPE,
        }
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                resp = await client.post(url, data=data)
        except Exception:
            logger.warning("msteams.graph: token endpoint unreachable", exc_info=True)
            return None
        if resp.status_code != 200:
            # Don't log the response body — it may carry tenant ids in a
            # Microsoft-formatted error envelope. Log the code only.
            logger.warning("msteams.graph: token endpoint returned %d", resp.status_code)
            return None
        try:
            payload: dict[str, Any] = resp.json()
        except Exception:
            return None
        token = payload.get("access_token")
        expires_in = payload.get("expires_in", 0)
        if not isinstance(token, str) or not isinstance(expires_in, int):
            return None
        _token_cache[cache_key] = (token, now + expires_in)
        return token


async def download_attachment(
    url: str, *, cfg: MsTeamsConfig, max_bytes: int,
) -> GraphResult:
    """Download an attachment by Graph-compatible URL with a hard size cap.

    The cap is enforced at TWO layers: a ``Content-Length`` check before
    reading (cheap reject for honest senders) AND a streaming read that
    bails the moment we cross the cap (defeats a sender lying about
    content length). A genuinely chunked / unknown-length response is read
    in capped chunks the same way.
    """
    if not url:
        return GraphResult(ok=False, reason="download_no_url")
    token = await get_app_token(cfg)
    if not token:
        return GraphResult(ok=False, reason="token_unavailable")
    headers = {"Authorization": f"Bearer {token}"}
    try:
        async with httpx.AsyncClient(timeout=15.0, follow_redirects=True) as client:
            async with client.stream("GET", url, headers=headers) as resp:
                if resp.status_code != 200:
                    return GraphResult(
                        ok=False, reason=f"download_http_{resp.status_code}",
                    )
                declared = resp.headers.get("content-length")
                if declared and declared.isdigit() and int(declared) > max_bytes:
                    return GraphResult(ok=False, reason="download_too_large")
                content_type = resp.headers.get("content-type", "")
                # Stream-read with running cap so an attacker lying about
                # content-length (or omitting it) can't OOM us.
                buf = bytearray()
                async for chunk in resp.aiter_bytes(chunk_size=64 * 1024):
                    buf.extend(chunk)
                    if len(buf) > max_bytes:
                        return GraphResult(ok=False, reason="download_too_large")
                return GraphResult(
                    ok=True, content=bytes(buf), content_type=content_type,
                )
    except Exception:
        logger.warning("msteams.graph: download failed", exc_info=True)
        return GraphResult(ok=False, reason="download_failed")


def _reset_token_cache_for_tests() -> None:
    """Test-only: clear the in-process token cache so a unit test can
    re-exercise the acquisition path deterministically."""
    _token_cache.clear()
    _token_locks.clear()
