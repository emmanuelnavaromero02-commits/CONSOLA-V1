#!/usr/bin/python3 -I
"""Security-critical projections for the two reviewed GCP plan profiles."""

from __future__ import annotations

import importlib.util
import re
from pathlib import Path
from typing import Any

_AUTHORITY_SPEC = importlib.util.spec_from_file_location(
    "omega_gcp_generate_release_authority",
    Path(__file__).with_name("generate_release_authority.py"),
)
if _AUTHORITY_SPEC is None or _AUTHORITY_SPEC.loader is None:
    raise RuntimeError("release-authority validator cannot be loaded")
release_authority_contract = importlib.util.module_from_spec(_AUTHORITY_SPEC)
_AUTHORITY_SPEC.loader.exec_module(release_authority_contract)

PROJECT = "project-dd5ba7fa-374c-4554-ae6"
PROJECT_NUMBER = "894064513501"
ENVIRONMENT = "staging"
SERVICE_ACCOUNT = (
    "serviceAccount:omega-staging-app@"
    "project-dd5ba7fa-374c-4554-ae6.iam.gserviceaccount.com"
)
PROVIDER = "registry.opentofu.org/hashicorp/google"
LABELS = {
    "app": "omega",
    "env": ENVIRONMENT,
    "managed_by": "terraform",
    "project": "modecissions",
}

CREATE_TYPES = {
    "google_project_iam_custom_role.release_backup_writer": (
        "google_project_iam_custom_role",
        "release_backup_writer",
    ),
    "google_secret_manager_secret.ghcr_pull_credentials": (
        "google_secret_manager_secret",
        "ghcr_pull_credentials",
    ),
    "google_storage_bucket.release_backups": (
        "google_storage_bucket",
        "release_backups",
    ),
    "google_storage_bucket_iam_member.app_release_backup": (
        "google_storage_bucket_iam_member",
        "app_release_backup",
    ),
    "google_secret_manager_secret_iam_member.app_ghcr_pull_credentials_access": (
        "google_secret_manager_secret_iam_member",
        "app_ghcr_pull_credentials_access",
    ),
}

RUNTIME_SECRET_SUFFIXES = {
    "internal_api_key_banxico_to_console": "internal_api_key_banxico_to_console",
    "internal_api_key_inegi_to_console": "internal_api_key_inegi_to_console",
    "internal_api_key_sec_edgar_to_console": "internal_api_key_sec_edgar_to_console",
    "omega_cartridge_banxico_password": "omega_cartridge_banxico_password",
    "omega_cartridge_inegi_password": "omega_cartridge_inegi_password",
    "omega_cartridge_sec_edgar_password": "omega_cartridge_sec_edgar_password",
}

ACCESS_SECRET_SUFFIXES = {
    "control_room_evidence_signing_key": "control_room_evidence_signing_key",
    "control_room_evidence_signing_key_id": "control_room_evidence_signing_key_id",
    "control_room_evidence_signing_previous_keys": (
        "control_room_evidence_signing_previous_keys"
    ),
    "gcs_hmac_access_key_id": "gcs_hmac_access_key_id",
    "gcs_hmac_secret_access_key": "gcs_hmac_secret_access_key",
}

SECRET_ADOPTION = {
    'google_secret_manager_secret.runtime["control_room_evidence_signing_key"]': {
        "suffix": "control_room_evidence_signing_key",
        "create_time": "2026-08-07T13:56:44.241257Z",
    },
    'google_secret_manager_secret.runtime["control_room_evidence_signing_key_id"]': {
        "suffix": "control_room_evidence_signing_key_id",
        "create_time": "2026-08-07T13:56:40.450856Z",
    },
    'google_secret_manager_secret.runtime["control_room_evidence_signing_previous_keys"]': {
        "suffix": "control_room_evidence_signing_previous_keys",
        "create_time": "2026-08-07T13:56:46.610416Z",
    },
}


class ContractError(RuntimeError):
    pass


def _variable(payload: dict[str, Any], name: str) -> Any:
    variables = payload.get("variables")
    entry = variables.get(name) if isinstance(variables, dict) else None
    if not isinstance(entry, dict) or set(entry) != {"value"}:
        raise ContractError(f"plan variable {name} is absent or ambiguous")
    return entry["value"]


def validate_authority_variables(
    payload: dict[str, Any],
    *,
    revoke_secret_accessor: bool = False,
    release_authority: dict[str, Any] | None = None,
) -> str:
    expected = {
        "project_id": PROJECT,
        "project_number": PROJECT_NUMBER,
        "billing_account_id": "01A3E0-B708F4-6EA299",
        "region": "us-central1",
        "zone": "us-central1-a",
        "environment": ENVIRONMENT,
        "canonical_writer": True,
        "revoke_project_secret_accessor": revoke_secret_accessor,
        "source_bucket": "omega-gcp-source-project-dd5ba7fa-374c-4554-ae6",
        "foundation_predecessor": None,
        "app_machine_type": "e2-standard-4",
        "boot_disk_size_gb": 60,
        "data_disk_size_gb": 150,
        "admin_email": "emmanuelromero060601@gmail.com",
        "public_console_domain": "console.7businesssolutions.com",
        "public_workspace_domain": "workspace.7businesssolutions.com",
        "enable_https": True,
        "certificate_manager_map_name": "sevenbs-production-map",
        "enable_lb_logging": True,
        "lb_log_sample_rate": 1,
        "enable_airflow_scheduler": True,
        "lakehouse_bucket_name": "",
        "lakehouse_endpoint": "storage.googleapis.com",
        "enable_budget": True,
        "monthly_budget_limit_usd": 100,
        "monthly_budget_currency": "EUR",
        "cloud_armor_rate_limit_count": 300,
    }
    variables = payload.get("variables")
    expected_names = {
        "project_id",
        "project_number",
        "billing_account_id",
        "region",
        "zone",
        "environment",
        "canonical_writer",
        "revoke_project_secret_accessor",
        "source_bucket",
        "source_object",
        "source_sha",
        "controller_ref",
        "foundation_predecessor",
        "source_generation",
        "source_size_bytes",
        "source_archive_sha256",
        "app_machine_type",
        "boot_image",
        "host_package_versions",
        "secret_versions",
        "boot_disk_size_gb",
        "data_disk_size_gb",
        "admin_email",
        "public_console_domain",
        "public_workspace_domain",
        "enable_https",
        "certificate_manager_map_name",
        "enable_lb_logging",
        "lb_log_sample_rate",
        "enable_airflow_scheduler",
        "lakehouse_bucket_name",
        "lakehouse_endpoint",
        "enable_budget",
        "monthly_budget_limit_usd",
        "monthly_budget_currency",
        "cloud_armor_rate_limit_count",
    }
    if not isinstance(variables, dict) or set(variables) != expected_names:
        raise ContractError("plan variable inventory differs from the reviewed module")
    for name, value in expected.items():
        if _variable(payload, name) != value:
            raise ContractError(
                f"plan variable {name} differs from canonical authority"
            )
    source_sha = _variable(payload, "source_sha")
    source_object = _variable(payload, "source_object")
    generation = _variable(payload, "source_generation")
    source_size = _variable(payload, "source_size_bytes")
    archive_sha = _variable(payload, "source_archive_sha256")
    boot_image = _variable(payload, "boot_image")
    if (
        not isinstance(source_sha, str)
        or re.fullmatch(r"[0-9a-f]{40}", source_sha) is None
        or source_object != f"deploy-artifacts/{source_sha}/repo.tar.gz"
        or not isinstance(generation, str)
        or re.fullmatch(r"[1-9][0-9]*", generation) is None
        or not isinstance(source_size, int)
        or isinstance(source_size, bool)
        or not 1 <= source_size <= 2_147_483_648
        or not isinstance(archive_sha, str)
        or re.fullmatch(r"[0-9a-f]{64}", archive_sha) is None
        or not isinstance(boot_image, str)
        or re.fullmatch(
            r"projects/ubuntu-os-cloud/global/images/ubuntu-2204-jammy-v[0-9]{8}",
            boot_image,
        )
        is None
    ):
        raise ContractError("source archive or boot image identity is not immutable")
    packages = _variable(payload, "host_package_versions")
    package_names = {
        "ca_certificates",
        "containerd_io",
        "curl",
        "docker_buildx_plugin",
        "docker_ce",
        "docker_ce_cli",
        "docker_compose_plugin",
        "gnupg",
        "iptables",
        "jq",
        "lsof",
        "openssl",
        "python3",
    }
    if (
        not isinstance(packages, dict)
        or set(packages) != package_names
        or any(
            not isinstance(value, str)
            or re.fullmatch(r"[0-9A-Za-z][0-9A-Za-z.+:~_-]{2,159}", value) is None
            or "exact" in value.lower()
            for value in packages.values()
        )
    ):
        raise ContractError("host package version inventory is not exact")
    secret_versions = _variable(payload, "secret_versions")
    secret_names = {
        "control_room_evidence_signing_key_id",
        "control_room_evidence_signing_key",
        "control_room_evidence_signing_previous_keys",
        "gcs_hmac_access_key_id",
        "gcs_hmac_secret_access_key",
    }
    if not isinstance(secret_versions, dict) or set(secret_versions) != secret_names:
        raise ContractError("secret-version inventory differs")
    required_secrets = secret_names - {
        "gcs_hmac_access_key_id",
        "gcs_hmac_secret_access_key",
    }
    if any(
        not isinstance(secret_versions[name], str)
        or re.fullmatch(r"[1-9][0-9]*", secret_versions[name]) is None
        for name in required_secrets
    ):
        raise ContractError("required secret versions are not exact numeric IDs")
    hmac = (
        secret_versions["gcs_hmac_access_key_id"],
        secret_versions["gcs_hmac_secret_access_key"],
    )
    if hmac != ("", "") and any(
        not isinstance(value, str) or re.fullmatch(r"[1-9][0-9]*", value) is None
        for value in hmac
    ):
        raise ContractError("GCS HMAC secret versions are not an exact pair")
    if release_authority is not None:
        try:
            authority = release_authority_contract.validate_authority_receipt(
                release_authority
            )
        except release_authority_contract.AuthorityError as error:
            raise ContractError("reviewed authority receipt is invalid") from error
        authority_variables = {
            "boot_image": "boot_image",
            "canonical_writer": "canonical_writer",
            "certificate_manager_map_name": "certificate_manager_map_name",
            "controller_ref": "controller_ref",
            "enable_airflow_scheduler": "enable_airflow_scheduler",
            "foundation_predecessor": "foundation_predecessor",
            "host_package_versions": "host_package_versions",
            "lakehouse_bucket_name": "lakehouse_bucket_name",
            "lakehouse_endpoint": "lakehouse_endpoint",
            "public_console_domain": "public_console_domain",
            "public_workspace_domain": "public_workspace_domain",
            "secret_versions": "secret_versions",
            "source_archive_sha256": "source_archive_sha256",
            "source_bucket": "source_bucket",
            "source_generation": "source_generation",
            "source_object": "source_object",
            "source_sha": "source_sha",
            "source_size_bytes": "source_size_bytes",
        }
        for variable_name, authority_name in authority_variables.items():
            if _variable(payload, variable_name) != authority[authority_name]:
                raise ContractError(
                    f"plan variable {variable_name} differs from reviewed authority"
                )
    controller_ref = _variable(payload, "controller_ref")
    if (
        not isinstance(controller_ref, str)
        or re.fullmatch(r"[0-9a-f]{40}", controller_ref) is None
    ):
        raise ContractError("plan controller_ref is not one full Git SHA")
    return controller_ref


def _truthy_paths(value: Any, prefix: tuple[str, ...] = ()) -> set[tuple[str, ...]]:
    if isinstance(value, dict):
        paths: set[tuple[str, ...]] = set()
        for key, item in value.items():
            if not isinstance(key, str):
                raise ContractError("plan unknown/sensitive tree has a non-string key")
            paths |= _truthy_paths(item, (*prefix, key))
        return paths
    if isinstance(value, list):
        paths = set()
        for index, item in enumerate(value):
            paths |= _truthy_paths(item, (*prefix, str(index)))
        return paths
    return set() if value in (False, None) else {prefix}


def _require_change(change: Any, actions: list[str]) -> dict[str, Any]:
    required = {
        "actions",
        "before",
        "after",
        "after_unknown",
        "before_sensitive",
        "after_sensitive",
    }
    if (
        not isinstance(change, dict)
        or not required.issubset(change)
        or set(change) - (required | {"replace_paths"})
    ):
        raise ContractError("security-critical resource change shape differs")
    if change["actions"] != actions or change.get("replace_paths") not in (None, []):
        raise ContractError("security-critical resource actions differ")
    return change


def _require_item(
    item: dict[str, Any],
    expected_type: str,
    expected_name: str,
    *,
    expected_index: str | None = None,
) -> None:
    expected_keys = {"address", "mode", "type", "name", "provider_name", "change"}
    if expected_index is not None:
        expected_keys.add("index")
    if (
        set(item) != expected_keys
        or item.get("mode") != "managed"
        or item.get("type") != expected_type
        or item.get("name") != expected_name
        or item.get("provider_name") != PROVIDER
        or item.get("index") != expected_index
    ):
        raise ContractError("security-critical provider/resource identity differs")


def _require_fields(after: dict[str, Any], expected: dict[str, Any]) -> None:
    for key, value in expected.items():
        if key not in after or after[key] != value:
            raise ContractError(f"security-critical planned field {key} differs")


def _secret_address(address: str, prefix: str) -> str | None:
    match = re.fullmatch(re.escape(prefix) + r'\["([a-z0-9_]+)"\]', address)
    return match.group(1) if match else None


def _expected_identity(address: str) -> tuple[str, str, str | None]:
    if address in CREATE_TYPES:
        resource_type, name = CREATE_TYPES[address]
        return resource_type, name, None
    key = _secret_address(address, "google_secret_manager_secret.runtime")
    if key in RUNTIME_SECRET_SUFFIXES:
        return "google_secret_manager_secret", "runtime", RUNTIME_SECRET_SUFFIXES[key]
    key = _secret_address(
        address, "google_secret_manager_secret_iam_member.app_runtime_secret_access"
    )
    if key in ACCESS_SECRET_SUFFIXES:
        return (
            "google_secret_manager_secret_iam_member",
            "app_runtime_secret_access",
            ACCESS_SECRET_SUFFIXES[key],
        )
    raise ContractError("create address has no security projection")


def validate_create(address: str, item: dict[str, Any]) -> None:
    resource_type, expected_name, suffix = _expected_identity(address)
    _require_item(item, resource_type, expected_name, expected_index=suffix)
    change = _require_change(item.get("change"), ["create"])
    if change["before"] is not None or not isinstance(change["after"], dict):
        raise ContractError("create before/after shape differs")
    after = change["after"]
    effective_labels = LABELS | {"goog-terraform-provisioned": "true"}
    if resource_type == "google_project_iam_custom_role":
        permissions = [
            "storage.buckets.get",
            "storage.objects.create",
            "storage.objects.get",
        ]
        expected_after = {
            "project": PROJECT,
            "role_id": "omegaReleaseBackupWriter",
            "title": "OMEGA release backup writer",
            "description": (
                "Create and verify release backup generations without list, "
                "overwrite, or delete"
            ),
            "permissions": permissions,
            "stage": "GA",
        }
        if (
            after != expected_after
            or change["after_unknown"]
            != {"deleted": True, "id": True, "name": True, "permissions": [False] * 3}
            or change["before_sensitive"] is not False
            or change["after_sensitive"] != {"permissions": [False] * 3}
        ):
            raise ContractError("custom role create projection differs")
    elif resource_type == "google_secret_manager_secret":
        secret_suffix = suffix or "ghcr_pull_credentials"
        replication = [
            {"auto": [{"customer_managed_encryption": []}], "user_managed": []}
        ]
        expected_after = {
            "annotations": None,
            "deletion_protection": False,
            "effective_labels": effective_labels,
            "labels": LABELS,
            "project": PROJECT,
            "replication": replication,
            "rotation": [],
            "secret_id": f"omega-{ENVIRONMENT}-{secret_suffix}",
            "tags": None,
            "terraform_labels": effective_labels,
            "timeouts": None,
            "topics": [],
            "ttl": None,
            "version_aliases": None,
            "version_destroy_ttl": None,
        }
        expected_unknown = {
            "create_time": True,
            "effective_annotations": True,
            "effective_labels": {},
            "expire_time": True,
            "id": True,
            "labels": {},
            "name": True,
            "replication": replication,
            "rotation": [],
            "terraform_labels": {},
            "topics": [],
        }
        expected_sensitive = {
            "effective_annotations": {},
            "effective_labels": {},
            "labels": {},
            "replication": replication,
            "rotation": [],
            "terraform_labels": {},
            "topics": [],
        }
        if (
            after != expected_after
            or change["after_unknown"] != expected_unknown
            or change["before_sensitive"] is not False
            or change["after_sensitive"] != expected_sensitive
        ):
            raise ContractError("secret create projection differs")
    elif resource_type == "google_secret_manager_secret_iam_member":
        secret_suffix = suffix or "ghcr_pull_credentials"
        expected_after = {
            "condition": [],
            "project": PROJECT,
            "secret_id": f"omega-{ENVIRONMENT}-{secret_suffix}",
            "role": "roles/secretmanager.secretAccessor",
            "member": SERVICE_ACCOUNT,
        }
        if (
            after != expected_after
            or change["after_unknown"] != {"condition": [], "etag": True, "id": True}
            or change["before_sensitive"] is not False
            or change["after_sensitive"] != {"condition": []}
        ):
            raise ContractError("secret IAM create projection differs")
    elif resource_type == "google_storage_bucket":
        expected_after = {
            "autoclass": [],
            "cors": [],
            "custom_placement_config": [],
            "default_event_based_hold": None,
            "effective_labels": effective_labels,
            "enable_object_retention": None,
            "encryption": [],
            "force_destroy": False,
            "hierarchical_namespace": [],
            "ip_filter": [],
            "labels": LABELS,
            "lifecycle_rule": [],
            "location": "US-CENTRAL1",
            "logging": [],
            "name": f"omega-{ENVIRONMENT}-release-backups-{PROJECT_NUMBER}",
            "public_access_prevention": "enforced",
            "requester_pays": None,
            "retention_policy": [{"is_locked": False, "retention_period": 604800}],
            "soft_delete_policy": [{"retention_duration_seconds": 2592000}],
            "storage_class": "STANDARD",
            "terraform_labels": effective_labels,
            "timeouts": None,
            "uniform_bucket_level_access": True,
            "versioning": [{"enabled": True}],
        }
        expected_unknown = {
            "autoclass": [],
            "cors": [],
            "custom_placement_config": [],
            "effective_labels": {},
            "encryption": [],
            "hierarchical_namespace": [],
            "id": True,
            "ip_filter": [],
            "labels": {},
            "lifecycle_rule": [],
            "logging": [],
            "project": True,
            "project_number": True,
            "retention_policy": [{}],
            "rpo": True,
            "self_link": True,
            "soft_delete_policy": [{"effective_time": True}],
            "terraform_labels": {},
            "time_created": True,
            "updated": True,
            "url": True,
            "versioning": [{}],
            "website": True,
        }
        expected_sensitive = {
            "autoclass": [],
            "cors": [],
            "custom_placement_config": [],
            "effective_labels": {},
            "encryption": [],
            "hierarchical_namespace": [],
            "ip_filter": [],
            "labels": {},
            "lifecycle_rule": [],
            "logging": [],
            "retention_policy": [{}],
            "soft_delete_policy": [{}],
            "terraform_labels": {},
            "versioning": [{}],
            "website": [],
        }
        if (
            after != expected_after
            or change["after_unknown"] != expected_unknown
            or change["before_sensitive"] is not False
            or change["after_sensitive"] != expected_sensitive
        ):
            raise ContractError("release backup bucket create projection differs")
    elif resource_type == "google_storage_bucket_iam_member":
        expected_after = {
            "bucket": f"omega-{ENVIRONMENT}-release-backups-{PROJECT_NUMBER}",
            "condition": [],
            "role": f"projects/{PROJECT}/roles/omegaReleaseBackupWriter",
            "member": SERVICE_ACCOUNT,
        }
        if (
            after != expected_after
            or change["after_unknown"] != {"condition": [], "etag": True, "id": True}
            or change["before_sensitive"] is not False
            or change["after_sensitive"] != {"condition": []}
        ):
            raise ContractError("release backup IAM create projection differs")


def validate_iam_delete(item: dict[str, Any]) -> None:
    _require_item(item, "google_project_iam_member", "app_project_roles")
    change = _require_change(item.get("change"), ["delete"])
    if not isinstance(change["before"], dict) or change["after"] is not None:
        raise ContractError("IAM revoke before/after shape differs")
    _require_fields(
        change["before"],
        {
            "project": PROJECT,
            "role": "roles/secretmanager.secretAccessor",
            "member": SERVICE_ACCOUNT,
        },
    )
    if _truthy_paths(change["after_unknown"]):
        raise ContractError("IAM revoke contains unknown fields")


def changed_paths(
    left: Any, right: Any, prefix: tuple[str, ...] = ()
) -> set[tuple[str, ...]]:
    missing = object()
    if isinstance(left, dict) and isinstance(right, dict):
        paths: set[tuple[str, ...]] = set()
        for key in set(left) | set(right):
            paths |= changed_paths(
                left.get(key, missing),
                right.get(key, missing),
                (*prefix, key),
            )
        return paths
    return set() if left == right else {prefix}


def validate_secret_adoption(address: str, item: dict[str, Any]) -> None:
    """Validate the provider shape captured by the metadata-only adoption plan."""
    identity = SECRET_ADOPTION.get(address)
    if identity is None:
        raise ContractError("secret adoption address is not reviewed")
    suffix = identity["suffix"]
    _require_item(
        item,
        "google_secret_manager_secret",
        "runtime",
        expected_index=suffix,
    )
    change = item.get("change")
    expected_change_keys = {
        "actions",
        "before",
        "after",
        "after_unknown",
        "before_sensitive",
        "after_sensitive",
        "importing",
    }
    if not isinstance(change, dict) or set(change) != expected_change_keys:
        raise ContractError("secret adoption provider change shape differs")

    secret_id = f"omega-{ENVIRONMENT}-{suffix}"
    resource_id = f"projects/{PROJECT}/secrets/{secret_id}"
    replication = [{"auto": [{"customer_managed_encryption": []}], "user_managed": []}]
    before = {
        "annotations": {},
        "create_time": identity["create_time"],
        "deletion_protection": False,
        "effective_annotations": {},
        "effective_labels": LABELS | {"managed_by": "migration"},
        "expire_time": "",
        "id": resource_id,
        "labels": {},
        "name": f"projects/{PROJECT_NUMBER}/secrets/{secret_id}",
        "project": PROJECT,
        "replication": replication,
        "rotation": [],
        "secret_id": secret_id,
        "tags": None,
        "terraform_labels": {},
        "timeouts": None,
        "topics": [],
        "ttl": None,
        "version_aliases": {},
        "version_destroy_ttl": "",
    }
    after = before | {
        "effective_labels": LABELS,
        "labels": LABELS,
        "terraform_labels": LABELS,
    }
    sensitive = {
        "annotations": {},
        "effective_annotations": {},
        "effective_labels": {},
        "labels": {},
        "replication": replication,
        "rotation": [],
        "terraform_labels": {},
        "topics": [],
        "version_aliases": {},
    }
    expected_paths = {
        ("effective_labels", "managed_by"),
        *(("labels", key) for key in LABELS),
        *(("terraform_labels", key) for key in LABELS),
    }
    if (
        change["actions"] != ["update"]
        or change["importing"] != {"id": resource_id}
        or change["before"] != before
        or change["after"] != after
        or change["after_unknown"] != {}
        or change["before_sensitive"] != sensitive
        or change["after_sensitive"] != sensitive
        or changed_paths(change["before"], change["after"]) != expected_paths
    ):
        raise ContractError("secret adoption is not the exact labels-only import")


def validate_foundation_drift(items: Any) -> None:
    if not isinstance(items, list):
        raise ContractError("resource_drift is not a list")
    if not items:
        return
    expected = {
        "google_compute_url_map.public": {"host_rule", "fingerprint"},
        "google_compute_target_https_proxy.public[0]": {"certificate_map"},
    }
    addresses = {item.get("address") for item in items if isinstance(item, dict)}
    if addresses != set(expected) or len(items) != 2:
        raise ContractError("resource_drift address manifest differs")
    for item in items:
        address = item["address"]
        expected_type = (
            "google_compute_url_map"
            if address == "google_compute_url_map.public"
            else "google_compute_target_https_proxy"
        )
        _require_item(item, expected_type, "public")
        change = _require_change(item.get("change"), ["update"])
        before, after = change["before"], change["after"]
        if not isinstance(before, dict) or not isinstance(after, dict):
            raise ContractError("edge drift before/after is absent")
        tops = {path[0] for path in changed_paths(before, after) if path}
        if not tops or not tops.issubset(expected[address]):
            raise ContractError("edge drift paths differ from reviewed state staleness")
        if _truthy_paths(change["after_unknown"]):
            raise ContractError("edge drift contains unknown fields")
        if address == "google_compute_url_map.public":
            rules = after.get("host_rule")
            normalized = (
                {
                    (tuple(rule.get("hosts", [])), rule.get("path_matcher"))
                    for rule in rules
                    if isinstance(rule, dict)
                }
                if isinstance(rules, list)
                else set()
            )
            if normalized != {
                (("*",), "console"),
                (("gcp-workspace.7businesssolutions.com",), "workspace"),
                (("workspace.7businesssolutions.com",), "workspace"),
            }:
                raise ContractError("live URL-map drift target differs")
        elif after.get("certificate_map") != (
            "//certificatemanager.googleapis.com/projects/"
            f"{PROJECT}/locations/global/certificateMaps/sevenbs-production-map"
        ):
            raise ContractError("live HTTPS proxy certificate-map drift differs")
