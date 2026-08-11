from __future__ import annotations

import importlib.util
import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
CONTROLLER_PATH = ROOT / "scripts" / "gcp_release.py"


def _load_controller():
    spec = importlib.util.spec_from_file_location(
        "omega_gcp_release_routing_tests", CONTROLLER_PATH
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


CONTROLLER = _load_controller()
PUBLIC_IP = "203.0.113.42"
INSTANCE_ID = "123456789"
SOURCE_SHA = "1" * 40


def _payload(*, attested_at: datetime | None = None) -> dict:
    observed = attested_at or datetime.now(timezone.utc) - timedelta(minutes=1)
    return {
        "schema": CONTROLLER.ROUTING_SCHEDULER_ATTESTATION_SCHEMA,
        "scope": "routing-and-scheduler-observation-only",
        "attested_at": observed.isoformat(),
        "source_sha": SOURCE_SHA,
        "instance_id": INSTANCE_ID,
        "gcp_public_ip": PUBLIC_IP,
        "gcp_console_domain": CONTROLLER.CANONICAL_PUBLIC_TLS_NAME,
        "gcp_console_addresses": [PUBLIC_IP],
        "gcp_workspace_domain": "workspace.7businesssolutions.com",
        "gcp_workspace_addresses": [PUBLIC_IP],
        "gcp_scheduler_count": 1,
        "transfer_job": {
            "name": CONTROLLER.CANONICAL_TRANSFER_JOB,
            "status": "DISABLED",
            "last_operation_name": "transferOperations/closed-operation",
            "last_operation_status": "SUCCESS",
            "last_operation_ended_at": "2026-08-10T00:00:00+00:00",
        },
        "aws": {
            "origin": {
                "alb_hostname": CONTROLLER.CANONICAL_AWS_ORIGIN_ALB,
                "https_healthz_status": 502,
                "http_redirect_status": 301,
                "frozen_source_sha": CONTROLLER.CANONICAL_AWS_ORIGIN_FROZEN_SHA,
            },
            "destination": {
                "alb_hostname": CONTROLLER.CANONICAL_AWS_DESTINATION_ALB,
                "https_healthz_status": 200,
                "scheduler_status": "unhealthy",
                "scheduler_last_heartbeat_at": (
                    CONTROLLER.CANONICAL_AWS_DESTINATION_SCHEDULER_HEARTBEAT
                ),
                "canonical_dns_target": False,
                "scheduled_writer_count": 0,
            },
            "db_api_hard_fence_proven": False,
            "hard_fence_phase": "16-17",
        },
        "forensic_evidence": {
            "observed_at": CONTROLLER.CANONICAL_AWS_FORENSIC_OBSERVED_AT,
            "event_at": CONTROLLER.CANONICAL_AWS_FORENSIC_EVENT_AT,
            "reference": CONTROLLER.CANONICAL_AWS_FORENSIC_REFERENCE,
            "sha256": CONTROLLER.CANONICAL_AWS_FORENSIC_SHA256,
        },
        "decision": {
            "canonical_cloud": "GCP",
            "canonical_writer": "GCP",
            "aws_role": "standby",
            "exactly_one_scheduled_writer_gcp": True,
            "checkpoint_zero_aws_writer_gate": "BLOCKED",
            "deployment_authorized": False,
        },
    }


def _validation_kwargs() -> dict[str, str]:
    return {
        "source_sha": SOURCE_SHA,
        "instance_id": INSTANCE_ID,
        "project": "omega-production",
        "environment": "production",
        "console_domain": CONTROLLER.CANONICAL_PUBLIC_TLS_NAME,
        "workspace_domain": "workspace.7businesssolutions.com",
    }


def _mock_validation_io(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        CONTROLLER,
        "run",
        lambda *_args, **_kwargs: CONTROLLER.RemoteResult(0, f"{PUBLIC_IP}\n", ""),
    )
    monkeypatch.setattr(
        CONTROLLER,
        "revalidate_external_routing_scheduler_live",
        lambda *_args, **_kwargs: {
            "observed_at": datetime.now(timezone.utc).isoformat()
        },
    )


def _validate(payload: dict) -> dict:
    return CONTROLLER.validate_external_routing_scheduler_attestation(
        json.dumps(payload).encode(), **_validation_kwargs()
    )


def test_scoped_attestation_accepts_only_the_exact_observed_topology(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _mock_validation_io(monkeypatch)
    payload = _payload()

    assert _validate(payload) == payload
    with pytest.raises(SystemExit, match="BLOCKED.*AWS database and API write fence"):
        CONTROLLER.require_zero_aws_writer_gate(payload, operation="deploy")


def test_bound_release_identity_parses_one_exact_tag_object_manifest_binding(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    tag = "v1.45.207-beta"
    digest = f"sha256:{'a' * 64}"
    tag_object = "b" * 40
    monkeypatch.setattr(
        CONTROLLER,
        "validate_published_release_identity",
        lambda *_args, **_kwargs: "1.45.207-beta",
    )

    def fake_git(*args: str) -> str:
        if args[:2] == ("rev-parse", f"refs/tags/{tag}"):
            return tag_object
        if args[:2] == ("cat-file", "tag"):
            return (
                f"object {SOURCE_SHA}\ntype commit\ntag {tag}\n"
                "tagger Release <release@example.com> 0 +0000\n\n"
                f"release\nOMEGA-Release-Candidate-Manifest-SHA256: {digest}\n"
            )
        raise AssertionError(args)

    monkeypatch.setattr(CONTROLLER, "git", fake_git)
    assert CONTROLLER.validate_bound_release_identity(tag, SOURCE_SHA) == (
        CONTROLLER.BoundReleaseIdentity("1.45.207-beta", digest, tag_object)
    )


def test_bound_release_identity_rejects_duplicate_manifest_bindings(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    tag = "v1.45.207-beta"
    binding = f"OMEGA-Release-Candidate-Manifest-SHA256: sha256:{'a' * 64}"
    monkeypatch.setattr(
        CONTROLLER,
        "validate_published_release_identity",
        lambda *_args, **_kwargs: "1.45.207-beta",
    )
    monkeypatch.setattr(
        CONTROLLER,
        "git",
        lambda *args: (
            "b" * 40
            if args[0] == "rev-parse"
            else f"object {SOURCE_SHA}\ntype commit\ntag {tag}\n\n{binding}\n{binding}\n"
        ),
    )
    with pytest.raises(ValueError, match="one exact sealed manifest binding"):
        CONTROLLER.validate_bound_release_identity(tag, SOURCE_SHA)


@pytest.mark.parametrize(
    ("path", "value"),
    [
        (("aws", "origin", "alb_hostname"), "other.example.com"),
        (("aws", "destination", "alb_hostname"), "other.example.com"),
        (("aws", "origin", "frozen_source_sha"), "2" * 40),
        (
            ("aws", "destination", "scheduler_last_heartbeat_at"),
            "2026-08-07T01:26:05.241563+00:00",
        ),
        (("forensic_evidence", "sha256"), "3" * 64),
        (("scope",), "cross-cloud-hard-fence"),
    ],
)
def test_scoped_attestation_rejects_allowlist_or_forensic_drift(
    monkeypatch: pytest.MonkeyPatch,
    path: tuple[str, ...],
    value: object,
) -> None:
    _mock_validation_io(monkeypatch)
    payload = _payload()
    target = payload
    for key in path[:-1]:
        target = target[key]
    target[path[-1]] = value

    with pytest.raises((ValueError, RuntimeError)):
        _validate(payload)


@pytest.mark.parametrize(
    "attested_at",
    [
        datetime.now(timezone.utc) + timedelta(seconds=30),
        datetime.now(timezone.utc) - timedelta(minutes=16),
    ],
)
def test_scoped_attestation_rejects_future_and_stale_timestamps(
    monkeypatch: pytest.MonkeyPatch, attested_at: datetime
) -> None:
    _mock_validation_io(monkeypatch)
    with pytest.raises(ValueError, match="15-minute TTL"):
        _validate(_payload(attested_at=attested_at))


def test_same_attestation_is_rejected_when_deploy_crosses_its_ttl(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _mock_validation_io(monkeypatch)
    attested = datetime(2026, 8, 11, 12, 0, tzinfo=timezone.utc)
    payload = _payload(attested_at=attested)

    class _FrozenDateTime(datetime):
        current = attested + timedelta(minutes=1)

        @classmethod
        def now(cls, tz=None):  # noqa: ANN001
            value = cls.current
            return value if tz is None else value.astimezone(tz)

    monkeypatch.setattr(CONTROLLER, "datetime", _FrozenDateTime)
    assert _validate(payload) == payload

    _FrozenDateTime.current = attested + timedelta(minutes=16)
    with pytest.raises(ValueError, match="15-minute TTL"):
        _validate(payload)


def test_postdeploy_attestation_must_be_distinct_and_observed_after_completion(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    completed = datetime(2026, 8, 11, 12, 0, tzinfo=timezone.utc)
    payload = _payload(attested_at=completed + timedelta(seconds=1))
    predeploy = b"predeploy-evidence"
    postdeploy = b"postdeploy-evidence"
    monkeypatch.setattr(
        CONTROLLER,
        "read_private_external_routing_scheduler_attestation",
        lambda *_args, **_kwargs: (postdeploy, payload),
    )

    raw, observed = CONTROLLER.wait_for_fresh_postdeploy_routing_scheduler_attestation(
        tmp_path / "post.json",
        predeploy_sha256=CONTROLLER.hashlib.sha256(predeploy).hexdigest(),
        not_before=completed,
        timeout_seconds=30,
    )
    assert raw == postdeploy
    assert observed == payload


@pytest.mark.parametrize(
    "failure", ["same-bytes", "predates-completion", "equal-completion"]
)
def test_postdeploy_attestation_rejects_reuse_or_predeploy_observation(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, failure: str
) -> None:
    completed = datetime(2026, 8, 11, 12, 0, tzinfo=timezone.utc)
    predeploy = b"predeploy-evidence"
    payload = _payload(
        attested_at=(
            completed - timedelta(seconds=1)
            if failure == "predates-completion"
            else completed
            if failure == "equal-completion"
            else completed + timedelta(seconds=1)
        )
    )
    raw = predeploy if failure == "same-bytes" else b"different-evidence"
    monkeypatch.setattr(
        CONTROLLER,
        "read_private_external_routing_scheduler_attestation",
        lambda *_args, **_kwargs: (raw, payload),
    )
    ticks = iter((0.0, 31.0))
    monkeypatch.setattr(CONTROLLER.time, "monotonic", lambda: next(ticks))

    with pytest.raises(RuntimeError, match="reused|predates"):
        CONTROLLER.wait_for_fresh_postdeploy_routing_scheduler_attestation(
            tmp_path / "post.json",
            predeploy_sha256=CONTROLLER.hashlib.sha256(predeploy).hexdigest(),
            not_before=completed,
            timeout_seconds=30,
        )


class _Socket:
    def __init__(self, address: str):
        self.address = address
        self.sent = b""

    def sendall(self, value: bytes) -> None:
        self.sent += value

    def close(self) -> None:
        return None


class _Response:
    status = 200

    def __init__(self, stream: _Socket):
        self.stream = stream

    def begin(self) -> None:
        return None

    def read(self, _size: int) -> bytes:
        return b"{}"

    def getheaders(self) -> list[tuple[str, str]]:
        return [("Content-Type", "application/json")]


def test_https_probe_visits_every_a_record_with_verified_canonical_sni_and_host(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sockets: list[_Socket] = []
    wrapped: list[tuple[str, str]] = []

    class _Context:
        def wrap_socket(self, raw: _Socket, *, server_hostname: str) -> _Socket:
            wrapped.append((raw.address, server_hostname))
            return raw

    monkeypatch.setattr(
        CONTROLLER,
        "_resolve_external_a_records",
        lambda _hostname, _port: {"192.0.2.11", "192.0.2.12"},
    )
    monkeypatch.setattr(
        CONTROLLER.socket,
        "create_connection",
        lambda endpoint, timeout: sockets.append(_Socket(endpoint[0])) or sockets[-1],
    )
    monkeypatch.setattr(CONTROLLER.ssl, "create_default_context", lambda: _Context())
    monkeypatch.setattr(CONTROLLER.http.client, "HTTPResponse", _Response)

    results = CONTROLLER._probe_external_endpoint(
        CONTROLLER.CANONICAL_AWS_DESTINATION_ALB,
        http_host=CONTROLLER.CANONICAL_PUBLIC_TLS_NAME,
        path="/healthz",
        tls=True,
    )

    assert [row[0] for row in results] == ["192.0.2.11", "192.0.2.12"]
    assert wrapped == [
        ("192.0.2.11", CONTROLLER.CANONICAL_PUBLIC_TLS_NAME),
        ("192.0.2.12", CONTROLLER.CANONICAL_PUBLIC_TLS_NAME),
    ]
    assert all(
        f"Host: {CONTROLLER.CANONICAL_PUBLIC_TLS_NAME}\r\n".encode() in item.sent
        for item in sockets
    )


@pytest.mark.parametrize(
    "location",
    [
        f"https://{CONTROLLER.CANONICAL_PUBLIC_TLS_NAME}/healthz",
        f"https://{CONTROLLER.CANONICAL_PUBLIC_TLS_NAME}:443/healthz",
    ],
)
def test_http_redirect_accepts_only_the_exact_canonical_health_route(
    monkeypatch: pytest.MonkeyPatch, location: str
) -> None:
    monkeypatch.setattr(
        CONTROLLER,
        "_probe_external_endpoint",
        lambda *_args, **_kwargs: [("192.0.2.10", 301, {"location": location}, b"")],
    )
    CONTROLLER._probe_external_http_redirect(
        CONTROLLER.CANONICAL_AWS_ORIGIN_ALB,
        http_host=CONTROLLER.CANONICAL_PUBLIC_TLS_NAME,
        expected_status=301,
    )


def test_http_redirect_rejects_same_host_with_a_different_path(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        CONTROLLER,
        "_probe_external_endpoint",
        lambda *_args, **_kwargs: [
            (
                "192.0.2.10",
                301,
                {"location": (f"https://{CONTROLLER.CANONICAL_PUBLIC_TLS_NAME}/other")},
                b"",
            )
        ],
    )
    with pytest.raises(RuntimeError, match="redirect changed"):
        CONTROLLER._probe_external_http_redirect(
            CONTROLLER.CANONICAL_AWS_ORIGIN_ALB,
            http_host=CONTROLLER.CANONICAL_PUBLIC_TLS_NAME,
            expected_status=301,
        )


def test_live_scheduler_probe_compares_status_and_exact_heartbeat(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    payload = _payload()
    monkeypatch.setattr(
        CONTROLLER, "_resolve_public_addresses", lambda _hostname: {PUBLIC_IP}
    )
    monkeypatch.setattr(
        CONTROLLER, "_probe_external_health_status", lambda *a, **k: None
    )
    monkeypatch.setattr(
        CONTROLLER, "_probe_external_http_redirect", lambda *a, **k: None
    )
    monkeypatch.setattr(
        CONTROLLER,
        "_probe_external_json_status",
        lambda *a, **k: {
            "scheduler": {
                "status": "unhealthy",
                "latest_scheduler_heartbeat": "2026-08-07T01:26:05.241563+00:00",
            }
        },
    )

    with pytest.raises(RuntimeError, match="scheduler status or heartbeat changed"):
        CONTROLLER.revalidate_external_routing_scheduler_live(
            payload,
            console_domain=CONTROLLER.CANONICAL_PUBLIC_TLS_NAME,
            workspace_domain="workspace.7businesssolutions.com",
        )


def test_mutating_commands_gate_before_upload_or_remote_and_reprobe_after_deploy() -> (
    None
):
    source = CONTROLLER_PATH.read_text(encoding="utf-8")
    reconcile = source[
        source.index("def command_reconcile_pipeline_runs") : source.index(
            "def command_deploy"
        )
    ]
    deploy = source[
        source.index("def command_deploy") : source.index("def command_rehearsal")
    ]
    backup = source[
        source.index("def command_backup") : source.index(
            "def command_reconcile_pipeline_runs"
        )
    ]

    assert (
        reconcile.index("require_zero_aws_writer_gate")
        < reconcile.index("upload_reconciliation_manifest_immutable")
        < reconcile.index("remote_script")
    )
    assert (
        deploy.index("require_zero_aws_writer_gate")
        < deploy.index("upload_external_routing_scheduler_attestation_immutable")
        < deploy.index("remote_script")
    )
    assert deploy.rindex("revalidate_external_routing_scheduler_live") > deploy.index(
        "remote_script"
    )
    assert deploy.index(
        "wait_for_fresh_postdeploy_routing_scheduler_attestation"
    ) > deploy.index("remote_script")
    assert deploy.index('if remote_status != "PASS"') < deploy.index(
        "wait_for_fresh_postdeploy_routing_scheduler_attestation"
    )
    assert deploy.index("write_evidence") < deploy.index(
        "wait_for_fresh_postdeploy_routing_scheduler_attestation"
    )
    assert (
        backup.index("read_private_external_routing_scheduler_attestation")
        < backup.index("require_zero_aws_writer_gate")
        < backup.index("upload_external_routing_scheduler_attestation_immutable")
        < backup.index("remote_script")
    )


def test_published_pull_receipt_precedes_the_fresh_backup_and_cas() -> None:
    preflight = (ROOT / "scripts/gcp/image-preflight.sh").read_text(encoding="utf-8")
    reconcile = (ROOT / "scripts/gcp/reconcile-pipeline-runs.sh").read_text(
        encoding="utf-8"
    )
    controller = CONTROLLER_PATH.read_text(encoding="utf-8")
    backup_command = controller[
        controller.index("def command_backup") : controller.index(
            "def command_reconcile_pipeline_runs"
        )
    ]

    assert '"${WORKDIR}/completion.json"' in preflight
    assert '"manifest_sha256": sys.argv[2]' in preflight
    assert '"lock_sha256": sys.argv[3]' in preflight
    assert 'install -m 0400 "${WORKDIR}/completion.json"' in preflight
    assert "PREFLIGHT_COMPLETION_SHA256" in reconcile
    assert "backup_created_at <= completed_at" in reconcile
    assert (
        reconcile.index("published release then fresh backup")
        < reconcile.index('"$WATCHDOG" arm "$$" "$OPERATION_MARKER"')
        < reconcile.index("run_engine apply")
    )
    assert '"published_release_preflight"' in reconcile
    assert "validate_bound_release_identity(args.release_tag, args.candidate_ref)" in (
        backup_command
    )


def test_current_scoped_schema_blocks_remote_reconciliation_before_fencing() -> None:
    script = (ROOT / "scripts/gcp/reconcile-pipeline-runs.sh").read_text(
        encoding="utf-8"
    )
    blocked = script.index('fail "pre-mutation reconciliation gate"')
    assert blocked < script.index('"$WATCHDOG" arm "$$" "$OPERATION_MARKER"')
    assert blocked < script.index('"${COMPOSE[@]}" stop --timeout 60')
    assert blocked < script.index("run_engine apply")
    assert 'get("db_api_hard_fence_proven") is not False' in script
