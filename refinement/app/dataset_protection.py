from __future__ import annotations


PROTECTED_AUTHORITY_DATASETS = frozenset(
    {
        "sap_successfactors_talent_benchmark_internal",
        "sap_successfactors_talent_employee_profile",
        "sap_successfactors_talent_cpa_scores",
        "sap_successfactors_talent_readiness",
        "sap_successfactors_talent_9box",
        "sap_successfactors_talent_9box_operational",
        "sap_successfactors_talent_promotion_alignment",
        "sap_successfactors_talent_calibration_sensitivity",
        "sap_successfactors_talent_retention_risk",
        "sap_successfactors_talent_role_fit_assignments",
        "sap_successfactors_talent_role_profile",
        "sap_successfactors_talent_mobility_history",
        "sap_successfactors_talent_action_candidates",
        "sap_successfactors_talent_signals",
        "sap_successfactors_talent_operational_features",
        "sap_successfactors_talent_simulation_inputs",
    }
)

SERVER_OWNED_DATASET_PREFIXES = ("sap_successfactors_",)


class ProtectedDatasetError(PermissionError):

    def __init__(self) -> None:
        super().__init__("dataset is server-owned and cannot be replaced")


def is_protected_dataset(name: str) -> bool:
    normalized = str(name or "").strip().casefold()
    return normalized in PROTECTED_AUTHORITY_DATASETS or normalized.startswith(
        SERVER_OWNED_DATASET_PREFIXES
    )


def assert_dataset_is_writable(name: str) -> None:
    if is_protected_dataset(name):
        raise ProtectedDatasetError()
