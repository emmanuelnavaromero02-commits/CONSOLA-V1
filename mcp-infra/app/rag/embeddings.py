"""
Gemini text embeddings via google-genai SDK.

Uses the model configured in EMBED_MODEL (default gemini-embedding-001) with:
  - outputDimensionality=EMBED_DIM (MRL truncation, server-side, fits HNSW indexes)
  - task_type=RETRIEVAL_DOCUMENT for documents (improves recall)
  - task_type=RETRIEVAL_QUERY for queries (asymmetric retrieval)

Batching strategy:
  - Splits inputs by both item count (≤_BATCH_MAX_ITEMS) and total chars
    (≤_BATCH_CHAR_BUDGET) so long chunks don't overflow the per-request
    token limit even when the item count is fine.
  - Runs up to _MAX_CONCURRENCY batches in parallel; 429s are caught and
    retried with backoff parsed from the error payload.
"""
from __future__ import annotations

import asyncio
import re

from google import genai
from google.genai import types as gtypes

from app.rag.config import GEMINI_API_KEY, EMBED_MODEL, EMBED_DIM

_client: genai.Client | None = None


def _gemini_client() -> genai.Client:
    global _client
    if _client is None:
        api_key = (GEMINI_API_KEY or "").strip()
        if not api_key or api_key == "dummy-local-key":
            raise RuntimeError("GEMINI_API_KEY is required to generate RAG embeddings")
        _client = genai.Client(api_key=api_key)
    return _client

_DOC_CFG = gtypes.EmbedContentConfig(
    outputDimensionality=EMBED_DIM,
    taskType="RETRIEVAL_DOCUMENT",
)
_QUERY_CFG = gtypes.EmbedContentConfig(
    outputDimensionality=EMBED_DIM,
    taskType="RETRIEVAL_QUERY",
)

_BATCH_MAX_ITEMS   = 100       # Gemini embed_content per-request item cap
_BATCH_CHAR_BUDGET = 80_000    # ≈20k tokens — safe under per-request token limit
_MAX_CONCURRENCY   = 4         # parallel batches; tune against your quota

# Match retryDelay across the shapes Google returns it in:
#   "retry in 5s" / "retry after 5 seconds"
#   retryDelay: "5s"  /  retry_delay: "5s"
#   retry_delay { seconds: 5 }   (proto text format)
_RETRY_PATTERNS = [
    re.compile(r"retry\s*(?:in|after)\s+([\d.]+)\s*s", re.IGNORECASE),
    re.compile(r"retry[_]?delay[\s:=\"']*([\d.]+)\s*s", re.IGNORECASE),
    re.compile(r"retry[_]?delay[^}]*?seconds:\s*([\d.]+)", re.IGNORECASE),
]


def _parse_retry_delay(msg: str, attempt: int) -> float:
    for pat in _RETRY_PATTERNS:
        m = pat.search(msg)
        if m:
            return min(float(m.group(1)) + 2, 90)
    return min(10 * (attempt + 1), 90)


async def _embed_with_retry(contents: list[str], cfg) -> list:
    """Single embed call with retry on 429."""
    for attempt in range(4):
        try:
            response = await _gemini_client().aio.models.embed_content(
                model=EMBED_MODEL, contents=contents, config=cfg,
            )
            return response.embeddings
        except Exception as exc:
            msg = str(exc)
            rate_limited = "429" in msg or "RESOURCE_EXHAUSTED" in msg
            if rate_limited and attempt < 3:
                await asyncio.sleep(_parse_retry_delay(msg, attempt))
                continue
            raise


def _plan_batches(texts: list[str]) -> list[tuple[int, int]]:
    """Greedy split into [start, end) ranges respecting item count + char budget."""
    batches: list[tuple[int, int]] = []
    start = 0
    chars = 0
    for i, t in enumerate(texts):
        item_len = len(t)
        if i > start and (
            i - start >= _BATCH_MAX_ITEMS
            or chars + item_len > _BATCH_CHAR_BUDGET
        ):
            batches.append((start, i))
            start = i
            chars = 0
        chars += item_len
    if start < len(texts):
        batches.append((start, len(texts)))
    return batches


async def embed_documents(texts: list[str]) -> list[list[float]]:
    """Embed document chunks for storage. task_type=RETRIEVAL_DOCUMENT."""
    if not texts:
        return []
    plans = _plan_batches(texts)
    sem = asyncio.Semaphore(_MAX_CONCURRENCY)

    async def _run(start: int, end: int):
        async with sem:
            return await _embed_with_retry(texts[start:end], _DOC_CFG)

    batch_results = await asyncio.gather(*(_run(s, e) for s, e in plans))
    out: list[list[float]] = []
    for embs in batch_results:
        out.extend(e.values for e in embs)
    return out


async def embed_query(text: str) -> list[float]:
    """Embed a search query. task_type=RETRIEVAL_QUERY. Retries on 429."""
    embeddings = await _embed_with_retry([text], _QUERY_CFG)
    return embeddings[0].values


# ── Backwards-compatible aliases (older callers) ──────────────────────────────
async def embed(texts: list[str]) -> list[list[float]]:
    return await embed_documents(texts)


async def embed_one(text: str) -> list[float]:
    return await embed_query(text)
