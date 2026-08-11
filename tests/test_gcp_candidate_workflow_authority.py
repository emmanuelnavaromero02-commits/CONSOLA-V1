from __future__ import annotations

import hashlib
import importlib.util
import json
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
SOURCE_SHA = "a" * 40
VERSION = "1.45.207-beta"
RUN_ID = "987654321"
RUN_ATTEMPT = "1"


def _load_module():
    path = REPO / "scripts/gcp_release.py"
    spec = importlib.util.spec_from_file_location("gcp_release_candidate_test", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _manifest(*, run_id: str = RUN_ID, run_attempt: str = RUN_ATTEMPT) -> bytes:
    payload = {
        "candidate_tag": f"candidate-{SOURCE_SHA}",
        "github_run_attempt": run_attempt,
        "github_run_id": run_id,
        "images": [{} for _ in range(15)],
        "registry": "ghcr.io",
        "release_tag": f"v{VERSION}",
        "repository": "emmanuelnavaromero02-commits/CONSOLA-V1",
        "schema_version": 1,
        "source": ("https://github.com/emmanuelnavaromero02-commits/CONSOLA-V1"),
        "source_sha": SOURCE_SHA,
        "version": VERSION,
    }
    return (json.dumps(payload, sort_keys=True, separators=(",", ":")) + "\n").encode()


def _run_payload() -> dict[str, object]:
    repository = {"full_name": "emmanuelnavaromero02-commits/CONSOLA-V1"}
    return {
        "id": int(RUN_ID),
        "run_attempt": int(RUN_ATTEMPT),
        "head_sha": SOURCE_SHA,
        "head_branch": "main",
        "event": "workflow_dispatch",
        "status": "completed",
        "conclusion": "success",
        "path": ".github/workflows/release-candidate.yml",
        "repository": repository,
        "head_repository": repository,
    }


def _artifact_payload() -> dict[str, object]:
    artifact_id = 123456
    return {
        "total_count": 1,
        "artifacts": [
            {
                "id": artifact_id,
                "name": f"release-candidate-{SOURCE_SHA}",
                "expired": False,
                "archive_download_url": (
                    "https://api.github.com/repos/"
                    "emmanuelnavaromero02-commits/CONSOLA-V1/actions/artifacts/"
                    f"{artifact_id}/zip"
                ),
                "workflow_run": {"id": int(RUN_ID), "head_sha": SOURCE_SHA},
            }
        ],
    }


def _install_gh_mock(
    monkeypatch, module, *, run_payload=None, artifact_payload=None, raw=None
):
    run_document = _run_payload() if run_payload is None else run_payload
    artifact_document = (
        _artifact_payload() if artifact_payload is None else artifact_payload
    )
    manifest = _manifest() if raw is None else raw

    def fake_run(command, **_kwargs):
        if command[:3] == ["gh", "api", "--method"]:
            path = command[-1]
            payload = (
                artifact_document if path.endswith("?per_page=100") else run_document
            )
            return subprocess.CompletedProcess(command, 0, json.dumps(payload), "")
        if command[:3] == ["gh", "run", "download"]:
            destination = Path(command[command.index("--dir") + 1])
            (destination / "release-candidate.json").write_bytes(manifest)
            return subprocess.CompletedProcess(command, 0, "", "")
        raise AssertionError(command)

    monkeypatch.setattr(module, "run", fake_run)


def test_candidate_workflow_authority_is_exact_run_and_byte_bound(monkeypatch) -> None:
    module = _load_module()
    raw = _manifest()
    _install_gh_mock(monkeypatch, module, raw=raw)

    authority = module.validate_candidate_workflow_authority(
        source_sha=SOURCE_SHA,
        version=VERSION,
        run_id=RUN_ID,
        run_attempt=RUN_ATTEMPT,
    )

    expected = hashlib.sha256(raw).hexdigest()
    assert authority.run_id == RUN_ID
    assert authority.run_attempt == RUN_ATTEMPT
    assert authority.manifest_digest == f"sha256:{expected}"
    assert authority.payload_sha256 == expected
    assert authority.artifact_id == "123456"


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("head_sha", "b" * 40),
        ("run_attempt", 2),
        ("conclusion", "failure"),
        ("status", "in_progress"),
        ("path", ".github/workflows/other.yml"),
    ],
)
def test_candidate_workflow_authority_rejects_run_drift(
    monkeypatch, field: str, value: object
) -> None:
    module = _load_module()
    payload = _run_payload()
    payload[field] = value
    _install_gh_mock(monkeypatch, module, run_payload=payload)

    with pytest.raises(RuntimeError, match="run/head/attempt"):
        module.validate_candidate_workflow_authority(
            source_sha=SOURCE_SHA,
            version=VERSION,
            run_id=RUN_ID,
            run_attempt=RUN_ATTEMPT,
        )


def test_candidate_workflow_authority_rejects_artifact_metadata_drift(
    monkeypatch,
) -> None:
    module = _load_module()
    payload = _artifact_payload()
    payload["artifacts"][0]["workflow_run"]["id"] = int(RUN_ID) + 1
    _install_gh_mock(monkeypatch, module, artifact_payload=payload)

    with pytest.raises(RuntimeError, match="artifact metadata"):
        module.validate_candidate_workflow_authority(
            source_sha=SOURCE_SHA,
            version=VERSION,
            run_id=RUN_ID,
            run_attempt=RUN_ATTEMPT,
        )


def test_candidate_workflow_authority_rejects_payload_run_drift(monkeypatch) -> None:
    module = _load_module()
    _install_gh_mock(monkeypatch, module, raw=_manifest(run_attempt="2"))

    with pytest.raises(RuntimeError, match="artifact identity"):
        module.validate_candidate_workflow_authority(
            source_sha=SOURCE_SHA,
            version=VERSION,
            run_id=RUN_ID,
            run_attempt=RUN_ATTEMPT,
        )


def test_legacy_tag_identity_is_recorded_separately_from_runtime_authority(
    monkeypatch,
) -> None:
    module = _load_module()
    tag = "v1.45.205-beta"
    tag_object = "b" * 40
    tag_commit = "c" * 40
    runtime_ref = "d" * 40
    git_values = {
        ("remote", "get-url", "origin"): (
            "git@github.com:emmanuelnavaromero02-commits/CONSOLA-V1.git"
        ),
        ("cat-file", "-t", f"refs/tags/{tag}"): "tag",
        ("rev-parse", f"refs/tags/{tag}"): tag_object,
        ("rev-parse", f"refs/tags/{tag}^{{commit}}"): tag_commit,
        ("cat-file", "tag", f"refs/tags/{tag}"): (
            f"object {tag_commit}\ntype commit\ntag {tag}\n"
            "tagger Release <release@example.com> 0 +0000\n\nlegacy\n"
        ),
    }
    monkeypatch.setattr(module, "git", lambda *args, **_kwargs: git_values[args])

    def fake_run(command, **_kwargs):
        if command[:4] == ["git", "fetch", "origin", "--tags"]:
            return subprocess.CompletedProcess(command, 0, "", "")
        if command[:3] == ["git", "ls-remote", "--tags"]:
            return subprocess.CompletedProcess(
                command,
                0,
                (
                    f"{tag_object}\trefs/tags/{tag}\n"
                    f"{tag_commit}\trefs/tags/{tag}^{{}}\n"
                ),
                "",
            )
        raise AssertionError(command)

    monkeypatch.setattr(module, "run", fake_run)
    validated = []
    monkeypatch.setattr(
        module,
        "validate_current_live_source",
        lambda source_ref, version: validated.append((source_ref, version)) or version,
    )

    identity = module.validate_legacy_rollback_identity(
        target_tag=tag,
        runtime_source_ref=runtime_ref,
        helper_ref=SOURCE_SHA,
        purpose="rollback",
    )

    assert identity == module.LegacyRollbackIdentity(
        version="1.45.205-beta",
        tag_object_sha=tag_object,
        tag_commit=tag_commit,
        runtime_source_ref=runtime_ref,
    )
    assert validated == [
        (tag_commit, "1.45.205-beta"),
        (runtime_ref, "1.45.205-beta"),
    ]

    def remote_drift(command, **_kwargs):
        if command[:4] == ["git", "fetch", "origin", "--tags"]:
            return subprocess.CompletedProcess(command, 0, "", "")
        if command[:3] == ["git", "ls-remote", "--tags"]:
            return subprocess.CompletedProcess(
                command,
                0,
                (
                    f"{'e' * 40}\trefs/tags/{tag}\n"
                    f"{tag_commit}\trefs/tags/{tag}^{{}}\n"
                ),
                "",
            )
        raise AssertionError(command)

    monkeypatch.setattr(module, "run", remote_drift)
    with pytest.raises(ValueError, match="remote legacy rollback tag differs"):
        module.validate_legacy_rollback_identity(
            target_tag=tag,
            runtime_source_ref=runtime_ref,
            helper_ref=SOURCE_SHA,
            purpose="rollback",
        )

    monkeypatch.setattr(module, "run", fake_run)

    def reject_version(source_ref: str, version: str) -> str:
        if source_ref == tag_commit:
            raise ValueError("current live VERSION differs from operator contract")
        return version

    monkeypatch.setattr(module, "validate_current_live_source", reject_version)
    with pytest.raises(ValueError, match="VERSION differs"):
        module.validate_legacy_rollback_identity(
            target_tag=tag,
            runtime_source_ref=runtime_ref,
            helper_ref=SOURCE_SHA,
            purpose="rollback",
        )
