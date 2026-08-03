"""Packaged datasets that carry approval or readiness authority.

A caller holding ``datasets.write`` may load data, but must never be able to
redefine the datasets that decide whether a benchmark counts as approved:
rewriting the SQL that emits the ``approval_*`` columns would let it mint its
own approval. Separation of duties between whoever loads data and whoever
approves it.

This lives apart from ``dataset_store`` so the tool boundary can enforce it
even where the store itself is substituted.
"""

from __future__ import annotations


PROTECTED_AUTHORITY_DATASETS = frozenset(
    {
        "sap_successfactors_talent_benchmark_internal",
        "sap_successfactors_talent_readiness",
        "sap_successfactors_talent_9box",
    }
)


class ProtectedDatasetError(PermissionError):
    """Raised when a generic writer targets a packaged authority dataset."""

    def __init__(self) -> None:
        super().__init__("dataset is server-owned and cannot be replaced")


def is_protected_dataset(name: str) -> bool:
    return str(name or "").strip().casefold() in PROTECTED_AUTHORITY_DATASETS


def assert_dataset_is_writable(name: str) -> None:
    if is_protected_dataset(name):
        raise ProtectedDatasetError()
