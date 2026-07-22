from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from app.services.control_room.business_runtime_evidence import (
    runtime_row_evidence_fields,
)


_RAW_EVIDENCE_FIELDS = frozenset(
    {
        "analysis_evidence",
        "evidence",
        "evidence_id",
        "evidence_pack",
        "evidence_pack_id",
        "evidence_refs",
    }
)
INVALID_SCOPE_STATUS = "invalid_scope"
_OBSERVED_AT_FIELDS = (
    "detected_at",
    "generated_at",
    "observation_date",
    "freshness_at",
    "period_key",
    "as_of",
    "mes",
    "semana",
    "spend_month",
)


def _text(value: Any) -> str:
    return str(value or "").strip()


def _without_raw_evidence(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {
            key: _without_raw_evidence(nested)
            for key, nested in value.items()
            if str(key) not in _RAW_EVIDENCE_FIELDS
        }
    if isinstance(value, list):
        return [_without_raw_evidence(entry) for entry in value]
    if isinstance(value, tuple):
        return tuple(_without_raw_evidence(entry) for entry in value)
    return value


def _declared_scope_values(value: Any, field: str) -> set[str]:
    if isinstance(value, Mapping):
        declared = {
            _text(nested)
            for key, nested in value.items()
            if str(key) == field and _text(nested)
        }
        for nested in value.values():
            declared.update(_declared_scope_values(nested, field))
        return declared
    if isinstance(value, (list, tuple)):
        declared: set[str] = set()
        for nested in value:
            declared.update(_declared_scope_values(nested, field))
        return declared
    return set()


def context_scope(context: Mapping[str, Any] | None) -> tuple[str, str]:
    values = context or {}
    tenant = values.get("active_tenant_id") or values.get("tenant_id")
    workspace = values.get("active_workspace_id") or values.get("workspace_id")
    return _text(tenant), _text(workspace)


def has_partial_scope(item: Mapping[str, Any]) -> bool:
    return bool(_text(item.get("tenant_id"))) != bool(_text(item.get("workspace_id")))


def scope_matches(
    item: Mapping[str, Any], *, tenant_id: str, workspace_id: str
) -> bool:
    expected = (_text(tenant_id), _text(workspace_id))
    actual = (_text(item.get("tenant_id")), _text(item.get("workspace_id")))
    return all(expected) and actual == expected


@dataclass(frozen=True)
class CommandScopeBoundary:
    tenant_id: str
    workspace_id: str
    diagnostic: dict[str, Any] | None = None

    def matches(self, item: Mapping[str, Any]) -> bool:
        return scope_matches(
            item,
            tenant_id=self.tenant_id,
            workspace_id=self.workspace_id,
        )


def _scope_diagnostic(
    item: Mapping[str, Any], *, tenant_id: str, workspace_id: str, reason: str
) -> dict[str, Any]:
    scoped = dict(item)
    scoped["tenant_id"] = _text(tenant_id) or None
    scoped["workspace_id"] = _text(workspace_id) or None
    scoped["data_status"] = INVALID_SCOPE_STATUS
    details = scoped.get("details")
    scoped["details"] = {
        **(dict(details) if isinstance(details, Mapping) else {}),
        "scope_validation": reason,
    }
    return scoped


def canonical_scoped_item(
    item: Mapping[str, Any], *, tenant_id: str | None, workspace_id: str | None
) -> dict[str, Any]:
    tenant = _text(tenant_id)
    workspace = _text(workspace_id)
    if not tenant or not workspace:
        return _scope_diagnostic(
            item,
            tenant_id=tenant,
            workspace_id=workspace,
            reason="incomplete_authorized_scope",
        )
    if not scope_matches(item, tenant_id=tenant, workspace_id=workspace):
        return _scope_diagnostic(
            item,
            tenant_id=tenant,
            workspace_id=workspace,
            reason="source_scope_mismatch",
        )
    return {**item, "tenant_id": tenant, "workspace_id": workspace}


class ScopedSourceRow(dict[str, Any]):
    __slots__ = ("source_row", "scope_valid")

    def __init__(
        self,
        projected: Mapping[str, Any],
        *,
        source_row: Mapping[str, Any],
        scope_valid: bool,
    ) -> None:
        super().__init__(projected)
        self.source_row = dict(source_row)
        self.scope_valid = scope_valid


def scoped_source_row(
    row: Mapping[str, Any], *, tenant_id: str | None, workspace_id: str | None
) -> ScopedSourceRow:
    sanitized = _without_raw_evidence(row)
    projected = canonical_scoped_item(
        sanitized,
        tenant_id=tenant_id,
        workspace_id=workspace_id,
    )
    tenant = _text(tenant_id)
    workspace = _text(workspace_id)
    nested_scope_matches = _declared_scope_values(row, "tenant_id") == {
        tenant
    } and _declared_scope_values(row, "workspace_id") == {workspace}
    if (
        projected.get("data_status") != INVALID_SCOPE_STATUS
        and not nested_scope_matches
    ):
        projected = _scope_diagnostic(
            sanitized,
            tenant_id=tenant,
            workspace_id=workspace,
            reason="source_scope_mismatch",
        )
    return ScopedSourceRow(
        projected,
        source_row=row,
        scope_valid=projected.get("data_status") != INVALID_SCOPE_STATUS,
    )


def scoped_runtime_evidence_fields(
    row: Mapping[str, Any],
    *,
    source_dataset: str,
    source_system: str,
    cartridge: str,
    locator_field: str,
    observed_at: str,
) -> dict[str, Any]:
    if not isinstance(row, ScopedSourceRow) or not row.scope_valid:
        return {}
    return runtime_row_evidence_fields(
        source_dataset=source_dataset,
        source_system=source_system,
        cartridge=cartridge,
        tenant_id=_text(row.get("tenant_id")),
        workspace_id=_text(row.get("workspace_id")),
        source_row=row.source_row,
        locator_field=locator_field,
        observed_at=observed_at,
    )


def scoped_source_row_with_evidence(
    row: Mapping[str, Any],
    *,
    tenant_id: str | None,
    workspace_id: str | None,
    source_dataset: str,
    source_system: str,
    cartridge: str,
    locator_fields: Sequence[str],
) -> ScopedSourceRow:
    scoped = scoped_source_row(
        row,
        tenant_id=tenant_id,
        workspace_id=workspace_id,
    )
    locator_field = next(
        (field for field in locator_fields if scoped.get(field) is not None), ""
    )
    observed_at = next(
        (
            str(scoped[field])
            for field in _OBSERVED_AT_FIELDS
            if scoped.get(field) is not None and str(scoped[field]).strip()
        ),
        "",
    )
    if locator_field and observed_at:
        scoped.update(
            scoped_runtime_evidence_fields(
                scoped,
                source_dataset=source_dataset,
                source_system=source_system,
                cartridge=cartridge,
                locator_field=locator_field,
                observed_at=observed_at,
            )
        )
    return scoped


def partial_scope_diagnostic_item(
    item_id: str, *, tenant_id: str, workspace_id: str
) -> dict[str, Any]:
    return _scope_diagnostic(
        {"id": item_id, "kind": "source_state", "item_kind": "source_state"},
        tenant_id=tenant_id,
        workspace_id=workspace_id,
        reason="incomplete_authorized_scope",
    )


def command_scope_boundary(
    context: Mapping[str, Any] | None, item_id: str
) -> CommandScopeBoundary:
    tenant_id, workspace_id = context_scope(context)
    diagnostic = None
    if not tenant_id or not workspace_id:
        diagnostic = partial_scope_diagnostic_item(
            item_id,
            tenant_id=tenant_id,
            workspace_id=workspace_id,
        )
    return CommandScopeBoundary(tenant_id, workspace_id, diagnostic)


__all__ = (
    "INVALID_SCOPE_STATUS",
    "canonical_scoped_item",
    "command_scope_boundary",
    "context_scope",
    "has_partial_scope",
    "partial_scope_diagnostic_item",
    "scope_matches",
    "scoped_runtime_evidence_fields",
    "scoped_source_row",
    "scoped_source_row_with_evidence",
)
