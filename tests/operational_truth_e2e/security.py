from __future__ import annotations

import hashlib
import hmac
import json
import os
import time
import uuid
from typing import Any

import requests

from runtime.runtime_security_context import build_materialize_context


REFINEMENT = os.environ["REFINEMENT_URL"].rstrip("/")
MCP_INFRA = os.environ["MCP_INFRA_URL"].rstrip("/")


def _headers(target: str) -> dict[str, str]:
    return {
        "X-Internal-Service": "airflow",
        "X-API-Key": os.environ[f"INTERNAL_API_KEY_AIRFLOW_TO_{target}"],
    }


def _materialize_context(
    scope: dict[str, str], *, run_id: str, now: int | None = None
) -> dict[str, Any]:
    return build_materialize_context(
        tenant_id=scope["tenant_id"],
        workspace_id=scope["workspace_id"],
        cartridge_id="replicon",
        dataset_name="pnl_mensual",
        run_id=run_id,
        now=now,
    )


def _materialize(context: dict[str, Any]) -> requests.Response:
    return requests.post(
        f"{REFINEMENT}/mcp/invoke",
        json={
            "tool": "materialize",
            "args": {"name": "pnl_mensual"},
            "security_context": context,
        },
        headers=_headers("REFINEMENT"),
        timeout=60,
    )


def assert_refinement_rejects_bad_signatures(scope: dict[str, str]) -> None:
    invalid = _materialize_context(scope, run_id=f"bad-{uuid.uuid4().hex}")
    invalid["_signature"] = "0" * 64
    expired = _materialize_context(
        scope,
        run_id=f"expired-{uuid.uuid4().hex}",
        now=int(time.time()) - 600,
    )
    for context in (invalid, expired):
        assert _materialize(context).status_code == 403


def assert_refinement_hmac_replay_rejected(scope: dict[str, str]) -> None:
    run_id = f"hmac-replay-{uuid.uuid4().hex}"
    context = _materialize_context(scope, run_id=run_id)
    first = _materialize(context)
    assert first.status_code == 200, first.text[:500]
    assert _materialize(context).status_code == 403
    fresh = _materialize(_materialize_context(scope, run_id=run_id))
    assert fresh.status_code == 200, fresh.text[:500]


def _sign_mcp_context(scope: dict[str, str]) -> dict[str, Any]:
    marker = f"tenant_id={scope['tenant_id']}/workspace_id={scope['workspace_id']}/"
    context: dict[str, Any] = {
        "trusted": True,
        "source": "airflow",
        "role": "admin",
        "tenant_id": scope["tenant_id"],
        "workspace_id": scope["workspace_id"],
        "permissions": ["datasets.read"],
        "allowed_cartridges": ["replicon"],
        "allowed_buckets": ["lakehouse"],
        "allowed_prefixes": [
            "raw/replicon/",
            f"silver/replicon/{marker}",
            f"gold/replicon/{marker}",
        ],
        "_signed_at": int(time.time()),
        "_signature_version": "hmac-sha256-v1",
    }
    canonical = json.dumps(context, sort_keys=True, separators=(",", ":")).encode()
    context["_signature"] = hmac.new(
        os.environ["SECURITY_CONTEXT_SIGNING_KEY"].encode(),
        canonical,
        hashlib.sha256,
    ).hexdigest()
    return context


def signed_pipeline_trigger_context(scope: dict[str, str]) -> dict[str, Any]:
    context = _sign_mcp_context(scope)
    context["permissions"] = ["pipelines.run"]
    context.pop("_signature", None)
    canonical = json.dumps(context, sort_keys=True, separators=(",", ":")).encode()
    context["_signature"] = hmac.new(
        os.environ["SECURITY_CONTEXT_SIGNING_KEY"].encode(),
        canonical,
        hashlib.sha256,
    ).hexdigest()
    return context


def assert_mcp_raw_cross_tenant_denied(
    allowed: dict[str, str], foreign: dict[str, str]
) -> None:
    context = _sign_mcp_context(allowed)
    prefixes = {
        "allowed": (
            "raw/replicon/OperationalTruthProbe/"
            f"tenant_id={allowed['tenant_id']}/workspace_id={allowed['workspace_id']}/"
        ),
        "foreign": (
            "raw/replicon/OperationalTruthProbe/"
            f"tenant_id={foreign['tenant_id']}/workspace_id={foreign['workspace_id']}/"
        ),
    }
    response = requests.post(
        f"{MCP_INFRA}/mcp/invoke",
        json={
            "tool": "minio_list_objects",
            "args": {"prefix": prefixes["allowed"], "bucket": "lakehouse"},
            "security_context": context,
        },
        headers=_headers("MCP_INFRA"),
        timeout=30,
    )
    assert response.status_code == 200, response.text[:500]
    objects = response.json()["result"]["objects"]
    assert objects and all(prefixes["allowed"] in item["name"] for item in objects)
    denied = requests.post(
        f"{MCP_INFRA}/mcp/invoke",
        json={
            "tool": "minio_list_objects",
            "args": {"prefix": prefixes["foreign"], "bucket": "lakehouse"},
            "security_context": context,
        },
        headers=_headers("MCP_INFRA"),
        timeout=30,
    )
    assert denied.status_code in {403, 404}


def assert_mcp_unpublished_object_hidden(
    scope: dict[str, str], object_key: str
) -> None:
    response = requests.post(
        f"{MCP_INFRA}/mcp/invoke",
        json={
            "tool": "minio_get_parquet_schema",
            "args": {"object_path": object_key, "bucket": "lakehouse"},
            "security_context": _sign_mcp_context(scope),
        },
        headers=_headers("MCP_INFRA"),
        timeout=30,
    )
    assert response.status_code != 200
    assert object_key not in response.text
