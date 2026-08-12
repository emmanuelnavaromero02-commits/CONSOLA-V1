#!/usr/bin/python3 -I
"""Fail closed unless a reviewed Terraform plan has no destroy/replacement."""

from __future__ import annotations

import importlib.util
import json
import os
import re
import stat
import sys
from pathlib import Path
from typing import Any

_CONTRACT_SPEC = importlib.util.spec_from_file_location(
    "omega_gcp_terraform_plan_contract",
    Path(__file__).with_name("terraform_plan_contract.py"),
)
if _CONTRACT_SPEC is None or _CONTRACT_SPEC.loader is None:
    raise RuntimeError("Terraform plan contract cannot be loaded")
plan_contract = importlib.util.module_from_spec(_CONTRACT_SPEC)
_CONTRACT_SPEC.loader.exec_module(plan_contract)


MAX_PLAN_BYTES = 64 * 1024 * 1024
EXPECTED_CREATE = {
    "google_project_iam_custom_role.release_backup_writer",
    "google_secret_manager_secret.ghcr_pull_credentials",
    'google_secret_manager_secret.runtime["internal_api_key_banxico_to_console"]',
    'google_secret_manager_secret.runtime["internal_api_key_inegi_to_console"]',
    'google_secret_manager_secret.runtime["internal_api_key_sec_edgar_to_console"]',
    'google_secret_manager_secret.runtime["omega_cartridge_banxico_password"]',
    'google_secret_manager_secret.runtime["omega_cartridge_inegi_password"]',
    'google_secret_manager_secret.runtime["omega_cartridge_sec_edgar_password"]',
    "google_secret_manager_secret_iam_member.app_ghcr_pull_credentials_access",
    'google_secret_manager_secret_iam_member.app_runtime_secret_access["control_room_evidence_signing_key"]',
    'google_secret_manager_secret_iam_member.app_runtime_secret_access["control_room_evidence_signing_key_id"]',
    'google_secret_manager_secret_iam_member.app_runtime_secret_access["control_room_evidence_signing_previous_keys"]',
    'google_secret_manager_secret_iam_member.app_runtime_secret_access["gcs_hmac_access_key_id"]',
    'google_secret_manager_secret_iam_member.app_runtime_secret_access["gcs_hmac_secret_access_key"]',
    "google_storage_bucket.release_backups",
    "google_storage_bucket_iam_member.app_release_backup",
}
EXPECTED_FORGET: set[str] = set()
EXPECTED_UPDATE = {
    "google_compute_target_http_proxy.public": "url-map",
    "google_compute_target_http_proxy.workspace": "workspace-url-map",
}
DEFERRED_SECRET_ACCESSOR_DELETE = (
    'google_project_iam_member.app_project_roles["roles/secretmanager.secretAccessor"]'
)
SECRET_ADOPTION_ADDRESSES = frozenset(plan_contract.SECRET_ADOPTION)
SECRET_ADOPTION_SERVICES = frozenset(
    {
        "artifactregistry.googleapis.com",
        "billingbudgets.googleapis.com",
        "cloudresourcemanager.googleapis.com",
        "compute.googleapis.com",
        "iam.googleapis.com",
        "iap.googleapis.com",
        "logging.googleapis.com",
        "monitoring.googleapis.com",
        "oslogin.googleapis.com",
        "secretmanager.googleapis.com",
        "serviceusage.googleapis.com",
        "storage.googleapis.com",
    }
)
SECRET_ADOPTION_NOOPS = frozenset(
    f'google_project_service.required["{service}"]'
    for service in SECRET_ADOPTION_SERVICES
)
SECRET_ADOPTION_PASS_CHECKS = frozenset(
    {"var.environment", "var.project_id", "var.region", "var.zone"}
)
SECRET_ADOPTION_UNKNOWN_CHECKS = frozenset(
    {
        "check.canonical_https_contract",
        "check.canonical_scheduler",
        "check.canonical_writer_requires_https",
        "check.public_domain_pair",
        "check.public_domains_are_distinct",
        "check.public_edge_mode_is_explicit",
        "check.source_object_matches_sha",
        "check.zone_matches_region",
        "google_compute_instance.app",
        "var.admin_email",
        "var.app_machine_type",
        "var.billing_account_id",
        "var.boot_disk_size_gb",
        "var.boot_image",
        "var.canonical_writer",
        "var.certificate_manager_map_name",
        "var.cloud_armor_rate_limit_count",
        "var.controller_ref",
        "var.data_disk_size_gb",
        "var.foundation_predecessor",
        "var.host_package_versions",
        "var.lakehouse_bucket_name",
        "var.lakehouse_endpoint",
        "var.lb_log_sample_rate",
        "var.monthly_budget_currency",
        "var.monthly_budget_limit_usd",
        "var.project_number",
        "var.public_console_domain",
        "var.public_workspace_domain",
        "var.secret_versions",
        "var.source_archive_sha256",
        "var.source_bucket",
        "var.source_generation",
        "var.source_object",
        "var.source_sha",
        "var.source_size_bytes",
    }
)
SECRET_ADOPTION_PLAN_KEYS = frozenset(
    {
        "checks",
        "configuration",
        "errored",
        "format_version",
        "planned_values",
        "prior_state",
        "resource_changes",
        "terraform_version",
        "timestamp",
        "variables",
    }
)


class PlanError(RuntimeError):
    pass


def _no_duplicates(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    value: dict[str, Any] = {}
    for key, item in pairs:
        if key in value:
            raise PlanError("plan JSON contains duplicate keys")
        value[key] = item
    return value


def _read_owned(path: Path, *, maximum: int, label: str) -> bytes:
    if not path.is_absolute():
        raise PlanError("plan JSON path must be absolute")
    descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
    try:
        before = os.fstat(descriptor)
        if (
            not stat.S_ISREG(before.st_mode)
            or before.st_uid != os.geteuid()
            or stat.S_IMODE(before.st_mode) & 0o022
            or before.st_nlink != 1
            or not 1 <= before.st_size <= MAX_PLAN_BYTES
        ):
            raise PlanError(f"{label} descriptor identity is unsafe")
        raw = os.read(descriptor, before.st_size + 1)
        after = os.fstat(descriptor)
        if len(raw) != before.st_size or (
            before.st_dev,
            before.st_ino,
            before.st_size,
            before.st_mtime_ns,
            before.st_ctime_ns,
        ) != (
            after.st_dev,
            after.st_ino,
            after.st_size,
            after.st_mtime_ns,
            after.st_ctime_ns,
        ):
            raise PlanError(f"{label} changed during authoritative read")
    finally:
        os.close(descriptor)
    return raw


def load_plan_bytes(raw: bytes) -> dict[str, Any]:
    try:
        payload = json.loads(
            raw.decode("utf-8", errors="strict"), object_pairs_hook=_no_duplicates
        )
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise PlanError("plan JSON is invalid") from error
    if not isinstance(payload, dict):
        raise PlanError("plan JSON root is not an object")
    return payload


def load_plan(path: Path) -> dict[str, Any]:
    return load_plan_bytes(_read_owned(path, maximum=MAX_PLAN_BYTES, label="plan JSON"))


def _contains_secret_version_resource(value: Any) -> bool:
    if isinstance(value, dict):
        if value.get("type") == "google_secret_manager_secret_version":
            return True
        address = value.get("address")
        if isinstance(address, str) and address.startswith(
            "google_secret_manager_secret_version."
        ):
            return True
        return any(_contains_secret_version_resource(item) for item in value.values())
    if isinstance(value, list):
        return any(_contains_secret_version_resource(item) for item in value)
    return False


def _expected_adoption_check_address(display: str) -> dict[str, str]:
    if display.startswith("check."):
        return {"kind": "check", "name": display[6:], "to_display": display}
    if display.startswith("var."):
        return {"kind": "var", "name": display[4:], "to_display": display}
    if display == "google_compute_instance.app":
        return {
            "kind": "resource",
            "mode": "managed",
            "name": "app",
            "to_display": display,
            "type": "google_compute_instance",
        }
    raise PlanError("secret-adoption check address is not reviewed")


def _validate_secret_adoption_checks(checks: Any) -> None:
    expected_status = {
        **{name: "pass" for name in SECRET_ADOPTION_PASS_CHECKS},
        **{name: "unknown" for name in SECRET_ADOPTION_UNKNOWN_CHECKS},
    }
    if not isinstance(checks, list) or len(checks) != len(expected_status):
        raise PlanError("secret-adoption check inventory differs from captured plan")
    observed: set[str] = set()
    for check in checks:
        if not isinstance(check, dict) or not isinstance(check.get("address"), dict):
            raise PlanError("secret-adoption check shape differs from captured plan")
        display = check["address"].get("to_display")
        if (
            not isinstance(display, str)
            or display in observed
            or display not in expected_status
            or check["address"] != _expected_adoption_check_address(display)
            or check.get("status") != expected_status[display]
        ):
            raise PlanError("secret-adoption check result differs from captured plan")
        observed.add(display)
        if expected_status[display] == "pass":
            if set(check) != {"address", "instances", "status"} or check.get(
                "instances"
            ) != [{"address": {"to_display": display}, "status": "pass"}]:
                raise PlanError("secret-adoption passing-check projection differs")
        elif set(check) != {"address", "status"}:
            raise PlanError("secret-adoption unknown-check projection differs")
    if observed != set(expected_status):
        raise PlanError("secret-adoption check inventory differs from captured plan")


def _validate_secret_adoption_envelope(payload: dict[str, Any]) -> None:
    if (
        set(payload) != SECRET_ADOPTION_PLAN_KEYS
        or payload.get("errored") is not False
        or payload.get("format_version") != "1.2"
        or payload.get("terraform_version") != "1.11.6"
        or not isinstance(payload.get("timestamp"), str)
        or re.fullmatch(
            r"[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}Z",
            payload["timestamp"],
        )
        is None
    ):
        raise PlanError("secret-adoption plan envelope differs from read-only capture")
    configuration = payload.get("configuration")
    prior_state = payload.get("prior_state")
    planned_values = payload.get("planned_values")
    if (
        not isinstance(configuration, dict)
        or set(configuration) != {"provider_config", "root_module"}
        or not isinstance(prior_state, dict)
        or set(prior_state) != {"format_version", "terraform_version", "values"}
        or not isinstance(planned_values, dict)
        or set(planned_values) != {"root_module"}
        or _contains_secret_version_resource(configuration)
        or _contains_secret_version_resource(prior_state)
        or _contains_secret_version_resource(planned_values)
    ):
        raise PlanError(
            "secret-adoption plan includes an unreviewed resource projection"
        )
    root_module = planned_values.get("root_module")
    resources = root_module.get("resources") if isinstance(root_module, dict) else None
    resource_changes = payload.get("resource_changes")
    change_map = (
        {
            item.get("address"): item.get("change")
            for item in resource_changes
            if isinstance(item, dict)
        }
        if isinstance(resource_changes, list)
        else {}
    )
    expected_addresses = SECRET_ADOPTION_ADDRESSES | SECRET_ADOPTION_NOOPS
    if (
        not isinstance(root_module, dict)
        or set(root_module) != {"resources"}
        or not isinstance(resources, list)
        or len(resources) != len(expected_addresses)
    ):
        raise PlanError("secret-adoption planned resource inventory differs")
    addresses: set[str] = set()
    for item in resources:
        if (
            not isinstance(item, dict)
            or set(item)
            != {
                "address",
                "mode",
                "type",
                "name",
                "index",
                "provider_name",
                "schema_version",
                "values",
                "sensitive_values",
            }
            or item.get("address") not in expected_addresses
            or item.get("address") in addresses
            or item.get("mode") != "managed"
            or item.get("provider_name") != plan_contract.PROVIDER
            or item.get("schema_version") != 0
            or not isinstance(item.get("values"), dict)
            or not isinstance(item.get("sensitive_values"), dict)
        ):
            raise PlanError("secret-adoption planned resource projection differs")
        address = item["address"]
        addresses.add(address)
        change = change_map.get(address)
        if (
            not isinstance(change, dict)
            or item["values"] != change.get("after")
            or item["sensitive_values"] != change.get("after_sensitive")
        ):
            raise PlanError("secret-adoption planned/change projection differs")
        if address in SECRET_ADOPTION_ADDRESSES:
            expected_index = plan_contract.SECRET_ADOPTION[address]["suffix"]
            if (
                item.get("type") != "google_secret_manager_secret"
                or item.get("name") != "runtime"
                or item.get("index") != expected_index
            ):
                raise PlanError("secret-adoption planned secret identity differs")
        else:
            service = address.removeprefix(
                'google_project_service.required["'
            ).removesuffix('"]')
            if (
                service not in SECRET_ADOPTION_SERVICES
                or item.get("type") != "google_project_service"
                or item.get("name") != "required"
                or item.get("index") != service
            ):
                raise PlanError("secret-adoption planned dependency identity differs")
    if addresses != expected_addresses:
        raise PlanError("secret-adoption planned resource inventory differs")
    _validate_secret_adoption_checks(payload.get("checks"))


def validate_plan(
    payload: dict[str, Any],
    *,
    profile: str = "foundation",
    expected_create: set[str] | None = None,
    expected_forget: set[str] | None = None,
    expected_update: set[str] | None = None,
) -> dict[str, int]:
    if profile not in {"foundation", "iam-revoke", "secret-adoption"}:
        raise PlanError("Terraform plan profile is unsupported")
    if profile == "secret-adoption":
        if any(
            contract is not None
            for contract in (expected_create, expected_forget, expected_update)
        ):
            raise PlanError("secret-adoption manifest cannot be caller-overridden")
        _validate_secret_adoption_envelope(payload)
    else:
        if (
            payload.get("applyable") is not True
            or payload.get("complete") is not True
            or payload.get("errored") is not False
            or payload.get("deferred_changes") != []
        ):
            raise PlanError("Terraform plan is incomplete, errored, or deferred")
        checks = payload.get("checks")
        if not isinstance(checks, list) or any(
            not isinstance(check, dict) or check.get("status") != "pass"
            for check in checks
        ):
            raise PlanError("Terraform plan checks are not all pass")
        if payload.get("format_version") not in {"1.1", "1.2"}:
            raise PlanError("Terraform plan format is unsupported")
    try:
        plan_contract.validate_authority_variables(
            payload,
            revoke_secret_accessor=profile == "iam-revoke",
        )
        if profile == "foundation":
            plan_contract.validate_foundation_drift(payload.get("resource_drift"))
        elif profile == "iam-revoke" and payload.get("resource_drift") != []:
            raise plan_contract.ContractError("IAM revoke plan contains resource drift")
    except plan_contract.ContractError as error:
        raise PlanError(str(error)) from error
    changes = payload.get("resource_changes")
    if not isinstance(changes, list):
        raise PlanError("Terraform plan resource_changes is absent")
    counts = {"create": 0, "delete": 0, "forget": 0, "replace": 0, "update": 0}
    addresses: set[str] = set()
    imports: set[str] = set()
    action_manifest: dict[str, tuple[str, ...]] = {}
    for item in changes:
        if not isinstance(item, dict) or set(item) - {
            "address",
            "module_address",
            "mode",
            "type",
            "name",
            "index",
            "provider_name",
            "change",
            "deposed",
            "action_reason",
            "previous_address",
        }:
            raise PlanError("Terraform resource-change shape differs")
        address = item.get("address")
        change = item.get("change")
        if (
            not isinstance(address, str)
            or re.fullmatch(r"[A-Za-z0-9_./\[\]\"-]+", address) is None
            or address in addresses
            or not isinstance(change, dict)
        ):
            raise PlanError("Terraform resource-change identity differs")
        addresses.add(address)
        if "importing" in change:
            imports.add(address)
        actions = change.get("actions")
        valid_actions = {
            ("create",),
            ("delete",),
            ("forget",),
            ("no-op",),
            ("read",),
            ("update",),
            ("create", "delete"),
            ("delete", "create"),
        }
        if not isinstance(actions, list) or tuple(actions) not in valid_actions:
            raise PlanError("Terraform resource actions are invalid")
        action_manifest[address] = tuple(actions)
        if "delete" in actions:
            counts["delete"] += 1
            if "create" in actions:
                counts["replace"] += 1
        elif "create" in actions:
            counts["create"] += 1
        elif "update" in actions:
            counts["update"] += 1
        elif "forget" in actions:
            counts["forget"] += 1
    creates = {
        address
        for address, actions in action_manifest.items()
        if actions == ("create",)
    }
    forgets = {
        address
        for address, actions in action_manifest.items()
        if actions == ("forget",)
    }
    updates = {
        address
        for address, actions in action_manifest.items()
        if actions == ("update",)
    }
    deletes = {
        address
        for address, actions in action_manifest.items()
        if actions == ("delete",)
    }
    if profile != "secret-adoption" and imports:
        raise PlanError("Terraform plan imports require the sealed adoption profile")
    if profile == "secret-adoption":
        expected_manifest = {
            **{address: ("update",) for address in SECRET_ADOPTION_ADDRESSES},
            **{address: ("no-op",) for address in SECRET_ADOPTION_NOOPS},
        }
        if action_manifest != expected_manifest or imports != SECRET_ADOPTION_ADDRESSES:
            raise PlanError("secret-adoption action/import manifest differs")
        try:
            for item in changes:
                address = item["address"]
                if address in SECRET_ADOPTION_ADDRESSES:
                    plan_contract.validate_secret_adoption(address, item)
                else:
                    _validate_secret_adoption_noop(address, item)
        except plan_contract.ContractError as error:
            raise PlanError(str(error)) from error
        if counts != {
            "create": 0,
            "delete": 0,
            "forget": 0,
            "replace": 0,
            "update": 3,
        }:
            raise PlanError("secret-adoption mutation count differs")
        return counts
    default_create = EXPECTED_CREATE if profile == "foundation" else set()
    default_forget = EXPECTED_FORGET if profile == "foundation" else set()
    default_update = set(EXPECTED_UPDATE) if profile == "foundation" else set()
    create_contract = default_create if expected_create is None else expected_create
    forget_contract = default_forget if expected_forget is None else expected_forget
    update_contract = default_update if expected_update is None else expected_update
    if (
        creates != create_contract
        or forgets != forget_contract
        or updates != update_contract
    ):
        raise PlanError("Terraform plan create/update/forget manifest differs")
    for address in updates:
        _validate_redirect_proxy_update(address, changes, action_manifest)
    try:
        for item in changes:
            if item.get("address") in EXPECTED_CREATE:
                plan_contract.validate_create(item["address"], item)
    except plan_contract.ContractError as error:
        raise PlanError(str(error)) from error
    allowed_deletes = (
        {DEFERRED_SECRET_ACCESSOR_DELETE} if profile == "iam-revoke" else set()
    )
    if counts["replace"] != 0:
        raise PlanError("Terraform plan contains destroy/replacement actions")
    if deletes != allowed_deletes:
        raise PlanError("Terraform plan delete manifest differs")
    if profile == "iam-revoke":
        item = next(
            value
            for value in changes
            if value.get("address") == DEFERRED_SECRET_ACCESSOR_DELETE
        )
        try:
            plan_contract.validate_iam_delete(item)
        except plan_contract.ContractError as error:
            raise PlanError(str(error)) from error
    return counts


def _validate_secret_adoption_noop(address: str, item: dict[str, Any]) -> None:
    if address not in SECRET_ADOPTION_NOOPS:
        raise PlanError("secret-adoption dependency is not reviewed")
    service = address.removeprefix('google_project_service.required["').removesuffix(
        '"]'
    )
    if (
        set(item)
        != {"address", "mode", "type", "name", "index", "provider_name", "change"}
        or item.get("mode") != "managed"
        or item.get("type") != "google_project_service"
        or item.get("name") != "required"
        or item.get("index") != service
        or item.get("provider_name") != plan_contract.PROVIDER
    ):
        raise PlanError("secret-adoption dependency identity differs")
    values = {
        "disable_dependent_services": None,
        "disable_on_destroy": False,
        "id": f"{plan_contract.PROJECT}/{service}",
        "project": plan_contract.PROJECT,
        "service": service,
        "timeouts": None,
    }
    if item.get("change") != {
        "actions": ["no-op"],
        "before": values,
        "after": values,
        "after_unknown": {},
        "before_sensitive": {},
        "after_sensitive": {},
    }:
        raise PlanError("secret-adoption dependency no-op projection differs")


def _validate_redirect_proxy_update(
    address: str,
    changes: list[Any],
    action_manifest: dict[str, tuple[str, ...]],
) -> None:
    if address not in EXPECTED_UPDATE or action_manifest.get(address) != ("update",):
        raise PlanError("Terraform proxy update is not allowlisted")
    item = next(value for value in changes if value.get("address") == address)
    expected_name = "public" if address.endswith(".public") else "workspace"
    if (
        item.get("mode") != "managed"
        or item.get("type") != "google_compute_target_http_proxy"
        or item.get("name") != expected_name
        or item.get("provider_name") != plan_contract.PROVIDER
        or item.get("deposed") not in (None, "")
    ):
        raise PlanError("Terraform proxy provider/resource identity differs")
    change = item.get("change")
    if not isinstance(change, dict) or set(change) != {
        "actions",
        "before",
        "after",
        "after_unknown",
        "before_sensitive",
        "after_sensitive",
        "replace_paths",
    }:
        raise PlanError("Terraform proxy change shape differs")
    before = change.get("before") if isinstance(change, dict) else None
    after = change.get("after") if isinstance(change, dict) else None
    if not isinstance(before, dict) or not isinstance(after, dict):
        raise PlanError("Terraform proxy update lacks exact before/after")

    missing = object()

    def differing_paths(
        left: Any, right: Any, prefix: tuple[str, ...] = ()
    ) -> set[tuple[str, ...]]:
        if isinstance(left, dict) and isinstance(right, dict):
            paths: set[tuple[str, ...]] = set()
            for key in set(left) | set(right):
                paths |= differing_paths(
                    left.get(key, missing),
                    right.get(key, missing),
                    (*prefix, key),
                )
            return paths
        return set() if left == right else {prefix}

    def contains_unknown(value: Any) -> bool:
        if isinstance(value, dict):
            return any(contains_unknown(item) for item in value.values())
        if isinstance(value, list):
            return any(contains_unknown(item) for item in value)
        return value is not False

    if differing_paths(before, after) != {("url_map",)}:
        raise PlanError("Terraform proxy update changes more than url_map")
    old_url = before.get("url_map")
    new_url = after.get("url_map")
    url_pattern = re.compile(
        r"https://www[.]googleapis[.]com/compute/v1/projects/"
        r"([a-z][a-z0-9-]{4,29})/global/urlMaps/(omega-(staging|production)-.+)"
    )
    old_match = url_pattern.fullmatch(str(old_url))
    new_match = url_pattern.fullmatch(str(new_url))
    if old_match is None or new_match is None:
        raise PlanError("Terraform proxy url_map self-link is invalid")
    expected_old_suffix = EXPECTED_UPDATE[address]
    prefix = "omega-staging"
    if (
        old_match.group(1) != plan_contract.PROJECT
        or new_match.group(1) != plan_contract.PROJECT
        or old_match.group(3) != "staging"
        or new_match.group(3) != "staging"
        or old_match.group(2) != f"{prefix}-{expected_old_suffix}"
        or new_match.group(2) != f"{prefix}-http-redirect"
        or change.get("replace_paths") not in (None, [])
    ):
        raise PlanError("Terraform proxy redirect transition differs")
    unknown = change["after_unknown"]
    if not isinstance(unknown, dict) or contains_unknown(unknown):
        raise PlanError("Terraform proxy update contains an unknown value")
    before_sensitive = change["before_sensitive"]
    after_sensitive = change["after_sensitive"]
    if (
        not isinstance(before_sensitive, dict)
        or not isinstance(after_sensitive, dict)
        or before_sensitive != after_sensitive
        or contains_unknown(before_sensitive)
    ):
        raise PlanError("Terraform proxy sensitivity manifest differs")


def main(argv: list[str] | None = None) -> int:
    del argv
    raise PlanError(
        "standalone binary/JSON verification is forbidden; use terraform_transaction.py"
    )


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except PlanError as error:
        print(f"OMEGA_GCP_TERRAFORM_PLAN\tFAIL\t{error}", file=sys.stderr)
        raise SystemExit(1) from None
