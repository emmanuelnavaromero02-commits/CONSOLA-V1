from __future__ import annotations

import importlib.util
import json
import os
import stat
import subprocess
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
PATH = REPO / "scripts/gcp/terraform_transaction.py"
SPEC = importlib.util.spec_from_file_location("terraform_transaction", PATH)
assert SPEC is not None and SPEC.loader is not None
transaction = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(transaction)


def test_cli_has_no_external_plan_json_or_arbitrary_terraform_flags() -> None:
    parser = transaction.parser()
    plan = parser.parse_args(
        [
            "plan",
            "--transaction",
            "/tmp/transaction",
            "--tfvars",
            "/tmp/config.tfvars",
            "--release-authority",
            "/tmp/authority.json",
            "--tofu",
            "/usr/local/bin/tofu",
            "--gh-config",
            "/tmp/gh",
            "--gcloud-config",
            "/tmp/gcloud",
            "--expected-account",
            "operator@example.com",
        ]
    )
    assert plan.profile == "foundation"
    iam = parser.parse_args(
        [
            "plan",
            "--transaction",
            "/tmp/transaction",
            "--tfvars",
            "/tmp/config.tfvars",
            "--release-authority",
            "/tmp/authority.json",
            "--tofu",
            "/usr/local/bin/tofu",
            "--gh-config",
            "/tmp/gh",
            "--gcloud-config",
            "/tmp/gcloud",
            "--expected-account",
            "operator@example.com",
            "--profile",
            "iam-revoke",
        ]
    )
    assert transaction._plan_profile_args(iam.profile) == [
        "-var=revoke_project_secret_accessor=true"
    ]
    assert transaction._profile_uses_runtime_transition(iam.profile) is False
    with pytest.raises(SystemExit):
        parser.parse_args(
            [
                "apply",
                "--transaction",
                "/tmp/transaction",
                "--tfvars",
                "/tmp/config.tfvars",
                "--tofu",
                "/usr/local/bin/tofu",
                "--gh-config",
                "/tmp/gh",
                "--gcloud-config",
                "/tmp/gcloud",
                "--expected-account",
                "operator@example.com",
                "--target",
                "google_compute_instance.app",
            ]
        )


def test_secret_adoption_profile_owns_exactly_three_fixed_targets() -> None:
    parser = transaction.parser()
    arguments = [
        "--transaction",
        "/tmp/transaction",
        "--tfvars",
        "/tmp/config.tfvars",
        "--tofu",
        "/usr/local/bin/tofu",
        "--gh-config",
        "/tmp/gh",
        "--gcloud-config",
        "/tmp/gcloud",
        "--expected-account",
        "operator@example.com",
        "--profile",
        "secret-adoption",
    ]
    apply = parser.parse_args(["apply", *arguments])
    assert apply.profile == "secret-adoption"
    assert transaction._plan_profile_args(apply.profile) == [
        f"-target={address}" for address in transaction.SECRET_ADOPTION_TARGETS
    ]
    assert transaction.SECRET_ADOPTION_TARGETS == tuple(
        sorted(transaction.verifier.SECRET_ADOPTION_ADDRESSES)
    )
    assert len(transaction.SECRET_ADOPTION_TARGETS) == 3
    assert all(
        "secret_version" not in target for target in transaction.SECRET_ADOPTION_TARGETS
    )
    assert transaction._profile_uses_runtime_transition("foundation") is True
    assert transaction._profile_uses_runtime_transition("secret-adoption") is False
    with pytest.raises(SystemExit):
        parser.parse_args(
            [
                "apply",
                *arguments,
                "--target",
                'google_secret_manager_secret.runtime["attacker"]',
            ]
        )


def test_release_authority_requires_exact_private_regular_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    authority = tmp_path / "authority.json"
    authority.write_text("{}\n", encoding="utf-8")
    authority.chmod(0o600)
    with pytest.raises(transaction.TransactionError, match="mode 0400"):
        transaction._release_authority(authority)
    authority.chmod(0o400)
    with pytest.raises(transaction.TransactionError, match="receipt is invalid"):
        transaction._release_authority(authority)
    monkeypatch.setattr(
        transaction.verifier.plan_contract.release_authority_contract,
        "validate_authority_receipt",
        lambda value: value,
    )
    descriptor, info, raw, value = transaction._release_authority(authority)
    try:
        assert stat.S_IMODE(info.st_mode) == 0o400
        assert raw == b"{}\n"
        assert value == {}
    finally:
        os.close(descriptor)


def test_release_authority_rejects_noncanonical_bytes_even_when_value_validates(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    authority = tmp_path / "authority.json"
    authority.write_text("{ }\n", encoding="utf-8")
    authority.chmod(0o400)
    monkeypatch.setattr(
        transaction.verifier.plan_contract.release_authority_contract,
        "validate_authority_receipt",
        lambda value: value,
    )
    with pytest.raises(transaction.TransactionError, match="exact canonical JSON"):
        transaction._release_authority(authority)


def test_transaction_config_identity_and_plan_age_are_fail_closed(
    tmp_path: Path,
) -> None:
    config = tmp_path / "gh"
    config.mkdir(mode=0o700)
    config.chmod(0o700)
    identity = transaction._config_identity(config, "GitHub CLI")
    assert identity["path"] == str(config)
    assert identity["mode"] == 0o700
    config.chmod(0o777)
    with pytest.raises(transaction.TransactionError, match="ownership/mode"):
        transaction._config_identity(config, "GitHub CLI")

    transaction._require_fresh_manifest(
        {"created_at": datetime.now(timezone.utc).isoformat()}
    )
    stale = datetime.now(timezone.utc) - timedelta(
        seconds=transaction.MAX_PLAN_AGE_SECONDS + 1
    )
    with pytest.raises(transaction.TransactionError, match="freshness window"):
        transaction._require_fresh_manifest({"created_at": stale.isoformat()})


def test_startup_bytes_are_derived_only_from_same_plan_output() -> None:
    raw = b"#!/bin/bash -p\nprintf ok\n"
    payload = {
        "planned_values": {
            "outputs": {
                "startup_script_sha256": {"value": transaction._sha(raw)},
                "startup_script_base64": {
                    "value": __import__("base64").b64encode(raw).decode(),
                    "sensitive": True,
                },
            }
        }
    }
    assert transaction._startup_bytes(payload) == raw
    payload["planned_values"]["outputs"]["startup_script_base64"]["sensitive"] = False
    with pytest.raises(transaction.TransactionError, match="not sensitive"):
        transaction._startup_bytes(payload)


def test_manifest_rejects_missing_or_extra_security_fields(tmp_path: Path) -> None:
    manifest = tmp_path / "manifest.json"
    payload = {key: None for key in transaction.MANIFEST_KEYS}
    payload["attacker"] = True
    manifest.write_text(json.dumps(payload), encoding="utf-8")
    manifest.chmod(0o400)
    directory = os.open(tmp_path, os.O_RDONLY | os.O_DIRECTORY)
    try:
        with pytest.raises(transaction.TransactionError, match="shape differs"):
            transaction._manifest(directory)
    finally:
        os.close(directory)


def test_manifest_rejects_duplicate_json_keys(tmp_path: Path) -> None:
    manifest = tmp_path / "manifest.json"
    manifest.write_text('{"schema_version":1,"schema_version":1}\n', encoding="utf-8")
    manifest.chmod(0o400)
    directory = os.open(tmp_path, os.O_RDONLY | os.O_DIRECTORY)
    try:
        with pytest.raises(transaction.TransactionError, match="malformed"):
            transaction._manifest(directory)
    finally:
        os.close(directory)


def test_clean_environment_disables_user_cli_configuration(tmp_path: Path) -> None:
    config = tmp_path / "gcloud"
    data = tmp_path / "data"
    environment = transaction._clean_env(config, data)
    assert environment["HOME"] == "/var/empty"
    assert environment["TF_CLI_CONFIG_FILE"] == "/dev/null"
    assert environment["TF_DATA_DIR"] == str(data)
    assert "TF_CLI_ARGS" not in environment


def test_operator_executes_only_the_explicit_sealed_gcloud_path(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config = tmp_path / "gcloud-config"
    config.mkdir(mode=0o700)
    sealed = tmp_path / "sealed-gcloud"
    sealed.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    sealed.chmod(0o700)
    commands: list[list[str]] = []

    def run(command: list[str], **_kwargs: object) -> bytes:
        commands.append(command)
        if "auth" in command:
            return b"operator@example.com\n"
        return (transaction.verifier.plan_contract.PROJECT + "\n").encode()

    monkeypatch.setattr(transaction, "_run", run)
    result = transaction._operator(
        sealed, config, "operator@example.com", {"PATH": str(tmp_path)}
    )
    assert result["account"] == "operator@example.com"
    assert len(commands) == 2
    assert all(
        command[:5]
        == [
            str(transaction.GCLOUD_PYTHON),
            "-I",
            "-S",
            "-B",
            str(sealed),
        ]
        for command in commands
    )


def test_command_errors_never_expose_subprocess_stderr(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sentinel = "TOP-SECRET-SENTINEL"
    monkeypatch.setattr(
        transaction.subprocess,
        "run",
        lambda *_args, **_kwargs: subprocess.CompletedProcess(
            args=[], returncode=23, stdout=b"", stderr=sentinel.encode()
        ),
    )
    with pytest.raises(transaction.CommandError) as captured:
        transaction._run(["/bin/false"], cwd=REPO, env={}, timeout=1, maximum=1024)
    assert captured.value.returncode == 23
    assert sentinel not in str(captured.value)


def test_controller_helper_manifest_includes_operator_control_plane() -> None:
    assert {
        "scripts/gcp/terraform_transaction.py",
        "scripts/gcp/terraform_plan_contract.py",
        "scripts/gcp/verify_terraform_plan.py",
        "scripts/gcp/verify_edge_tls.py",
        "scripts/gcp/startup_metadata_transaction.py",
        "scripts/gcp/generate_release_authority.py",
    }.issubset(transaction.HELPERS)


def test_provider_bundle_detects_post_seal_binary_mutation(tmp_path: Path) -> None:
    source = tmp_path / "source-providers"
    source.mkdir(mode=0o700)
    binary = source / "terraform-provider-google_v7.10.0_x5"
    binary.write_bytes(b"reviewed-provider")
    binary.chmod(0o500)
    transaction_path = tmp_path / "transaction"
    transaction_path.mkdir(mode=0o700)
    destination = transaction_path / transaction.PROVIDER_BUNDLE_MEMBER
    contract = transaction.verifier.plan_contract.release_authority_contract
    inventory = contract._runtime_tree_snapshot(source, destination)
    info = destination.stat()
    manifest = {
        "provider_bundle": {
            "path": str(destination),
            "device": info.st_dev,
            "inode": info.st_ino,
            "mode": 0o500,
            "inventory_sha256": inventory["sha256"],
            "files": inventory["files"],
            "size": inventory["size"],
        }
    }
    assert transaction._provider_bundle(manifest) == destination
    copied = destination / binary.name
    copied.chmod(0o700)
    copied.write_bytes(b"substituted-provider")
    with pytest.raises(transaction.TransactionError, match="inventory differs"):
        transaction._provider_bundle(manifest)


def test_controller_bootstrap_precedes_every_local_helper_import() -> None:
    source = (REPO / "scripts/gcp/terraform_transaction.py").read_text(encoding="utf-8")
    assert source.index("_bootstrap_controller()") < source.index("_load_verifier()")
    assert '"-plugin-dir=" + str(provider_path)' in source
    assert source.count("_attest_provider_installation(") >= 6


def test_foundation_postcheck_cannot_emit_success_before_startup_handoff() -> None:
    source = (REPO / "scripts/gcp/terraform_transaction.py").read_text(encoding="utf-8")
    function = source[
        source.index("def _finish_postcheck(") : source.index(
            "def _new_apply_directory("
        )
    ]
    execute = function.index("_execute_startup_foundation(")
    evidence = function.index("_startup_evidence(", execute)
    edge = function.index('"post-transition"', evidence)
    live_recheck = function.index("_execute_startup_foundation(", edge)
    final = function.index('"apply-receipt.json"', live_recheck)
    marker = function.index("APPLIED_AND_POSTCHECKED", final)
    assert execute < evidence < edge < live_recheck < final < marker
    assert "startup_metadata_receipt_sha256" in transaction.APPLY_RECEIPT_KEYS
    assert "foundation_handoff_receipt_sha256" in transaction.APPLY_RECEIPT_KEYS


def test_foundation_make_targets_delegate_only_to_sealed_transaction() -> None:
    makefile = (REPO / "Makefile").read_text(encoding="utf-8")
    runbook = (REPO / "docs/runbook/16_gcp_canonical_day2_release.md").read_text(
        encoding="utf-8"
    )
    for action in ("plan", "apply", "status", "recover"):
        target = f"gcp-foundation-{action}:"
        block = makefile[makefile.index(target) :]
        block = block.split("\n\n", 1)[0]
        assert f"gcp-terraform-{action}" in block
        assert "startup_metadata_transaction.py" not in block
        assert f"make gcp-foundation-{action}" in runbook
    assert "must never be invoked directly" in runbook


def test_backend_is_exact_tracked_canonical_projection() -> None:
    raw = transaction.BACKEND_CONFIG.read_bytes()
    assert transaction._validate_backend(raw) == {
        "bucket": "omega-gcp-tfstate-project-dd5ba7fa-374c-4554-ae6",
        "prefix": "infra/terraform-gcp/staging",
    }
    for malicious in (
        raw + b'impersonate_service_account = "attacker@example.com"\n',
        raw.replace(b"omega-gcp-tfstate", b"attacker-state"),
        raw.replace(b"infra/terraform-gcp/staging", b"elsewhere"),
    ):
        with pytest.raises(
            transaction.TransactionError, match="canonical state backend"
        ):
            transaction._validate_backend(malicious)


def test_dev_fd_consumers_require_an_explicit_rewind(tmp_path: Path) -> None:
    value = tmp_path / "input"
    value.write_bytes(b"exact-bytes")
    descriptor, _, raw = transaction._safe_path(value, maximum=1024)
    try:
        assert raw == b"exact-bytes"
        exhausted = subprocess.run(
            ["/bin/cat", f"/dev/fd/{descriptor}"],
            pass_fds=(descriptor,),
            check=True,
            capture_output=True,
        )
        assert exhausted.stdout == b""
        os.lseek(descriptor, 0, os.SEEK_SET)
        rewound = subprocess.run(
            ["/bin/cat", f"/dev/fd/{descriptor}"],
            pass_fds=(descriptor,),
            check=True,
            capture_output=True,
        )
        assert rewound.stdout == raw
    finally:
        os.close(descriptor)


def test_write_member_loops_short_writes_and_reads_back(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    directory = os.open(tmp_path, os.O_RDONLY | os.O_DIRECTORY)
    real_write = os.write

    def short_write(descriptor: int, payload: bytes | memoryview) -> int:
        return real_write(descriptor, payload[: max(1, len(payload) // 3)])

    monkeypatch.setattr(transaction.os, "write", short_write)
    try:
        identity = transaction._write_member(
            directory, "receipt.json", b"0123456789\n", 0o400
        )
    finally:
        os.close(directory)
    assert (tmp_path / "receipt.json").read_bytes() == b"0123456789\n"
    assert identity["sha256"] == transaction._sha(b"0123456789\n")
    assert stat.S_IMODE((tmp_path / "receipt.json").stat().st_mode) == 0o400


def _manifest_value(*, created_at: str | None = None) -> dict[str, object]:
    value: dict[str, object] = {key: None for key in transaction.MANIFEST_KEYS}
    value.update(
        {
            "schema_version": 1,
            "profile": "foundation",
            "git_head": "a" * 40,
            "controller_ref": "a" * 40,
            "startup_script_sha256": "b" * 64,
            "startup_script": {"sha256": "b" * 64},
            "created_at": created_at or datetime.now(timezone.utc).isoformat(),
            "tool": {"sha256": "c" * 64},
            "plan_tool_copy": {"sha256": "c" * 64},
            "plan": {"sha256": "d" * 64},
            "plan_json_sha256": "e" * 64,
            "tfvars": {"sha256": "2" * 64},
            "backend_config": {"sha256": "3" * 64},
            "backend_projection": {},
            "release_authority": {"sha256": "f" * 64},
            "release_authority_source_path": "/tmp/release-authority.json",
            "authority_live_verification": {},
            "tracked_config_sha256": "4" * 64,
            "helpers": {},
            "operator": {"account": "operator@example.com"},
            "github_config": {},
            "gcloud": {"sha256": "5" * 64},
            "control_bundle": {
                "path": "/tmp/transaction/control-bundle",
                "device": 1,
                "inode": 2,
                "mode": 0o500,
                "git_head": "a" * 40,
                "tracked_config_sha256": "4" * 64,
                "helpers": {},
                "inventory_sha256": "6" * 64,
                "files": len(transaction.HELPERS),
            },
            "provider_bundle": {
                "path": "/tmp/transaction/provider-bundle",
                "device": 1,
                "inode": 3,
                "mode": 0o500,
                "inventory_sha256": "7" * 64,
                "files": 1,
                "size": 1,
            },
        }
    )
    return value


def _intent_value(
    manifest: dict[str, object], manifest_raw: bytes
) -> dict[str, object]:
    now = datetime.now(timezone.utc).isoformat()
    return {
        "schema_version": 1,
        "state": "apply-started",
        "profile": manifest["profile"],
        "git_head": manifest["git_head"],
        "manifest_sha256": transaction._sha(manifest_raw),
        "plan_sha256": manifest["plan"]["sha256"],
        "plan_json_sha256": manifest["plan_json_sha256"],
        "startup_script_sha256": manifest["startup_script_sha256"],
        "release_authority_sha256": manifest["release_authority"]["sha256"],
        "operator": manifest["operator"],
        "tool_sha256": manifest["tool"]["sha256"],
        "authority_live_verification": {
            "authority_sha256": manifest["release_authority"]["sha256"],
            "source_sha": manifest["git_head"],
            "controller_ref": manifest["controller_ref"],
            "verified_at": now,
        },
        "terraform_state_before": {
            "lineage": "12345678-1234-1234-1234-123456789abc",
            "serial": 17,
            "sha256": "1" * 64,
            "pulled_at": now,
        },
        "created_at": now,
    }


def test_apply_state_machine_is_append_only_and_idempotently_classified(
    tmp_path: Path,
) -> None:
    manifest = _manifest_value()
    manifest["profile"] = "secret-adoption"
    manifest["startup_script_sha256"] = None
    manifest["startup_script"] = None
    manifest_raw = transaction._canonical_json(manifest)
    directory = os.open(tmp_path, os.O_RDONLY | os.O_DIRECTORY)
    try:
        assert (
            transaction._existing_apply_state(directory, manifest, manifest_raw)
            == "new"
        )
        intent = _intent_value(manifest, manifest_raw)
        intent_raw, _identity = transaction._write_state_member(
            directory, "apply-intent.json", intent
        )
        assert (
            transaction._existing_apply_state(directory, manifest, manifest_raw)
            == "indeterminate"
        )
        result = {
            "schema_version": 1,
            "state": "apply-exited-zero",
            "intent_sha256": transaction._sha(intent_raw),
            "output_sha256": transaction._sha(b"safe output"),
            "output_size_bytes": len(b"safe output"),
            "completed_at": datetime.now(timezone.utc).isoformat(),
        }
        result_raw, _identity = transaction._write_state_member(
            directory, "apply-result.json", result
        )
        assert (
            transaction._existing_apply_state(directory, manifest, manifest_raw)
            == "postcheck"
        )
        receipt = {
            "schema_version": 1,
            "state": "applied-and-postchecked",
            "profile": manifest["profile"],
            "git_head": manifest["git_head"],
            "manifest_sha256": transaction._sha(manifest_raw),
            "plan_sha256": manifest["plan"]["sha256"],
            "plan_json_sha256": manifest["plan_json_sha256"],
            "startup_script_sha256": manifest["startup_script_sha256"],
            "startup_metadata_intent_sha256": None,
            "startup_metadata_receipt_sha256": None,
            "foundation_execution_intent_sha256": None,
            "foundation_handoff_receipt_sha256": None,
            "foundation_boot_id": None,
            "intent_sha256": transaction._sha(intent_raw),
            "result_sha256": transaction._sha(result_raw),
            "authority_live_verification": intent["authority_live_verification"],
            "completed_at": datetime.now(timezone.utc).isoformat(),
        }
        transaction._write_state_member(directory, "apply-receipt.json", receipt)
        assert (
            transaction._existing_apply_state(directory, manifest, manifest_raw)
            == "complete"
        )
    finally:
        os.close(directory)


def test_atomic_state_publication_recovers_crash_after_link(tmp_path: Path) -> None:
    value = {"schema_version": 1, "state": "test"}
    raw = transaction._canonical_json(value)
    temporary = tmp_path / ".phase.json.tmp"
    temporary.write_bytes(raw)
    temporary.chmod(0o400)
    os.link(temporary, tmp_path / "phase.json")
    directory = os.open(tmp_path, os.O_RDONLY | os.O_DIRECTORY)
    try:
        published, _identity = transaction._write_state_member(
            directory, "phase.json", value
        )
    finally:
        os.close(directory)
    assert published == raw
    assert not temporary.exists()
    assert (tmp_path / "phase.json").stat().st_nlink == 1


def test_state_validators_reject_bool_integer_confusion(tmp_path: Path) -> None:
    manifest = _manifest_value()
    manifest_raw = transaction._canonical_json(manifest)
    intent = _intent_value(manifest, manifest_raw)
    intent["terraform_state_before"]["serial"] = True
    with pytest.raises(transaction.TransactionError, match="intent differs"):
        transaction._validate_apply_intent(intent, manifest, manifest_raw)
    with pytest.raises(transaction.TransactionError, match="result identity"):
        transaction._validate_apply_result(
            {
                "schema_version": 1,
                "state": "apply-exited-zero",
                "intent_sha256": "a" * 64,
                "output_sha256": "b" * 64,
                "output_size_bytes": True,
                "completed_at": datetime.now(timezone.utc).isoformat(),
            },
            "a" * 64,
        )


def test_stale_preflight_does_not_allocate_apply_directory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    transaction_path = tmp_path / "transaction"
    transaction_path.mkdir(mode=0o700)
    stale = datetime.now(timezone.utc) - timedelta(
        seconds=transaction.MAX_PLAN_AGE_SECONDS + 1
    )
    manifest = _manifest_value(created_at=stale.isoformat())
    manifest_path = transaction_path / "manifest.json"
    manifest_path.write_bytes(transaction._canonical_json(manifest))
    manifest_path.chmod(0o400)
    monkeypatch.setattr(
        transaction,
        "_control_bundle",
        lambda _manifest: transaction_path / "control-bundle",
    )
    monkeypatch.setattr(
        transaction,
        "_provider_bundle",
        lambda _manifest: transaction_path / "provider-bundle",
    )
    args = __import__("argparse").Namespace(
        transaction=transaction_path,
        profile="foundation",
    )
    with pytest.raises(transaction.TransactionError, match="freshness window"):
        transaction.apply(args)
    assert not list(transaction_path.glob("apply-data-*"))


def test_status_and_recover_cli_do_not_accept_apply_or_plan_inputs() -> None:
    parser = transaction.parser()
    status = parser.parse_args(
        ["status", "--transaction", "/tmp/transaction", "--profile", "foundation"]
    )
    assert status.handler is transaction.status
    recover = parser.parse_args(
        [
            "recover",
            "--transaction",
            "/tmp/transaction",
            "--gh-config",
            "/tmp/gh",
            "--gcloud-config",
            "/tmp/gcloud",
            "--expected-account",
            "operator@example.com",
        ]
    )
    assert recover.handler is transaction.recover
    assert not hasattr(recover, "tofu")
    assert not hasattr(recover, "tfvars")


def test_terraform_state_identity_retains_only_lineage_serial_and_hash(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    raw = json.dumps(
        {
            "version": 4,
            "terraform_version": "1.11.6",
            "serial": 8,
            "lineage": "12345678-1234-1234-1234-123456789abc",
            "outputs": {"sensitive": {"value": "TOP-SECRET"}},
            "resources": [],
        }
    ).encode()
    monkeypatch.setattr(transaction, "_run", lambda *_args, **_kwargs: raw)
    identity = transaction._terraform_state_identity(Path("/tmp/tofu"), {})
    assert identity["serial"] == 8
    assert identity["sha256"] == transaction._sha(raw)
    assert "TOP-SECRET" not in json.dumps(identity)


def test_iam_apply_requires_active_outer_controller_bound_to_inner_plan(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    outer = tmp_path / "iam-controller"
    inner = outer / "terraform"
    events = outer / "events"
    outer.mkdir(mode=0o700)
    inner.mkdir(mode=0o700)
    events.mkdir(mode=0o700)
    manifest = _manifest_value()
    manifest["profile"] = "iam-revoke"
    manifest["startup_script_sha256"] = None
    manifest["startup_script"] = None
    inner_raw = transaction._canonical_json(manifest)
    inner_path = inner / "manifest.json"
    inner_path.write_bytes(inner_raw)
    inner_path.chmod(0o400)
    outer_manifest: dict[str, object] = {
        key: None for key in transaction.IAM_CONTROLLER_MANIFEST_KEYS
    }
    outer_manifest.update(
        {
            "schema_version": 1,
            "profile": "iam-revoke",
            "state": "sealed",
            "source": {"git_head": manifest["git_head"], "files": {}},
            "operator": {"account": manifest["operator"]["account"]},
            "terraform": {
                "directory": "terraform",
                "manifest_sha256": transaction._sha(inner_raw),
                "manifest_inode": inner_path.stat().st_ino,
                "plan_sha256": manifest["plan"]["sha256"],
                "plan_json_sha256": manifest["plan_json_sha256"],
            },
        }
    )
    outer_raw = transaction._canonical_json(outer_manifest)
    outer_path = outer / "manifest.json"
    outer_path.write_bytes(outer_raw)
    outer_path.chmod(0o400)
    event = {
        "schema_version": 1,
        "sequence": 1,
        "event": "mutation-intent",
        "manifest_sha256": transaction._sha(outer_raw),
        "occurred_at": datetime.now(timezone.utc).isoformat(),
        "details": {"state": "mutation-authorized"},
    }
    event_path = events / "00000001-mutation-intent.json"
    event_path.write_bytes(transaction._canonical_json(event))
    event_path.chmod(0o400)

    with pytest.raises(transaction.TransactionError, match="reversible controller"):
        transaction._require_active_iam_controller(
            None, inner, inner_path.stat(), inner_raw, manifest
        )

    def locked(_descriptor: int, operation: int) -> None:
        if operation & transaction.fcntl.LOCK_NB:
            raise BlockingIOError

    monkeypatch.setattr(transaction.fcntl, "flock", locked)
    descriptor = transaction._require_active_iam_controller(
        outer, inner, inner_path.stat(), inner_raw, manifest
    )
    os.close(descriptor)

    event["details"] = {"state": "unreviewed"}
    event_path.chmod(0o600)
    event_path.write_bytes(transaction._canonical_json(event))
    event_path.chmod(0o400)
    with pytest.raises(transaction.TransactionError, match="intent differs"):
        transaction._require_active_iam_controller(
            outer, inner, inner_path.stat(), inner_raw, manifest
        )
