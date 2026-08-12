from __future__ import annotations

import argparse
import base64
import copy
import hashlib
import http.server
import io
import json
import os
import stat
import subprocess
import sys
import tarfile
import threading
import urllib.parse
import urllib.request
import zlib
from pathlib import Path

import pytest

from scripts.gcp import safe_io


_ORIGINAL_REQUIRE_ROOT = safe_io._require_root


@pytest.fixture(autouse=True)
def _allow_non_root_test_calls(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(safe_io, "_require_root", lambda: None)


def test_root_requirement_cannot_be_bypassed_by_process_environment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(safe_io.os, "geteuid", lambda: 1000)
    monkeypatch.setenv("OMEGA_SAFE_IO_TEST_NON_ROOT", "1")

    with pytest.raises(SystemExit, match="remote host operation requires root"):
        _ORIGINAL_REQUIRE_ROOT()


def test_docker_storage_paths_reject_symlink_or_unsafe_mode(tmp_path: Path) -> None:
    owner_uid = os.geteuid()
    owner_gid = os.getegid()
    fstab = tmp_path / "fstab"
    fstab.write_text("# canonical test\n", encoding="utf-8")
    fstab.chmod(0o644)
    external = tmp_path / "external"
    external.mkdir()
    mountpoint = tmp_path / "docker"
    mountpoint.symlink_to(external, target_is_directory=True)

    with pytest.raises(SystemExit, match="mountpoint ownership/path"):
        safe_io._validate_docker_storage_paths(
            mountpoint=mountpoint,
            fstab=fstab,
            owner_uid=owner_uid,
            owner_gid=owner_gid,
            create_mountpoint=False,
        )
    assert external.is_dir()

    mountpoint.unlink()
    mountpoint.mkdir(mode=0o777)
    mountpoint.chmod(0o777)
    with pytest.raises(SystemExit, match="mountpoint ownership/path"):
        safe_io._validate_docker_storage_paths(
            mountpoint=mountpoint,
            fstab=fstab,
            owner_uid=owner_uid,
            owner_gid=owner_gid,
            create_mountpoint=False,
        )


def test_docker_fstab_is_zero_or_one_exact_row_only(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setattr(safe_io.os, "geteuid", lambda: os.getuid())
    monkeypatch.setattr(safe_io.os, "getegid", lambda: os.getgid())
    device = Path("/dev/disk/by-id/google-omega-docker-data")
    mountpoint = Path("/var/lib/docker")
    canonical = f"{device} {mountpoint} ext4 discard,defaults,nofail 0 2"
    fstab = tmp_path / "fstab"
    fstab.write_text("# test\n", encoding="utf-8")
    fstab.chmod(0o644)

    safe_io._reconcile_docker_fstab(
        fstab=fstab,
        device=device,
        mountpoint=mountpoint,
        append_missing=True,
    )
    first = fstab.read_bytes()
    assert first.decode().splitlines() == ["# test", canonical]
    safe_io._reconcile_docker_fstab(
        fstab=fstab,
        device=device,
        mountpoint=mountpoint,
        append_missing=True,
    )
    assert fstab.read_bytes() == first

    fstab.write_text(f"{canonical}\n{canonical}\n", encoding="utf-8")
    fstab.chmod(0o644)
    with pytest.raises(SystemExit, match="duplicated or noncanonical"):
        safe_io._reconcile_docker_fstab(
            fstab=fstab,
            device=device,
            mountpoint=mountpoint,
            append_missing=True,
        )

    fstab.write_text(
        f"{device} {mountpoint} ext4 defaults 0 2\n",
        encoding="utf-8",
    )
    fstab.chmod(0o644)
    with pytest.raises(SystemExit, match="duplicated or noncanonical"):
        safe_io._reconcile_docker_fstab(
            fstab=fstab,
            device=device,
            mountpoint=mountpoint,
            append_missing=True,
        )


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
    def __init__(self, value: bytes, headers: dict[str, str] | None = None) -> None:
        super().__init__(value)
        self.headers = headers or {}

    def __enter__(self) -> _Response:
        return self

    def __exit__(self, *_args: object) -> None:
        self.close()


class _Opener:
    def __init__(self, handler: object) -> None:
        self._handler = handler

    def open(self, request: object, timeout: int) -> _Response:
        return self._handler(request, timeout)  # type: ignore[operator]


def test_authenticated_gcp_io_ignores_hostile_process_proxies(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    payload = b"direct-only-release"
    token = "server-owned-token-never-log"
    generation = "987654321"
    object_name = "deploy-artifacts/" + "a" * 40 + "/repo.tar.gz"
    hostile_proxy = "http://127.0.0.1:9"
    calls: list[tuple[str, int, dict[str, str]]] = []
    proxy_handlers: list[dict[str, str]] = []

    for variable in (
        "HTTP_PROXY",
        "HTTPS_PROXY",
        "ALL_PROXY",
        "http_proxy",
        "https_proxy",
        "all_proxy",
    ):
        monkeypatch.setenv(variable, hostile_proxy)
    monkeypatch.setenv("NO_PROXY", "")
    monkeypatch.setenv("no_proxy", "")

    def forbidden_urlopen(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("ambient-proxy-aware urlopen must never be used")

    def direct_open(request: object, timeout: int) -> _Response:
        url = request.full_url
        headers = {key.lower(): value for key, value in request.header_items()}
        calls.append((url, timeout, headers))
        if url.startswith("http://metadata.google.internal/"):
            assert headers == {"metadata-flavor": "Google"}
            return _Response(
                json.dumps(
                    {"access_token": token, "expires_in": 3599, "token_type": "Bearer"}
                ).encode(),
                {"Metadata-Flavor": "Google"},
            )
        assert headers == {"authorization": f"Bearer {token}"}
        if url.startswith("https://storage.googleapis.com/storage/v1/"):
            return _Response(
                json.dumps(
                    {
                        "bucket": "omega-source-bucket",
                        "name": object_name,
                        "generation": generation,
                        "size": str(len(payload)),
                        "crc32c": base64.b64encode(
                            safe_io._crc32c(payload).to_bytes(4, "big")
                        ).decode(),
                        "md5Hash": base64.b64encode(
                            hashlib.md5(payload, usedforsecurity=False).digest()
                        ).decode(),
                    }
                ).encode()
            )
        assert url.startswith("https://storage.googleapis.com/download/storage/v1/")
        return _Response(payload)

    def guarded_build_opener(*handlers: object) -> _Opener:
        assert len(handlers) == 3
        handler, redirect_handler, https_handler = handlers
        assert isinstance(handler, urllib.request.ProxyHandler)
        assert isinstance(https_handler, urllib.request.HTTPSHandler)
        proxy_handlers.append(handler.proxies)
        assert handler.proxies == {}
        with pytest.raises(RuntimeError, match="redirects are forbidden"):
            redirect_handler.redirect_request(
                None, None, 307, "redirect", {}, "https://attacker.invalid"
            )
        return _Opener(direct_open)

    monkeypatch.setattr(safe_io.urllib.request, "urlopen", forbidden_urlopen)
    monkeypatch.setattr(safe_io.urllib.request, "build_opener", guarded_build_opener)
    output = tmp_path / "release.tar.gz"

    assert (
        safe_io.main(
            [
                "gcs-download",
                "--uri",
                f"gs://omega-source-bucket/{object_name}",
                "--generation",
                generation,
                "--size",
                str(len(payload)),
                "--sha256",
                hashlib.sha256(payload).hexdigest(),
                "--output",
                str(output),
            ]
        )
        == 0
    )

    assert output.read_bytes() == payload
    assert [timeout for _url, timeout, _headers in calls] == [10, 30, 180]
    assert proxy_handlers == [{}, {}, {}]
    captured = capsys.readouterr()
    assert token not in captured.out
    assert token not in captured.err


def test_authenticated_gcp_io_rejects_cross_origin_redirect_before_forwarding(
    capsys: pytest.CaptureFixture[str],
) -> None:
    token = "server-owned-token-never-forward"
    destination_requests: list[str | None] = []

    class Destination(http.server.BaseHTTPRequestHandler):
        def do_GET(self) -> None:  # noqa: N802
            destination_requests.append(self.headers.get("Authorization"))
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(b"{}")

        def log_message(self, *_args: object) -> None:
            pass

    destination = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Destination)
    destination_thread = threading.Thread(target=destination.serve_forever)
    destination_thread.start()
    destination_url = f"http://127.0.0.1:{destination.server_port}/stolen"

    class Origin(http.server.BaseHTTPRequestHandler):
        def do_GET(self) -> None:  # noqa: N802
            self.send_response(307)
            self.send_header("Location", destination_url)
            self.end_headers()

        def log_message(self, *_args: object) -> None:
            pass

    origin = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Origin)
    origin_thread = threading.Thread(target=origin.serve_forever)
    origin_thread.start()
    try:
        with pytest.raises(RuntimeError, match="redirects are forbidden"):
            safe_io._request_json(
                f"http://127.0.0.1:{origin.server_port}/redirect",
                token,
            )
    finally:
        origin.shutdown()
        destination.shutdown()
        origin.server_close()
        destination.server_close()
        origin_thread.join()
        destination_thread.join()

    assert destination_requests == []
    captured = capsys.readouterr()
    assert token not in captured.out
    assert token not in captured.err


def _fake_proc_entry(
    proc_root: Path,
    *,
    pid: int,
    cgroup: str,
    executable: Path | None = None,
    arguments: tuple[str, ...] = (),
    starttime: str = "12345",
) -> Path:
    root = proc_root / str(pid)
    root.mkdir()
    (root / "cgroup").write_text(f"0::{cgroup}\n", encoding="utf-8")
    stat_fields = ["S", *("0" for _ in range(18)), starttime]
    (root / "stat").write_text(
        f"{pid} (containerd-shim) {' '.join(stat_fields)}\n", encoding="utf-8"
    )
    if executable is not None:
        (root / "exe").symlink_to(executable)
        (root / "cmdline").write_bytes(
            b"\0".join(value.encode() for value in arguments) + b"\0"
        )
        (root / "status").write_text(
            "Name:\tshim\nUid:\t0\t0\t0\t0\n", encoding="utf-8"
        )
    return root


def _remove_fake_proc(root: Path) -> None:
    if not root.exists():
        return
    for child in root.iterdir():
        child.unlink()
    root.rmdir()


@pytest.mark.parametrize(
    "cgroup",
    (
        "/docker/short-id",
        "/system.slice/docker-short.scope",
        f"/docker/{'a' * 64}/docker/{'b' * 64}",
        f"/system.slice/docker-{'a' * 64}.scope/docker-{'b' * 64}.scope",
        f"/foreign.slice/{'a' * 64}",
        f"docker/{'a' * 64}",
    ),
)
def test_moby_cgroup_identity_rejects_any_ambiguous_or_malformed_docker_intent(
    cgroup: str,
) -> None:
    with pytest.raises(RuntimeError, match="ambiguous or malformed"):
        safe_io._moby_cgroup_identity(cgroup)


class _FakePidfds:
    def __init__(self, proc_root: Path, on_signal=None) -> None:
        self.proc_root = proc_root
        self.on_signal = on_signal
        self.next_fd = 10_000
        self.identities: dict[int, tuple[int, str | None]] = {}
        self.closed: set[int] = set()

    def open(self, pid: int) -> int:
        pidfd = self.next_fd
        self.next_fd += 1
        self.identities[pidfd] = (
            pid,
            safe_io._proc_starttime(self.proc_root / str(pid)),
        )
        return pidfd

    def send(self, pidfd: int, requested_signal: int) -> None:
        if pidfd in self.closed or pidfd not in self.identities:
            raise ProcessLookupError
        pid, starttime = self.identities[pidfd]
        if safe_io._proc_starttime(self.proc_root / str(pid)) != starttime:
            raise ProcessLookupError
        if requested_signal != 0 and self.on_signal is not None:
            self.on_signal(pid, requested_signal)

    def close(self, pidfd: int) -> None:
        self.closed.add(pidfd)


def test_moby_fence_kills_only_exact_root_cgroup_v2_workloads(
    tmp_path: Path,
) -> None:
    proc_root = tmp_path / "proc"
    cgroup_root = tmp_path / "cgroup"
    proc_root.mkdir()
    cgroup_root.mkdir()
    (cgroup_root / "cgroup.controllers").write_text("cpu memory\n", encoding="utf-8")
    shim_executable = tmp_path / "containerd-shim-runc-v2"
    shim_executable.write_text("shim\n", encoding="utf-8")
    moby_id = "a" * 64
    other_id = "b" * 64
    moby_relative = f"/system.slice/docker-{moby_id}.scope/delegated/workload"
    moby_cgroup = cgroup_root / "system.slice" / f"docker-{moby_id}.scope"
    moby_cgroup.mkdir(parents=True)
    (moby_cgroup / "delegated" / "workload").mkdir(parents=True)
    (moby_cgroup / "cgroup.kill").write_text("", encoding="utf-8")
    (moby_cgroup / "cgroup.events").write_text("populated 1\n", encoding="utf-8")
    moby_shim = _fake_proc_entry(
        proc_root,
        pid=101,
        cgroup="/system.slice/containerd.service",
        executable=shim_executable,
        arguments=(
            str(shim_executable),
            "-namespace",
            "moby",
            "-id",
            moby_id,
        ),
    )
    moby_task = _fake_proc_entry(proc_root, pid=102, cgroup=moby_relative)
    unrelated_executable = tmp_path / "unrelated-worker"
    unrelated_executable.write_text("worker\n", encoding="utf-8")
    other_shim = _fake_proc_entry(
        proc_root,
        pid=201,
        cgroup="/user.slice/unrelated.scope",
        executable=unrelated_executable,
        arguments=(
            str(shim_executable),
            "-namespace",
            "k8s.io",
            "-id",
            other_id,
        ),
    )
    killed_cgroups: list[Path] = []
    signalled: list[tuple[int, int]] = []

    def cgroup_kill(path: Path) -> None:
        killed_cgroups.append(path)
        assert path == moby_cgroup / "cgroup.kill"
        (moby_cgroup / "cgroup.events").write_text("populated 0\n", encoding="utf-8")
        _remove_fake_proc(moby_task)

    def signal_process(pid: int, requested_signal: int) -> None:
        signalled.append((pid, requested_signal))
        assert pid == 101
        _remove_fake_proc(moby_shim)

    pidfds = _FakePidfds(proc_root, signal_process)

    result = safe_io._fence_moby_workloads(
        proc_root=proc_root,
        cgroup_root=cgroup_root,
        shim_executable=shim_executable,
        write_cgroup_kill=cgroup_kill,
        open_pidfd=pidfds.open,
        send_pidfd_signal=pidfds.send,
        close_pidfd=pidfds.close,
        sleeper=lambda _seconds: None,
    )

    assert result == {
        "status": "PASS",
        "container_ids_fenced": 1,
        "stable_empty_sweeps": 2,
    }
    assert killed_cgroups == [moby_cgroup / "cgroup.kill"] * 2
    assert signalled == [(101, safe_io.signal.SIGTERM)]
    assert other_shim.exists()


@pytest.mark.parametrize(
    "arguments",
    (
        ("-namespace", "k8s.io", "-id", "b" * 64),
        ("-namespace", "moby"),
        ("-namespace", "moby", "-id", "short"),
        ("-namespace", "moby", "-id", "a" * 64, "-id", "b" * 64),
    ),
)
def test_moby_fence_rejects_foreign_or_malformed_containerd_shims(
    tmp_path: Path,
    arguments: tuple[str, ...],
) -> None:
    proc_root = tmp_path / "proc"
    cgroup_root = tmp_path / "cgroup"
    proc_root.mkdir()
    cgroup_root.mkdir()
    (cgroup_root / "cgroup.controllers").write_text("cpu\n", encoding="utf-8")
    shim_executable = tmp_path / "containerd-shim-runc-v2"
    shim_executable.write_text("shim\n", encoding="utf-8")
    _fake_proc_entry(
        proc_root,
        pid=211,
        cgroup="/system.slice/containerd.service",
        executable=shim_executable,
        arguments=(str(shim_executable), *arguments),
    )
    pidfds = _FakePidfds(proc_root)

    with pytest.raises(RuntimeError, match="shim|non-moby"):
        safe_io._fence_moby_workloads(
            proc_root=proc_root,
            cgroup_root=cgroup_root,
            shim_executable=shim_executable,
            open_pidfd=pidfds.open,
            send_pidfd_signal=pidfds.send,
            close_pidfd=pidfds.close,
            sleeper=lambda _seconds: None,
        )


def test_moby_fence_never_accepts_a_malformed_docker_cgroup_as_empty(
    tmp_path: Path,
) -> None:
    proc_root = tmp_path / "proc"
    cgroup_root = tmp_path / "cgroup"
    proc_root.mkdir()
    cgroup_root.mkdir()
    (cgroup_root / "cgroup.controllers").write_text("cpu\n", encoding="utf-8")
    _fake_proc_entry(proc_root, pid=212, cgroup="/docker/short-id")
    pidfds = _FakePidfds(proc_root)

    with pytest.raises(RuntimeError, match="ambiguous or malformed"):
        safe_io._fence_moby_workloads(
            proc_root=proc_root,
            cgroup_root=cgroup_root,
            shim_executable=tmp_path / "containerd-shim-runc-v2",
            open_pidfd=pidfds.open,
            send_pidfd_signal=pidfds.send,
            close_pidfd=pidfds.close,
            sleeper=lambda _seconds: None,
        )


def test_moby_fence_fails_closed_without_cgroup_kill(
    tmp_path: Path,
) -> None:
    proc_root = tmp_path / "proc"
    cgroup_root = tmp_path / "cgroup"
    proc_root.mkdir()
    cgroup_root.mkdir()
    (cgroup_root / "cgroup.controllers").write_text("cpu memory\n", encoding="utf-8")
    container_id = "c" * 64
    relative = f"/docker/{container_id}"
    cgroup = cgroup_root / "docker" / container_id
    cgroup.mkdir(parents=True)
    (cgroup / "cgroup.events").write_text("populated 1\n", encoding="utf-8")
    _fake_proc_entry(proc_root, pid=301, cgroup=relative)
    pidfds = _FakePidfds(proc_root)

    with pytest.raises(RuntimeError, match="lacks cgroup-v2 kill support"):
        safe_io._fence_moby_workloads(
            proc_root=proc_root,
            cgroup_root=cgroup_root,
            shim_executable=tmp_path / "containerd-shim-runc-v2",
            open_pidfd=pidfds.open,
            send_pidfd_signal=pidfds.send,
            close_pidfd=pidfds.close,
            sleeper=lambda _seconds: None,
        )


def test_moby_fence_rejects_a_symlinked_cgroup_component(tmp_path: Path) -> None:
    proc_root = tmp_path / "proc"
    cgroup_root = tmp_path / "cgroup"
    escaped_root = tmp_path / "escaped"
    proc_root.mkdir()
    cgroup_root.mkdir()
    escaped_root.mkdir()
    (cgroup_root / "cgroup.controllers").write_text("cpu\n", encoding="utf-8")
    container_id = "d" * 64
    escaped_cgroup = escaped_root / f"docker-{container_id}.scope"
    escaped_cgroup.mkdir()
    (escaped_cgroup / "cgroup.kill").write_text("", encoding="utf-8")
    (escaped_cgroup / "cgroup.events").write_text("populated 1\n", encoding="utf-8")
    (cgroup_root / "system.slice").symlink_to(escaped_root, target_is_directory=True)
    _fake_proc_entry(
        proc_root,
        pid=401,
        cgroup=f"/system.slice/docker-{container_id}.scope",
    )
    pidfds = _FakePidfds(proc_root)

    with pytest.raises(RuntimeError, match="link or non-directory"):
        safe_io._fence_moby_workloads(
            proc_root=proc_root,
            cgroup_root=cgroup_root,
            shim_executable=tmp_path / "containerd-shim-runc-v2",
            open_pidfd=pidfds.open,
            send_pidfd_signal=pidfds.send,
            close_pidfd=pidfds.close,
            sleeper=lambda _seconds: None,
        )


def test_moby_fence_tracks_and_terminates_a_member_that_escapes_scope(
    tmp_path: Path,
) -> None:
    proc_root = tmp_path / "proc"
    cgroup_root = tmp_path / "cgroup"
    proc_root.mkdir()
    cgroup_root.mkdir()
    (cgroup_root / "cgroup.controllers").write_text("cpu\n", encoding="utf-8")
    container_id = "e" * 64
    relative = f"/docker/{container_id}/delegated"
    cgroup = cgroup_root / "docker" / container_id
    cgroup.mkdir(parents=True)
    (cgroup / "delegated").mkdir()
    (cgroup / "cgroup.kill").write_text("", encoding="utf-8")
    (cgroup / "cgroup.events").write_text("populated 1\n", encoding="utf-8")
    member = _fake_proc_entry(proc_root, pid=501, cgroup=relative, starttime="991")
    signalled: list[tuple[int, int]] = []

    def cgroup_kill(_path: Path) -> None:
        if member.exists():
            (member / "cgroup").write_text(
                "0::/user.slice/escaped.scope\n", encoding="utf-8"
            )
        (cgroup / "cgroup.events").write_text("populated 0\n", encoding="utf-8")

    def signal_process(pid: int, requested_signal: int) -> None:
        signalled.append((pid, requested_signal))
        _remove_fake_proc(member)

    pidfds = _FakePidfds(proc_root, signal_process)

    result = safe_io._fence_moby_workloads(
        proc_root=proc_root,
        cgroup_root=cgroup_root,
        shim_executable=tmp_path / "containerd-shim-runc-v2",
        write_cgroup_kill=cgroup_kill,
        open_pidfd=pidfds.open,
        send_pidfd_signal=pidfds.send,
        close_pidfd=pidfds.close,
        sleeper=lambda _seconds: None,
    )

    assert result["status"] == "PASS"
    assert signalled == [(501, safe_io.signal.SIGTERM)]


def test_moby_fence_pid_reuse_between_recheck_and_signal_stays_pidfd_bound(
    tmp_path: Path,
) -> None:
    proc_root = tmp_path / "proc"
    cgroup_root = tmp_path / "cgroup"
    proc_root.mkdir()
    cgroup_root.mkdir()
    (cgroup_root / "cgroup.controllers").write_text("cpu\n", encoding="utf-8")
    container_id = "9" * 64
    relative = f"/docker/{container_id}"
    cgroup = cgroup_root / "docker" / container_id
    cgroup.mkdir(parents=True)
    (cgroup / "cgroup.kill").write_text("", encoding="utf-8")
    (cgroup / "cgroup.events").write_text("populated 1\n", encoding="utf-8")
    member = _fake_proc_entry(proc_root, pid=551, cgroup=relative, starttime="111")
    pidfds = _FakePidfds(proc_root)
    zero_probes = 0
    signal_attempts: list[int] = []

    def send_pidfd(pidfd: int, requested_signal: int) -> None:
        nonlocal zero_probes
        if requested_signal != 0:
            signal_attempts.append(pidfd)
        pidfds.send(pidfd, requested_signal)
        if requested_signal == 0:
            zero_probes += 1
            if zero_probes == 2:
                # PID 551 is replaced after the exact recheck but before the
                # termination call. The retained pidfd still names only the
                # original process and therefore cannot hit the replacement.
                (member / "stat").write_text(
                    "551 (replacement) S " + "0 " * 18 + "222\n",
                    encoding="utf-8",
                )
                (member / "cgroup").write_text(
                    "0::/user.slice/replacement.scope\n", encoding="utf-8"
                )

    def cgroup_kill(_path: Path) -> None:
        (cgroup / "cgroup.events").write_text("populated 0\n", encoding="utf-8")

    result = safe_io._fence_moby_workloads(
        proc_root=proc_root,
        cgroup_root=cgroup_root,
        shim_executable=tmp_path / "containerd-shim-runc-v2",
        write_cgroup_kill=cgroup_kill,
        open_pidfd=pidfds.open,
        send_pidfd_signal=send_pidfd,
        close_pidfd=pidfds.close,
        sleeper=lambda _seconds: None,
    )

    assert result["status"] == "PASS"
    assert signal_attempts == [10_000]
    assert signal_attempts != [551]
    assert safe_io._proc_starttime(member) == "222"


def test_moby_fence_fails_closed_on_unreadable_proc_cgroup(tmp_path: Path) -> None:
    proc_root = tmp_path / "proc"
    cgroup_root = tmp_path / "cgroup"
    proc_root.mkdir()
    cgroup_root.mkdir()
    (cgroup_root / "cgroup.controllers").write_text("cpu\n", encoding="utf-8")
    process = proc_root / "601"
    process.mkdir()
    (process / "cgroup").mkdir()
    pidfds = _FakePidfds(proc_root)

    with pytest.raises(RuntimeError, match="cannot read process cgroup identity"):
        safe_io._fence_moby_workloads(
            proc_root=proc_root,
            cgroup_root=cgroup_root,
            shim_executable=tmp_path / "containerd-shim-runc-v2",
            open_pidfd=pidfds.open,
            send_pidfd_signal=pidfds.send,
            close_pidfd=pidfds.close,
            sleeper=lambda _seconds: None,
        )


def test_moby_fence_never_signals_pid_one_from_a_forged_scope(
    tmp_path: Path,
) -> None:
    proc_root = tmp_path / "proc"
    cgroup_root = tmp_path / "cgroup"
    proc_root.mkdir()
    cgroup_root.mkdir()
    (cgroup_root / "cgroup.controllers").write_text("cpu\n", encoding="utf-8")
    container_id = "f" * 64
    _fake_proc_entry(proc_root, pid=1, cgroup=f"/docker/{container_id}")
    signalled: list[tuple[int, int]] = []
    pidfds = _FakePidfds(
        proc_root, lambda pid, requested: signalled.append((pid, requested))
    )

    with pytest.raises(RuntimeError, match="forbidden host PID"):
        safe_io._fence_moby_workloads(
            proc_root=proc_root,
            cgroup_root=cgroup_root,
            shim_executable=tmp_path / "containerd-shim-runc-v2",
            open_pidfd=pidfds.open,
            send_pidfd_signal=pidfds.send,
            close_pidfd=pidfds.close,
            sleeper=lambda _seconds: None,
        )

    assert signalled == []


def test_gcs_download_binds_metadata_and_media_to_exact_identity(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    payload = b"immutable-release-bytes"
    expected_sha = hashlib.sha256(payload).hexdigest()
    token = "server-owned-oauth-token"
    calls: dict[str, object] = {}

    monkeypatch.setattr(safe_io, "_metadata_token", lambda **_kwargs: token)

    def metadata(url: str, supplied_token: str, **_kwargs: object) -> dict:
        calls["metadata_url"] = url
        calls["metadata_token"] = supplied_token
        return {
            "bucket": "omega-source-bucket",
            "name": "deploy-artifacts/" + "a" * 40 + "/repo.tar.gz",
            "generation": "987654321",
            "size": str(len(payload)),
            "crc32c": base64.b64encode(
                safe_io._crc32c(payload).to_bytes(4, "big")
            ).decode(),
            "md5Hash": base64.b64encode(
                hashlib.md5(payload, usedforsecurity=False).digest()
            ).decode(),
        }

    def media(request: object, timeout: int) -> _Response:
        calls["media_url"] = request.full_url
        calls["authorization"] = request.get_header("Authorization")
        calls["timeout"] = timeout
        return _Response(payload)

    monkeypatch.setattr(safe_io, "_request_json", metadata)
    monkeypatch.setattr(safe_io, "_direct_opener", lambda: _Opener(media))
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


def test_release_backup_permissions_bind_exact_vm_identity_and_deny_mutation(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    service_account = (
        "omega-staging-app@project-dd5ba7fa-374c-4554-ae6.iam.gserviceaccount.com"
    )
    observed: dict[str, object] = {}
    monkeypatch.setattr(safe_io, "_require_root", lambda: None)
    monkeypatch.setattr(safe_io, "_metadata_service_account", lambda: service_account)
    monkeypatch.setattr(safe_io, "_metadata_token", lambda: "server-owned-token")

    def permissions(url: str, token: str, **_kwargs: object) -> dict:
        observed["url"] = url
        observed["token"] = token
        requested = urllib.parse.parse_qs(urllib.parse.urlsplit(url).query)[
            "permissions"
        ]
        assert "storage.buckets.delete" in requested
        assert "storage.buckets.setIamPolicy" in requested
        assert "storage.objects.setRetention" in requested
        return {
            "permissions": [
                "storage.buckets.get",
                "storage.objects.create",
                "storage.objects.get",
            ]
        }

    monkeypatch.setattr(safe_io, "_request_json", permissions)
    assert (
        safe_io.main(
            [
                "gcs-release-backup-permissions",
                "--bucket",
                "omega-staging-release-backups",
                "--expected-service-account",
                service_account,
            ]
        )
        == 0
    )
    evidence = json.loads(capsys.readouterr().out)
    assert evidence["bucket"] == "omega-staging-release-backups"
    assert evidence["service_account"] == service_account
    assert observed["token"] == "server-owned-token"


@pytest.mark.parametrize(
    "payload",
    [
        {"permissions": ["storage.objects.create"], "extra": []},
        {
            "permissions": [
                "storage.buckets.get",
                "storage.objects.create",
                "storage.objects.get",
                "storage.buckets.delete",
            ]
        },
        {
            "permissions": [
                "storage.buckets.get",
                "storage.objects.create",
                "storage.objects.get",
                "storage.objects.get",
            ]
        },
    ],
)
def test_release_backup_permissions_fail_closed_on_response_or_privilege_drift(
    monkeypatch: pytest.MonkeyPatch,
    payload: dict[str, object],
) -> None:
    service_account = (
        "omega-staging-app@project-dd5ba7fa-374c-4554-ae6.iam.gserviceaccount.com"
    )
    monkeypatch.setattr(safe_io, "_require_root", lambda: None)
    monkeypatch.setattr(safe_io, "_metadata_service_account", lambda: service_account)
    monkeypatch.setattr(safe_io, "_metadata_token", lambda: "server-owned-token")
    monkeypatch.setattr(safe_io, "_request_json", lambda *_args, **_kwargs: payload)
    with pytest.raises(SystemExit):
        safe_io.main(
            [
                "gcs-release-backup-permissions",
                "--bucket",
                "omega-staging-release-backups",
                "--expected-service-account",
                service_account,
            ]
        )


@pytest.mark.parametrize("mismatch", ["generation", "size", "sha256", "crc32c", "md5"])
def test_gcs_download_rejects_any_identity_mismatch_and_removes_output(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    mismatch: str,
) -> None:
    payload = b"reviewed"
    reviewed_sha = hashlib.sha256(payload).hexdigest()
    metadata_called = False
    media_called = False
    monkeypatch.setattr(safe_io, "_metadata_token", lambda **_kwargs: "oauth")

    def metadata(_url: str, _token: str, **_kwargs: object) -> dict:
        nonlocal metadata_called
        metadata_called = True
        return {
            "bucket": "omega-source-bucket",
            "name": "deploy-artifacts/" + "b" * 40 + "/repo.tar.gz",
            "generation": "43" if mismatch == "generation" else "42",
            "size": str(len(payload) + 1 if mismatch == "size" else len(payload)),
            "crc32c": base64.b64encode(
                (
                    safe_io._crc32c(payload) + (1 if mismatch == "crc32c" else 0)
                ).to_bytes(4, "big")
            ).decode(),
            "md5Hash": base64.b64encode(
                hashlib.md5(
                    b"different" if mismatch == "md5" else payload,
                    usedforsecurity=False,
                ).digest()
            ).decode(),
        }

    def media(_request: object, timeout: int) -> _Response:
        nonlocal media_called
        del timeout
        media_called = True
        return _Response(b"tampered" if mismatch == "sha256" else payload)

    monkeypatch.setattr(safe_io, "_request_json", metadata)
    monkeypatch.setattr(safe_io, "_direct_opener", lambda: _Opener(media))
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
    assert media_called is (mismatch in {"sha256", "crc32c", "md5"})
    assert not output.exists()


def _startup_contract() -> dict[str, object]:
    deploy_ref = "a" * 40
    return {
        "schema_version": 1,
        "project_id": "omega-production",
        "environment": "production",
        "controller_ref": "c" * 40,
        "foundation_predecessor": None,
        "host_identity": {
            "project_id": "omega-production",
            "instance_id": "4767392334132429161",
            "instance_name": "omega-production-app",
            "zone": "us-central1-a",
            "service_account_email": (
                "omega-production-app@omega-production.iam.gserviceaccount.com"
            ),
        },
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
            "technical_console": "",
            "technical_workspace": "",
        },
        "admin_email": "operator@example.com",
        "cookie_secure": True,
        "data_disk_size_bytes": 150 * 1024**3,
        "host_package_versions": {
            "ca_certificates": "20240203~22.04.1",
            "curl": "7.81.0-1ubuntu1.20",
            "gnupg": "2.2.27-3ubuntu2.4",
            "iptables": "1.8.7-1ubuntu5.2",
            "jq": "1.6-2.1ubuntu3",
            "lsof": "4.93.2+dfsg-1.1build2",
            "openssl": "3.0.2-0ubuntu1.20",
            "python3": "3.10.6-1~22.04.1",
            "docker_ce": "5:28.5.1-1~ubuntu.22.04~jammy",
            "docker_ce_cli": "5:28.5.1-1~ubuntu.22.04~jammy",
            "containerd_io": "1.7.27-1",
            "docker_buildx_plugin": "0.30.1-1~ubuntu.22.04~jammy",
            "docker_compose_plugin": "5.0.0-1~ubuntu.22.04~jammy",
        },
        "secret_versions": {
            "control_room_evidence_signing_key_id": "1",
            "control_room_evidence_signing_key": "2",
            "control_room_evidence_signing_previous_keys": "3",
            "gcs_hmac_access_key_id": "",
            "gcs_hmac_secret_access_key": "",
        },
        "lakehouse_bucket": "omega-production-lakehouse",
        "release_backup_bucket": "omega-production-release-backups-894064513501",
        "lakehouse_endpoint": "storage.googleapis.com",
        "canonical_writer": True,
        "enable_airflow_scheduler": True,
        "exact_runtime_contract_ready": False,
        "secret_prefix": "omega-production-",
        "compose_override": "services:\n  api:\n    restart: unless-stopped\n",
    }


def _startup_input(payload: object) -> io.StringIO:
    encoded = base64.b64encode(
        json.dumps(payload, separators=(",", ":")).encode("utf-8")
    ).decode("ascii")
    return io.StringIO(encoded)


def test_startup_scheduler_role_must_exactly_match_canonical_writer(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    for writer, scheduler, accepted in (
        (True, True, True),
        (False, False, True),
        (True, False, False),
        (False, True, False),
    ):
        payload = _startup_contract()
        payload["canonical_writer"] = writer
        payload["enable_airflow_scheduler"] = scheduler
        output = tmp_path / f"startup-{writer}-{scheduler}.json"
        monkeypatch.setattr(sys, "stdin", _startup_input(payload))
        if accepted:
            assert safe_io.main(["startup-config", "--output", str(output)]) == 0
            assert output.is_file()
        else:
            with pytest.raises(SystemExit, match="scheduler must exactly match"):
                safe_io.main(["startup-config", "--output", str(output)])
            assert not output.exists()


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
            ("controller_ref",),
            "c" * 39 + ";",
            "controller ref is invalid",
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


def test_host_identity_is_exact_metadata_bound_root_owned_and_append_only(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    payload = _startup_contract()
    source = Path("/run/omega-gcp-bootstrap.ABC123/startup-contract.json")
    etc = tmp_path / "etc"
    etc.mkdir(mode=0o755)
    output = etc / "omega" / "gcp-host-identity.json"
    monkeypatch.setattr(safe_io, "HOST_IDENTITY_PATH", output)
    contract_raw = (
        json.dumps(payload, sort_keys=True, separators=(",", ":")) + "\n"
    ).encode()
    original_read = safe_io._exact_file_bytes

    def read(path: Path, **kwargs):
        if path == source:
            return contract_raw
        return original_read(path, **kwargs)

    metadata = {
        "project/project-id": b"omega-production",
        "instance/id": b"4767392334132429161",
        "instance/name": b"omega-production-app",
        "instance/zone": b"projects/894064513501/zones/us-central1-a",
        "instance/service-accounts/default/email": (
            b"omega-production-app@omega-production.iam.gserviceaccount.com"
        ),
    }
    monkeypatch.setattr(safe_io, "_exact_file_bytes", read)
    monkeypatch.setattr(
        safe_io,
        "_metadata_attribute",
        lambda path, maximum: metadata[path][:maximum],
    )

    command = [
        "host-identity-install",
        "--startup-contract",
        str(source),
        "--output",
        str(output),
    ]
    assert safe_io.main(command) == 0
    expected = {"schema_version": 1, **payload["host_identity"]}
    assert (
        output.read_bytes()
        == (json.dumps(expected, sort_keys=True, separators=(",", ":")) + "\n").encode()
    )
    assert stat.S_IMODE(output.stat().st_mode) == 0o400
    assert stat.S_IMODE(output.parent.stat().st_mode) == 0o755
    before = output.stat()
    assert safe_io.main(command) == 0
    after = output.stat()
    assert (before.st_dev, before.st_ino) == (after.st_dev, after.st_ino)


def test_host_identity_mismatch_fails_before_publication(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    payload = _startup_contract()
    source = Path("/run/omega-gcp-bootstrap.ABC123/startup-contract.json")
    etc = tmp_path / "etc"
    etc.mkdir(mode=0o755)
    output = etc / "omega" / "gcp-host-identity.json"
    monkeypatch.setattr(safe_io, "HOST_IDENTITY_PATH", output)
    monkeypatch.setattr(
        safe_io,
        "_exact_file_bytes",
        lambda path, **_kwargs: (
            json.dumps(payload, sort_keys=True, separators=(",", ":")) + "\n"
        ).encode()
        if path == source
        else pytest.fail("output was read after mismatched metadata"),
    )
    metadata = {
        "project/project-id": b"attacker-project",
        "instance/id": b"4767392334132429161",
        "instance/name": b"omega-production-app",
        "instance/zone": b"projects/894064513501/zones/us-central1-a",
        "instance/service-accounts/default/email": (
            b"omega-production-app@omega-production.iam.gserviceaccount.com"
        ),
    }
    monkeypatch.setattr(
        safe_io,
        "_metadata_attribute",
        lambda path, maximum: metadata[path][:maximum],
    )
    with pytest.raises(SystemExit, match="differs from Terraform authority"):
        safe_io.main(
            [
                "host-identity-install",
                "--startup-contract",
                str(source),
                "--output",
                str(output),
            ]
        )
    assert not output.exists()


def test_startup_identities_emit_distinct_deploy_and_controller_refs(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    payload = _startup_contract()
    output = tmp_path / "startup.json"
    monkeypatch.setattr(sys, "stdin", _startup_input(payload))
    assert safe_io.main(["startup-config", "--output", str(output)]) == 0
    assert safe_io.main(["startup-identities", "--path", str(output)]) == 0
    assert capsys.readouterr().out == f"{'a' * 40}\t{'c' * 40}\n"

    output.chmod(0o644)
    with pytest.raises(SystemExit, match="exact private file"):
        safe_io.main(["startup-identities", "--path", str(output)])


def test_startup_predecessor_is_explicit_and_never_inferred(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    payload = _startup_contract()
    output = tmp_path / "startup.json"
    monkeypatch.setattr(sys, "stdin", _startup_input(payload))
    assert safe_io.main(["startup-config", "--output", str(output)]) == 0
    assert safe_io.main(["startup-predecessor", "--path", str(output)]) == 0
    assert capsys.readouterr().out == "none\n"

    payload["foundation_predecessor"] = {
        "marker_sha256": "d" * 64,
        "watchdog_state_sha256": "e" * 64,
        "deploy_ref": "f" * 40,
        "helper_ref": "1" * 40,
        "startup_config_sha256": "2" * 64,
    }
    output = tmp_path / "startup-with-predecessor.json"
    monkeypatch.setattr(sys, "stdin", _startup_input(payload))
    assert safe_io.main(["startup-config", "--output", str(output)]) == 0
    assert safe_io.main(["startup-predecessor", "--path", str(output)]) == 0
    assert capsys.readouterr().out == (
        f"{'d' * 64}\t{'e' * 64}\t{'f' * 40}\t{'1' * 40}\t{'2' * 64}\n"
    )


def _compose_services() -> list[str]:
    return [
        "console",
        "workspace",
        "refinement",
        "vault",
        "mcp-infra",
        "airflow",
        "replicon",
        "hubspot",
        "salesforce",
        "banxico",
        "inegi",
        "sec-edgar",
        "sap-hcm",
        "sap-successfactors",
        "sap-s4hana",
        "postgres",
        "postgres_gold",
        "redis",
        "minio",
        "mailhog",
        "superset",
        "airflow-scheduler",
        "airflow-init",
        "minio-init",
        "postgres_dev_seed",
        "superset-init",
    ]


def test_reboot_service_selector_starts_superset_but_never_scheduler_or_one_shots(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    inventory = tmp_path / "services"
    inventory.write_text("\n".join(_compose_services()) + "\n", encoding="utf-8")
    inventory.chmod(0o600)
    assert safe_io.main(["compose-reboot-services", "--path", str(inventory)]) == 0
    selected = capsys.readouterr().out.splitlines()
    assert len(selected) == 16
    assert "superset" in selected
    assert not set(selected) & {
        "airflow-scheduler",
        "airflow-init",
        "minio-init",
        "postgres_dev_seed",
        "superset-init",
    }


@pytest.mark.parametrize(
    "mutation", ["empty", "missing", "duplicate", "extra", "blank", "control"]
)
def test_reboot_service_selector_fails_closed_before_any_start(
    tmp_path: Path,
    mutation: str,
) -> None:
    services = _compose_services()
    if mutation == "empty":
        services = []
    elif mutation == "missing":
        services.remove("superset")
    elif mutation == "duplicate":
        services.append("console")
    elif mutation == "extra":
        services.append("attacker")
    elif mutation == "blank":
        services.insert(2, "")
    else:
        services[0] = "console\tattacker"
    inventory = tmp_path / "services"
    inventory.write_text(
        "\n".join(services) + ("\n" if services else ""), encoding="utf-8"
    )
    inventory.chmod(0o600)
    with pytest.raises(SystemExit):
        safe_io.main(["compose-reboot-services", "--path", str(inventory)])


@pytest.mark.parametrize(
    ("mode", "expected_rc", "expect_start"),
    [
        ("config-fails", 90, False),
        ("empty", 90, False),
        ("extra", 90, False),
        ("valid", 0, True),
    ],
)
def test_reboot_compose_producer_failure_can_never_broaden_start(
    tmp_path: Path, mode: str, expected_rc: int, expect_start: bool
) -> None:
    reboot = (
        Path(__file__).resolve().parents[1] / "scripts/gcp/reboot-runtime.sh"
    ).read_text(encoding="utf-8")
    function = reboot[
        reboot.index("start_exact_application_services() {") : reboot.index(
            "\n\nstart_exact_application_services",
            reboot.index("start_exact_application_services() {"),
        )
    ].replace("/run/omega-gcp-compose-services.XXXXXX", f"{tmp_path}/services.XXXXXX")
    compose = tmp_path / "compose"
    compose.write_text(
        """#!/bin/bash
set -eu
if [[ "$1" == "config" && "$2" == "--services" ]]; then
  [[ "$MODE" != "config-fails" ]] || exit 41
  [[ "$MODE" != "empty" ]] || exit 0
  printf '%s\\n' console workspace refinement vault mcp-infra airflow replicon hubspot salesforce banxico inegi sec-edgar sap-hcm sap-successfactors sap-s4hana postgres postgres_gold redis minio mailhog superset airflow-scheduler airflow-init minio-init postgres_dev_seed superset-init
  [[ "$MODE" != "extra" ]] || printf '%s\\n' attacker
  exit 0
fi
printf '%s\\n' "$*" >>"$START_LOG"
""",
        encoding="utf-8",
    )
    compose.chmod(0o700)
    selector = tmp_path / "selector"
    selector.write_text(
        f"""#!{sys.executable}
import sys
sys.path.insert(0, {str(Path(__file__).resolve().parents[1])!r})
from scripts.gcp import safe_io
safe_io._require_root = lambda: None
raise SystemExit(safe_io.main(sys.argv[1:]))
""",
        encoding="utf-8",
    )
    selector.chmod(0o700)
    start_log = tmp_path / "start.log"
    harness = f"""set -Eeuo pipefail
{function}
COMPOSE=({str(compose)!r})
SAFE_IO={str(selector)!r}
fail() {{ exit 90; }}
start_exact_application_services
"""
    result = subprocess.run(
        ["bash", "-c", harness],
        env={"MODE": mode, "START_LOG": str(start_log), "PATH": os.environ["PATH"]},
        text=True,
        capture_output=True,
        check=False,
    )
    assert result.returncode == expected_rc, result.stderr
    assert start_log.exists() is expect_start
    if expect_start:
        argv = start_log.read_text(encoding="utf-8").split()
        assert argv[:2] == ["start", "--"]
        assert argv[2:] == [
            name
            for name in _compose_services()
            if name
            in {
                "console",
                "workspace",
                "refinement",
                "vault",
                "mcp-infra",
                "airflow",
                "replicon",
                "hubspot",
                "salesforce",
                "banxico",
                "inegi",
                "sec-edgar",
                "sap-hcm",
                "sap-successfactors",
                "sap-s4hana",
                "superset",
            }
        ]


def test_system_python_executes_real_path_identity_subcommands(tmp_path: Path) -> None:
    """Exercise the host Python entrypoint, including the Ubuntu 3.9 APIs."""
    system_python = Path("/usr/bin/python3")
    if not system_python.is_file():
        pytest.skip("system Python is unavailable")
    repo = Path(__file__).resolve().parents[1]
    helper = repo / "scripts/gcp/safe_io.py"
    source = helper.read_text(encoding="utf-8")
    assert ".stat(follow_symlinks=False)" not in source

    env_file = tmp_path / "runtime.env"
    env_file.write_text("APP_ENV=production\n", encoding="utf-8")
    env_file.chmod(0o600)
    validated = subprocess.run(
        [system_python, "-I", helper, "env-validate", "--path", env_file],
        text=True,
        capture_output=True,
        check=False,
    )
    assert validated.returncode == 0, validated.stderr

    archive = _tar(
        tmp_path / "system-python.tar.gz",
        [("release/VERSION", None, b"1.45.207-beta\n", None)],
    )
    destination = tmp_path / "extracted"
    harness = "\n".join(
        (
            "import sys",
            f"sys.path.insert(0, {str(repo)!r})",
            "from scripts.gcp import safe_io",
            "safe_io._require_root = lambda: None",
            "raise SystemExit(safe_io.main(sys.argv[1:]))",
        )
    )
    extracted = subprocess.run(
        [
            system_python,
            "-I",
            "-c",
            harness,
            "safe-extract",
            "--archive",
            archive,
            "--destination",
            destination,
        ],
        text=True,
        capture_output=True,
        check=False,
    )
    assert extracted.returncode == 0, extracted.stderr
    assert (destination / "release/VERSION").read_bytes() == b"1.45.207-beta\n"


def test_durable_foundation_receipt_binds_boot_metadata_and_exact_evidence(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    deploy_ref = "a" * 40
    helper_ref = "b" * 40
    contract_sha = "c" * 64
    opt = tmp_path / "opt"
    shared = opt / "modecissions/shared"
    shared.mkdir(parents=True)
    opt.chmod(0o755)
    (opt / "modecissions").chmod(0o755)
    shared.chmod(0o755)
    marker = shared / "operation-state.json"
    marker.write_text(
        json.dumps(
            {
                "schema_version": 2,
                "operation": "foundation-ready",
                "state": "awaiting-runtime-authority",
                "deploy_ref": deploy_ref,
                "helper_ref": helper_ref,
                "updated_at": "2026-08-12T00:00:00+00:00",
                "startup_contract_sha256": contract_sha,
                "helper_sha256": {
                    name: format(index + 1, "x") * 64
                    for index, name in enumerate(
                        (
                            "bootstrap_runtime",
                            "safe_io",
                            "metadata_firewall",
                            "operation_gate",
                            "operation_watchdog",
                            "reboot_runtime",
                            "runtime_contract",
                        )
                    )
                },
            },
            sort_keys=True,
            separators=(",", ":"),
        )
        + "\n",
        encoding="utf-8",
    )
    marker.chmod(0o600)
    watchdog_state = tmp_path / "watchdog-state.json"
    watchdog_state.write_text('{"mode":"foundation"}\n', encoding="utf-8")
    watchdog_state.chmod(0o600)
    terminal = tmp_path / "terminal-state.json"
    terminal.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "terminal_state": "foundation-fenced",
                "deploy_ref": deploy_ref,
                "version": "",
                "helper_ref": helper_ref,
                "startup_contract_sha256": contract_sha,
                "completed_at": "2026-08-12T00:00:01+00:00",
                "foundation_marker_sha256": hashlib.sha256(
                    marker.read_bytes()
                ).hexdigest(),
                "foundation_watchdog_state_sha256": hashlib.sha256(
                    watchdog_state.read_bytes()
                ).hexdigest(),
            },
            sort_keys=True,
            separators=(",", ":"),
        )
        + "\n",
        encoding="utf-8",
    )
    terminal.chmod(0o600)
    boot_id = tmp_path / "boot_id"
    boot_id.write_text("12345678-1234-1234-1234-123456789abc\n", encoding="ascii")
    proc_stat = tmp_path / "stat"
    proc_stat.write_text("cpu 1 2 3 4\nbtime 1786460000\n", encoding="ascii")
    metadata = {
        "instance/attributes/startup-script": b"#!/bin/bash -p\nexit 0\n",
        "instance/id": b"123456789",
        "instance/zone": b"projects/894064513501/zones/us-central1-a",
    }
    monkeypatch.setattr(safe_io, "CANONICAL_OPT_ROOT", opt)
    monkeypatch.setattr(safe_io, "CANONICAL_EVIDENCE_ROOT", shared)
    monkeypatch.setattr(safe_io, "WATCHDOG_STATE", watchdog_state)
    monkeypatch.setattr(safe_io, "PROC_BOOT_ID", boot_id)
    monkeypatch.setattr(safe_io, "PROC_STAT", proc_stat)
    monkeypatch.setattr(
        safe_io, "_metadata_attribute", lambda path, **_kwargs: metadata[path]
    )
    args = argparse.Namespace(
        terminal_receipt=str(terminal),
        operation_marker=str(marker),
        deploy_ref=deploy_ref,
        helper_ref=helper_ref,
        startup_contract_sha256=contract_sha,
    )

    expected_value = safe_io._foundation_receipt_payload(args)
    receipts = shared / "foundation-receipts"
    receipts.mkdir(mode=0o700)
    interrupted = receipts / f".{helper_ref}.tmp"
    interrupted.write_text(
        json.dumps(expected_value, sort_keys=True, separators=(",", ":")) + "\n",
        encoding="utf-8",
    )
    interrupted.chmod(0o600)
    assert safe_io.command_foundation_receipt_publish(args) == 0
    first = json.loads(capsys.readouterr().out)
    receipt = shared / "foundation-receipts" / f"{helper_ref}.json"
    assert not interrupted.exists()
    first_raw = receipt.read_bytes()
    assert first["sha256"] == hashlib.sha256(first_raw).hexdigest()
    assert stat.S_IMODE(receipt.stat().st_mode) == 0o600
    value = json.loads(first_raw)
    assert (
        value["live_startup_script_sha256"]
        == hashlib.sha256(metadata["instance/attributes/startup-script"]).hexdigest()
    )
    assert value["boot_id"] == "12345678-1234-1234-1234-123456789abc"

    os.link(receipt, interrupted)
    assert receipt.stat().st_nlink == 2
    assert safe_io.command_foundation_receipt_publish(args) == 0
    assert not interrupted.exists()
    assert receipt.stat().st_nlink == 1
    assert receipt.read_bytes() == first_raw
    capsys.readouterr()
    collect = argparse.Namespace(
        path=str(receipt),
        deploy_ref=deploy_ref,
        helper_ref=helper_ref,
        startup_contract_sha256=contract_sha,
    )
    assert safe_io.command_foundation_receipt_collect(collect) == 0
    projection = json.loads(capsys.readouterr().out)
    assert projection["receipt"] == value
    assert projection["sha256"] == hashlib.sha256(first_raw).hexdigest()

    boot_id.write_text("abcdefab-cdef-abcd-efab-cdefabcdefab\n", encoding="ascii")
    with pytest.raises(SystemExit, match="current boot"):
        safe_io.command_foundation_receipt_collect(collect)
    boot_id.write_text("12345678-1234-1234-1234-123456789abc\n", encoding="ascii")

    for field, replacement in (
        ("schema_version", True),
        ("boot_started_epoch", True),
        ("instance_id", 123456789),
        ("startup_contract_sha256", int(contract_sha, 16)),
        ("completed_at", 1),
    ):
        mutation = dict(value)
        mutation[field] = replacement
        with pytest.raises(SystemExit, match="receipt"):
            safe_io._validate_foundation_receipt(
                mutation,
                deploy_ref=deploy_ref,
                helper_ref=helper_ref,
                startup_contract_sha256=contract_sha,
            )

    watchdog_state.write_text('{"mode":"tampered"}\n', encoding="utf-8")
    with pytest.raises(SystemExit, match="live evidence differs"):
        safe_io.command_foundation_receipt_collect(collect)
    with pytest.raises(SystemExit, match="foundation evidence differs"):
        safe_io.command_foundation_receipt_publish(args)
