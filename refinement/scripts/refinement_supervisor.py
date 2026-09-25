from __future__ import annotations

import grp
import os
from pathlib import Path
import pwd
import signal
import subprocess
import sys
import time


APP_USER = "refinement-app"
VERIFIER_USER = "refinement-verifier"
RUNTIME_GROUP = "omega-refinement"
SOCKET_PATH = "/run/omega/publication-verifier.sock"
SPILL_DIR = os.environ.get("DUCKDB_TEMP_DIRECTORY", "/var/lib/omega/duckdb-spill")


def _verifier_startup_timeout_seconds() -> float:
    raw = os.environ.get("PUBLICATION_VERIFIER_STARTUP_TIMEOUT_SECONDS", "60")
    try:
        timeout = float(raw)
    except ValueError as exc:
        raise RuntimeError(
            "publication verifier startup timeout must be between 1 and 120 seconds"
        ) from exc
    if not 1 <= timeout <= 120:
        raise RuntimeError(
            "publication verifier startup timeout must be between 1 and 120 seconds"
        )
    return timeout


def _identity(name: str) -> tuple[int, int]:
    entry = pwd.getpwnam(name)
    return entry.pw_uid, entry.pw_gid


def _drop(uid: int, gid: int):
    def apply() -> None:
        os.setgroups([gid])
        os.setgid(gid)
        os.setuid(uid)

    return apply


def _read_verifier_dsn() -> str:
    path = Path(
        os.environ.get(
            "GOLD_VERIFIER_DATABASE_URL_FILE",
            "/run/secrets/gold_verifier_database_url",
        )
    )
    stat = path.stat()
    if stat.st_mode & 0o077:
        raise RuntimeError("publication verifier secret permissions are unsafe")
    value = path.read_text(encoding="utf-8").strip()
    if not value.startswith(("postgresql://", "postgresql+psycopg2://")):
        raise RuntimeError("publication verifier secret is unavailable")
    return value


def _child_env(*, verifier: bool, verifier_dsn: str = "") -> dict[str, str]:
    env = dict(os.environ)
    env.pop("GOLD_VERIFIER_DATABASE_URL_FILE", None)
    env.pop("GOLD_VERIFIER_DATABASE_URL", None)
    if verifier:
        env.pop("DATABASE_URL", None)
        env.pop("GOLD_DATABASE_URL", None)
        env.pop("GOLD_PUBLISHER_DATABASE_URL", None)
        env["GOLD_VERIFIER_DATABASE_URL"] = verifier_dsn
        env["HOME"] = f"/home/{VERIFIER_USER}"
    else:
        env["HOME"] = f"/home/{APP_USER}"
    env["PUBLICATION_VERIFIER_SOCKET"] = SOCKET_PATH
    return env


def _exec_unprivileged(command: list[str]) -> None:
    uid, gid = _identity(APP_USER)
    env = _child_env(verifier=False)
    os.setgroups([gid])
    os.setgid(gid)
    os.setuid(uid)
    os.environ.clear()
    os.environ.update(env)
    os.execvp(command[0], command)


def _terminate(process: subprocess.Popen) -> None:
    if process.poll() is None:
        process.terminate()
        try:
            process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=5)


def supervise(command: list[str]) -> int:
    if os.geteuid() != 0:
        raise RuntimeError("Refinement supervisor requires root bootstrap authority")
    if not command or command[0] != "uvicorn":
        _exec_unprivileged(command or ["sh"])
    verifier_dsn = _read_verifier_dsn()
    app_uid, app_gid = _identity(APP_USER)
    verifier_uid, verifier_gid = _identity(VERIFIER_USER)
    if app_gid != verifier_gid or app_gid != grp.getgrnam(RUNTIME_GROUP).gr_gid:
        raise RuntimeError("Refinement process identities are inconsistent")
    runtime = Path(SOCKET_PATH).parent
    runtime.mkdir(parents=True, exist_ok=True)
    os.chown(runtime, verifier_uid, verifier_gid)
    os.chmod(runtime, 0o770)  # nosec B103

    spill = Path(SPILL_DIR)
    spill.mkdir(parents=True, exist_ok=True)
    os.chown(spill, app_uid, app_gid)
    os.chmod(spill, 0o2770)  # nosec B103 -- setgid: new files inherit the group
    Path(SOCKET_PATH).unlink(missing_ok=True)
    verifier_env = _child_env(verifier=True, verifier_dsn=verifier_dsn)
    verifier_env["PUBLICATION_APP_UID"] = str(app_uid)
    verifier_startup_timeout = _verifier_startup_timeout_seconds()
    verifier = subprocess.Popen(
        [sys.executable, "-m", "app.publication_verifier_worker"],
        env=verifier_env,
        preexec_fn=_drop(verifier_uid, verifier_gid),
    )
    deadline = time.monotonic() + verifier_startup_timeout
    while not Path(SOCKET_PATH).exists():
        if verifier.poll() is not None or time.monotonic() >= deadline:
            _terminate(verifier)
            raise RuntimeError("publication verifier failed to start")
        time.sleep(0.05)
    app = subprocess.Popen(
        command,
        env=_child_env(verifier=False),
        preexec_fn=_drop(app_uid, app_gid),
    )

    def stop(_signal, _frame) -> None:
        _terminate(app)
        _terminate(verifier)

    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    try:
        while app.poll() is None and verifier.poll() is None:
            time.sleep(0.2)
    finally:
        _terminate(app)
        _terminate(verifier)
    return app.returncode if app.returncode is not None else 1


if __name__ == "__main__":
    raise SystemExit(supervise(sys.argv[1:]))
