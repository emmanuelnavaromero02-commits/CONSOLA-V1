from __future__ import annotations

import hashlib
import json
import re
import uuid
from dataclasses import dataclass
from typing import Any

_RUN_NAMESPACE = uuid.UUID("9dbb39ad-9c45-4acc-aa6a-19dc2207ded8")
_DATASET_NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,127}$")


def canonical_digest(value: Any) -> str:
    payload = json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, default=str
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


@dataclass(frozen=True)
class PublicationScope:
    tenant_id: str
    workspace_id: str
    dataset: str
    layer: str

    @classmethod
    def from_dataset(
        cls, dataset: dict[str, Any], user_context: dict[str, Any] | None
    ) -> PublicationScope:
        context = user_context or {}
        tenant = str(context.get("tenant_id") or "").strip()
        workspace = str(context.get("workspace_id") or "").strip()
        name = str(dataset.get("name") or "").strip()
        layer = str(dataset.get("layer") or "silver").strip().lower()
        if not tenant or not workspace:
            raise ValueError("Materialization requires tenant_id and workspace_id")
        uuid.UUID(tenant)
        uuid.UUID(workspace)
        if not _DATASET_NAME.fullmatch(name):
            raise ValueError("Invalid materialization dataset")
        if layer not in {"silver", "gold"}:
            raise ValueError("Invalid materialization layer")
        return cls(tenant, workspace, name, layer)


@dataclass(frozen=True)
class PublicationIdentity:
    scope: PublicationScope
    materialization_run_id: uuid.UUID
    input_digest: str
    contract_digest: str
    expected_head_run_id: str | None

    @classmethod
    def build(
        cls,
        dataset: dict[str, Any],
        user_context: dict[str, Any] | None,
        *,
        input_state: list[dict[str, Any]] | None = None,
        expected_head_run_id: str | None = None,
    ) -> PublicationIdentity:
        scope = PublicationScope.from_dataset(dataset, user_context)
        inputs = {
            "sources": dataset.get("sources") or [],
            "sql": dataset.get("sql_def") or dataset.get("sql") or "",
            "source_load_date": dataset.get("source_load_date"),
            "source_batch_id": dataset.get("source_batch_id"),
            "resolved_source_state": input_state or [],
        }
        contract = {
            "dataset": scope.dataset,
            "layer": scope.layer,
            "cartridge": dataset.get("cartridge") or "unknown",
            "column_mapping": dataset.get("column_mapping") or {},
            # v2: raw inputs are fingerprinted by {key, size, etag} from the
            # listing instead of by a SHA-256 of every object's bytes. The
            # digest is not comparable across the two, so the version is part
            # of the contract and every dataset re-materializes once.
            "publication_contract": "staged-cas/v2",
        }
        input_digest = canonical_digest(inputs)
        contract_digest = canonical_digest(contract)
        run_key = ":".join(
            (
                scope.tenant_id,
                scope.workspace_id,
                scope.dataset,
                scope.layer,
                input_digest,
                contract_digest,
                expected_head_run_id or "empty",
            )
        )
        return cls(
            scope,
            uuid.uuid5(_RUN_NAMESPACE, run_key),
            input_digest,
            contract_digest,
            expected_head_run_id,
        )
