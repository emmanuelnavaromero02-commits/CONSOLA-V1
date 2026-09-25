from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import math
import os
import re

import boto3
from botocore.config import Config as BotoConfig
from botocore.exceptions import BotoCoreError, ClientError, NoCredentialsError, NoRegionError

from app.rag.config import BEDROCK_REGION, EMBED_DIM, EMBED_MODEL

_client = None
_log = logging.getLogger(__name__)
_fallback_warned = False
_force_local_fallback = False

_MAX_CONCURRENCY = 8
_MAX_RETRIES = 4

_RETRY_DELAY_PATTERNS = [
    re.compile(r"retry\s*(?:in|after)\s+([\d.]+)\s*s", re.IGNORECASE),
]


class EmbeddingProviderError(RuntimeError):
    pass


def _local_fallback_enabled() -> bool:
    raw = os.environ.get("RAG_LOCAL_EMBEDDING_FALLBACK", "true").strip().lower()
    return raw in {"1", "true", "yes", "on", "auto"}


def _local_embedding(text: str) -> list[float]:
    dim = max(1, EMBED_DIM)
    vec = [0.0] * dim
    tokens = re.findall(r"[a-z0-9_./:-]+", (text or "").lower())
    if not tokens:
        tokens = [text or ""]
    for token in tokens:
        units = [token]
        if len(token) > 3:
            units.extend(token[idx : idx + 3] for idx in range(len(token) - 2))
        for unit in units:
            digest = hashlib.sha256(unit.encode("utf-8", errors="ignore")).digest()
            idx = int.from_bytes(digest[:8], "big") % dim
            sign = 1.0 if digest[8] & 1 else -1.0
            vec[idx] += sign
    norm = math.sqrt(sum(value * value for value in vec)) or 1.0
    return [value / norm for value in vec]


def _provider_error_message(exc: Exception) -> str:
    if isinstance(exc, (NoCredentialsError, NoRegionError)):
        return (
            "Bedrock embeddings are not configured. Set AWS credentials and "
            "BEDROCK_REGION/EMBED_MODEL for RAG indexing/search."
        )
    if isinstance(exc, ClientError):
        code = exc.response.get("Error", {}).get("Code", "ClientError")
        return f"Bedrock embeddings failed with {code}: {exc.response.get('Error', {}).get('Message', str(exc))}"
    return f"Bedrock embeddings failed: {exc}"


def _fallback_or_raise(text: str, exc: Exception) -> list[float]:
    global _fallback_warned, _force_local_fallback
    message = _provider_error_message(exc)
    if not _local_fallback_enabled():
        raise EmbeddingProviderError(message) from exc
    _force_local_fallback = True
    if not _fallback_warned:
        _log.warning("RAG local embedding fallback enabled because Bedrock failed: %s", message)
        _fallback_warned = True
    return _local_embedding(text)


def _bedrock_client():
    global _client
    if _client is None:
        _client = boto3.client(
            "bedrock-runtime",
            config=BotoConfig(
                region_name=BEDROCK_REGION,
                retries={"max_attempts": 5, "mode": "adaptive"},
                read_timeout=30,
                connect_timeout=10,
            ),
        )
    return _client


def _parse_retry_delay(msg: str, attempt: int) -> float:
    for pattern in _RETRY_DELAY_PATTERNS:
        match = pattern.search(msg)
        if match:
            return min(float(match.group(1)) + 1, 60)
    return min(2 ** attempt, 30)


def _invoke_sync(text: str) -> list[float]:
    if _force_local_fallback and _local_fallback_enabled():
        return _local_embedding(text)
    body = json.dumps({
        "inputText": text,
        "dimensions": EMBED_DIM,
        "normalize": True,
    })
    last_exc: Exception | None = None
    for attempt in range(_MAX_RETRIES):
        try:
            response = _bedrock_client().invoke_model(
                modelId=EMBED_MODEL,
                body=body,
                accept="application/json",
                contentType="application/json",
            )
            payload = json.loads(response["body"].read())
            return payload["embedding"]
        except ClientError as exc:
            code = exc.response.get("Error", {}).get("Code", "")
            retryable = code in {"ThrottlingException", "ServiceUnavailableException"}
            last_exc = exc
            if retryable and attempt < _MAX_RETRIES - 1:
                import time
                time.sleep(_parse_retry_delay(str(exc), attempt))
                continue
            return _fallback_or_raise(text, exc)
        except (NoCredentialsError, NoRegionError) as exc:
            return _fallback_or_raise(text, exc)
        except BotoCoreError as exc:
            last_exc = exc
            if attempt < _MAX_RETRIES - 1:
                import time
                time.sleep(_parse_retry_delay(str(exc), attempt))
                continue
            return _fallback_or_raise(text, exc)
        except Exception as exc:
            last_exc = exc
            if attempt < _MAX_RETRIES - 1:
                import time
                time.sleep(_parse_retry_delay(str(exc), attempt))
                continue
            return _fallback_or_raise(text, exc)
    return _fallback_or_raise(text, last_exc or RuntimeError("Bedrock invoke failed without raising"))


async def _invoke(text: str) -> list[float]:
    return await asyncio.to_thread(_invoke_sync, text)


async def embed_documents(texts: list[str]) -> list[list[float]]:
    if not texts:
        return []
    sem = asyncio.Semaphore(_MAX_CONCURRENCY)

    async def _one(text: str) -> list[float]:
        async with sem:
            return await _invoke(text)

    return await asyncio.gather(*(_one(text) for text in texts))


async def embed_query(text: str) -> list[float]:
    return await _invoke(text)


async def embed(texts: list[str]) -> list[list[float]]:
    return await embed_documents(texts)


async def embed_one(text: str) -> list[float]:
    return await embed_query(text)
