from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass
from hashlib import sha256


@dataclass(frozen=True, order=True)
class BusinessSurfaceIdentity:
    domain: str
    cartridge_id: str
    module_id: str

    @property
    def section_id(self) -> str:
        structural_key = "\x1f".join((self.domain, self.cartridge_id, self.module_id))
        digest = sha256(structural_key.encode("utf-8")).hexdigest()[:20]
        return f"business-section-{digest}"


def _containers(item: Mapping[str, object]) -> tuple[Mapping[str, object], ...]:
    nested = tuple(
        value
        for key in ("metadata", "details")
        if isinstance((value := item.get(key)), Mapping)
    )
    return (item, *nested)


def _text(item: Mapping[str, object], *keys: str) -> str:
    for values in _containers(item):
        for key in keys:
            value = str(values.get(key) or "").strip()
            if value:
                return value
    return ""


_CAMEL_BOUNDARY = re.compile(r"(?<=[a-z0-9])(?=[A-Z])|(?<=[A-Z])(?=[A-Z][a-z])")
_ALNUM_BOUNDARY = re.compile(r"(?<=[A-Za-z])(?=\d)|(?<=\d)(?=[A-Za-z])")
_IDENTIFIER_BOUNDARY = re.compile(r"[./:\\_\-\s\[\]]+")
_EXPLICIT_NAMESPACE_BOUNDARY = re.compile(r"[./:\\\[\]]")
_NAMESPACE_MARKERS = frozenset({"analytics", "tenant"})


def _title_candidates(
    item: Mapping[str, object],
    *keys: str,
) -> tuple[str, ...]:
    return tuple(
        value
        for values in _containers(item)
        for key in keys
        if (value := str(values.get(key) or "").strip())
    )


def _is_technical_title(
    item: Mapping[str, object],
    identity: BusinessSurfaceIdentity,
    value: str,
) -> bool:
    candidate_tokens = _canonical_identifier(value)
    technical_ids = {
        tokens
        for candidate in (
            identity.module_id,
            *_title_candidates(
                item,
                "module_id",
                "source_dataset",
                "dataset",
                "gold_table",
            ),
        )
        if (tokens := _canonical_identifier(candidate))
    }
    return any(
        candidate_tokens == technical_id
        or _is_namespaced_variant(
            value,
            candidate_tokens=candidate_tokens,
            technical_id=technical_id,
        )
        for technical_id in technical_ids
    )


def _canonical_identifier(value: str) -> tuple[str, ...]:
    expanded = _CAMEL_BOUNDARY.sub(" ", value)
    expanded = _ALNUM_BOUNDARY.sub(" ", expanded)
    return tuple(
        token.casefold()
        for token in _IDENTIFIER_BOUNDARY.split(expanded)
        if token.strip()
    )


def _is_namespaced_variant(
    value: str,
    *,
    candidate_tokens: tuple[str, ...],
    technical_id: tuple[str, ...],
) -> bool:
    if len(technical_id) < 2 or len(candidate_tokens) <= len(technical_id):
        return False
    prefix = candidate_tokens[: -len(technical_id)]
    return candidate_tokens[-len(technical_id) :] == technical_id and (
        prefix[0] in _NAMESPACE_MARKERS
        or bool(_EXPLICIT_NAMESPACE_BOUNDARY.search(value))
    )


def resolve_business_surface_identity(
    item: Mapping[str, object],
) -> BusinessSurfaceIdentity | None:
    domain = _text(item, "domain")
    cartridge_id = _text(item, "cartridge_id", "cartridge", "connector_id")
    module_id = _text(item, "module_id") or _text(
        item,
        "source_dataset",
        "dataset",
        "gold_table",
    )
    if not domain or not cartridge_id or not module_id:
        return None
    return BusinessSurfaceIdentity(
        domain=domain,
        cartridge_id=cartridge_id,
        module_id=module_id,
    )


def surface_section_title(
    item: Mapping[str, object],
    identity: BusinessSurfaceIdentity,
) -> str:
    candidates = _title_candidates(
        item,
        "module",
        "module_label",
        "module_name",
        "business_label",
        "cartridge_label",
        "connector_label",
        "domain_label",
    )
    for candidate in candidates:
        if not _is_technical_title(item, identity, candidate):
            return candidate
    if not _is_technical_title(item, identity, identity.domain):
        return identity.domain
    return "Business context"


__all__ = (
    "BusinessSurfaceIdentity",
    "resolve_business_surface_identity",
    "surface_section_title",
)
