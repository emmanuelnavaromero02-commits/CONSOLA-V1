from __future__ import annotations

import argparse
import base64
import hashlib
import importlib.util
import io
import json
import os
import re
import stat
import subprocess
import tarfile
from pathlib import Path
from typing import Any

import pytest

REPO = Path(__file__).resolve().parents[1]
SCRIPT = REPO / "scripts/gcp/generate_release_authority.py"
SPEC = importlib.util.spec_from_file_location("gcp_release_authority", SCRIPT)
assert SPEC is not None and SPEC.loader is not None
authority = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(authority)
SHA = "a" * 40
CONTROLLER = "b" * 40
TREE = "f" * 40


def _json_bytes(value: object) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":")).encode()


def _workflow(name: str, run_id: int) -> dict[str, object]:
    path, _gate = authority.WORKFLOWS[name]
    return {
        "conclusion": "success",
        "event": "push",
        "head_branch": "main",
        "head_repository": authority.REPOSITORY,
        "head_sha": SHA,
        "html_url": f"https://github.com/{authority.REPOSITORY}/actions/runs/{run_id}",
        "id": run_id,
        "name": name,
        "path": path,
        "run_attempt": 1,
        "status": "completed",
        "workflow_id": run_id + 1000,
    }


def _job(name: str, run_id: int, job_id: int) -> dict[str, object]:
    _path, gate = authority.WORKFLOWS[name]
    return {
        "conclusion": "success",
        "head_sha": SHA,
        "html_url": (
            f"https://github.com/{authority.REPOSITORY}/actions/runs/"
            f"{run_id}/job/{job_id}"
        ),
        "id": job_id,
        "name": gate,
        "run_attempt": 1,
        "status": "completed",
    }


def _github_responses() -> dict[str, object]:
    workflows = [
        _workflow(name, 100 + index)
        for index, name in enumerate(authority.REQUIRED_CHECKS)
    ]
    responses: dict[str, object] = {
        f"repos/{authority.REPOSITORY}/git/ref/heads/main": {
            "object": {
                "sha": SHA,
                "type": "commit",
                "url": f"https://api.github.com/repos/{authority.REPOSITORY}/git/commits/{SHA}",
            },
            "ref": "refs/heads/main",
        },
        f"repos/{authority.REPOSITORY}/commits/{SHA}": {
            "html_url": f"https://github.com/{authority.REPOSITORY}/commit/{SHA}",
            "sha": SHA,
        },
        f"repos/{authority.REPOSITORY}/actions/runs?head_sha={SHA}&per_page=100": {
            "total_count": len(workflows),
            "workflow_runs": workflows,
        },
    }
    for index, name in enumerate(authority.REQUIRED_CHECKS):
        run_id = 100 + index
        responses[
            f"repos/{authority.REPOSITORY}/actions/runs/{run_id}/jobs?per_page=100"
        ] = {"jobs": [_job(name, run_id, 200 + index)], "total_count": 1}
    return responses


def _install_github_run(
    monkeypatch: pytest.MonkeyPatch,
    responses: dict[str, object | bytes],
) -> list[list[str]]:
    commands: list[list[str]] = []

    def fake_run(
        command: list[str],
        *,
        cwd: Path = REPO,
        env: dict[str, str],
        timeout: int = 60,
        maximum: int = authority.MAX_COMMAND,
    ) -> bytes:
        del cwd, timeout, maximum
        commands.append(command)
        assert command[1:6] == ["api", "--hostname", "github.com", "--method", "GET"]
        assert command[7] == "--jq"
        assert env == {
            "GH_CONFIG_DIR": str(GH_CONFIG),
            "HOME": str(GH_CONFIG.parent.parent),
            "LANG": "C.UTF-8",
            "LC_ALL": "C.UTF-8",
            "NO_COLOR": "1",
            "PATH": "/usr/bin:/bin",
        }
        value = responses[command[6]]
        return value if isinstance(value, bytes) else _json_bytes(value)

    monkeypatch.setattr(authority, "_run", fake_run)
    return commands


@pytest.fixture
def config_dirs(tmp_path: Path) -> tuple[Path, Path]:
    config = tmp_path / ".config"
    config.mkdir(mode=0o700)
    gh = config / "gh"
    gcloud = tmp_path / "gcloud"
    gh.mkdir(mode=0o700)
    gcloud.mkdir(mode=0o700)
    (gh / "config.yml").write_text("version: 1\nhttp_unix_socket:\n", encoding="utf-8")
    (gh / "hosts.yml").write_text("github.com:\n", encoding="utf-8")
    (gh / "config.yml").chmod(0o600)
    (gh / "hosts.yml").chmod(0o600)
    return gh, gcloud


GH_CONFIG = Path("/placeholder")


def test_strict_json_rejects_duplicate_keys() -> None:
    with pytest.raises(authority.AuthorityError, match="duplicate JSON key"):
        authority._json(b'{"sha":"good","sha":"attacker"}', "evidence")


def test_bounded_command_never_echoes_secret_stderr(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    result = subprocess.CompletedProcess(
        ["tool"], 9, stdout=b"", stderr=b"sensitive-stderr-marker"
    )
    monkeypatch.setattr(authority.subprocess, "run", lambda *args, **kwargs: result)
    with pytest.raises(authority.AuthorityError) as caught:
        authority._run(["tool"], env={})
    assert "sensitive-stderr-marker" not in str(caught.value)


def test_tool_rejects_an_operator_owned_substitute(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    tool = tmp_path / "gh"
    tool.write_bytes(b"#!/bin/sh\nexit 0\n")
    tool.chmod(0o700)
    monkeypatch.setitem(authority.TOOL_HASHES, "GitHub CLI", "0" * 64)
    with pytest.raises(authority.AuthorityError, match="not the reviewed executable"):
        authority._tool(tool, "GitHub CLI")


def test_package_lock_is_a_closed_committed_blob(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    raw = (REPO / authority.PACKAGE_LOCK).read_bytes()
    monkeypatch.setattr(authority, "_git_blob", lambda *args, **kwargs: (raw, "c" * 40))
    image, packages, evidence = authority._package_lock(CONTROLLER)
    assert image.endswith("v20260702")
    assert set(packages) == set(authority.PACKAGE_NAMES)
    assert evidence == {
        "content_sha256": hashlib.sha256(raw).hexdigest(),
        "git_blob_id": "c" * 40,
        "path": authority.PACKAGE_LOCK,
        "schema_version": 1,
    }


@pytest.mark.parametrize(
    "mutation",
    [
        lambda value: value.update({"attacker": True}),
        lambda value: value["packages"].pop("curl"),
        lambda value: value["packages"].update({"curl": "latest"}),
        lambda value: value.update({"boot_image": "ubuntu-2204-lts"}),
        lambda value: value.update({"schema_version": True}),
    ],
)
def test_package_lock_rejects_open_or_mutable_inputs(
    monkeypatch: pytest.MonkeyPatch,
    mutation: Any,
) -> None:
    value = json.loads((REPO / authority.PACKAGE_LOCK).read_text())
    mutation(value)
    monkeypatch.setattr(
        authority,
        "_git_blob",
        lambda *args, **kwargs: (_json_bytes(value), "c" * 40),
    )
    with pytest.raises(authority.AuthorityError):
        authority._package_lock(CONTROLLER)


def test_github_proves_exact_main_and_four_attempt_one_gates(
    monkeypatch: pytest.MonkeyPatch,
    config_dirs: tuple[Path, Path],
) -> None:
    global GH_CONFIG
    GH_CONFIG = config_dirs[0]
    commands = _install_github_run(monkeypatch, _github_responses())
    result = authority._github(Path("/trusted/gh"), GH_CONFIG, SHA)
    assert result["main_sha"] == SHA
    assert [item["name"] for item in result["checks"]] == list(
        authority.REQUIRED_CHECKS
    )
    assert all(item["run_attempt"] == 1 for item in result["checks"])
    assert len(commands) == 8
    assert all("token" not in " ".join(command).lower() for command in commands)


@pytest.mark.parametrize(
    ("target", "field", "value", "message"),
    [
        ("ref", "sha", "d" * 40, "main ref differs"),
        ("workflow", "head_sha", "d" * 40, "workflow is not exact"),
        ("workflow", "run_attempt", 2, "workflow is not exact"),
        ("workflow", "run_attempt", True, "workflow is not exact"),
        ("workflow", "id", True, "workflow is not exact"),
        ("workflow", "id", 0, "workflow is not exact"),
        ("workflow", "workflow_id", True, "workflow is not exact"),
        ("workflow", "conclusion", "failure", "workflow is not exact"),
        ("workflow", "event", "pull_request", "workflow is not exact"),
        ("workflow", "head_repository", "attacker/fork", "workflow is not exact"),
        ("job", "conclusion", "failure", "gate job is not exact"),
        ("job", "run_attempt", 2, "gate job is not exact"),
        ("job", "run_attempt", True, "gate job is not exact"),
        ("job", "id", True, "gate job is not exact"),
        ("job", "id", 0, "gate job is not exact"),
    ],
)
def test_github_rejects_malicious_or_stale_approval_evidence(
    monkeypatch: pytest.MonkeyPatch,
    config_dirs: tuple[Path, Path],
    target: str,
    field: str,
    value: object,
    message: str,
) -> None:
    global GH_CONFIG
    GH_CONFIG = config_dirs[0]
    responses = _github_responses()
    if target == "ref":
        responses[f"repos/{authority.REPOSITORY}/git/ref/heads/main"]["object"][
            field
        ] = value
    elif target == "workflow":
        actions = responses[
            f"repos/{authority.REPOSITORY}/actions/runs?head_sha={SHA}&per_page=100"
        ]
        actions["workflow_runs"][0][field] = value
    else:
        jobs = responses[
            f"repos/{authority.REPOSITORY}/actions/runs/100/jobs?per_page=100"
        ]
        jobs["jobs"][0][field] = value
    _install_github_run(monkeypatch, responses)
    with pytest.raises(authority.AuthorityError, match=message):
        authority._github(Path("/trusted/gh"), GH_CONFIG, SHA)


def test_github_rejects_duplicate_json_from_api(
    monkeypatch: pytest.MonkeyPatch,
    config_dirs: tuple[Path, Path],
) -> None:
    global GH_CONFIG
    GH_CONFIG = config_dirs[0]
    responses = _github_responses()
    responses[f"repos/{authority.REPOSITORY}/git/ref/heads/main"] = (
        b'{"ref":"refs/heads/main","ref":"attacker","object":{}}'
    )
    _install_github_run(monkeypatch, responses)
    with pytest.raises(authority.AuthorityError, match="duplicate JSON key"):
        authority._github(Path("/trusted/gh"), GH_CONFIG, SHA)


def _object_metadata(raw: bytes) -> dict[str, object]:
    return {
        "bucket": authority.SOURCE_BUCKET,
        "contentType": "application/gzip",
        "crc32c": "AAAAAA==",
        "etag": "etag",
        "generation": "123456",
        "id": "object-id",
        "kind": "storage#object",
        "md5Hash": base64.b64encode(
            hashlib.md5(raw, usedforsecurity=False).digest()
        ).decode(),
        "mediaLink": "https://storage.googleapis.com/download",
        "metadata": {"sha256": hashlib.sha256(raw).hexdigest()},
        "metageneration": "1",
        "name": f"deploy-artifacts/{SHA}/repo.tar.gz",
        "selfLink": "https://storage.googleapis.com/storage/v1/object",
        "size": str(len(raw)),
        "storageClass": "STANDARD",
        "timeCreated": "2026-08-12T00:00:00Z",
        "timeStorageClassUpdated": "2026-08-12T00:00:00Z",
        "updated": "2026-08-12T00:00:00Z",
    }


def test_archive_tree_hash_reconstructs_exact_git_modes_paths_and_bytes(
    tmp_path: Path,
) -> None:
    archive = tmp_path / "repo.tar.gz"
    payload = b"reviewed\n"
    with tarfile.open(
        archive,
        "w:gz",
        format=tarfile.PAX_FORMAT,
        pax_headers={"comment": SHA},
    ) as bundle:
        member = tarfile.TarInfo("reviewed.txt")
        member.mode = 0o664
        member.size = len(payload)
        bundle.addfile(member, io.BytesIO(payload))
    blob = authority._git_object_sha(b"blob", payload)
    expected = authority._git_object_sha(b"tree", b"100644 reviewed.txt\0" + blob).hex()
    assert authority._archive_tree_sha(archive, source_ref=SHA) == expected


def test_archive_tree_hash_rejects_spoofed_commit_marker_and_duplicate_path(
    tmp_path: Path,
) -> None:
    archive = tmp_path / "repo.tar.gz"
    with tarfile.open(
        archive,
        "w:gz",
        format=tarfile.PAX_FORMAT,
        pax_headers={"comment": "9" * 40},
    ) as bundle:
        for payload in (b"first", b"second"):
            member = tarfile.TarInfo("same.txt")
            member.mode = 0o664
            member.size = len(payload)
            bundle.addfile(member, io.BytesIO(payload))
    with pytest.raises(authority.AuthorityError, match="commit marker"):
        authority._archive_tree_sha(archive, source_ref=SHA)

    with tarfile.open(
        archive,
        "w:gz",
        format=tarfile.PAX_FORMAT,
        pax_headers={"comment": SHA},
    ) as bundle:
        for payload in (b"first", b"second"):
            member = tarfile.TarInfo("same.txt")
            member.mode = 0o664
            member.size = len(payload)
            bundle.addfile(member, io.BytesIO(payload))
    with pytest.raises(authority.AuthorityError, match="duplicate paths"):
        authority._archive_tree_sha(archive, source_ref=SHA)

    with tarfile.open(
        archive,
        "w:gz",
        format=tarfile.PAX_FORMAT,
        pax_headers={"comment": SHA},
    ) as bundle:
        for name in ("parent", "parent/child"):
            member = tarfile.TarInfo(name)
            member.mode = 0o664
            member.size = 1
            bundle.addfile(member, io.BytesIO(b"x"))
    with pytest.raises(authority.AuthorityError, match="parent file"):
        authority._archive_tree_sha(archive, source_ref=SHA)


def test_archive_rejects_concatenated_and_trailing_gzip_members(tmp_path: Path) -> None:
    first = tmp_path / "first.tar.gz"
    second = tmp_path / "second.tar.gz"
    for path, name in ((first, "one"), (second, "two")):
        with tarfile.open(
            path,
            "w:gz",
            format=tarfile.PAX_FORMAT,
            pax_headers={"comment": SHA},
        ) as bundle:
            payload = name.encode()
            member = tarfile.TarInfo(name)
            member.mode = 0o664
            member.size = len(payload)
            bundle.addfile(member, io.BytesIO(payload))
    concatenated = tmp_path / "concatenated.tar.gz"
    concatenated.write_bytes(first.read_bytes() + second.read_bytes())
    with pytest.raises(authority.AuthorityError, match="concatenated or trailing"):
        authority._archive_tree_sha(concatenated, source_ref=SHA)
    trailing = tmp_path / "trailing.tar.gz"
    trailing.write_bytes(first.read_bytes() + b"unreviewed-trailer")
    with pytest.raises(authority.AuthorityError, match="concatenated or trailing"):
        authority._archive_tree_sha(trailing, source_ref=SHA)


def test_archive_parser_consumes_the_same_held_inode_that_was_hashed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    archive = tmp_path / "repo.tar.gz"
    with tarfile.open(
        archive,
        "w:gz",
        format=tarfile.PAX_FORMAT,
        pax_headers={"comment": SHA},
    ) as bundle:
        payload = b"held"
        member = tarfile.TarInfo("held")
        member.mode = 0o664
        member.size = len(payload)
        bundle.addfile(member, io.BytesIO(payload))
    descriptor = os.open(archive, os.O_RDONLY)
    try:
        info = os.fstat(descriptor)
        authority._hash_descriptor(descriptor, info, authority.MAX_ARCHIVE)
        real_open = authority.os.open

        def guarded_open(path: object, *args: object, **kwargs: object) -> int:
            if path == archive:
                pytest.fail("archive pathname was reopened")
            return real_open(path, *args, **kwargs)

        monkeypatch.setattr(authority.os, "open", guarded_open)
        authority._archive_tree_sha(descriptor, source_ref=SHA)
    finally:
        os.close(descriptor)


def test_private_config_builders_reject_transport_and_impersonation_overrides(
    tmp_path: Path,
) -> None:
    source = tmp_path / "gcloud"
    (source / "configurations").mkdir(parents=True)
    (source / "active_config").write_text("default\n")
    (source / "credentials.db").write_bytes(b"credentials")
    config = source / "configurations/config_default"
    config.write_text(
        f"[core]\naccount = {authority.OPERATOR_ACCOUNT}\n"
        f"project = {authority.PROJECT}\n"
        "impersonate_service_account = attacker@example.com\n"
    )
    with pytest.raises(authority.AuthorityError, match="override"):
        authority._private_gcloud_config(source, tmp_path / "private-gcloud")

    gh = tmp_path / ".config/gh"
    gh.mkdir(parents=True)
    (gh / "hosts.yml").write_text(
        "github.com:\n"
        "    git_protocol: https\n"
        "    users:\n"
        f"        {authority.GITHUB_ACCOUNT}:\n"
        f"    user: {authority.GITHUB_ACCOUNT}\n"
        "    http_unix_socket: /tmp/attacker.sock\n"
    )
    with pytest.raises(authority.AuthorityError, match="transport override"):
        authority._private_gh_config(gh, tmp_path / "private-gh")


def test_gcloud_sdk_digest_is_a_closed_reviewed_tree() -> None:
    assert re.fullmatch(r"[0-9a-f]{64}", authority.GCLOUD_SDK_TREE_SHA256)
    assert authority.GCLOUD_SDK_TREE_SHA256 != "0" * 64


def test_runtime_snapshot_rejects_member_swap_between_stat_and_open(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = tmp_path / "source"
    source.mkdir(mode=0o700)
    member = source / "member"
    replacement = source / "replacement"
    member.write_bytes(b"reviewed")
    replacement.write_bytes(b"attacker")
    real_open = authority.os.open
    swapped = False

    def racing_open(path: object, flags: int, *args: object, **kwargs: object) -> int:
        nonlocal swapped
        if path == "member" and kwargs.get("dir_fd") is not None and not swapped:
            swapped = True
            os.replace(member, source / "original")
            os.replace(replacement, member)
        return real_open(path, flags, *args, **kwargs)

    monkeypatch.setattr(authority.os, "open", racing_open)
    with pytest.raises(authority.AuthorityError, match="pathname was substituted"):
        authority._runtime_tree_snapshot(source, tmp_path / "private")


def test_python_canary_rejects_one_non_os_loaded_image(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    framework = tmp_path / "Library/Frameworks/Python.framework"
    version = framework / "Versions/3.14"
    python = version / "bin/python3.14"
    python.parent.mkdir(parents=True)
    python.write_bytes(b"python")
    python.chmod(0o500)
    origins = {}
    for name in ("_sqlite3", "_ssl", "sqlite3", "ssl", "sysconfig"):
        origin = version / f"lib/python3.14/{name}.py"
        origin.parent.mkdir(parents=True, exist_ok=True)
        origin.write_bytes(b"module")
        origins[name] = str(origin)
    images = []
    for relative in ("Python", "lib/libcrypto.3.dylib", "lib/libssl.3.dylib"):
        loaded = version / relative
        loaded.parent.mkdir(parents=True, exist_ok=True)
        loaded.write_bytes(b"native")
        images.append(str(loaded))
    images.append("/opt/homebrew/lib/libattacker.dylib")
    value = {
        "base_prefix": str(version),
        "executable": str(python),
        "images": images,
        "origins": origins,
        "openssl": "OpenSSL 3.0.20 7 Apr 2026",
        "path": [str(version / "lib/python3.14")],
        "prefix": str(version),
        "version": [3, 14, 5],
    }
    monkeypatch.setattr(authority, "_run", lambda *args, **kwargs: _json_bytes(value))
    with pytest.raises(authority.AuthorityError, match="origin differs"):
        authority._private_python_canary(python, framework)


def test_gcs_object_is_downloaded_by_exact_generation_and_read_back(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    archive = b"immutable-source-archive"
    metadata = _object_metadata(archive)
    calls = 0

    def fake_gcloud(*args: Any, **kwargs: Any) -> tuple[object, str]:
        nonlocal calls
        calls += 1
        return metadata, str(calls) * 64

    copy_commands: list[list[str]] = []

    def fake_run(command: list[str], **kwargs: Any) -> bytes:
        del kwargs
        copy_commands.append(command)
        target = Path(command[-3])
        target.write_bytes(archive)
        return b""

    monkeypatch.setattr(authority, "_gcloud", fake_gcloud)
    monkeypatch.setattr(authority, "_run", fake_run)
    monkeypatch.setattr(
        authority, "_archive_tree_sha", lambda _path, *, source_ref: TREE
    )
    result, generation, size = authority._source_object(
        Path("/trusted/gcloud"), {}, SHA, source_tree_sha=TREE
    )
    assert generation == "123456"
    assert size == len(archive)
    assert result["sha256"] == hashlib.sha256(archive).hexdigest()
    assert result["git_tree_sha"] == TREE
    assert result["custom_metadata_sha256"] == hashlib.sha256(archive).hexdigest()
    assert result["sha256_source"] == "gcs-custom-metadata+generation-bound-download"
    assert any(part.endswith(f"#{generation}") for part in copy_commands[0])
    assert calls == 2


def test_gcs_object_without_custom_sha_uses_generation_bound_download(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    archive = b"immutable-generation-bound-archive"
    metadata = _object_metadata(archive)
    del metadata["metadata"]
    monkeypatch.setattr(
        authority, "_gcloud", lambda *args, **kwargs: (metadata, "1" * 64)
    )

    def fake_run(command: list[str], **kwargs: Any) -> bytes:
        del kwargs
        Path(command[-3]).write_bytes(archive)
        return b""

    monkeypatch.setattr(authority, "_run", fake_run)
    monkeypatch.setattr(
        authority, "_archive_tree_sha", lambda _path, *, source_ref: TREE
    )
    result, generation, size = authority._source_object(
        Path("/trusted/gcloud"), {}, SHA, source_tree_sha=TREE
    )
    assert generation == "123456"
    assert size == len(archive)
    assert result["custom_metadata_sha256"] is None
    assert result["sha256"] == hashlib.sha256(archive).hexdigest()
    assert result["git_tree_sha"] == TREE
    assert result["sha256_source"] == "generation-bound-download"


def test_gcs_custom_sha_mismatch_fails(monkeypatch: pytest.MonkeyPatch) -> None:
    archive = b"immutable-generation-bound-archive"
    metadata = _object_metadata(archive)
    metadata["metadata"] = {"sha256": "f" * 64}
    monkeypatch.setattr(
        authority, "_gcloud", lambda *args, **kwargs: (metadata, "1" * 64)
    )

    def fake_run(command: list[str], **kwargs: Any) -> bytes:
        del kwargs
        Path(command[-3]).write_bytes(archive)
        return b""

    monkeypatch.setattr(authority, "_run", fake_run)
    with pytest.raises(authority.AuthorityError, match="download differs"):
        authority._source_object(Path("/trusted/gcloud"), {}, SHA, source_tree_sha=TREE)


def test_gcs_md5_mismatch_fails(monkeypatch: pytest.MonkeyPatch) -> None:
    archive = b"immutable-generation-bound-archive"
    metadata = _object_metadata(archive)
    metadata["md5Hash"] = base64.b64encode(bytes(16)).decode()
    del metadata["metadata"]
    monkeypatch.setattr(
        authority, "_gcloud", lambda *args, **kwargs: (metadata, "1" * 64)
    )

    def fake_run(command: list[str], **kwargs: Any) -> bytes:
        del kwargs
        Path(command[-3]).write_bytes(archive)
        return b""

    monkeypatch.setattr(authority, "_run", fake_run)
    with pytest.raises(authority.AuthorityError, match="download differs"):
        authority._source_object(Path("/trusted/gcloud"), {}, SHA, source_tree_sha=TREE)


def test_gcs_rejects_unreviewed_custom_metadata(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    archive = b"archive"
    metadata = _object_metadata(archive)
    metadata["metadata"]["attacker"] = "value"
    monkeypatch.setattr(
        authority, "_gcloud", lambda *args, **kwargs: (metadata, "1" * 64)
    )
    with pytest.raises(authority.AuthorityError, match="custom metadata schema"):
        authority._source_object(Path("/trusted/gcloud"), {}, SHA, source_tree_sha=TREE)


def test_gcs_mutation_between_download_and_readback_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    archive = b"archive"
    first = _object_metadata(archive)
    second = dict(first) | {"generation": "123457"}
    answers = iter(((first, "1" * 64), (second, "2" * 64)))
    monkeypatch.setattr(authority, "_gcloud", lambda *args, **kwargs: next(answers))

    def fake_run(command: list[str], **kwargs: Any) -> bytes:
        del kwargs
        Path(command[-3]).write_bytes(archive)
        return b""

    monkeypatch.setattr(authority, "_run", fake_run)
    monkeypatch.setattr(
        authority, "_archive_tree_sha", lambda _path, *, source_ref: TREE
    )
    with pytest.raises(authority.AuthorityError, match="mutated"):
        authority._source_object(Path("/trusted/gcloud"), {}, SHA, source_tree_sha=TREE)


def test_gcs_download_hash_mismatch_fails(monkeypatch: pytest.MonkeyPatch) -> None:
    metadata = _object_metadata(b"expected")
    monkeypatch.setattr(
        authority, "_gcloud", lambda *args, **kwargs: (metadata, "1" * 64)
    )

    def fake_run(command: list[str], **kwargs: Any) -> bytes:
        del kwargs
        Path(command[-3]).write_bytes(b"attacker")
        return b""

    monkeypatch.setattr(authority, "_run", fake_run)
    with pytest.raises(authority.AuthorityError, match="download differs"):
        authority._source_object(Path("/trusted/gcloud"), {}, SHA, source_tree_sha=TREE)


def test_secret_versions_select_highest_enabled_without_payload_access(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    commands: list[list[str]] = []

    def fake_gcloud(
        gcloud: Path,
        env: dict[str, str],
        args: list[str],
        label: str,
        **kwargs: Any,
    ) -> tuple[object, str]:
        del gcloud, env, label, kwargs
        commands.append(args)
        secret = args[3]
        prefix = f"projects/894064513501/secrets/{secret}/versions/"
        return (
            [
                {
                    "createTime": "2026-08-12T00:00:00Z",
                    "etag": "etag-2",
                    "name": f"{prefix}2",
                    "state": "ENABLED",
                },
                {
                    "createTime": "2026-08-11T00:00:00Z",
                    "etag": "etag-10",
                    "name": f"{prefix}10",
                    "state": "ENABLED",
                },
            ],
            "1" * 64,
        )

    monkeypatch.setattr(authority, "_gcloud", fake_gcloud)
    selected, evidence = authority._secret_versions(Path("/trusted/gcloud"), {})
    assert set(selected) == set(authority.SECRET_NAMES)
    assert set(selected.values()) == {"10"}
    assert all(item["version"] == "10" for item in evidence)
    flattened = " ".join(part for command in commands for part in command)
    assert " access " not in f" {flattened} "
    assert "payload" not in flattened.lower()


def test_secret_version_open_schema_fails(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_gcloud(*args: Any, **kwargs: Any) -> tuple[object, str]:
        return (
            [
                {
                    "createTime": "now",
                    "etag": "etag",
                    "name": (
                        "projects/894064513501/secrets/omega-staging-"
                        "control_room_evidence_signing_key/versions/1"
                    ),
                    "payload": "forbidden",
                    "state": "ENABLED",
                }
            ],
            "1" * 64,
        )

    monkeypatch.setattr(authority, "_gcloud", fake_gcloud)
    with pytest.raises(authority.AuthorityError, match="schema differs"):
        authority._secret_versions(Path("/trusted/gcloud"), {})


def _live_instance(
    *, writer: bool = True, schema_version: object = 1
) -> tuple[dict[str, object], dict[str, object], bytes]:
    contract = {
        "canonical_writer": writer,
        "controller_ref": "c" * 40,
        "enable_airflow_scheduler": writer,
        "environment": "staging",
        "project_id": authority.PROJECT,
        "schema_version": schema_version,
        "source": {"ref": "d" * 40},
    }
    encoded = base64.b64encode(_json_bytes(contract)).decode()
    startup = f"#!/bin/bash\nSTARTUP_CONFIG_BASE64='{encoded}'\n".encode()
    instance = {
        "disks": [
            {
                "boot": True,
                "source": (
                    "https://www.googleapis.com/compute/v1/projects/"
                    f"{authority.PROJECT}/zones/{authority.ZONE}/disks/boot-disk"
                ),
            }
        ],
        "id": "123456789",
        "labels": {},
        "metadata": {"items": [{"key": "startup-script", "value": startup.decode()}]},
        "name": authority.INSTANCE,
        "serviceAccounts": [
            {
                "email": authority.SERVICE_ACCOUNT,
                "scopes": ["https://www.googleapis.com/auth/cloud-platform"],
            }
        ],
        "status": "RUNNING",
        "tags": {},
        "zone": f"projects/{authority.PROJECT}/zones/{authority.ZONE}",
    }
    disk = {
        "sourceImage": (
            "https://www.googleapis.com/compute/v1/"
            "projects/ubuntu-os-cloud/global/images/ubuntu-2204-jammy-v20260702"
        )
    }
    return instance, disk, startup


def test_live_vm_binds_startup_writer_scheduler_and_boot_image(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    instance, disk, startup = _live_instance()
    answers = iter(((instance, "1" * 64), (disk, "2" * 64)))
    monkeypatch.setattr(authority, "_gcloud", lambda *args, **kwargs: next(answers))
    result = authority._live_vm(
        Path("/trusted/gcloud"),
        {},
        "projects/ubuntu-os-cloud/global/images/ubuntu-2204-jammy-v20260702",
    )
    assert result["canonical_writer"] is True
    assert result["enable_airflow_scheduler"] is True
    assert result["old_startup_sha256"] == hashlib.sha256(startup).hexdigest()


def test_live_vm_rejects_non_writer_startup(monkeypatch: pytest.MonkeyPatch) -> None:
    instance, disk, _startup = _live_instance(writer=False)
    answers = iter(((instance, "1" * 64), (disk, "2" * 64)))
    monkeypatch.setattr(authority, "_gcloud", lambda *args, **kwargs: next(answers))
    with pytest.raises(authority.AuthorityError, match="unique writer/scheduler"):
        authority._live_vm(
            Path("/trusted/gcloud"),
            {},
            "projects/ubuntu-os-cloud/global/images/ubuntu-2204-jammy-v20260702",
        )


def test_live_vm_rejects_boolean_startup_schema(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    instance, disk, _startup = _live_instance(schema_version=True)
    answers = iter(((instance, "1" * 64), (disk, "2" * 64)))
    monkeypatch.setattr(authority, "_gcloud", lambda *args, **kwargs: next(answers))
    with pytest.raises(authority.AuthorityError, match="unique writer/scheduler"):
        authority._live_vm(
            Path("/trusted/gcloud"),
            {},
            "projects/ubuntu-os-cloud/global/images/ubuntu-2204-jammy-v20260702",
        )


def test_exclusive_writer_is_mode_0400_full_write_and_no_overwrite(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    path = tmp_path.resolve() / "release-authority.json"
    original_write = authority.os.write

    def partial_write(descriptor: int, raw: bytes) -> int:
        return original_write(descriptor, raw[: max(1, len(raw) // 3)])

    monkeypatch.setattr(authority.os, "write", partial_write)
    authority._write_exclusive(path, b"exact-authority-bytes")
    assert path.read_bytes() == b"exact-authority-bytes"
    assert stat.S_IMODE(path.stat().st_mode) == 0o400
    with pytest.raises(FileExistsError):
        authority._write_exclusive(path, b"replacement")
    assert path.read_bytes() == b"exact-authority-bytes"


def test_exclusive_writer_rejects_non_private_parent(tmp_path: Path) -> None:
    parent = tmp_path / "shared"
    parent.mkdir(mode=0o700)
    parent.chmod(0o777)
    path = parent / "release-authority.json"

    with pytest.raises(authority.AuthorityError, match="output parent"):
        authority._write_exclusive(path, b"exact-authority-bytes")

    assert not path.exists()


@pytest.mark.parametrize(
    ("changed", "message"),
    [
        ("live_vm", "live VM changed"),
        ("secrets", "secret versions changed"),
        ("github", "GitHub evidence changed"),
    ],
)
def test_build_repeats_every_mutable_authority_projection(
    monkeypatch: pytest.MonkeyPatch,
    changed: str,
    message: str,
) -> None:
    git = {
        "controller_ref": CONTROLLER,
        "main_ref": "refs/heads/main",
        "main_sha": SHA,
        "repository": authority.REPOSITORY,
        "source_tree_sha": "1" * 40,
    }
    github = {"main_sha": SHA}
    live = {"old_startup_sha256": "2" * 64}
    versions = ({name: "1" for name in authority.SECRET_NAMES}, [{"stable": True}])
    github_values = iter(
        (github, github | {"main_sha": "9" * 40})
        if changed == "github"
        else (github, github)
    )
    live_values = iter(
        (live, live | {"old_startup_sha256": "9" * 64})
        if changed == "live_vm"
        else (live, live)
    )
    secret_values = iter(
        (versions, (versions[0], [{"stable": False}]))
        if changed == "secrets"
        else (versions, versions)
    )
    monkeypatch.setattr(authority, "_clean_git", lambda *args: git)
    monkeypatch.setattr(
        authority, "_git_blob", lambda *args, **kwargs: (b"generator", "3" * 40)
    )
    monkeypatch.setattr(
        authority, "_read_regular", lambda *args, **kwargs: (b"generator", None)
    )
    monkeypatch.setattr(
        authority,
        "_package_lock",
        lambda *args, **kwargs: (
            "projects/ubuntu-os-cloud/global/images/ubuntu-2204-jammy-v20260702",
            {},
            {},
        ),
    )
    monkeypatch.setattr(
        authority,
        "_tool",
        lambda path, label: (path, {"label": label}),
    )
    monkeypatch.setattr(authority, "_github", lambda *args: next(github_values))
    monkeypatch.setattr(authority, "_gcloud_env", lambda *args, **kwargs: {})
    monkeypatch.setattr(
        authority,
        "_source_object",
        lambda *args, **kwargs: (
            {
                "name": f"deploy-artifacts/{SHA}/repo.tar.gz",
                "sha256": "4" * 64,
            },
            "1",
            1,
        ),
    )
    monkeypatch.setattr(authority, "_live_vm", lambda *args: next(live_values))
    monkeypatch.setattr(
        authority, "_secret_versions", lambda *args: next(secret_values)
    )
    monkeypatch.setattr(authority, "validate_authority_receipt", lambda value: value)
    args = argparse.Namespace(
        controller_ref=CONTROLLER,
        gcloud_config=Path("/config/gcloud"),
        gh_config=Path("/config/gh"),
        source_ref=SHA,
    )
    with pytest.raises(authority.AuthorityError, match=message):
        authority._build(
            args,
            runtime={
                "gh": Path("/trusted/gh"),
                "gh_config": args.gh_config,
                "gh_identity": {"label": "GitHub CLI"},
                "gcloud": Path("/trusted/gcloud.py"),
                "gcloud_config": args.gcloud_config,
                "gcloud_identity": {"label": "gcloud CLI"},
                "gcloud_sdk_identity": {"sha256": authority.GCLOUD_SDK_TREE_SHA256},
                "python": Path("/trusted/python"),
                "python_framework": Path("/trusted/Python.framework"),
                "python_identity": {"sha256": authority.PYTHON_RUNTIME_TREE_SHA256},
            },
        )


def _built_authority() -> dict[str, object]:
    lock = json.loads((REPO / authority.PACKAGE_LOCK).read_text())
    source_hash = "f" * 64
    secret_versions = {name: "1" for name in authority.SECRET_NAMES}
    checks = []
    for index, name in enumerate(authority.REQUIRED_CHECKS, start=1):
        workflow_path, gate = authority.WORKFLOWS[name]
        checks.append(
            {
                "conclusion": "success",
                "gate_job": gate,
                "gate_job_id": index,
                "head_sha": SHA,
                "name": name,
                "query_sha256": str(index) * 64,
                "run_attempt": 1,
                "status": "completed",
                "workflow_path": workflow_path,
                "workflow_run_id": 100 + index,
            }
        )
    value: dict[str, object] = {
        "approved_main_sha": SHA,
        "boot_image": lock["boot_image"],
        "canonical_writer": True,
        "certificate_manager_map_name": "sevenbs-production-map",
        "controller_ref": CONTROLLER,
        "enable_airflow_scheduler": True,
        "foundation_predecessor": None,
        "host_package_versions": lock["packages"],
        "lakehouse_bucket_name": "",
        "lakehouse_endpoint": "storage.googleapis.com",
        "old_live_startup_sha256": "e" * 64,
        "provenance": {
            "generator": {
                "content_sha256": "1" * 64,
                "git_blob_id": "2" * 40,
                "path": authority.GENERATOR,
            },
            "git": {
                "controller_ref": CONTROLLER,
                "main_ref": "refs/heads/main",
                "main_sha": SHA,
                "repository": authority.REPOSITORY,
                "source_tree_sha": "3" * 40,
            },
            "github": {
                "checks": checks,
                "commit_url": f"https://github.com/{authority.REPOSITORY}/commit/{SHA}",
                "main_ref": "refs/heads/main",
                "main_sha": SHA,
                "query_sha256": "4" * 64,
                "repository": authority.REPOSITORY,
            },
            "live_vm": {
                "boot_disk": "omega-staging-app",
                "boot_image": lock["boot_image"],
                "canonical_writer": True,
                "enable_airflow_scheduler": True,
                "instance_id": "123456789",
                "name": authority.INSTANCE,
                "old_startup_sha256": "e" * 64,
                "project": authority.PROJECT,
                "query_sha256": "5" * 64,
                "service_account": authority.SERVICE_ACCOUNT,
                "startup_contract_schema": "legacy-pre-foundation",
                "startup_controller_ref": None,
                "startup_source_ref": "6" * 40,
                "zone": authority.ZONE,
            },
            "package_lock": {
                "content_sha256": "7" * 64,
                "git_blob_id": "8" * 40,
                "path": authority.PACKAGE_LOCK,
                "schema_version": 1,
            },
            "secret_versions": [
                {
                    "create_time": "2026-08-12T00:00:00Z",
                    "etag": f'"etag-{index}"',
                    "name": name,
                    "query_sha256": format(index + 10, "x") * 64,
                    "state": "ENABLED",
                    "version": "1",
                }
                for index, name in enumerate(authority.SECRET_NAMES)
            ],
            "source_object": {
                "bucket": authority.SOURCE_BUCKET,
                "crc32c": base64.b64encode(bytes(4)).decode(),
                "custom_metadata_sha256": None,
                "generation": "1",
                "git_tree_sha": "3" * 40,
                "md5_hash": base64.b64encode(bytes(16)).decode(),
                "metageneration": "1",
                "name": f"deploy-artifacts/{SHA}/repo.tar.gz",
                "query_sha256": "d" * 64,
                "sha256": source_hash,
                "sha256_source": "generation-bound-download",
                "size": 1,
                "storage_class": "STANDARD",
                "updated": "2026-08-12T00:00:00Z",
            },
            "tools": {
                "gcloud": {
                    "content_sha256": authority.TOOL_HASHES["gcloud CLI"],
                    "path": authority.TOOL_PATHS["gcloud CLI"],
                    "size": 5962,
                },
                "gh": {
                    "content_sha256": authority.TOOL_HASHES["GitHub CLI"],
                    "path": authority.TOOL_PATHS["GitHub CLI"],
                    "size": 37448466,
                },
            },
        },
        "public_console_domain": "console.7businesssolutions.com",
        "public_workspace_domain": "workspace.7businesssolutions.com",
        "schema_version": 2,
        "secret_versions": secret_versions,
        "source_archive_sha256": source_hash,
        "source_bucket": authority.SOURCE_BUCKET,
        "source_generation": "1",
        "source_object": f"deploy-artifacts/{SHA}/repo.tar.gz",
        "source_sha": SHA,
        "source_size_bytes": 1,
    }
    value["evidence_chain_sha256"] = authority._sha(authority._canonical(value))
    return value


def _rechain(value: dict[str, object]) -> None:
    value.pop("evidence_chain_sha256", None)
    value["evidence_chain_sha256"] = authority._sha(authority._canonical(value))


def test_pure_receipt_validator_accepts_and_normalizes_closed_v2() -> None:
    value = _built_authority()
    normalized = authority.validate_authority_receipt(value)
    assert normalized == value
    assert normalized is not value
    normalized["source_generation"] = "999"
    assert value["source_generation"] == "1"


def test_system_python39_loads_cli_and_runs_pure_validator(tmp_path: Path) -> None:
    system_python = Path("/usr/bin/python3")
    assert system_python.is_file()
    environment = {"LANG": "C", "LC_ALL": "C", "PATH": "/usr/bin:/bin"}
    help_result = subprocess.run(
        [str(system_python), "-I", str(SCRIPT), "--help"],
        cwd=REPO,
        env=environment,
        stdin=subprocess.DEVNULL,
        capture_output=True,
        check=False,
        timeout=15,
    )
    assert help_result.returncode == 0, help_result.stderr.decode(errors="replace")
    assert b"{generate,verify}" in help_result.stdout

    receipt = tmp_path / "authority.json"
    receipt.write_bytes(authority._canonical(_built_authority()))
    validation_code = """
import importlib.util
import json
import sys

spec = importlib.util.spec_from_file_location("release_authority_under_test", sys.argv[1])
assert spec is not None and spec.loader is not None
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
with open(sys.argv[2], "rb") as source:
    value = json.load(source)
assert module.validate_authority_receipt(value) == value
"""
    validation_result = subprocess.run(
        [
            str(system_python),
            "-I",
            "-c",
            validation_code,
            str(SCRIPT),
            str(receipt),
        ],
        cwd=REPO,
        env=environment,
        stdin=subprocess.DEVNULL,
        capture_output=True,
        check=False,
        timeout=15,
    )
    assert validation_result.returncode == 0, validation_result.stderr.decode(
        errors="replace"
    )


@pytest.mark.parametrize(
    "path",
    [
        (),
        ("provenance",),
        ("provenance", "generator"),
        ("provenance", "git"),
        ("provenance", "github"),
        ("provenance", "github", "checks", 0),
        ("provenance", "live_vm"),
        ("provenance", "package_lock"),
        ("provenance", "secret_versions", 0),
        ("provenance", "source_object"),
        ("provenance", "tools"),
        ("provenance", "tools", "gcloud"),
        ("provenance", "tools", "gh"),
    ],
)
def test_every_receipt_object_surface_rejects_extra_keys(
    path: tuple[object, ...],
) -> None:
    value = _built_authority()
    node: Any = value
    for part in path:
        node = node[part]
    node["attacker"] = "unreviewed"
    _rechain(value)
    with pytest.raises(authority.AuthorityError, match="schema differs"):
        authority.validate_authority_receipt(value)


@pytest.mark.parametrize(
    ("path", "replacement"),
    [
        (("schema_version",), True),
        (("source_size_bytes",), True),
        (("provenance", "git", "main_sha"), "9" * 40),
        (("provenance", "github", "checks", 0, "gate_job_id"), True),
        (("provenance", "github", "checks", 0, "workflow_run_id"), True),
        (("provenance", "github", "checks", 0, "run_attempt"), 2),
        (("provenance", "github", "checks", 0, "run_attempt"), True),
        (("provenance", "github", "checks", 0, "head_sha"), "9" * 40),
        (("provenance", "live_vm", "canonical_writer"), False),
        (("provenance", "live_vm", "boot_image"), "mutable-family"),
        (("provenance", "package_lock", "schema_version"), 2),
        (("provenance", "package_lock", "schema_version"), True),
        (("provenance", "live_vm", "startup_contract_schema"), True),
        (("provenance", "secret_versions", 0, "state"), "DISABLED"),
        (("provenance", "source_object", "generation"), "2"),
        (("provenance", "source_object", "git_tree_sha"), "9" * 40),
        (("provenance", "source_object", "size"), True),
        (("provenance", "source_object", "sha256"), "9" * 64),
        (("provenance", "source_object", "storage_class"), []),
        (("provenance", "tools", "gh", "content_sha256"), "9" * 64),
        (("provenance", "tools", "gh", "size"), True),
    ],
)
def test_pure_receipt_validator_rejects_identity_mutations(
    path: tuple[object, ...], replacement: object
) -> None:
    value = _built_authority()
    node: Any = value
    for part in path[:-1]:
        node = node[part]
    node[path[-1]] = replacement
    _rechain(value)
    with pytest.raises(authority.AuthorityError):
        authority.validate_authority_receipt(value)


def test_pure_receipt_validator_accepts_matching_custom_gcs_sha() -> None:
    value = _built_authority()
    source = value["provenance"]["source_object"]
    source["custom_metadata_sha256"] = value["source_archive_sha256"]
    source["sha256_source"] = "gcs-custom-metadata+generation-bound-download"
    _rechain(value)
    assert authority.validate_authority_receipt(value) == value


def test_verify_rejects_chain_tampering_before_live_queries(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    path = tmp_path / "authority.json"
    value = _built_authority()
    value["source_generation"] = "2"
    path.write_bytes(authority._canonical(value))
    path.chmod(0o400)
    monkeypatch.setattr(
        authority,
        "_build",
        lambda args: pytest.fail("tampered authority reached live evidence"),
    )
    args = argparse.Namespace(authority=path)
    with pytest.raises(authority.AuthorityError, match="evidence chain differs"):
        authority.verify(args)


def test_verify_rejects_noncanonical_raw_whitespace_before_live_queries(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    path = tmp_path / "authority.json"
    value = _built_authority()
    path.write_bytes(b" " + authority._canonical(value))
    path.chmod(0o400)
    monkeypatch.setattr(
        authority,
        "_build",
        lambda args: pytest.fail("noncanonical authority reached live evidence"),
    )
    with pytest.raises(authority.AuthorityError, match="bytes are not canonical"):
        authority.verify(argparse.Namespace(authority=path))


def test_verify_rebuilds_and_exactly_compares_current_evidence(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    path = tmp_path / "authority.json"
    value = _built_authority()
    path.write_bytes(authority._canonical(value))
    path.chmod(0o400)
    monkeypatch.setattr(authority, "_build", lambda args: value)
    args = argparse.Namespace(authority=path)
    assert authority.verify(args) == value
    assert args.source_ref == SHA
    assert args.controller_ref == CONTROLLER


def test_verify_requires_private_mode(tmp_path: Path) -> None:
    path = tmp_path / "authority.json"
    path.write_bytes(authority._canonical(_built_authority()))
    path.chmod(0o600)
    with pytest.raises(authority.AuthorityError, match="mode 0400"):
        authority.verify(argparse.Namespace(authority=path))


def test_git_identity_requires_clean_exact_head_origin_main_and_ancestry(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    answers = {
        ("status", "--porcelain=v1", "--untracked-files=all"): b"",
        ("rev-parse", "--verify", "HEAD"): CONTROLLER.encode() + b"\n",
        ("rev-parse", "--verify", f"{SHA}^{{commit}}"): SHA.encode() + b"\n",
        ("rev-parse", "--verify", f"{CONTROLLER}^{{commit}}"): CONTROLLER.encode()
        + b"\n",
        ("rev-parse", "--verify", "refs/remotes/origin/main"): SHA.encode() + b"\n",
        ("merge-base", "--is-ancestor", CONTROLLER, SHA): b"",
        ("rev-parse", f"{SHA}^{{tree}}"): b"f" * 40 + b"\n",
    }
    monkeypatch.setattr(authority, "_git", lambda args, **kwargs: answers[tuple(args)])
    result = authority._clean_git(SHA, CONTROLLER)
    assert result["main_sha"] == SHA
    answers[("status", "--porcelain=v1", "--untracked-files=all")] = b" M file\n"
    with pytest.raises(authority.AuthorityError, match="not exactly clean"):
        authority._clean_git(SHA, CONTROLLER)


def test_committed_blob_rejects_path_traversal() -> None:
    with pytest.raises(authority.AuthorityError, match="path is invalid"):
        authority._git_blob(SHA, "../secret", maximum=100)


def test_cli_has_only_fixed_generate_and_verify_actions() -> None:
    parser = authority._parser()
    with pytest.raises(SystemExit):
        parser.parse_args(["arbitrary"])
    with pytest.raises(SystemExit):
        parser.parse_args(["generate", "--destroy"])
