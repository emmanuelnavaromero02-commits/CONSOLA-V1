from __future__ import annotations

import argparse
import base64
import gzip
import hashlib
import importlib.util
import json
from pathlib import Path

import pytest


REPO = Path(__file__).resolve().parents[1]
MODULE_PATH = REPO / "scripts/gcp/startup_metadata_transaction.py"
SPEC = importlib.util.spec_from_file_location(
    "startup_metadata_transaction", MODULE_PATH
)
assert SPEC and SPEC.loader
transaction = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(transaction)

OLD_SHA = "3" * 64
NEW_SHA = "4" * 64
CONTROLLER = "a" * 40
SOURCE = "b" * 40


def test_startup_mutator_has_no_standalone_operator_interface() -> None:
    source = MODULE_PATH.read_text(encoding="utf-8")
    assert 'add_argument("--execute-foundation"' not in source
    assert "internal-only helper" in source


def test_sealed_gcloud_environment_binds_framework_and_forbids_homebrew() -> None:
    config = Path("/private/config")
    python = Path(
        "/private/runtime/Library/Frameworks/Python.framework/"
        "Versions/3.14/bin/python3.14"
    )
    framework = Path("/private/runtime/Library/Frameworks/Python.framework")
    environment = transaction._gcloud_process_env(config, python, framework)
    assert environment["DYLD_FRAMEWORK_PATH"] == str(framework.parent)
    assert environment["DYLD_LIBRARY_PATH"] == str(framework / "Versions/3.14/lib")
    assert environment["CLOUDSDK_PYTHON"] == str(python)
    assert "PYTHONHOME" not in environment
    assert "homebrew" not in "\n".join(environment.values()).lower()


def _config() -> dict[str, object]:
    return {
        "schema_version": 1,
        "project_id": transaction.PROJECT,
        "environment": "staging",
        "controller_ref": CONTROLLER,
        "foundation_predecessor": None,
        "host_identity": {
            "project_id": transaction.PROJECT,
            "instance_id": transaction.INSTANCE_ID,
            "instance_name": transaction.INSTANCE,
            "zone": transaction.ZONE,
            "service_account_email": transaction.SERVICE_ACCOUNT,
        },
        "source": {"ref": SOURCE},
        "canonical_writer": True,
        "enable_airflow_scheduler": True,
        "exact_runtime_contract_ready": False,
    }


def _candidate(blobs: dict[str, bytes]) -> bytes:
    values = {
        "STARTUP_CONFIG_BASE64": base64.b64encode(
            json.dumps(_config(), sort_keys=True, separators=(",", ":")).encode()
        ).decode()
    }
    for variable, path in transaction.HELPERS.items():
        values[variable] = base64.b64encode(
            gzip.compress(blobs[path], mtime=0)
        ).decode()
    rendered = blobs[transaction.TEMPLATE].decode()
    for variable, placeholder in transaction.ASSIGNMENTS.items():
        rendered = rendered.replace("${" + placeholder + "}", values[variable])
    return rendered.encode()


def _blobs() -> dict[str, bytes]:
    template = (
        "#!/bin/bash -p\n"
        + "\n".join(
            f"{variable}='${{{placeholder}}}'"
            for variable, placeholder in transaction.ASSIGNMENTS.items()
        )
        + "\n"
    )
    return {
        transaction.TEMPLATE: template.encode(),
        **{
            path: f"reviewed:{path}\n".encode() for path in transaction.HELPERS.values()
        },
    }


def _instance(
    startup: bytes,
    *,
    fingerprint: str = "fingerprint_A=",
    started: str = "2026-07-09T18:22:16.291-07:00",
) -> dict:
    return {
        "id": transaction.INSTANCE_ID,
        "name": transaction.INSTANCE,
        "zone": (
            f"{transaction.COMPUTE_ROOT}/projects/{transaction.PROJECT}/zones/"
            f"{transaction.ZONE}"
        ),
        "status": "RUNNING",
        "lastStartTimestamp": started,
        "serviceAccounts": [
            {"email": transaction.SERVICE_ACCOUNT, "scopes": [transaction.SCOPE]}
        ],
        "metadata": {
            "fingerprint": fingerprint,
            "items": [
                {"key": "enable-oslogin", "value": "TRUE"},
                {"key": "startup-script", "value": startup.decode()},
            ],
        },
    }


class FakeCompute:
    def __init__(self, old: bytes, new: bytes, *, fail_after_cas: bool = False) -> None:
        self.old = old
        self.new = new
        self.current = old
        self.fail_after_cas = fail_after_cas
        self.calls: list[tuple[str, object]] = []

    def request(self, url: str, *, body: dict | None = None) -> dict:
        self.calls.append((url, body))
        if url.endswith("/setMetadata"):
            assert body == {
                "fingerprint": "fingerprint_A=",
                "items": [
                    {"key": "enable-oslogin", "value": "TRUE"},
                    {"key": "startup-script", "value": self.new.decode()},
                ],
            }
            self.current = self.new
            return {
                "name": "operation-123",
                "status": "PENDING",
                "operationType": "setMetadata",
            }
        if "/operations/" in url:
            return {
                "name": "operation-123",
                "status": "DONE",
                "operationType": "setMetadata",
            }
        if self.fail_after_cas and self.current == self.new:
            self.fail_after_cas = False
            raise transaction.TransactionError("injected post-CAS read failure")
        return _instance(
            self.current,
            fingerprint="fingerprint_A="
            if self.current == self.old
            else "fingerprint_B=",
        )


class FoundationCompute(FakeCompute):
    def __init__(self, old: bytes, new: bytes) -> None:
        super().__init__(old, new)
        self.started = "2026-07-09T18:22:16.291-07:00"
        self.reset_count = 0

    def request(self, url: str, *, body: dict | None = None) -> dict:
        if url.endswith("/reset"):
            self.calls.append((url, body))
            assert body == {}
            self.reset_count += 1
            self.started = "2026-08-12T10:11:12.123+00:00"
            return {
                "name": "operation-456",
                "status": "PENDING",
                "operationType": "reset",
            }
        if url.endswith("/operations/operation-456"):
            self.calls.append((url, body))
            return {
                "name": "operation-456",
                "status": "DONE",
                "operationType": "reset",
            }
        if url.endswith("/setMetadata") or "/operations/" in url:
            return super().request(url, body=body)
        self.calls.append((url, body))
        return _instance(
            self.current,
            fingerprint="fingerprint_A="
            if self.current == self.old
            else "fingerprint_B=",
            started=self.started,
        )


def test_compute_refreshes_operator_token_before_long_poll() -> None:
    seen: list[str | None] = []

    class Response:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def read(self, _maximum):
            return b"{}"

    class Opener:
        def open(self, request, *, timeout):
            assert timeout > 0
            seen.append(request.get_header("Authorization"))
            return Response()

    refreshed = "b" * 40
    compute = transaction.Compute(
        "a" * 40,
        deadline=transaction.time.monotonic() + 60,
        token_provider=lambda: refreshed,
    )
    compute.opener = Opener()
    compute.token_refresh_at = 0

    assert compute.request(transaction.COMPUTE_ROOT + "/projects/example") == {}
    assert seen == [f"Bearer {refreshed}"]


def _host_projection(
    args: argparse.Namespace,
    startup_contract_sha256: str,
    *,
    startup_sha256: str | None = None,
    completed_at: str = "2026-08-12T10:12:00+00:00",
) -> bytes:
    host = {
        "schema_version": 1,
        "state": "foundation-fenced",
        "deploy_ref": args.source_ref,
        "source_sha": args.source_ref,
        "helper_ref": args.controller_ref,
        "controller_ref": args.controller_ref,
        "startup_contract_sha256": startup_contract_sha256,
        "live_startup_script_sha256": startup_sha256 or args.new_sha256,
        "foundation_marker_sha256": "1" * 64,
        "foundation_watchdog_state_sha256": "2" * 64,
        "terminal_receipt_sha256": "3" * 64,
        "instance_id": transaction.INSTANCE_ID,
        "zone": transaction.ZONE,
        "boot_id": "12345678-1234-1234-1234-123456789abc",
        "boot_started_epoch": 1_786_512_672,
        "completed_at": completed_at,
    }
    return transaction._canonical(
        {
            "receipt": host,
            "sha256": hashlib.sha256(transaction._canonical(host)).hexdigest(),
        }
    )


def _args(tmp_path: Path, candidate: Path) -> argparse.Namespace:
    return argparse.Namespace(
        repo=REPO,
        candidate_startup=candidate,
        old_sha256=hashlib.sha256(b"old startup\n").hexdigest(),
        new_sha256=hashlib.sha256(candidate.read_bytes()).hexdigest(),
        controller_ref=CONTROLLER,
        source_ref=SOURCE,
        receipt=tmp_path / "terminal.json",
        operator_account="operator@example.com",
        gcloud_config=tmp_path,
        gcloud=Path("/usr/bin/false"),
        timeout_seconds=180,
        foundation_receipt=tmp_path / "foundation.json",
        foundation_timeout_seconds=1,
        poll_seconds=0.001,
        confirm_exact_cas=True,
        confirm_exact_foundation_reset=True,
    )


def _patch_blob_reader(
    monkeypatch: pytest.MonkeyPatch, blobs: dict[str, bytes]
) -> None:
    monkeypatch.setattr(
        transaction, "_sealed_blob", lambda _repo, _ref, path: blobs[path]
    )


def test_candidate_binds_template_config_helpers_and_controller(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    blobs = _blobs()
    candidate = _candidate(blobs)
    transaction.validate_candidate(
        candidate,
        expected_sha256=hashlib.sha256(candidate).hexdigest(),
        controller_ref=CONTROLLER,
        source_ref=SOURCE,
        repo=REPO,
        blob_reader=lambda _repo, _ref, path: blobs[path],
    )
    tampered = dict(blobs)
    tampered["scripts/gcp/safe_io.py"] += b"tampered\n"
    with pytest.raises(transaction.TransactionError, match="helper differs"):
        transaction.validate_candidate(
            candidate,
            expected_sha256=hashlib.sha256(candidate).hexdigest(),
            controller_ref=CONTROLLER,
            source_ref=SOURCE,
            repo=REPO,
            blob_reader=lambda _repo, _ref, path: tampered[path],
        )


@pytest.mark.parametrize(
    "mutation",
    [
        lambda value: value.update(id="different"),
        lambda value: value.update(status="TERMINATED"),
        lambda value: value["serviceAccounts"][0].update(email="attacker@example.com"),
        lambda value: value["metadata"]["items"].append(
            {"key": "ssh-keys", "value": "x"}
        ),
        lambda value: value["metadata"]["items"][0].update(value="FALSE"),
    ],
)
def test_instance_identity_and_metadata_inventory_are_exact(mutation) -> None:
    startup = b"old startup\n"
    payload = _instance(startup)
    mutation(payload)
    with pytest.raises(transaction.TransactionError):
        transaction.validate_instance(
            payload, expected_startup_sha256=hashlib.sha256(startup).hexdigest()
        )


def test_transaction_uses_fingerprint_cas_preserves_keys_and_never_resets_vm(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    blobs = _blobs()
    candidate_raw = _candidate(blobs)
    candidate = tmp_path / "startup.sh"
    candidate.write_bytes(candidate_raw)
    args = _args(tmp_path, candidate)
    fake = FakeCompute(b"old startup\n", candidate_raw)
    _patch_blob_reader(monkeypatch, blobs)

    receipt = transaction.transact(args, compute=fake)

    assert receipt["status"] == "PASS"
    assert receipt["instance_reset_requested"] is False
    assert receipt["foundation_execution_state"] == "pending-controlled-reboot"
    assert args.receipt.is_file()
    assert Path(str(args.receipt) + ".intent").is_file()
    assert all(
        "reset" not in url.lower() and "start" not in url.lower()
        for url, _ in fake.calls
    )


def test_post_cas_interruption_leaves_intent_and_retry_recovers_without_second_write(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    blobs = _blobs()
    candidate_raw = _candidate(blobs)
    candidate = tmp_path / "startup.sh"
    candidate.write_bytes(candidate_raw)
    args = _args(tmp_path, candidate)
    fake = FakeCompute(b"old startup\n", candidate_raw, fail_after_cas=True)
    _patch_blob_reader(monkeypatch, blobs)

    with pytest.raises(transaction.TransactionError, match="injected"):
        transaction.transact(args, compute=fake)
    assert Path(str(args.receipt) + ".intent").is_file()
    writes_before = sum(url.endswith("/setMetadata") for url, _ in fake.calls)

    receipt = transaction.transact(args, compute=fake)
    writes_after = sum(url.endswith("/setMetadata") for url, _ in fake.calls)
    assert writes_before == writes_after == 1
    assert receipt["recovered"] is True


def test_unexpected_live_startup_never_writes_metadata(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    blobs = _blobs()
    candidate_raw = _candidate(blobs)
    candidate = tmp_path / "startup.sh"
    candidate.write_bytes(candidate_raw)
    args = _args(tmp_path, candidate)
    fake = FakeCompute(b"unexpected startup\n", candidate_raw)
    _patch_blob_reader(monkeypatch, blobs)

    with pytest.raises(transaction.TransactionError, match="neither"):
        transaction.transact(args, compute=fake)
    assert not any(url.endswith("/setMetadata") for url, _ in fake.calls)
    assert not Path(str(args.receipt) + ".intent").exists()


def test_foundation_execution_is_cas_then_one_reset_then_server_receipt(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    blobs = _blobs()
    candidate_raw = _candidate(blobs)
    candidate = tmp_path / "startup.sh"
    candidate.write_bytes(candidate_raw)
    args = _args(tmp_path, candidate)
    fake = FoundationCompute(b"old startup\n", candidate_raw)
    _patch_blob_reader(monkeypatch, blobs)

    def collect(_args, *, startup_contract_sha256, timeout_seconds):
        assert timeout_seconds > 0
        return _host_projection(args, startup_contract_sha256)

    receipt = transaction.execute_foundation(args, compute=fake, collector=collect)

    assert receipt["status"] == "PASS"
    assert receipt["host_receipt"]["state"] == "foundation-fenced"
    assert fake.reset_count == 1
    set_metadata = next(
        index
        for index, (url, _body) in enumerate(fake.calls)
        if url.endswith("/setMetadata")
    )
    reset = next(
        index for index, (url, _body) in enumerate(fake.calls) if url.endswith("/reset")
    )
    assert set_metadata < reset
    assert args.foundation_receipt.is_file()
    assert Path(str(args.receipt) + ".intent").is_file()
    assert Path(str(args.foundation_receipt) + ".intent").is_file()

    assert (
        transaction.execute_foundation(args, compute=fake, collector=collect) == receipt
    )
    assert fake.reset_count == 1
    with pytest.raises(transaction.TransactionError, match="handoff receipt"):
        transaction.execute_foundation(
            args,
            compute=fake,
            collector=lambda *_args, **_kwargs: None,
        )
    assert fake.reset_count == 1


def test_missing_handoff_fails_closed_and_recovery_never_resets_twice(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    blobs = _blobs()
    candidate_raw = _candidate(blobs)
    candidate = tmp_path / "startup.sh"
    candidate.write_bytes(candidate_raw)
    args = _args(tmp_path, candidate)
    args.foundation_timeout_seconds = 0.01
    fake = FoundationCompute(b"old startup\n", candidate_raw)
    _patch_blob_reader(monkeypatch, blobs)

    with pytest.raises(transaction.TransactionError, match="handoff receipt"):
        transaction.execute_foundation(
            args,
            compute=fake,
            collector=lambda *_args, **_kwargs: None,
        )
    assert fake.reset_count == 1
    assert not args.foundation_receipt.exists()

    def collect(_args, *, startup_contract_sha256, timeout_seconds):
        assert timeout_seconds > 0
        return _host_projection(args, startup_contract_sha256)

    args.foundation_timeout_seconds = 1
    receipt = transaction.execute_foundation(args, compute=fake, collector=collect)
    assert receipt["recovered"] is True
    assert fake.reset_count == 1


def test_host_receipt_with_wrong_startup_never_publishes_terminal_receipt(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    blobs = _blobs()
    candidate_raw = _candidate(blobs)
    candidate = tmp_path / "startup.sh"
    candidate.write_bytes(candidate_raw)
    args = _args(tmp_path, candidate)
    fake = FoundationCompute(b"old startup\n", candidate_raw)
    _patch_blob_reader(monkeypatch, blobs)

    def collect(_args, *, startup_contract_sha256, timeout_seconds):
        assert timeout_seconds > 0
        return _host_projection(
            args,
            startup_contract_sha256,
            startup_sha256="9" * 64,
        )

    with pytest.raises(transaction.TransactionError, match="host receipt identity"):
        transaction.execute_foundation(args, compute=fake, collector=collect)
    assert fake.reset_count == 1
    assert not args.foundation_receipt.exists()


def test_preboot_host_receipt_never_publishes_terminal_receipt(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    blobs = _blobs()
    candidate_raw = _candidate(blobs)
    candidate = tmp_path / "startup.sh"
    candidate.write_bytes(candidate_raw)
    args = _args(tmp_path, candidate)
    fake = FoundationCompute(b"old startup\n", candidate_raw)
    _patch_blob_reader(monkeypatch, blobs)

    def collect(_args, *, startup_contract_sha256, timeout_seconds):
        assert timeout_seconds > 0
        return _host_projection(
            args,
            startup_contract_sha256,
            completed_at="2026-08-12T10:11:00+00:00",
        )

    with pytest.raises(transaction.TransactionError, match="timestamp order"):
        transaction.execute_foundation(args, compute=fake, collector=collect)
    assert fake.reset_count == 1
    assert not args.foundation_receipt.exists()
