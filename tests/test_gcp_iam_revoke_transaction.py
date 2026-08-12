from __future__ import annotations

import argparse
import copy
import importlib.util
import json
import os
import stat
import subprocess
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest


REPO = Path(__file__).resolve().parents[1]
SCRIPT = REPO / "scripts/gcp/iam_revoke_transaction.py"
SPEC = importlib.util.spec_from_file_location(
    "omega_gcp_iam_revoke_transaction", SCRIPT
)
assert SPEC is not None and SPEC.loader is not None
iam = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(iam)


def _file_identity(path: str, *, executable: bool = False) -> dict[str, object]:
    return {
        "path": path,
        "device": 1,
        "inode": 2,
        "mode": 0o500 if executable else 0o400,
        "size": 10,
        "mtime_ns": 3,
        "sha256": "a" * 64,
    }


def _directory_identity(path: str) -> dict[str, object]:
    return {
        "path": path,
        "device": 1,
        "inode": 2,
        "mode": 0o700,
        "uid": os.geteuid(),
    }


def _matrix(stage: str) -> dict[str, object]:
    return {
        "status": "PASS",
        "stage": stage,
        "resource_grants": 6,
        "forbidden_resources_checked": 64 if stage == "revoke" else 0,
        "forbidden_access": 0,
        "canary_denied": stage == "revoke",
        "secret_values_read": False,
    }


def _check(stage: str, *, broad: bool, compensated: bool = False) -> dict[str, object]:
    result: dict[str, object] = {
        "broad_project_grant": broad,
        "resource_grants": list(iam.RESOURCE_GRANTS),
        "matrix": _matrix(stage),
    }
    if compensated:
        result["terraform_state"] = "requires-new-sealed-reconciliation"
    return result


def _manifest() -> dict[str, object]:
    return {
        "schema_version": 1,
        "profile": "iam-revoke",
        "state": "sealed",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "project": iam.PROJECT,
        "environment": iam.ENVIRONMENT,
        "zone": iam.ZONE,
        "instance": iam.INSTANCE,
        "role": iam.ROLE,
        "member": iam.MEMBER,
        "resource_grants": list(iam.RESOURCE_GRANTS),
        "forbidden_resource_count": 64,
        "source": {
            "git_head": "b" * 40,
            "files": {name: "c" * 64 for name in iam.SOURCE_MEMBERS},
        },
        "operator": {
            "account": "operator@example.com",
            "project": iam.PROJECT,
            "gcloud": _file_identity(
                "/opt/google-cloud-sdk/bin/gcloud", executable=True
            ),
            "gcloud_config": _directory_identity("/private/gcloud"),
        },
        "inputs": {
            "tfvars": _file_identity("/private/terraform.tfvars"),
            "release_authority": _file_identity("/private/release-authority.json"),
            "tofu": _file_identity("/private/tofu", executable=True),
            "gh_config": _directory_identity("/private/gh"),
        },
        "matrix_verifier": {
            "member": "matrix-verifier.sh",
            "device": 1,
            "inode": 2,
            "mode": 0o400,
            "size": 10,
            "mtime_ns": 3,
            "sha256": "d" * 64,
        },
        "terraform": {
            "directory": "terraform",
            "manifest_sha256": "e" * 64,
            "manifest_inode": 9,
            "plan_sha256": "f" * 64,
            "plan_json_sha256": "0" * 64,
        },
        "precheck": _check("grants", broad=True),
    }


def _args(transaction: Path) -> argparse.Namespace:
    return argparse.Namespace(
        transaction=transaction,
        gcloud_config=Path("/private/gcloud"),
        gh_config=Path("/private/gh"),
        tfvars=Path("/private/terraform.tfvars"),
        tofu=Path("/private/tofu"),
        expected_account="operator@example.com",
    )


def _sealed_transaction(tmp_path: Path) -> tuple[Path, dict[str, object]]:
    transaction = tmp_path / "iam-transaction"
    transaction.mkdir(mode=0o700)
    (transaction / "terraform").mkdir(mode=0o700)
    (transaction / "events").mkdir(mode=0o700)
    verifier = transaction / "matrix-verifier.sh"
    verifier.write_bytes(b"#!/bin/bash\n")
    verifier.chmod(0o400)
    info = verifier.stat()
    manifest = _manifest()
    manifest["matrix_verifier"] = iam._identity(info, verifier.read_bytes()) | {
        "member": "matrix-verifier.sh"
    }
    (transaction / "manifest.json").write_bytes(iam._canonical(manifest))
    (transaction / "manifest.json").chmod(0o400)
    return transaction, manifest


def _event_values(transaction: Path) -> list[dict[str, object]]:
    descriptor = os.open(transaction / "events", os.O_RDONLY | os.O_DIRECTORY)
    try:
        return iam._events(descriptor)
    finally:
        os.close(descriptor)


def test_inventory_is_closed_and_matches_the_live_probe_contract() -> None:
    assert iam.RESOURCE_GRANTS == (
        "control_room_evidence_signing_key_id",
        "control_room_evidence_signing_key",
        "control_room_evidence_signing_previous_keys",
        "gcs_hmac_access_key_id",
        "gcs_hmac_secret_access_key",
        "ghcr_pull_credentials",
    )
    assert iam.FORBIDDEN_RESOURCE_COUNT == 64
    source = (REPO / "scripts/gcp/verify-secret-access.sh").read_text(encoding="utf-8")
    assert "if len(forbidden) != 64" in source
    assert '"resource_grants": len(allow)' in source
    assert '"secret_values_read": False' in source


def test_manifest_is_canonical_closed_and_bool_is_not_an_integer() -> None:
    manifest = _manifest()
    raw = iam._canonical(manifest)
    assert iam._validate_manifest(manifest, raw) == manifest

    extra = copy.deepcopy(manifest)
    extra["unreviewed"] = True
    with pytest.raises(iam.IAMTransactionError, match="shape differs"):
        iam._validate_manifest(extra, iam._canonical(extra))

    boolean_schema = copy.deepcopy(manifest)
    boolean_schema["schema_version"] = True
    with pytest.raises(iam.IAMTransactionError, match="authority differs"):
        iam._validate_manifest(boolean_schema, iam._canonical(boolean_schema))

    pretty = json.dumps(manifest, indent=2).encode()
    with pytest.raises(iam.IAMTransactionError, match="canonical"):
        iam._validate_manifest(manifest, pretty)


@pytest.mark.parametrize(
    ("mutation", "message"),
    [
        (
            lambda value: value["source"]["files"].pop(iam.SOURCE_MEMBERS[0]),
            "source identity",
        ),
        (lambda value: value["operator"]["gcloud"].update(mode=0o777), "file identity"),
        (
            lambda value: value["inputs"]["gh_config"].update(inode=True),
            "directory identity",
        ),
        (
            lambda value: value["terraform"].update(plan_sha256="bad"),
            "Terraform authority",
        ),
        (
            lambda value: value["precheck"]["matrix"].update(secret_values_read=True),
            "precheck",
        ),
    ],
)
def test_manifest_rejects_nested_authority_drift(mutation, message: str) -> None:
    manifest = _manifest()
    mutation(manifest)
    with pytest.raises(iam.IAMTransactionError, match=message):
        iam._validate_manifest(manifest, iam._canonical(manifest))


def test_policy_binding_requires_one_unconditional_exact_member() -> None:
    policy = {
        "bindings": [
            {
                "role": iam.ROLE,
                "members": [iam.MEMBER, "serviceAccount:other@example.com"],
            },
            {"role": "roles/viewer", "members": [iam.MEMBER]},
        ]
    }
    assert iam._unconditional_binding_count(policy, iam.ROLE, iam.MEMBER) == 1

    conditional = copy.deepcopy(policy)
    conditional["bindings"][0]["condition"] = {"expression": "true"}
    with pytest.raises(iam.IAMTransactionError, match="conditional or ambiguous"):
        iam._unconditional_binding_count(conditional, iam.ROLE, iam.MEMBER)

    malformed = {"bindings": [{"role": iam.ROLE, "members": [3]}]}
    with pytest.raises(iam.IAMTransactionError, match="member shape"):
        iam._unconditional_binding_count(malformed, iam.ROLE, iam.MEMBER)


def test_resource_grants_queries_exactly_six_resource_policies(monkeypatch) -> None:
    seen: list[list[str]] = []

    def policy(_binary, _identity, _config, arguments, _label):
        seen.append(arguments)
        return {"bindings": [{"role": iam.ROLE, "members": [iam.MEMBER]}]}

    monkeypatch.setattr(iam, "_policy", policy)
    assert iam._resource_grants(Path("/gcloud"), {}, Path("/config")) == list(
        iam.RESOURCE_GRANTS
    )
    assert seen == [
        [
            "secrets",
            "get-iam-policy",
            f"omega-{iam.ENVIRONMENT}-{suffix}",
            f"--project={iam.PROJECT}",
        ]
        for suffix in iam.RESOURCE_GRANTS
    ]


def test_effective_matrix_streams_reviewed_probe_and_accepts_only_exact_result(
    monkeypatch,
) -> None:
    captured: dict[str, object] = {}

    def gcloud(_binary, _identity, _config, arguments, **kwargs):
        captured["arguments"] = arguments
        captured["input"] = kwargs["input_bytes"]
        return iam._canonical(_matrix("revoke"))

    monkeypatch.setattr(iam, "_gcloud", gcloud)
    verifier = b"#!/bin/bash -p\nserver-owned-verifier\n"
    assert iam._matrix(
        Path("/gcloud"), {}, Path("/config"), verifier, "revoke"
    ) == _matrix("revoke")
    assert captured["input"] == verifier
    arguments = captured["arguments"]
    assert "--tunnel-through-iap" in arguments
    assert any(
        item.endswith(f"{iam.PROJECT} {iam.ENVIRONMENT} revoke") for item in arguments
    )
    assert all("token" not in item.lower() for item in arguments)

    monkeypatch.setattr(
        iam,
        "_gcloud",
        lambda *_args, **_kwargs: iam._canonical(
            _matrix("revoke") | {"forbidden_resources_checked": 63}
        ),
    )
    with pytest.raises(iam.IAMTransactionError, match="matrix result differs"):
        iam._matrix(Path("/gcloud"), {}, Path("/config"), verifier, "revoke")


def test_pre_and_postcheck_repeat_project_binding_read_after_full_matrix(
    monkeypatch,
) -> None:
    monkeypatch.setattr(
        iam, "_resource_grants", lambda *_args: list(iam.RESOURCE_GRANTS)
    )
    monkeypatch.setattr(
        iam,
        "_matrix",
        lambda _binary, _identity, _config, _raw, stage: _matrix(stage),
    )
    pre_states = iter((True, False))
    monkeypatch.setattr(iam, "_broad_binding", lambda *_args: next(pre_states))
    with pytest.raises(iam.IAMTransactionError, match="changed during precheck"):
        iam._precheck(Path("/gcloud"), {}, Path("/config"), b"probe")

    post_states = iter((False, True))
    monkeypatch.setattr(iam, "_broad_binding", lambda *_args: next(post_states))
    with pytest.raises(iam.IAMTransactionError, match="changed during postcheck"):
        iam._postcheck(Path("/gcloud"), {}, Path("/config"), b"probe")


def test_restore_uses_no_condition_no_output_then_verifies_every_gate(
    monkeypatch,
) -> None:
    calls: list[list[str]] = []

    def gcloud(_binary, _identity, _config, arguments, **_kwargs):
        calls.append(arguments)
        return b""

    monkeypatch.setattr(iam, "_gcloud", gcloud)
    monkeypatch.setattr(
        iam,
        "_operator",
        lambda *_args: {"account": "operator@example.com", "project": iam.PROJECT},
    )
    monkeypatch.setattr(iam, "_broad_binding", lambda *_args: True)
    monkeypatch.setattr(
        iam, "_resource_grants", lambda *_args: list(iam.RESOURCE_GRANTS)
    )
    monkeypatch.setattr(iam, "_matrix", lambda *_args: _matrix("grants"))
    assert iam._restore(
        Path("/gcloud"),
        {},
        Path("/config"),
        b"probe",
        "operator@example.com",
    ) == _check("grants", broad=True, compensated=True)
    assert calls == [
        [
            "--account=operator@example.com",
            f"--project={iam.PROJECT}",
            "projects",
            "add-iam-policy-binding",
            iam.PROJECT,
            f"--member={iam.MEMBER}",
            f"--role={iam.ROLE}",
            "--condition=None",
            "--quiet",
            "--format=none",
        ]
    ]


def test_events_are_append_only_canonical_and_manifest_bound(tmp_path: Path) -> None:
    directory = tmp_path / "events"
    directory.mkdir(mode=0o700)
    descriptor = os.open(directory, os.O_RDONLY | os.O_DIRECTORY)
    values: list[dict[str, object]] = []
    manifest_sha = "a" * 64
    try:
        iam._append_event(
            descriptor,
            values,
            manifest_sha,
            "mutation-intent",
            {"state": "mutation-authorized"},
        )
        iam._append_event(
            descriptor,
            values,
            manifest_sha,
            "delete-applied",
            {"state": "delete-command-complete"},
        )
        iam._append_event(
            descriptor,
            values,
            manifest_sha,
            "postcheck-passed",
            {"result": _check("revoke", broad=False)},
        )
        iam._append_event(
            descriptor,
            values,
            manifest_sha,
            "terminal",
            {"state": "applied-and-postchecked"},
        )
        loaded = iam._events(descriptor, manifest_sha)
    finally:
        os.close(descriptor)
    assert [item["event"] for item in loaded] == [
        "mutation-intent",
        "delete-applied",
        "postcheck-passed",
        "terminal",
    ]
    assert all(
        stat.S_IMODE(path.stat().st_mode) == 0o400 for path in directory.iterdir()
    )

    first = directory / "00000001-mutation-intent.json"
    first.chmod(0o600)
    first.write_text(first.read_text(encoding="utf-8") + " ", encoding="utf-8")
    first.chmod(0o400)
    descriptor = os.open(directory, os.O_RDONLY | os.O_DIRECTORY)
    try:
        with pytest.raises(iam.IAMTransactionError):
            iam._events(descriptor, manifest_sha)
    finally:
        os.close(descriptor)


def test_event_history_rejects_result_without_attempt_and_wrong_manifest(
    tmp_path: Path,
) -> None:
    directory = tmp_path / "events"
    directory.mkdir(mode=0o700)
    descriptor = os.open(directory, os.O_RDONLY | os.O_DIRECTORY)
    values: list[dict[str, object]] = []
    try:
        iam._append_event(
            descriptor,
            values,
            "a" * 64,
            "mutation-intent",
            {"state": "mutation-authorized"},
        )
        iam._append_event(
            descriptor,
            values,
            "a" * 64,
            "compensation-verified",
            {
                "trigger": "apply-failure",
                "result": _check("grants", broad=True, compensated=True),
            },
        )
        with pytest.raises(iam.IAMTransactionError, match="lacks an attempt"):
            iam._events(descriptor, "a" * 64)
        with pytest.raises(iam.IAMTransactionError, match="another manifest"):
            iam._events(descriptor, "b" * 64)
    finally:
        os.close(descriptor)


def test_partial_atomic_event_crash_residue_is_removed_before_recovery(
    tmp_path: Path,
) -> None:
    directory = tmp_path / "events"
    directory.mkdir(mode=0o700)
    residue = directory / (".event-tmp-00000001-mutation-intent-0123456789abcdef")
    residue.write_bytes(b'{"partial":')
    residue.chmod(0o400)
    descriptor = os.open(directory, os.O_RDONLY | os.O_DIRECTORY)
    try:
        assert iam._events(descriptor, "a" * 64) == []
    finally:
        os.close(descriptor)
    assert list(directory.iterdir()) == []


def test_post_link_atomic_event_crash_residue_recovers_complete_event(
    tmp_path: Path,
) -> None:
    directory = tmp_path / "events"
    directory.mkdir(mode=0o700)
    value = {
        "schema_version": 1,
        "sequence": 1,
        "event": "mutation-intent",
        "manifest_sha256": "a" * 64,
        "occurred_at": datetime.now(timezone.utc).isoformat(),
        "details": {"state": "mutation-authorized"},
    }
    residue = directory / (".event-tmp-00000001-mutation-intent-fedcba9876543210")
    residue.write_bytes(iam._canonical(value))
    residue.chmod(0o400)
    canonical = directory / "00000001-mutation-intent.json"
    os.link(residue, canonical)
    descriptor = os.open(directory, os.O_RDONLY | os.O_DIRECTORY)
    try:
        assert iam._events(descriptor, "a" * 64) == [value]
    finally:
        os.close(descriptor)
    assert not residue.exists()
    assert canonical.stat().st_nlink == 1


def _patch_apply_authority(monkeypatch, manifest: dict[str, object]) -> None:
    monkeypatch.setattr(
        iam,
        "_validate_runtime_identity",
        lambda *_args: (Path("/sealed/gcloud"), {"sealed": True}, b"probe"),
    )
    monkeypatch.setattr(iam, "_precheck", lambda *_args: manifest["precheck"])


def test_plan_delegates_to_an_absent_nested_terraform_transaction(
    tmp_path: Path, monkeypatch
) -> None:
    transaction = tmp_path / "iam-transaction"
    transaction.mkdir(mode=0o700)
    verifier = tmp_path / "verifier.sh"
    verifier.write_bytes(b"#!/bin/bash -p\n")
    verifier.chmod(0o500)
    source = {
        "git_head": "a" * 40,
        "files": {name: "b" * 64 for name in iam.SOURCE_MEMBERS},
    }
    gcloud_identity = _file_identity("/gcloud", executable=True)
    directory_identity = _directory_identity("/config")
    operator = {"account": "operator@example.com", "project": iam.PROJECT}
    terraform_summary = {
        "directory": "terraform",
        "manifest_sha256": "c" * 64,
        "manifest_inode": 4,
        "plan_sha256": "d" * 64,
        "plan_json_sha256": "e" * 64,
    }
    observed: list[bool] = []

    monkeypatch.setattr(iam, "_source_identity", lambda: source)
    monkeypatch.setattr(
        iam,
        "_executable_identity",
        lambda _name: (Path("/gcloud"), gcloud_identity),
    )
    monkeypatch.setattr(iam, "_directory_identity", lambda *_args: directory_identity)
    monkeypatch.setattr(iam, "_operator", lambda *_args: operator)
    real_safe_file = iam._safe_file
    monkeypatch.setattr(
        iam,
        "_safe_file",
        lambda *_args, **_kwargs: real_safe_file(
            verifier, label="verifier", executable=True
        ),
    )
    monkeypatch.setattr(
        iam,
        "_input_identity",
        lambda path, _label, **_kwargs: _file_identity(str(path)),
    )
    monkeypatch.setattr(iam, "_precheck", lambda *_args: _check("grants", broad=True))

    def run_plan(_args, path: Path) -> None:
        observed.append(not path.exists())
        path.mkdir(mode=0o700)

    monkeypatch.setattr(iam, "_run_terraform_plan", run_plan)
    monkeypatch.setattr(iam, "_terraform_summary", lambda _fd: terraform_summary)
    monkeypatch.setattr(iam, "_validate_manifest", lambda value, _raw: value)
    args = _args(transaction)
    args.release_authority = Path("/private/release-authority.json")
    assert iam.plan(args) == 0
    assert observed == [True]
    assert (transaction / "terraform").is_dir()


def test_only_apply_passes_the_active_outer_controller_binding(monkeypatch) -> None:
    captured: list[list[str]] = []
    monkeypatch.setattr(
        iam,
        "_run",
        lambda command, **_kwargs: captured.append(command) or b"",
    )
    args = argparse.Namespace(
        transaction=Path("/private/outer"),
        tfvars=Path("/private/terraform.tfvars"),
        release_authority=Path("/private/release-authority.json"),
        tofu=Path("/private/tofu"),
        gh_config=Path("/private/gh"),
        gcloud_config=Path("/private/gcloud"),
        expected_account="operator@example.com",
    )
    iam._run_terraform_plan(args, Path("/private/outer/terraform"))
    iam._run_terraform_apply(args, Path("/private/outer/terraform"))
    assert "--iam-controller-transaction" not in captured[0]
    gate = captured[1].index("--iam-controller-transaction")
    assert captured[1][gate + 1] == "/private/outer"
    assert captured[1][4].endswith(
        "/terraform/control-bundle/scripts/gcp/terraform_transaction.py"
    )


def test_apply_success_has_exact_four_event_terminal_and_read_only_receipts(
    tmp_path: Path, monkeypatch, capsys
) -> None:
    transaction, manifest = _sealed_transaction(tmp_path)
    _patch_apply_authority(monkeypatch, manifest)
    monkeypatch.setattr(iam, "_run_terraform_apply", lambda *_args: None)
    monkeypatch.setattr(iam, "_postcheck", lambda *_args: _check("revoke", broad=False))

    assert iam.apply(_args(transaction)) == 0
    values = _event_values(transaction)
    assert [item["event"] for item in values] == [
        "mutation-intent",
        "delete-applied",
        "postcheck-passed",
        "terminal",
    ]
    assert values[-1]["details"] == {"state": "applied-and-postchecked"}
    assert stat.S_IMODE(transaction.stat().st_mode) == 0o500
    assert stat.S_IMODE((transaction / "events").stat().st_mode) == 0o500
    assert "forbidden_resources=64" in capsys.readouterr().out


def test_restart_after_terminal_publish_seals_crash_window_without_reapplying(
    tmp_path: Path, monkeypatch, capsys
) -> None:
    transaction, manifest = _sealed_transaction(tmp_path)
    descriptor = os.open(transaction / "events", os.O_RDONLY | os.O_DIRECTORY)
    events: list[dict[str, object]] = []
    manifest_sha = iam._sha256(iam._canonical(manifest))
    try:
        iam._append_event(
            descriptor,
            events,
            manifest_sha,
            "mutation-intent",
            {"state": "mutation-authorized"},
        )
        iam._append_event(
            descriptor,
            events,
            manifest_sha,
            "delete-applied",
            {"state": "delete-command-complete"},
        )
        iam._append_event(
            descriptor,
            events,
            manifest_sha,
            "postcheck-passed",
            {"result": _check("revoke", broad=False)},
        )
        iam._append_event(
            descriptor,
            events,
            manifest_sha,
            "terminal",
            {"state": "applied-and-postchecked"},
        )
    finally:
        os.close(descriptor)
    monkeypatch.setattr(
        iam,
        "_run_terraform_apply",
        lambda *_args: pytest.fail("terminal transaction must not apply twice"),
    )
    assert iam.apply(_args(transaction)) == 0
    assert stat.S_IMODE(transaction.stat().st_mode) == 0o500
    assert stat.S_IMODE((transaction / "events").stat().st_mode) == 0o500
    assert "ALREADY_APPLIED_AND_POSTCHECKED" in capsys.readouterr().out


@pytest.mark.parametrize("fail_after_delete", [False, True])
def test_any_apply_or_postcheck_failure_restores_and_seals_terminal(
    tmp_path: Path, monkeypatch, fail_after_delete: bool
) -> None:
    transaction, manifest = _sealed_transaction(tmp_path)
    _patch_apply_authority(monkeypatch, manifest)
    restored: list[bool] = []

    if fail_after_delete:
        monkeypatch.setattr(iam, "_run_terraform_apply", lambda *_args: None)

        def postcheck(*_args):
            raise iam.IAMTransactionError("closed postcheck failure")

        monkeypatch.setattr(iam, "_postcheck", postcheck)
    else:

        def apply_failure(*_args):
            raise iam.IAMTransactionError("closed apply failure")

        monkeypatch.setattr(iam, "_run_terraform_apply", apply_failure)

    def restore(*_args):
        restored.append(True)
        return _check("grants", broad=True, compensated=True)

    monkeypatch.setattr(iam, "_restore", restore)
    with pytest.raises(iam.IAMTransactionError, match="restored and verified"):
        iam.apply(_args(transaction))
    assert restored == [True]
    values = _event_values(transaction)
    names = [item["event"] for item in values]
    expected_prefix = ["mutation-intent"]
    if fail_after_delete:
        expected_prefix.append("delete-applied")
    assert names == [
        *expected_prefix,
        "compensation-started",
        "compensation-verified",
        "terminal",
    ]
    assert values[-2]["details"]["trigger"] == (
        "postcheck-failure" if fail_after_delete else "apply-failure"
    )
    assert values[-1]["details"] == {"state": "failed-restored"}
    assert stat.S_IMODE(transaction.stat().st_mode) == 0o500


def test_failed_compensation_remains_recoverable_and_never_claims_terminal(
    tmp_path: Path, monkeypatch
) -> None:
    transaction, manifest = _sealed_transaction(tmp_path)
    _patch_apply_authority(monkeypatch, manifest)
    monkeypatch.setattr(
        iam,
        "_run_terraform_apply",
        lambda *_args: (_ for _ in ()).throw(iam.IAMTransactionError("apply failed")),
    )
    monkeypatch.setattr(
        iam,
        "_restore",
        lambda *_args: (_ for _ in ()).throw(iam.IAMTransactionError("restore failed")),
    )
    with pytest.raises(iam.IAMTransactionError, match="restoration is unverified"):
        iam.apply(_args(transaction))
    values = _event_values(transaction)
    assert [item["event"] for item in values] == [
        "mutation-intent",
        "compensation-started",
        "compensation-failed",
    ]
    assert iam._terminal_state(values) is None
    assert stat.S_IMODE(transaction.stat().st_mode) == 0o700


def test_dangling_intent_is_restored_before_plan_freshness_or_git_checks(
    tmp_path: Path, monkeypatch
) -> None:
    transaction, manifest = _sealed_transaction(tmp_path)
    events_fd = os.open(transaction / "events", os.O_RDONLY | os.O_DIRECTORY)
    events: list[dict[str, object]] = []
    try:
        iam._append_event(
            events_fd,
            events,
            iam._sha256(iam._canonical(manifest)),
            "mutation-intent",
            {"state": "mutation-authorized"},
        )
    finally:
        os.close(events_fd)

    order: list[str] = []
    monkeypatch.setattr(
        iam,
        "_recovery_runtime_identity",
        lambda *_args: (Path("/gcloud"), {}, b"probe"),
    )
    monkeypatch.setattr(
        iam,
        "_validate_runtime_identity",
        lambda *_args: order.append("full-validation"),
    )

    def restore(*_args):
        order.append("restore")
        return _check("grants", broad=True, compensated=True)

    monkeypatch.setattr(iam, "_restore", restore)
    with pytest.raises(
        iam.IAMTransactionError, match="interrupted IAM revoke was restored"
    ):
        iam.apply(_args(transaction))
    assert order == ["restore"]
    assert _event_values(transaction)[-1]["details"] == {"state": "failed-restored"}


def test_signal_is_classified_without_recording_signal_or_command_output() -> None:
    error = iam.IAMTransactionInterrupted("SIGTERM")
    assert iam._trigger_for(error, False) == "signal"
    assert "SIGTERM" not in str(error)


def test_stale_plan_is_rejected() -> None:
    manifest = _manifest()
    manifest["created_at"] = (
        datetime.now(timezone.utc) - timedelta(seconds=iam.MAX_PLAN_AGE_SECONDS + 1)
    ).isoformat()
    assert iam._validate_manifest(manifest, iam._canonical(manifest)) == manifest
    with pytest.raises(iam.IAMTransactionError, match="plan is stale"):
        iam._require_fresh_manifest(manifest)


def test_system_python_39_compiles_and_help_exposes_separate_actions() -> None:
    compile_result = subprocess.run(
        ["/usr/bin/python3", "-m", "py_compile", str(SCRIPT)],
        check=False,
        capture_output=True,
        text=True,
    )
    assert compile_result.returncode == 0, compile_result.stderr
    help_result = subprocess.run(
        ["/usr/bin/python3", "-I", str(SCRIPT), "--help"],
        check=False,
        capture_output=True,
        text=True,
    )
    assert help_result.returncode == 0, help_result.stderr
    assert "{plan,apply,recover}" in help_result.stdout


def test_generic_terraform_commands_require_exact_iam_revoke_profile() -> None:
    source = SCRIPT.read_text(encoding="utf-8")
    assert source.count('"--profile",\n        "iam-revoke"') == 2
    assert source.count('"--iam-controller-transaction"') == 1
    assert '"--condition=None"' in source
    assert '"--format=none"' in source
    assert "stderr.decode" not in source
    assert "sys.stdout.buffer.write" not in source
