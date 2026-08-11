#!/usr/bin/python3 -I
"""Terminate and verify the exact PostgreSQL sessions owned by one migration."""

from __future__ import annotations

import argparse
import os
import re
import subprocess


_CONTAINER_ID = re.compile(r"^[0-9a-f]{64}$")
_RUN_ID = re.compile(r"^omega_migration_[1-9][0-9]{0,19}_[1-9][0-9]{0,29}$")
_TERMINATE_SQL = """\
SELECT pg_terminate_backend(pid)
  FROM pg_catalog.pg_stat_activity
 WHERE application_name = :'run_id'
   AND pid <> pg_backend_pid();
"""
_VERIFY_SQL = """\
SELECT count(*)
  FROM pg_catalog.pg_stat_activity
 WHERE application_name = :'run_id'
   AND pid <> pg_backend_pid();
"""


def _die(message: str) -> None:
    raise SystemExit(f"migration backend control: {message}")


def _psql_command(container_id: str, port: int, run_id: str) -> list[str]:
    return [
        "docker",
        "exec",
        "-i",
        container_id,
        "psql",
        "-X",
        "-v",
        "ON_ERROR_STOP=1",
        "-h",
        "/var/run/postgresql",
        "-p",
        str(port),
        "-U",
        "postgres",
        "-d",
        "postgres",
        "-At",
        "-v",
        f"run_id={run_id}",
    ]


def _run_psql(container_id: str, port: int, run_id: str, sql: str) -> bytes:
    try:
        result = subprocess.run(
            _psql_command(container_id, port, run_id),
            input=sql.encode("utf-8"),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
            timeout=30,
        )
    except (OSError, subprocess.TimeoutExpired):
        _die("the exact container session query could not complete")
    if result.returncode != 0 or len(result.stdout) > 65_536:
        _die("the exact container session query failed")
    return result.stdout


def terminate_and_verify(container_ids: tuple[str, str], run_id: str) -> None:
    if len(set(container_ids)) != 2 or any(
        _CONTAINER_ID.fullmatch(value) is None for value in container_ids
    ):
        _die("exactly two distinct full container IDs are required")
    if _RUN_ID.fullmatch(run_id) is None:
        _die("migration run identity is invalid")
    for container_id, port in zip(container_ids, (5432, 5433)):
        _run_psql(container_id, port, run_id, _TERMINATE_SQL)
        if _run_psql(container_id, port, run_id, _VERIFY_SQL) != b"0\n":
            _die("a migration backend remains active")


def main() -> int:
    if not __name__ == "__main__" or not os.path.isabs(__file__):
        _die("helper execution context is invalid")
    parser = argparse.ArgumentParser()
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--container-id", action="append", required=True)
    args = parser.parse_args()
    if len(args.container_id) != 2:
        _die("exactly two distinct full container IDs are required")
    terminate_and_verify(tuple(args.container_id), args.run_id)
    print("migration backends terminated and verified")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
