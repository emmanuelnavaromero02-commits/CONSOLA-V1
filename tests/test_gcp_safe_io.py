from __future__ import annotations

import base64
import copy
import hashlib
import io
import json
import os
import stat
import sys
import tarfile
import urllib.parse
from pathlib import Path

import pytest

from scripts.gcp import safe_io


@pytest.fixture(autouse=True)
def _allow_non_root_test_calls(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OMEGA_SAFE_IO_TEST_NON_ROOT", "1")


def _tar(
    path: Path,
    entries: list[tuple[str, bytes | None, bytes, str | None]],
) -> Path:
    """Build a gzip tar without relying on host filesystem semantics.

    Each tuple is ``(name, tar_type, payload, linkname)``. ``tar_type=None``
    creates a regular file.
    """

    with tarfile.open(path, "w:gz") as archive:
        for name, tar_type, payload, linkname in entries:
            member = tarfile.TarInfo(name)
            if tar_type is not None:
                member.type = tar_type
            if linkname is not None:
                member.linkname = linkname
            if member.isreg():
                member.size = len(payload)
                archive.addfile(member, io.BytesIO(payload))
            else:
                archive.addfile(member)
    return path


def _extract(archive: Path, destination: Path) -> int:
    return safe_io.main(
        [
            "safe-extract",
            "--archive",
            str(archive),
            "--destination",
            str(destination),
        ]
    )


def test_safe_extract_accepts_only_regular_files_and_directories(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    archive = _tar(
        tmp_path / "valid.tar.gz",
        [
            ("release/", tarfile.DIRTYPE, b"", None),
            ("release/VERSION", None, b"1.45.207-beta\n", None),
            ("release/bin/run", None, b"#!/bin/sh\n", None),
        ],
    )
    destination = tmp_path / "output"

    assert _extract(archive, destination) == 0
    assert (destination / "release/VERSION").read_bytes() == b"1.45.207-beta\n"
    assert (destination / "release/bin/run").read_bytes() == b"#!/bin/sh\n"
    assert not any(path.is_symlink() for path in destination.rglob("*"))
    evidence = json.loads(capsys.readouterr().out)
    assert evidence == {
        "expanded_bytes": 24,
        "links": 0,
        "regular_files": 2,
        "special_files": 0,
        "status": "PASS",
    }


@pytest.mark.parametrize(
    "name",
    [
        "../escaped",
        "nested/../../escaped",
        "/absolute/escaped",
        "windows\\escaped",
        "double//component",
        "dot/./component",
        "control\x01name",
    ],
)
def test_safe_extract_rejects_traversal_before_writing_members(
    tmp_path: Path, name: str
) -> None:
    archive = _tar(tmp_path / "traversal.tar.gz", [(name, None, b"owned", None)])
    destination = tmp_path / "output"

    with pytest.raises(SystemExit, match="unsafe path"):
        _extract(archive, destination)

    assert not (tmp_path / "escaped").exists()
    assert destination.is_dir()
    assert list(destination.iterdir()) == []


@pytest.mark.parametrize(
    ("tar_type", "linkname"),
    [
        (tarfile.SYMTYPE, "../../escaped"),
        (tarfile.LNKTYPE, "target"),
        (tarfile.FIFOTYPE, None),
        (tarfile.CHRTYPE, None),
        (tarfile.BLKTYPE, None),
    ],
)
def test_safe_extract_rejects_links_and_special_files(
    tmp_path: Path, tar_type: bytes, linkname: str | None
) -> None:
    archive = _tar(
        tmp_path / "special.tar.gz",
        [("unsafe", tar_type, b"", linkname)],
    )
    destination = tmp_path / "output"

    with pytest.raises(SystemExit, match="links, devices, fifos, and special"):
        _extract(archive, destination)

    assert list(destination.iterdir()) == []


def test_safe_extract_rejects_duplicate_normalized_names(tmp_path: Path) -> None:
    archive = _tar(
        tmp_path / "duplicate.tar.gz",
        [("same", None, b"first", None), ("same", None, b"second", None)],
    )
    destination = tmp_path / "output"

    with pytest.raises(SystemExit, match="duplicate paths"):
        _extract(archive, destination)

    assert list(destination.iterdir()) == []


@pytest.mark.parametrize(
    "entries",
    [
        [("parent", None, b"file", None), ("parent/child", None, b"child", None)],
        [("parent/child", None, b"child", None), ("parent", None, b"file", None)],
    ],
)
def test_safe_extract_rejects_file_directory_collisions_before_writing(
    tmp_path: Path,
    entries: list[tuple[str, bytes | None, bytes, str | None]],
) -> None:
    archive = _tar(tmp_path / "collision.tar.gz", entries)
    destination = tmp_path / "output"

    with pytest.raises(SystemExit, match="file/directory paths collide"):
        _extract(archive, destination)

    assert list(destination.iterdir()) == []


def _tree_sha256(root: Path, *, read_only: bool = False) -> str:
    return safe_io._tree_digest(root, set(), require_read_only=read_only)


def test_tree_digest_binds_file_and_directory_modes(tmp_path: Path) -> None:
    root = tmp_path / "release"
    executable_dir = root / "bin"
    executable_dir.mkdir(parents=True)
    executable = executable_dir / "run"
    executable.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    root.chmod(0o700)
    executable_dir.chmod(0o700)
    executable.chmod(0o700)

    executable_digest = _tree_sha256(root)
    executable.chmod(0o600)
    assert _tree_sha256(root) != executable_digest

    executable_dir.chmod(0o500)
    assert _tree_sha256(root) != executable_digest


def test_tree_digest_rejects_symlink_permission_and_owner_drift(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "release"
    root.mkdir(mode=0o700)
    payload = root / "payload"
    payload.write_text("immutable\n", encoding="utf-8")
    payload.chmod(0o600)

    link = root / "escaped"
    link.symlink_to(tmp_path / "outside")
    with pytest.raises(SystemExit, match="link or special"):
        _tree_sha256(root)
    link.unlink()

    payload.chmod(0o620)
    with pytest.raises(SystemExit, match="group/world writable"):
        _tree_sha256(root)
    payload.chmod(0o600)

    current_euid = os.geteuid()
    monkeypatch.setattr(safe_io.os, "geteuid", lambda: current_euid + 1)
    with pytest.raises(SystemExit, match="owned by the invoking identity"):
        _tree_sha256(root)

    current_egid = os.getegid()
    monkeypatch.setattr(safe_io.os, "geteuid", lambda: current_euid)
    monkeypatch.setattr(safe_io.os, "getegid", lambda: current_egid + 1)
    with pytest.raises(SystemExit, match="owned by the invoking identity"):
        _tree_sha256(root)


def test_tree_digest_read_only_gate_rejects_chmod_drift(tmp_path: Path) -> None:
    root = tmp_path / "release"
    scripts = root / "scripts"
    scripts.mkdir(parents=True)
    helper = scripts / "helper.sh"
    helper.write_text("#!/bin/sh\n", encoding="utf-8")
    root.chmod(0o500)
    scripts.chmod(0o500)
    helper.chmod(0o500)

    digest = _tree_sha256(root, read_only=True)
    assert len(digest) == 64

    helper.chmod(0o700)
    with pytest.raises(SystemExit, match=r"immutable \(read-only\)"):
        _tree_sha256(root, read_only=True)


def _write_env(path: Path, content: str, mode: int = 0o600) -> Path:
    path.write_text(content, encoding="utf-8")
    path.chmod(mode)
    return path


@pytest.mark.parametrize(
    ("content", "message"),
    [
        (" export FOO=value\n", "canonical assignment"),
        ("export FOO=value\n", "invalid or duplicate key"),
        ("lower=value\n", "invalid or duplicate key"),
        ("FOO=one\nFOO=two\n", "invalid or duplicate key"),
        ("FOO=$(touch /tmp/nope)\n", "executable/interpolated syntax"),
        ("FOO=${UNTRUSTED}\n", "executable/interpolated syntax"),
        ("FOO=`id`\n", "executable/interpolated syntax"),
        ("# comments only\n", "no assignments"),
    ],
)
def test_env_validate_rejects_hostile_or_noncanonical_syntax(
    tmp_path: Path, content: str, message: str
) -> None:
    path = _write_env(tmp_path / ".env", content)

    with pytest.raises(SystemExit, match=message):
        safe_io.main(["env-validate", "--path", str(path)])


def test_env_validate_rejects_migration_control_plane_and_unsafe_file(
    tmp_path: Path,
) -> None:
    path = _write_env(tmp_path / ".env", "OMEGA_MIGRATION_OLD_REF=abc\n")
    with pytest.raises(SystemExit, match="forbidden control-plane prefix"):
        safe_io.main(
            [
                "env-validate",
                "--path",
                str(path),
                "--forbid-prefix",
                "OMEGA_MIGRATION_",
            ]
        )

    path.chmod(0o644)
    with pytest.raises(SystemExit, match="mode 0600"):
        safe_io.main(["env-validate", "--path", str(path)])

    path.chmod(0o600)
    link = tmp_path / "linked.env"
    link.symlink_to(path)
    with pytest.raises(SystemExit, match="regular non-symlink"):
        safe_io.main(["env-validate", "--path", str(link)])


def test_env_set_reads_value_only_from_stdin_and_writes_private_atomic_data(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    path = _write_env(tmp_path / ".env", "EXISTING='literal'\n")
    secret = "server-owned token with spaces, 'quote', \\slash and ;semicolon"
    monkeypatch.setattr(sys, "stdin", io.StringIO(secret))

    argv = ["env-set", "--path", str(path), "--key", "GHCR_TOKEN"]
    assert secret not in argv
    assert safe_io.main(argv) == 0
    assert capsys.readouterr().out == ""
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    text = path.read_text(encoding="utf-8")
    assert "EXISTING='literal'" in text
    assert "GHCR_TOKEN='server-owned token with spaces, \\'quote\\', " in text
    assert "\\\\slash and ;semicolon'" in text
    assert safe_io.main(["env-validate", "--path", str(path)]) == 0

    with pytest.raises(SystemExit):
        safe_io.build_parser().parse_args([*argv, "--value", secret])


@pytest.mark.parametrize(
    ("key", "value", "message"),
    [
        ("OMEGA_MIGRATION_CANDIDATE_REF", "a" * 40, "invalid or reserved"),
        ("VALID_KEY", "line-one\nline-two", "forbidden delimiter"),
        ("VALID_KEY", "control\x01byte", "control characters"),
        ("VALID_KEY", "$(touch /tmp/nope)", "executable/interpolated syntax"),
        ("VALID_KEY", "$PLAIN", "executable/interpolated syntax"),
    ],
)
def test_env_set_rejects_control_keys_and_values_without_modifying_file(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    key: str,
    value: str,
    message: str,
) -> None:
    path = _write_env(tmp_path / ".env", "EXISTING='unchanged'\n")
    before = path.read_bytes()
    monkeypatch.setattr(sys, "stdin", io.StringIO(value))

    with pytest.raises(SystemExit, match=message):
        safe_io.main(["env-set", "--path", str(path), "--key", key])

    assert path.read_bytes() == before


class _Response(io.BytesIO):
    def __enter__(self) -> _Response:
        return self

    def __exit__(self, *_args: object) -> None:
        self.close()


def test_gcs_download_binds_metadata_and_media_to_exact_identity(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    payload = b"immutable-release-bytes"
    expected_sha = hashlib.sha256(payload).hexdigest()
    token = "server-owned-oauth-token"
    calls: dict[str, object] = {}

    monkeypatch.setattr(safe_io, "_metadata_token", lambda: token)

    def metadata(url: str, supplied_token: str) -> dict:
        calls["metadata_url"] = url
        calls["metadata_token"] = supplied_token
        return {
            "bucket": "omega-source-bucket",
            "name": "deploy-artifacts/" + "a" * 40 + "/repo.tar.gz",
            "generation": "987654321",
            "size": str(len(payload)),
        }

    def media(request: object, timeout: int) -> _Response:
        calls["media_url"] = request.full_url
        calls["authorization"] = request.get_header("Authorization")
        calls["timeout"] = timeout
        return _Response(payload)

    monkeypatch.setattr(safe_io, "_request_json", metadata)
    monkeypatch.setattr(safe_io.urllib.request, "urlopen", media)
    output = tmp_path / "artifact.tar.gz"
    uri = "gs://omega-source-bucket/deploy-artifacts/" + "a" * 40 + "/repo.tar.gz"

    assert (
        safe_io.main(
            [
                "gcs-download",
                "--uri",
                uri,
                "--generation",
                "987654321",
                "--size",
                str(len(payload)),
                "--sha256",
                expected_sha,
                "--output",
                str(output),
            ]
        )
        == 0
    )
    assert output.read_bytes() == payload
    assert stat.S_IMODE(output.stat().st_mode) == 0o600
    metadata_query = urllib.parse.parse_qs(
        urllib.parse.urlsplit(str(calls["metadata_url"])).query
    )
    media_query = urllib.parse.parse_qs(
        urllib.parse.urlsplit(str(calls["media_url"])).query
    )
    assert metadata_query == {
        "fields": ["bucket,name,generation,size,crc32c,md5Hash"],
        "generation": ["987654321"],
    }
    assert media_query == {"alt": ["media"], "generation": ["987654321"]}
    assert calls["metadata_token"] == token
    assert calls["authorization"] == f"Bearer {token}"
    assert calls["timeout"] == 180
    evidence = json.loads(capsys.readouterr().out)
    assert evidence["generation"] == "987654321"
    assert evidence["size_bytes"] == len(payload)
    assert evidence["sha256"] == expected_sha
    assert token not in json.dumps(evidence)
    assert token not in str(calls["metadata_url"])
    assert token not in str(calls["media_url"])


@pytest.mark.parametrize("mismatch", ["generation", "size", "sha256"])
def test_gcs_download_rejects_any_identity_mismatch_and_removes_output(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    mismatch: str,
) -> None:
    payload = b"reviewed"
    reviewed_sha = hashlib.sha256(payload).hexdigest()
    metadata_called = False
    media_called = False
    monkeypatch.setattr(safe_io, "_metadata_token", lambda: "oauth")

    def metadata(_url: str, _token: str) -> dict:
        nonlocal metadata_called
        metadata_called = True
        return {
            "bucket": "omega-source-bucket",
            "name": "deploy-artifacts/" + "b" * 40 + "/repo.tar.gz",
            "generation": "43" if mismatch == "generation" else "42",
            "size": str(len(payload) + 1 if mismatch == "size" else len(payload)),
        }

    def media(_request: object, timeout: int) -> _Response:
        nonlocal media_called
        del timeout
        media_called = True
        return _Response(b"tampered" if mismatch == "sha256" else payload)

    monkeypatch.setattr(safe_io, "_request_json", metadata)
    monkeypatch.setattr(safe_io.urllib.request, "urlopen", media)
    output = tmp_path / "artifact.tar.gz"

    with pytest.raises(SystemExit, match="differ"):
        safe_io.main(
            [
                "gcs-download",
                "--uri",
                "gs://omega-source-bucket/deploy-artifacts/"
                + "b" * 40
                + "/repo.tar.gz",
                "--generation",
                "42",
                "--size",
                str(len(payload)),
                "--sha256",
                reviewed_sha,
                "--output",
                str(output),
            ]
        )

    assert metadata_called
    assert media_called is (mismatch == "sha256")
    assert not output.exists()


def _startup_contract() -> dict[str, object]:
    deploy_ref = "a" * 40
    return {
        "schema_version": 1,
        "project_id": "omega-production",
        "environment": "production",
        "source": {
            "bucket": "omega-source-bucket",
            "object": f"deploy-artifacts/{deploy_ref}/repo.tar.gz",
            "ref": deploy_ref,
            "generation": "987654321",
            "size_bytes": 4096,
            "archive_sha256": "b" * 64,
        },
        "urls": {
            "public_console": "https://console.example.com",
            "public_workspace": "https://workspace.example.com",
            "public_airflow": "https://console.example.com/airflow",
            "technical_console": "http://192.0.2.10",
            "technical_workspace": "http://192.0.2.11",
        },
        "admin_email": "operator@example.com",
        "cookie_secure": True,
        "data_disk_size_bytes": 150 * 1024**3,
        "lakehouse_bucket": "omega-production-lakehouse",
        "release_backup_bucket": "omega-production-release-backups-894064513501",
        "lakehouse_endpoint": "storage.googleapis.com",
        "enable_airflow_scheduler": True,
        "secret_prefix": "omega-production-",
        "compose_override": "services:\n  api:\n    restart: unless-stopped\n",
    }


def _startup_input(payload: object) -> io.StringIO:
    encoded = base64.b64encode(
        json.dumps(payload, separators=(",", ":")).encode("utf-8")
    ).decode("ascii")
    return io.StringIO(encoded)


def test_startup_config_writes_private_normalized_json_atomically_and_fsyncs(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    payload = _startup_contract()
    output = tmp_path / "startup.json"
    replace_calls: list[tuple[Path, Path]] = []
    fsync_descriptors: list[int] = []
    original_replace = safe_io.os.replace
    original_fsync = safe_io.os.fsync

    def tracked_replace(source: str | Path, destination: str | Path) -> None:
        replace_calls.append((Path(source), Path(destination)))
        original_replace(source, destination)

    def tracked_fsync(descriptor: int) -> None:
        fsync_descriptors.append(descriptor)
        original_fsync(descriptor)

    monkeypatch.setattr(safe_io.os, "replace", tracked_replace)
    monkeypatch.setattr(safe_io.os, "fsync", tracked_fsync)
    monkeypatch.setattr(sys, "stdin", _startup_input(payload))

    assert safe_io.main(["startup-config", "--output", str(output)]) == 0

    assert capsys.readouterr().out == ""
    assert stat.S_IMODE(output.stat().st_mode) == 0o600
    assert output.read_text(encoding="utf-8") == (
        json.dumps(payload, sort_keys=True, separators=(",", ":")) + "\n"
    )
    assert len(replace_calls) == 1
    temporary, published = replace_calls[0]
    assert temporary.parent == output.parent
    assert temporary.name.startswith(f".{output.name}.")
    assert published == output
    assert not temporary.exists()
    assert len(fsync_descriptors) >= 2  # staged file, then containing directory


def _set_nested(
    payload: dict[str, object], path: tuple[str, ...], value: object
) -> None:
    target = payload
    for key in path[:-1]:
        child = target[key]
        assert isinstance(child, dict)
        target = child
    target[path[-1]] = value


@pytest.mark.parametrize(
    ("path", "value", "message"),
    [
        (("schema_version",), True, "schema is unsupported"),
        (("data_disk_size_bytes",), 8 * 1024**3, "disk byte identity"),
        (("project_id",), "omega-production;touch", "project id is invalid"),
        (
            ("urls", "public_console"),
            "https://console.example.com;touch",
            "URL is not canonical",
        ),
        (("admin_email",), "operator@example.com;touch", "email is invalid"),
        (
            ("source", "ref"),
            "a" * 39 + ";",
            "source ref is invalid",
        ),
        (
            ("source", "object"),
            "deploy-artifacts/../../payload.tar.gz",
            "source artifact identity is invalid",
        ),
        (
            ("lakehouse_endpoint",),
            "storage.googleapis.com;touch",
            "lakehouse endpoint is invalid",
        ),
        (("unexpected",), "ignored", "root keys differ"),
        (("source", "unexpected"), "ignored", "source keys differ"),
    ],
)
def test_startup_config_rejects_injected_or_nonexact_contract_without_output(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    path: tuple[str, ...],
    value: object,
    message: str,
) -> None:
    payload = copy.deepcopy(_startup_contract())
    _set_nested(payload, path, value)
    output = tmp_path / "startup.json"
    monkeypatch.setattr(sys, "stdin", _startup_input(payload))

    with pytest.raises(SystemExit, match=message):
        safe_io.main(["startup-config", "--output", str(output)])

    assert not output.exists()
    assert list(tmp_path.glob(f".{output.name}.*")) == []


def test_startup_config_publish_failure_leaves_no_output_or_temporary_file(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    output = tmp_path / "startup.json"
    monkeypatch.setattr(sys, "stdin", _startup_input(_startup_contract()))

    def fail_replace(_source: str | Path, _destination: str | Path) -> None:
        raise OSError("injected atomic publish failure")

    monkeypatch.setattr(safe_io.os, "replace", fail_replace)

    with pytest.raises(OSError, match="injected atomic publish failure"):
        safe_io.main(["startup-config", "--output", str(output)])

    assert not output.exists()
    assert list(tmp_path.glob(f".{output.name}.*")) == []


def test_fsync_file_rejects_a_symbolic_link(tmp_path: Path) -> None:
    source = tmp_path / "source"
    source.write_text("durable", encoding="utf-8")
    linked = tmp_path / "linked"
    linked.symlink_to(source)
    with pytest.raises(SystemExit, match="cannot be opened safely"):
        safe_io.main(["fsync-file", str(linked)])


def test_fsync_file_rejects_a_hard_link(tmp_path: Path) -> None:
    source = tmp_path / "source"
    source.write_text("durable", encoding="utf-8")
    linked = tmp_path / "linked"
    os.link(source, linked)
    with pytest.raises(SystemExit, match="link count is unsafe"):
        safe_io.main(["fsync-file", str(linked)])
