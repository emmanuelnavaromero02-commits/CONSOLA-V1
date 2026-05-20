"""
AWS Bedrock embeddings (Titan Text Embeddings v2 by default).

Uses the model configured in EMBED_MODEL (default amazon.titan-embed-text-v2:0)
with output dimensionality EMBED_DIM (Titan v2 supports 256 / 512 / 1024).

boto3 is synchronous, so the public async interface wraps invoke_model calls in
asyncio.to_thread and keeps the existing embed_documents / embed_query contract.
"""
from __future__ import annotations

import asyncio
import json
import re

import boto3
from botocore.config import Config as BotoConfig
from botocore.exceptions import ClientError

from app.rag.config import BEDROCK_REGION, EMBED_DIM, EMBED_MODEL

_client = None

_MAX_CONCURRENCY = 8
_MAX_RETRIES = 4

_RETRY_DELAY_PATTERNS = [
    re.compile(r"retry\s*(?:in|after)\s+([\d.]+)\s*s", re.IGNORECASE),
]


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
            raise
        except Exception as exc:
            last_exc = exc
            if attempt < _MAX_RETRIES - 1:
                import time
                time.sleep(_parse_retry_delay(str(exc), attempt))
                continue
            raise
    raise last_exc or RuntimeError("Bedrock invoke failed without raising")


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


# Backwards-compatible aliases for older callers.
async def embed(texts: list[str]) -> list[list[float]]:
    return await embed_documents(texts)


async def embed_one(text: str) -> list[float]:
    return await embed_query(text)
