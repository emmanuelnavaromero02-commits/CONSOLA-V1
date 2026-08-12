from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

import pytest


REPO = Path(__file__).resolve().parents[1]
GATE = REPO / "infra/terraform-gcp/templates/omega-operation-gate"
DEPLOY_REF = "a" * 40
SHARED_REF = "b" * 40
VERSION = "1.45.207-beta"


def test_production_gate_has_no_environment_controlled_test_bypass() -> None:
    assert "OMEGA_GCP_OPERATION_GATE_TEST" not in GATE.read_text(encoding="utf-8")
    assert "OMEGA_GCP_OPERATION_GATE_TEST" not in (
        REPO / "scripts/gcp/safe_io.py"
    ).read_text(encoding="utf-8")


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _release_tree_sha256(root: Path) -> str:
    digest = hashlib.sha256()
    root_mode = root.lstat().st_mode & 0o7777
    digest.update(f"d\0.\0{root_mode:04o}\0".encode())
    entries = [path for path in root.rglob("*")]
    for path in sorted(entries, key=lambda item: item.relative_to(root).as_posix()):
        info = path.lstat()
        relative = path.relative_to(root).as_posix()
        if path.is_dir():
            digest.update(f"d\0{relative}\0{info.st_mode & 0o7777:04o}\0".encode())
        else:
            raw = path.read_bytes()
            digest.update(f"f\0{relative}\0{info.st_mode & 0o7777:04o}\0".encode())
            digest.update(str(len(raw)).encode() + b"\0")
            digest.update(raw)
    return digest.hexdigest()


def _run_gate(
    app_root: Path,
    *arguments: str,
    **overrides: str,
) -> subprocess.CompletedProcess[str]:
    harness_root = app_root.parent / "operation-gate-harness"
    harness_root.mkdir(exist_ok=True)
    safe_io = REPO / "scripts/gcp/safe_io.py"
    launcher = harness_root / "safe-io"
    launcher.write_text(
        "\n".join(
            (
                f"#!{sys.executable}",
                "import importlib.util",
                "import sys",
                f"spec = importlib.util.spec_from_file_location('omega_safe_io', {str(safe_io)!r})",
                "module = importlib.util.module_from_spec(spec)",
                "spec.loader.exec_module(module)",
                "module._require_root = lambda: None",
                "raise SystemExit(module.main())",
                "",
            )
        ),
        encoding="utf-8",
    )
    launcher.chmod(0o700)
    watchdog = harness_root / "watchdog"
    watchdog.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    watchdog.chmod(0o700)
    timeout = harness_root / "timeout"
    timeout.write_text('#!/bin/sh\nshift 3\nexec "$@"\n', encoding="utf-8")
    timeout.chmod(0o700)
    env = {
        key: value
        for key, value in os.environ.items()
        if not key.startswith("OMEGA_GCP_")
    }
    env["OMEGA_GCP_APP_ROOT"] = str(app_root)
    env["OMEGA_GCP_SAFE_IO"] = str(launcher)
    env["OMEGA_GCP_OPERATION_WATCHDOG"] = str(watchdog)
    env["OMEGA_GCP_TIMEOUT"] = str(timeout)
    env.update(overrides)
    return subprocess.run(
        [GATE, *arguments],
        text=True,
        capture_output=True,
        check=False,
        env=env,
    )


@dataclass
class RuntimeLayout:
    app_root: Path
    bundle: Path
    helper_root: Path
    source_ref: str
    helper_mode: str
    reboot: Path
    contract: Path
    safe_io: Path
    release_tree_sha256: str

    def helper(self, contract: str = "normal") -> dict[str, object]:
        common: dict[str, object] = {
            "mode": self.helper_mode,
            "source_ref": self.source_ref,
            "source_artifact_uri": (
                "gs://omega-source-bucket/deploy-artifacts/"
                f"{self.source_ref}/repo.tar.gz"
            ),
            "reboot_runtime_sha256": _sha256(self.reboot),
            "runtime_contract_sha256": _sha256(self.contract),
        }
        if contract == "legacy-current":
            assert self.helper_mode == "current"
            return common
        if contract == "legacy-shared":
            assert self.helper_mode == "shared"
            return {**common, "source_artifact_sha256": "c" * 64}
        assert contract == "normal"
        return {
            **common,
            "source_artifact_generation": "987654321",
            "source_artifact_size_bytes": 4096,
            "source_artifact_sha256": "c" * 64,
            "safe_io_sha256": _sha256(self.safe_io),
        }

    def publish(self, helper: dict[str, object]) -> None:
        provenance_path = self.bundle / "runtime-provenance.json"
        (self.bundle / "bootstrap-state.json").write_text(
            json.dumps(
                {
                    "schema_version": 2,
                    "state": "complete",
                    "deploy_ref": DEPLOY_REF,
                    "version": VERSION,
                    "runtime_provenance_sha256": _sha256(provenance_path),
                    "release_tree_sha256": self.release_tree_sha256,
                    "canonical_writer": True,
                    "secret_versions": {
                        "control_room_evidence_signing_key_id": "1",
                        "control_room_evidence_signing_key": "2",
                        "control_room_evidence_signing_previous_keys": "3",
                        "gcs_hmac_access_key_id": "",
                        "gcs_hmac_secret_access_key": "",
                    },
                    "reboot_helper": helper,
                    "completed_at": "2026-08-12T00:00:00+00:00",
                }
            ),
            encoding="utf-8",
        )
        (self.bundle / "bootstrap-state.json").chmod(0o600)


def _runtime(tmp_path: Path, helper_mode: str = "current") -> RuntimeLayout:
    app_root = tmp_path / "modecissions"
    shared = app_root / "shared"
    bundles = shared / "state-bundles"
    release = app_root / "releases" / DEPLOY_REF
    release.mkdir(parents=True)
    bundles.mkdir(parents=True)
    app_root.chmod(0o755)
    shared.chmod(0o755)
    (app_root / "releases").chmod(0o755)
    release.chmod(0o755)
    bundles.chmod(0o700)
    (release / "VERSION").write_text(f"{VERSION}\n", encoding="utf-8")
    (release / "VERSION").chmod(0o444)
    (release / "infra").mkdir()
    base_compose = release / "infra/docker-compose.yml"
    shared_env = shared / "infra.env"
    gcp_compose = shared / "docker-compose.gcp.yml"
    base_compose.write_text("services: {}\n", encoding="utf-8")
    shared_env.write_text("APP_ENV=production\n", encoding="utf-8")
    gcp_compose.write_text("services: {}\n", encoding="utf-8")
    base_compose.chmod(0o444)
    shared_env.chmod(0o600)
    gcp_compose.chmod(0o600)
    (app_root / "current").symlink_to(release, target_is_directory=True)

    source_ref = DEPLOY_REF if helper_mode == "current" else SHARED_REF
    helper_root = (
        release / "scripts/gcp"
        if helper_mode == "current"
        else shared / "bin" / source_ref
    )
    helper_root.mkdir(parents=True)
    reboot = helper_root / "reboot-runtime.sh"
    contract = helper_root / "runtime_contract.py"
    safe_io = helper_root / "safe_io.py"
    reboot.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    contract.write_text("#!/usr/bin/env python3\n", encoding="utf-8")
    safe_io.write_text("#!/usr/bin/env python3\n", encoding="utf-8")
    for helper in (reboot, contract, safe_io):
        helper.chmod(0o755)

    bundle = bundles / "candidate"
    bundle.mkdir()
    bundle.chmod(0o700)
    (bundle / "runtime-provenance.json").write_text(
        json.dumps(
            {
                "schema_version": 2,
                "mode": "bootstrap",
                "compose_project": "infra",
                "deploy_ref": DEPLOY_REF,
                "version": VERSION,
                "runtime_input_sha256": {
                    "shared_env": _sha256(shared_env),
                    "base_compose": _sha256(base_compose),
                    "gcp_compose": _sha256(gcp_compose),
                },
            }
        ),
        encoding="utf-8",
    )
    (bundle / "runtime-provenance.json").chmod(0o600)
    for directory, directory_names, _file_names in os.walk(release):
        Path(directory).chmod(0o555)
        for name in directory_names:
            (Path(directory) / name).chmod(0o555)
    for path in release.rglob("*"):
        if path.is_file():
            path.chmod(path.lstat().st_mode & ~0o222)
    release_tree_sha256 = _release_tree_sha256(release)
    (shared / "runtime-state").symlink_to(bundle, target_is_directory=True)
    return RuntimeLayout(
        app_root=app_root,
        bundle=bundle,
        helper_root=helper_root,
        source_ref=source_ref,
        helper_mode=helper_mode,
        reboot=reboot,
        contract=contract,
        safe_io=safe_io,
        release_tree_sha256=release_tree_sha256,
    )


def _initializing_marker(app_root: Path) -> Path:
    shared = app_root / "shared"
    shared.mkdir(parents=True, exist_ok=True)
    app_root.chmod(0o755)
    shared.chmod(0o755)
    bundles = shared / "state-bundles"
    if bundles.exists():
        bundles.chmod(0o700)
    marker = shared / "operation-state.json"
    marker.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "operation": "bootstrap",
                "state": "initializing",
                "deploy_ref": DEPLOY_REF,
            }
        ),
        encoding="utf-8",
    )
    marker.chmod(0o600)
    return marker


def test_missing_runtime_state_fails_closed_without_first_boot_authorization(
    tmp_path: Path,
) -> None:
    app_root = tmp_path / "modecissions"
    (app_root / "shared/state-bundles").mkdir(parents=True)

    result = _run_gate(app_root)

    assert result.returncode == 76
    assert "runtime-state is missing" in result.stderr


def test_authorized_pristine_first_boot_requires_durable_initializing_marker(
    tmp_path: Path,
) -> None:
    app_root = tmp_path / "modecissions"
    (app_root / "shared/state-bundles").mkdir(parents=True)
    env = {
        "OMEGA_GCP_INITIAL_BOOTSTRAP": "1",
        "OMEGA_GCP_ALLOW_OPERATION_MARKER": "1",
    }

    missing = _run_gate(app_root, **env)
    assert missing.returncode == 76

    marker = _initializing_marker(app_root)
    wrong = json.loads(marker.read_text(encoding="utf-8"))
    wrong["state"] = "complete"
    marker.write_text(json.dumps(wrong), encoding="utf-8")
    invalid = _run_gate(app_root, **env)
    assert invalid.returncode == 76

    wrong["state"] = "initializing"
    marker.write_text(json.dumps(wrong), encoding="utf-8")
    accepted = _run_gate(app_root, **env)
    assert accepted.returncode == 0, accepted.stderr


@pytest.mark.parametrize(
    ("relative", "directory"),
    [
        ("current", False),
        ("previous", False),
        ("shared/runtime-state", False),
        ("shared/day2-initialized.json", False),
        ("shared/infra.env", False),
        ("shared/docker-compose.gcp.yml", False),
        ("shared/bootstrap-state.json", False),
        ("shared/runtime-provenance.json", False),
        ("releases/stale", True),
        ("shared/state-bundles/stale", True),
    ],
)
def test_first_boot_authorization_rejects_any_prior_runtime_evidence(
    tmp_path: Path,
    relative: str,
    directory: bool,
) -> None:
    app_root = tmp_path / "modecissions"
    _initializing_marker(app_root)
    residue = app_root / relative
    if directory:
        residue.mkdir(parents=True)
        (residue / "evidence").write_text("stale\n", encoding="utf-8")
    else:
        residue.parent.mkdir(parents=True, exist_ok=True)
        residue.write_text("stale\n", encoding="utf-8")

    result = _run_gate(
        app_root,
        OMEGA_GCP_INITIAL_BOOTSTRAP="1",
        OMEGA_GCP_ALLOW_OPERATION_MARKER="1",
    )

    assert result.returncode == 76


def test_operation_marker_fences_complete_state_without_explicit_allow(
    tmp_path: Path,
) -> None:
    runtime = _runtime(tmp_path)
    runtime.publish(runtime.helper())
    _initializing_marker(runtime.app_root)

    fenced = _run_gate(runtime.app_root)
    allowed = _run_gate(
        runtime.app_root,
        OMEGA_GCP_ALLOW_OPERATION_MARKER="1",
    )

    assert fenced.returncode == 75
    assert "operation is incomplete" in fenced.stderr
    assert allowed.returncode == 0, allowed.stderr


def test_dangling_operation_marker_symlink_always_fails_closed(
    tmp_path: Path,
) -> None:
    runtime = _runtime(tmp_path)
    runtime.publish(runtime.helper())
    marker = runtime.app_root / "shared/operation-state.json"
    marker.symlink_to(runtime.app_root / "missing-operation-state.json")

    default = _run_gate(runtime.app_root)
    attempted_allow = _run_gate(
        runtime.app_root,
        OMEGA_GCP_ALLOW_OPERATION_MARKER="1",
    )

    assert default.returncode == 75
    assert attempted_allow.returncode == 75
    assert "marker is a link" in attempted_allow.stderr


def test_docker_start_requires_separate_volatile_authorization(
    tmp_path: Path,
) -> None:
    runtime = _runtime(tmp_path)
    runtime.publish(runtime.helper())

    ordinary_verification = _run_gate(runtime.app_root)
    denied_start = _run_gate(runtime.app_root, "authorize-start")
    authorized_start = _run_gate(
        runtime.app_root,
        "authorize-start",
        OMEGA_GCP_RUNTIME_START_AUTHORIZED="1",
    )

    assert ordinary_verification.returncode == 0, ordinary_verification.stderr
    assert denied_start.returncode == 74
    assert "runtime-start authorization is missing" in denied_start.stderr
    assert authorized_start.returncode == 0, authorized_start.stderr


def test_legacy_provenance_schema_requires_one_time_adoption_flag(
    tmp_path: Path,
) -> None:
    runtime = _runtime(tmp_path)
    provenance = runtime.bundle / "runtime-provenance.json"
    payload = json.loads(provenance.read_text(encoding="utf-8"))
    payload["schema_version"] = 1
    provenance.write_text(json.dumps(payload), encoding="utf-8")
    runtime.publish(runtime.helper())

    ordinary = _run_gate(runtime.app_root)
    adoption = _run_gate(
        runtime.app_root,
        OMEGA_GCP_LEGACY_PROVENANCE_UPGRADE="1",
    )
    start = _run_gate(
        runtime.app_root,
        "authorize-start",
        OMEGA_GCP_RUNTIME_START_AUTHORIZED="1",
        OMEGA_GCP_LEGACY_PROVENANCE_UPGRADE="1",
    )
    start_without_upgrade = _run_gate(
        runtime.app_root,
        "authorize-start",
        OMEGA_GCP_RUNTIME_START_AUTHORIZED="1",
    )

    assert ordinary.returncode == 76
    assert "requires explicit adoption" in ordinary.stderr
    assert adoption.returncode == 0, adoption.stderr
    assert start.returncode == 0, start.stderr
    assert start_without_upgrade.returncode == 76
    assert "requires explicit adoption" in start_without_upgrade.stderr


@pytest.mark.parametrize("helper_mode", ["current", "shared"])
def test_exact_generation_size_sha_helper_contract_is_accepted(
    tmp_path: Path,
    helper_mode: str,
) -> None:
    runtime = _runtime(tmp_path, helper_mode)
    runtime.publish(runtime.helper())

    result = _run_gate(runtime.app_root)

    assert result.returncode == 0, result.stderr


def test_runtime_input_hash_drift_fails_closed(tmp_path: Path) -> None:
    runtime = _runtime(tmp_path)
    runtime.publish(runtime.helper())
    (runtime.app_root / "shared/infra.env").write_text(
        "APP_ENV=production\nDRIFT=true\n", encoding="utf-8"
    )

    result = _run_gate(runtime.app_root)

    assert result.returncode == 76
    assert "runtime input checksum differs: shared_env" in result.stderr


@pytest.mark.parametrize(
    ("helper_mode", "legacy_contract"),
    [("current", "legacy-current"), ("shared", "legacy-shared")],
)
def test_exact_legacy_helper_shape_requires_one_time_upgrade_flag(
    tmp_path: Path,
    helper_mode: str,
    legacy_contract: str,
) -> None:
    runtime = _runtime(tmp_path, helper_mode)
    runtime.publish(runtime.helper(legacy_contract))

    ordinary = _run_gate(runtime.app_root)
    upgrade = _run_gate(
        runtime.app_root,
        OMEGA_GCP_LEGACY_ARTIFACT_UPGRADE="1",
    )

    assert ordinary.returncode == 76
    assert upgrade.returncode == 0, upgrade.stderr


@pytest.mark.parametrize(
    "mutation",
    [
        {"source_artifact_generation": None},
        {"source_artifact_size_bytes": None},
        {"source_artifact_generation": "987654321"},
        {"source_artifact_size_bytes": 4096},
        {"source_artifact_sha256": None},
        {"safe_io_sha256": None},
        {"unexpected_field": "ignored-data"},
    ],
)
def test_legacy_current_upgrade_rejects_null_placeholders_and_extra_fields(
    tmp_path: Path,
    mutation: dict[str, object],
) -> None:
    runtime = _runtime(tmp_path, "current")
    helper = runtime.helper("legacy-current")
    helper.update(mutation)
    runtime.publish(helper)

    result = _run_gate(
        runtime.app_root,
        OMEGA_GCP_LEGACY_ARTIFACT_UPGRADE="1",
    )

    assert result.returncode == 76


@pytest.mark.parametrize(
    "mutation",
    [
        {"source_artifact_generation": None},
        {"source_artifact_size_bytes": 4096},
        {"safe_io_sha256": None},
        {"unexpected_field": "ignored-data"},
    ],
)
def test_legacy_shared_upgrade_rejects_nonexact_fields(
    tmp_path: Path,
    mutation: dict[str, object],
) -> None:
    runtime = _runtime(tmp_path, "shared")
    helper = runtime.helper("legacy-shared")
    helper.update(mutation)
    runtime.publish(helper)

    result = _run_gate(
        runtime.app_root,
        OMEGA_GCP_LEGACY_ARTIFACT_UPGRADE="1",
    )

    assert result.returncode == 76


def test_legacy_upgrade_flag_rejects_already_upgraded_helper_shape(
    tmp_path: Path,
) -> None:
    runtime = _runtime(tmp_path)
    runtime.publish(runtime.helper())

    result = _run_gate(
        runtime.app_root,
        OMEGA_GCP_LEGACY_ARTIFACT_UPGRADE="1",
    )

    assert result.returncode == 76


def test_normal_helper_contract_rejects_unreviewed_extra_fields(tmp_path: Path) -> None:
    runtime = _runtime(tmp_path)
    helper = runtime.helper()
    helper["unexpected_field"] = "ignored-data"
    runtime.publish(helper)

    result = _run_gate(runtime.app_root)

    assert result.returncode == 76


def test_normal_helper_contract_rejects_boolean_artifact_size(tmp_path: Path) -> None:
    runtime = _runtime(tmp_path)
    helper = runtime.helper()
    helper["source_artifact_size_bytes"] = True
    runtime.publish(helper)

    result = _run_gate(runtime.app_root)

    assert result.returncode == 76
