from __future__ import annotations

import base64
import copy
import importlib.util
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
PATH = REPO / "scripts/gcp/verify_terraform_plan.py"
SPEC = importlib.util.spec_from_file_location("verify_terraform_plan", PATH)
assert SPEC is not None and SPEC.loader is not None
plan = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(plan)


def _payload(actions: list[str]) -> dict[str, object]:
    return {
        "applyable": True,
        "complete": True,
        "errored": False,
        "deferred_changes": [],
        "resource_drift": [],
        "checks": [],
        "variables": {
            "project_id": {"value": plan.plan_contract.PROJECT},
            "project_number": {"value": plan.plan_contract.PROJECT_NUMBER},
            "billing_account_id": {"value": "01A3E0-B708F4-6EA299"},
            "region": {"value": "us-central1"},
            "zone": {"value": "us-central1-a"},
            "environment": {"value": plan.plan_contract.ENVIRONMENT},
            "canonical_writer": {"value": True},
            "revoke_project_secret_accessor": {"value": False},
            "source_bucket": {
                "value": "omega-gcp-source-project-dd5ba7fa-374c-4554-ae6"
            },
            "source_object": {"value": f"deploy-artifacts/{'b' * 40}/repo.tar.gz"},
            "source_sha": {"value": "b" * 40},
            "foundation_predecessor": {"value": None},
            "source_generation": {"value": "123"},
            "source_size_bytes": {"value": 1234},
            "source_archive_sha256": {"value": "c" * 64},
            "app_machine_type": {"value": "e2-standard-4"},
            "boot_image": {
                "value": "projects/ubuntu-os-cloud/global/images/ubuntu-2204-jammy-v20260702"
            },
            "host_package_versions": {
                "value": {
                    name: "1.2.3-1"
                    for name in (
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
                    )
                }
            },
            "secret_versions": {
                "value": {
                    "control_room_evidence_signing_key_id": "1",
                    "control_room_evidence_signing_key": "2",
                    "control_room_evidence_signing_previous_keys": "3",
                    "gcs_hmac_access_key_id": "4",
                    "gcs_hmac_secret_access_key": "5",
                }
            },
            "boot_disk_size_gb": {"value": 60},
            "data_disk_size_gb": {"value": 150},
            "admin_email": {"value": "emmanuelromero060601@gmail.com"},
            "public_console_domain": {"value": "console.7businesssolutions.com"},
            "public_workspace_domain": {"value": "workspace.7businesssolutions.com"},
            "enable_https": {"value": True},
            "certificate_manager_map_name": {"value": "sevenbs-production-map"},
            "enable_lb_logging": {"value": True},
            "lb_log_sample_rate": {"value": 1},
            "enable_airflow_scheduler": {"value": True},
            "lakehouse_bucket_name": {"value": ""},
            "lakehouse_endpoint": {"value": "storage.googleapis.com"},
            "enable_budget": {"value": True},
            "monthly_budget_limit_usd": {"value": 100},
            "monthly_budget_currency": {"value": "EUR"},
            "cloud_armor_rate_limit_count": {"value": 300},
            "controller_ref": {"value": "a" * 40},
        },
        "format_version": "1.2",
        "resource_changes": [
            {
                "address": "google_compute_instance.app",
                "mode": "managed",
                "type": "google_compute_instance",
                "name": "app",
                "provider_name": "registry.opentofu.org/hashicorp/google",
                "change": {"actions": actions},
            }
        ],
    }


def _adoption_change(address: str) -> dict[str, object]:
    identity = plan.plan_contract.SECRET_ADOPTION[address]
    suffix = identity["suffix"]
    labels = plan.plan_contract.LABELS
    secret_id = f"omega-staging-{suffix}"
    resource_id = f"projects/{plan.plan_contract.PROJECT}/secrets/{secret_id}"
    replication = [{"auto": [{"customer_managed_encryption": []}], "user_managed": []}]
    before = {
        "annotations": {},
        "create_time": identity["create_time"],
        "deletion_protection": False,
        "effective_annotations": {},
        "effective_labels": labels | {"managed_by": "migration"},
        "expire_time": "",
        "id": resource_id,
        "labels": {},
        "name": f"projects/{plan.plan_contract.PROJECT_NUMBER}/secrets/{secret_id}",
        "project": plan.plan_contract.PROJECT,
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
    return {
        "address": address,
        "mode": "managed",
        "type": "google_secret_manager_secret",
        "name": "runtime",
        "index": suffix,
        "provider_name": plan.plan_contract.PROVIDER,
        "change": {
            "actions": ["update"],
            "before": before,
            "after": before
            | {
                "effective_labels": labels,
                "labels": labels,
                "terraform_labels": labels,
            },
            "after_unknown": {},
            "before_sensitive": sensitive,
            "after_sensitive": sensitive,
            "importing": {"id": resource_id},
        },
    }


def _adoption_service_change(service: str) -> dict[str, object]:
    address = f'google_project_service.required["{service}"]'
    values = {
        "disable_dependent_services": None,
        "disable_on_destroy": False,
        "id": f"{plan.plan_contract.PROJECT}/{service}",
        "project": plan.plan_contract.PROJECT,
        "service": service,
        "timeouts": None,
    }
    return {
        "address": address,
        "mode": "managed",
        "type": "google_project_service",
        "name": "required",
        "index": service,
        "provider_name": plan.plan_contract.PROVIDER,
        "change": {
            "actions": ["no-op"],
            "before": values,
            "after": values,
            "after_unknown": {},
            "before_sensitive": {},
            "after_sensitive": {},
        },
    }


def _adoption_payload() -> dict[str, object]:
    payload = _payload(["no-op"])
    for key in ("applyable", "complete", "deferred_changes", "resource_drift"):
        del payload[key]
    changes = [
        *(
            _adoption_service_change(service)
            for service in sorted(plan.SECRET_ADOPTION_SERVICES)
        ),
        *(
            _adoption_change(address)
            for address in sorted(plan.SECRET_ADOPTION_ADDRESSES)
        ),
    ]
    payload |= {
        "terraform_version": "1.11.6",
        "timestamp": "2026-08-12T04:17:46Z",
        "configuration": {"provider_config": {}, "root_module": {}},
        "prior_state": {
            "format_version": "1.0",
            "terraform_version": "1.11.6",
            "values": {},
        },
        "resource_changes": changes,
        "planned_values": {
            "root_module": {
                "resources": [
                    {key: value for key, value in item.items() if key != "change"}
                    | {
                        "schema_version": 0,
                        "values": item["change"]["after"],
                        "sensitive_values": item["change"]["after_sensitive"],
                    }
                    for item in changes
                ]
            }
        },
        "checks": [
            {
                "address": plan._expected_adoption_check_address(display),
                "status": status,
            }
            | (
                {
                    "instances": [
                        {
                            "address": {"to_display": display},
                            "status": "pass",
                        }
                    ]
                }
                if status == "pass"
                else {}
            )
            for status, displays in (
                ("unknown", plan.SECRET_ADOPTION_UNKNOWN_CHECKS),
                ("pass", plan.SECRET_ADOPTION_PASS_CHECKS),
            )
            for display in sorted(displays)
        ],
    }
    return payload


def test_secret_adoption_accepts_only_the_three_captured_label_updates() -> None:
    payload = _adoption_payload()
    assert plan.validate_plan(payload, profile="secret-adoption") == {
        "create": 0,
        "delete": 0,
        "forget": 0,
        "replace": 0,
        "update": 3,
    }
    assert {
        item["change"]["importing"]["id"]
        for item in payload["resource_changes"]
        if item["address"] in plan.SECRET_ADOPTION_ADDRESSES
    } == {
        "projects/project-dd5ba7fa-374c-4554-ae6/secrets/"
        "omega-staging-control_room_evidence_signing_key",
        "projects/project-dd5ba7fa-374c-4554-ae6/secrets/"
        "omega-staging-control_room_evidence_signing_key_id",
        "projects/project-dd5ba7fa-374c-4554-ae6/secrets/"
        "omega-staging-control_room_evidence_signing_previous_keys",
    }


@pytest.mark.parametrize(
    "mutation",
    [
        lambda item: item["change"]["importing"].update(
            id="projects/attacker/secrets/x"
        ),
        lambda item: item["change"].update(actions=["create"]),
        lambda item: item["change"]["after"].update(deletion_protection=True),
        lambda item: item["change"]["after"].update(labels={"managed_by": "terraform"}),
        lambda item: item["change"].update(after_unknown={"secret_data": True}),
        lambda item: item["change"].update(replace_paths=[]),
    ],
)
def test_secret_adoption_fails_closed_on_import_or_provider_shape_drift(
    mutation,
) -> None:
    payload = _adoption_payload()
    item = next(
        value
        for value in payload["resource_changes"]
        if value["address"] in plan.SECRET_ADOPTION_ADDRESSES
    )
    mutation(item)
    with pytest.raises(plan.PlanError):
        plan.validate_plan(payload, profile="secret-adoption")


def test_secret_adoption_rejects_secret_versions_and_caller_manifests() -> None:
    payload = _adoption_payload()
    payload["configuration"]["root_module"] = {
        "resources": [
            {
                "address": "google_secret_manager_secret_version.payload",
                "type": "google_secret_manager_secret_version",
            }
        ]
    }
    with pytest.raises(plan.PlanError, match="unreviewed resource projection"):
        plan.validate_plan(payload, profile="secret-adoption")
    with pytest.raises(plan.PlanError, match="cannot be caller-overridden"):
        plan.validate_plan(
            _adoption_payload(),
            profile="secret-adoption",
            expected_update=set(plan.SECRET_ADOPTION_ADDRESSES),
        )


def test_foundation_excludes_adopted_creates_and_rejects_all_imports() -> None:
    assert plan.EXPECTED_CREATE.isdisjoint(plan.SECRET_ADOPTION_ADDRESSES)
    payload = _payload(["no-op"])
    payload["resource_changes"][0]["change"]["importing"] = {"id": "surprise"}
    with pytest.raises(plan.PlanError, match="imports require"):
        plan.validate_plan(
            payload,
            expected_create=set(),
            expected_forget=set(),
            expected_update=set(),
        )


def test_secret_adoption_planned_values_are_bound_to_the_exact_change() -> None:
    payload = _adoption_payload()
    planned = next(
        value
        for value in payload["planned_values"]["root_module"]["resources"]
        if value["address"] in plan.SECRET_ADOPTION_ADDRESSES
    )
    planned["values"] = copy.deepcopy(planned["values"])
    planned["values"]["secret_data"] = "must-never-appear"
    with pytest.raises(plan.PlanError, match="planned/change projection"):
        plan.validate_plan(payload, profile="secret-adoption")


@pytest.mark.parametrize("actions", [["delete", "create"], ["create", "delete"]])
def test_destroy_or_replacement_is_a_hard_failure(actions: list[str]) -> None:
    with pytest.raises(plan.PlanError, match="destroy/replacement"):
        plan.validate_plan(
            _payload(actions),
            expected_create=set(),
            expected_forget=set(),
            expected_update=set(),
        )


@pytest.mark.parametrize("actions", [["no-op"], ["create"], ["read"]])
def test_non_destructive_actions_are_accepted(actions: list[str]) -> None:
    counts = plan.validate_plan(
        _payload(actions),
        expected_create={"google_compute_instance.app"}
        if actions == ["create"]
        else set(),
        expected_forget=set(),
        expected_update=set(),
    )
    assert counts["delete"] == 0
    assert counts["replace"] == 0


def test_forget_requires_the_exact_reviewed_manifest() -> None:
    payload = _payload(["forget"])
    with pytest.raises(plan.PlanError, match="manifest differs"):
        plan.validate_plan(
            payload,
            expected_create=set(),
            expected_forget=set(),
            expected_update=set(),
        )
    assert (
        plan.validate_plan(
            payload,
            expected_create=set(),
            expected_forget={"google_compute_instance.app"},
            expected_update=set(),
        )["forget"]
        == 1
    )


def test_project_wide_secret_accessor_delete_requires_explicit_transaction() -> None:
    payload = _payload(["delete"])
    payload["resource_changes"][0] = {
        "address": plan.DEFERRED_SECRET_ACCESSOR_DELETE,
        "mode": "managed",
        "type": "google_project_iam_member",
        "name": "app_project_roles",
        "provider_name": plan.plan_contract.PROVIDER,
        "change": {
            "actions": ["delete"],
            "before": {
                "project": plan.plan_contract.PROJECT,
                "role": "roles/secretmanager.secretAccessor",
                "member": plan.plan_contract.SERVICE_ACCOUNT,
            },
            "after": None,
            "after_unknown": {},
            "before_sensitive": {},
            "after_sensitive": False,
            "replace_paths": [],
        },
    }
    with pytest.raises(plan.PlanError, match="delete manifest"):
        plan.validate_plan(
            payload,
            expected_create=set(),
            expected_forget=set(),
            expected_update=set(),
        )
    payload["variables"]["revoke_project_secret_accessor"]["value"] = True
    counts = plan.validate_plan(
        payload,
        profile="iam-revoke",
        expected_create=set(),
        expected_forget=set(),
        expected_update=set(),
    )
    assert counts["delete"] == 1


def _proxy_update(address: str, old_leaf: str, new_leaf: str = "http-redirect") -> dict:
    prefix = (
        f"https://www.googleapis.com/compute/v1/projects/{plan.plan_contract.PROJECT}/"
        "global/urlMaps/omega-staging-"
    )
    return {
        "address": address,
        "mode": "managed",
        "type": "google_compute_target_http_proxy",
        "name": address.rsplit(".", 1)[-1],
        "provider_name": "registry.opentofu.org/hashicorp/google",
        "change": {
            "actions": ["update"],
            "before": {"name": "stable", "url_map": prefix + old_leaf},
            "after": {"name": "stable", "url_map": prefix + new_leaf},
            "after_unknown": {"url_map": False},
            "before_sensitive": {"url_map": False},
            "after_sensitive": {"url_map": False},
            "replace_paths": [],
        },
    }


def _plan_payload(*changes: dict) -> dict[str, object]:
    payload = _payload(["no-op"])
    payload["resource_changes"] = list(changes)
    return payload


def test_only_two_exact_in_place_plaintext_proxy_redirects_are_allowed() -> None:
    payload = _plan_payload(
        _proxy_update("google_compute_target_http_proxy.public", "url-map"),
        _proxy_update(
            "google_compute_target_http_proxy.workspace",
            "workspace-url-map",
        ),
    )
    counts = plan.validate_plan(
        payload,
        expected_create=set(),
        expected_forget=set(),
    )
    assert counts == {
        "create": 0,
        "delete": 0,
        "forget": 0,
        "replace": 0,
        "update": 2,
    }


@pytest.mark.parametrize(
    "mutation",
    [
        lambda item: item["change"]["after"].update(name="changed"),
        lambda item: item["change"]["after"].update(url_map="evil"),
        lambda item: item["change"]["after_unknown"].update(url_map=True),
        lambda item: item["change"]["after_unknown"].update(
            http_keep_alive_timeout_sec=True
        ),
        lambda item: item["change"]["before_sensitive"].update(url_map=True),
        lambda item: item["change"]["after_sensitive"].update(other=False),
        lambda item: item["change"].update(importing={"id": "surprise"}),
        lambda item: item["change"].update(replace_paths=[["url_map"]]),
    ],
)
def test_proxy_redirect_update_fails_closed_on_extra_or_unknown_change(
    mutation,
) -> None:
    item = _proxy_update("google_compute_target_http_proxy.public", "url-map")
    mutation(item)
    payload = _plan_payload(item)
    with pytest.raises(plan.PlanError):
        plan.validate_plan(
            payload,
            expected_create=set(),
            expected_forget=set(),
            expected_update={"google_compute_target_http_proxy.public"},
        )


@pytest.mark.parametrize(
    "url",
    [
        "https://www.googleapis.com/compute/v1/projects/attacker-project/global/urlMaps/omega-staging-url-map",
        f"https://www.googleapis.com/compute/v1/projects/{plan.plan_contract.PROJECT}/global/urlMaps/omega-production-url-map",
    ],
)
def test_proxy_update_rejects_other_project_or_environment(url: str) -> None:
    item = _proxy_update("google_compute_target_http_proxy.public", "url-map")
    item["change"]["before"]["url_map"] = url
    item["change"]["after"]["url_map"] = url.rsplit("-", 2)[0] + "-http-redirect"
    with pytest.raises(plan.PlanError):
        plan.validate_plan(
            _plan_payload(item),
            expected_create=set(),
            expected_forget=set(),
            expected_update={"google_compute_target_http_proxy.public"},
        )


def test_proxy_diff_distinguishes_absent_key_from_explicit_null() -> None:
    item = _proxy_update("google_compute_target_http_proxy.public", "url-map")
    item["change"]["before"]["nullable"] = None
    with pytest.raises(plan.PlanError, match="more than url_map"):
        plan.validate_plan(
            _plan_payload(item),
            expected_create=set(),
            expected_forget=set(),
            expected_update={"google_compute_target_http_proxy.public"},
        )


def _malicious_reviewed_create(address: str) -> dict[str, object]:
    resource_type, name, _ = plan.plan_contract._expected_identity(address)
    return {
        "address": address,
        "mode": "managed",
        "type": resource_type,
        "name": name,
        "provider_name": plan.plan_contract.PROVIDER,
        "change": {
            "actions": ["create"],
            "before": None,
            "after": {
                "project": "attacker-project",
                "member": "allUsers",
                "permissions": ["storage.buckets.delete"],
            },
            "after_unknown": {"id": True},
            "before_sensitive": False,
            "after_sensitive": {},
            "replace_paths": [],
        },
    }


def test_exact_create_addresses_do_not_authorize_malicious_resource_content() -> None:
    changes = [
        _malicious_reviewed_create(address) for address in sorted(plan.EXPECTED_CREATE)
    ]
    changes.extend(
        [
            _proxy_update("google_compute_target_http_proxy.public", "url-map"),
            _proxy_update(
                "google_compute_target_http_proxy.workspace", "workspace-url-map"
            ),
        ]
    )
    with pytest.raises(plan.PlanError, match="create projection differs"):
        plan.validate_plan(_plan_payload(*changes))


def test_create_rejects_unknown_security_field_and_wrong_provider() -> None:
    address = "google_project_iam_custom_role.release_backup_writer"
    item = _malicious_reviewed_create(address)
    item["change"]["after_unknown"] = {"permissions": True}
    with pytest.raises(
        plan.plan_contract.ContractError, match="create projection differs"
    ):
        plan.plan_contract.validate_create(address, item)
    item["change"]["after_unknown"] = {"id": True}
    item["provider_name"] = "registry.opentofu.org/attacker/google"
    with pytest.raises(
        plan.plan_contract.ContractError, match="provider/resource identity"
    ):
        plan.plan_contract.validate_create(address, item)


@pytest.mark.parametrize(
    ("key", "value"),
    [
        ("applyable", False),
        ("complete", False),
        ("errored", True),
        ("deferred_changes", [{"reason": "provider"}]),
    ],
)
def test_incomplete_errored_deferred_or_drifted_plan_fails_closed(
    key: str, value: object
) -> None:
    payload = _payload(["no-op"])
    payload[key] = value
    with pytest.raises(plan.PlanError, match="incomplete, errored, or deferred"):
        plan.validate_plan(
            payload,
            expected_create=set(),
            expected_forget=set(),
            expected_update=set(),
        )


def test_unreviewed_resource_drift_fails_closed() -> None:
    payload = _payload(["no-op"])
    payload["resource_drift"] = [{"address": "google_compute_instance.attacker"}]
    with pytest.raises(plan.PlanError, match="resource_drift address manifest differs"):
        plan.validate_plan(
            payload,
            expected_create=set(),
            expected_forget=set(),
            expected_update=set(),
        )


@pytest.mark.parametrize("status", ["fail", "unknown", None])
def test_every_plan_check_must_explicitly_pass(status: object) -> None:
    payload = _payload(["no-op"])
    payload["checks"] = [{"address": {"kind": "check"}, "status": status}]
    with pytest.raises(plan.PlanError, match="checks are not all pass"):
        plan.validate_plan(
            payload,
            expected_create=set(),
            expected_forget=set(),
            expected_update=set(),
        )


def _release_authority(payload: dict[str, object]) -> dict[str, object]:
    variables = payload["variables"]
    contract = plan.plan_contract.release_authority_contract

    def value(name: str) -> object:
        return variables[name]["value"]

    source_sha = value("source_sha")
    controller_ref = value("controller_ref")
    checks = []
    for index, name in enumerate(contract.REQUIRED_CHECKS, start=1):
        workflow_path, gate = contract.WORKFLOWS[name]
        checks.append(
            {
                "conclusion": "success",
                "gate_job": gate,
                "gate_job_id": index,
                "head_sha": source_sha,
                "name": name,
                "query_sha256": format(index, "x") * 64,
                "run_attempt": 1,
                "status": "completed",
                "workflow_path": workflow_path,
                "workflow_run_id": 100 + index,
            }
        )
    authority = {
        "approved_main_sha": value("source_sha"),
        "boot_image": value("boot_image"),
        "canonical_writer": value("canonical_writer"),
        "certificate_manager_map_name": value("certificate_manager_map_name"),
        "controller_ref": controller_ref,
        "enable_airflow_scheduler": value("enable_airflow_scheduler"),
        "foundation_predecessor": value("foundation_predecessor"),
        "host_package_versions": value("host_package_versions"),
        "lakehouse_bucket_name": value("lakehouse_bucket_name"),
        "lakehouse_endpoint": value("lakehouse_endpoint"),
        "old_live_startup_sha256": "3" * 64,
        "provenance": {
            "generator": {
                "content_sha256": "1" * 64,
                "git_blob_id": "2" * 40,
                "path": contract.GENERATOR,
            },
            "git": {
                "controller_ref": controller_ref,
                "main_ref": "refs/heads/main",
                "main_sha": source_sha,
                "repository": contract.REPOSITORY,
                "source_tree_sha": "3" * 40,
            },
            "github": {
                "checks": checks,
                "commit_url": f"https://github.com/{contract.REPOSITORY}/commit/{source_sha}",
                "main_ref": "refs/heads/main",
                "main_sha": source_sha,
                "query_sha256": "4" * 64,
                "repository": contract.REPOSITORY,
            },
            "live_vm": {
                "boot_disk": "omega-staging-app",
                "boot_image": value("boot_image"),
                "canonical_writer": True,
                "enable_airflow_scheduler": True,
                "instance_id": "123456789",
                "name": contract.INSTANCE,
                "old_startup_sha256": "3" * 64,
                "project": contract.PROJECT,
                "query_sha256": "5" * 64,
                "service_account": contract.SERVICE_ACCOUNT,
                "startup_contract_schema": "legacy-pre-foundation",
                "startup_controller_ref": None,
                "startup_source_ref": "6" * 40,
                "zone": contract.ZONE,
            },
            "package_lock": {
                "content_sha256": "7" * 64,
                "git_blob_id": "8" * 40,
                "path": contract.PACKAGE_LOCK,
                "schema_version": 1,
            },
            "secret_versions": [
                {
                    "create_time": "2026-08-12T00:00:00Z",
                    "etag": f'"etag-{index}"',
                    "name": name,
                    "query_sha256": format(index + 10, "x") * 64,
                    "state": "ENABLED",
                    "version": value("secret_versions")[name],
                }
                for index, name in enumerate(contract.SECRET_NAMES)
            ],
            "source_object": {
                "bucket": value("source_bucket"),
                "crc32c": base64.b64encode(bytes(4)).decode(),
                "custom_metadata_sha256": None,
                "generation": value("source_generation"),
                "git_tree_sha": "3" * 40,
                "md5_hash": base64.b64encode(bytes(16)).decode(),
                "metageneration": "1",
                "name": value("source_object"),
                "query_sha256": "d" * 64,
                "sha256": value("source_archive_sha256"),
                "sha256_source": "generation-bound-download",
                "size": value("source_size_bytes"),
                "storage_class": "STANDARD",
                "updated": "2026-08-12T00:00:00Z",
            },
            "tools": {
                "gcloud": {
                    "content_sha256": contract.TOOL_HASHES["gcloud CLI"],
                    "path": contract.TOOL_PATHS["gcloud CLI"],
                    "size": 5962,
                },
                "gh": {
                    "content_sha256": contract.TOOL_HASHES["GitHub CLI"],
                    "path": contract.TOOL_PATHS["GitHub CLI"],
                    "size": 37448466,
                },
            },
        },
        "public_console_domain": value("public_console_domain"),
        "public_workspace_domain": value("public_workspace_domain"),
        "schema_version": 2,
        "secret_versions": value("secret_versions"),
        "source_archive_sha256": value("source_archive_sha256"),
        "source_bucket": value("source_bucket"),
        "source_generation": value("source_generation"),
        "source_object": value("source_object"),
        "source_sha": value("source_sha"),
        "source_size_bytes": value("source_size_bytes"),
    }
    authority["evidence_chain_sha256"] = contract._sha(contract._canonical(authority))
    return authority


def test_release_authority_must_match_every_external_release_input_exactly() -> None:
    payload = _payload(["no-op"])
    authority = _release_authority(payload)
    assert (
        plan.plan_contract.validate_authority_variables(
            payload, release_authority=authority
        )
        == "a" * 40
    )
    authority["source_generation"] = "999999"
    with pytest.raises(plan.plan_contract.ContractError, match="reviewed authority"):
        plan.plan_contract.validate_authority_variables(
            payload, release_authority=authority
        )


@pytest.mark.parametrize(
    ("key", "value"),
    [
        ("source_sha", "d" * 40),
        (
            "boot_image",
            "projects/ubuntu-os-cloud/global/images/ubuntu-2204-jammy-v20991231",
        ),
        (
            "host_package_versions",
            {
                name: "9.9.9-attacker"
                for name in _payload(["no-op"])["variables"]["host_package_versions"][
                    "value"
                ]
            },
        ),
    ],
)
def test_syntactically_valid_but_unapproved_release_input_is_rejected(
    key: str, value: object
) -> None:
    payload = _payload(["no-op"])
    authority = _release_authority(payload)
    payload["variables"][key]["value"] = value
    if key == "source_sha":
        payload["variables"]["source_object"]["value"] = (
            f"deploy-artifacts/{value}/repo.tar.gz"
        )
    with pytest.raises(plan.plan_contract.ContractError, match="reviewed authority"):
        plan.plan_contract.validate_authority_variables(
            payload, release_authority=authority
        )
