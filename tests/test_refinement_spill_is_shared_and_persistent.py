"""The DuckDB spill directory is shared by two users and must outlive the container.

On 2026-09-23 every materialization in production failed. The visible error was
`publication verifier unavailable`, then `Broken pipe`; the real one appeared
only in the container log:

    publication verification rejected: PermissionError

refinement runs two processes with different uids that share a group:
`refinement-app` (1000) and `refinement-verifier` (1001). The spill directory
was created by whichever touched it first -- the app, via os.makedirs -- and
landed 0755 owned by that user, so the verifier could not write into it and
every publication was rejected.

Two properties follow, and they are independent:

  * the directory belongs to the shared group, setgid, so neither process can
    lock the other out and new files inherit the group; and
  * it is a mounted volume, not a path in the container's writable layer, so a
    spill in flight cannot be swept away by anything that cleans temporary
    paths, and a recreate or a host reboot does not lose it.

The size ceiling is a separate guarantee and is asserted here too: the spill
shares a filesystem with the Postgres data directories, so an uncapped spill is
a disk-full outage for the whole stack, not just for refinement.
"""

from __future__ import annotations

import re
from pathlib import Path

import yaml

REPO = Path(__file__).resolve().parents[1]
AWS_COMPOSE = REPO / "infra" / "terraform" / "deploy" / "docker-compose.aws.yml"
SUPERVISOR = REPO / "refinement" / "scripts" / "refinement_supervisor.py"

VOLUME_NAME = "refinement_spill"


def _refinement() -> dict:
    doc = yaml.safe_load(AWS_COMPOSE.read_text(encoding="utf-8"))
    return doc["services"]["refinement"], doc


def _spill_default() -> str:
    service, _ = _refinement()
    raw = str(service["environment"]["DUCKDB_TEMP_DIRECTORY"])
    return raw.split(":-", 1)[1].rstrip("}")


# ── persistence ────────────────────────────────────────────────────────────


def test_spill_is_a_named_volume_not_the_writable_layer() -> None:
    service, doc = _refinement()
    target = _spill_default()

    mounts = [str(v) for v in (service.get("volumes") or []) if isinstance(v, str)]
    assert any(m.endswith(f":{target}") for m in mounts), (
        f"{target} is not backed by a volume. In the container's writable layer "
        "a spill is lost on recreate and can be swept mid-materialization."
    )
    assert VOLUME_NAME in (doc.get("volumes") or {}), (
        f"{VOLUME_NAME} is mounted but never declared, so compose would treat it "
        "as anonymous and a recreate would silently start from an empty one"
    )


def test_spill_is_not_under_tmp() -> None:
    """/tmp is exactly the path a system cleaner is entitled to empty."""
    target = _spill_default()

    assert not target.startswith("/tmp/"), (
        f"the spill default is {target}; anything under /tmp may be reclaimed "
        "while a materialization is using it"
    )
    assert target.startswith("/"), "DuckDB rejects a relative temp_directory"


# ── shared access ──────────────────────────────────────────────────────────


def test_supervisor_prepares_the_spill_for_both_processes() -> None:
    """Root prepares it once, before either unprivileged process starts."""
    source = SUPERVISOR.read_text(encoding="utf-8")

    assert "SPILL_DIR" in source, "the supervisor does not know about the spill"
    assert re.search(r"os\.chmod\(\s*spill,\s*0o2770", source), (
        "the spill is not setgid 0o2770. Without setgid, files one process "
        "creates are unreadable to the other and the group is not inherited."
    )
    assert re.search(r"os\.chown\(\s*spill,\s*app_uid,\s*app_gid", source), (
        "the spill is not chowned to the shared runtime group"
    )


def test_spill_is_prepared_before_either_child_starts() -> None:
    """Order matters: a fresh volume arrives root-owned 0755."""
    source = SUPERVISOR.read_text(encoding="utf-8")
    spill_at = source.index("spill.mkdir(")
    verifier_at = source.index("app.publication_verifier_worker")

    assert spill_at < verifier_at, (
        "the verifier is started before the spill directory is prepared; it "
        "would inherit a directory it cannot write to"
    )


# ── the ceiling is a separate guarantee ────────────────────────────────────


def test_the_size_ceiling_still_applies_to_the_new_location() -> None:
    """Moving the directory must not quietly drop its cap.

    The spill shares a filesystem with the Postgres data directories. Uncapped,
    DuckDB will use ~90% of it and take the whole stack down with a full disk.
    """
    service, _ = _refinement()
    raw = str(service["environment"]["DUCKDB_MAX_TEMP_DIRECTORY_SIZE"])
    default = raw.split(":-", 1)[1].rstrip("}")

    assert default, "DUCKDB_MAX_TEMP_DIRECTORY_SIZE has no default"
    match = re.fullmatch(r"(\d+(?:\.\d+)?)\s*([KMGT]i?B)", default, re.IGNORECASE)
    assert match, f"the spill cap {default!r} is not a DuckDB size literal"
    assert float(match.group(1)) > 0, "the spill cap is zero"


def test_memory_limit_and_spill_are_both_declared_by_the_service() -> None:
    """Neither may fall back to the shared .env, which a re-render can empty."""
    service, _ = _refinement()
    env = service["environment"]

    for key in ("DUCKDB_MEMORY_LIMIT", "DUCKDB_TEMP_DIRECTORY", "DUCKDB_MAX_TEMP_DIRECTORY_SIZE"):
        assert key in env, f"{key} is not declared by the service"
        assert ":-" in str(env[key]), f"{key} has no default of its own"
