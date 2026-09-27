from __future__ import annotations

import re
import shlex
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
CARTRIDGE_DOCKERFILES = sorted((REPO / "cartridges").glob("*/Dockerfile"))
SERVICE_DOCKERFILES = [
    REPO / name / "Dockerfile" for name in ("console", "mcp-infra", "refinement", "vault", "workspace")
]
# Service images that still ship single-stage builds or code owned by the runtime
# user. They are follow-up work; the list must shrink as each one is fixed.
FOLLOW_UP_EXCEPTIONS = {
    "console/Dockerfile",
    "mcp-infra/Dockerfile",
    "refinement/Dockerfile",
    "vault/Dockerfile",
    "workspace/Dockerfile",
}
SQL_CARTRIDGES = {"hubspot", "replicon", "salesforce", "sap_b1", "sap_hcm", "sap_s4hana", "sap_successfactors"}
FORBIDDEN_RUNTIME_PACKAGES = ("curl", "wget", "build-essential", "gcc", "g++", "make", "libpq-dev")


def _instructions(path: Path) -> list[tuple[str, str]]:
    text = path.read_text(encoding="utf-8").replace("\\\n", " ")
    rows = []
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        keyword, _, rest = stripped.partition(" ")
        rows.append((keyword.upper(), rest.strip()))
    return rows


def _final_stage(path: Path) -> list[tuple[str, str]]:
    rows = _instructions(path)
    starts = [index for index, (keyword, _) in enumerate(rows) if keyword == "FROM"]
    return rows[starts[-1] :]


def _violations(path: Path) -> list[str]:
    rows = _instructions(path)
    final = _final_stage(path)
    found = []
    if sum(1 for keyword, _ in rows if keyword == "FROM") < 2:
        found.append("single-stage build")
    for keyword, value in final:
        if keyword == "RUN":
            installs = re.findall(r"apt-get install\s+(.*?)(?:&&|$)", value)
            for install in installs:
                packages = [token for token in shlex.split(install) if not token.startswith("-")]
                for package in FORBIDDEN_RUNTIME_PACKAGES:
                    if package in packages:
                        found.append(f"runtime stage installs {package}")
            if re.search(r"chown\s+-R\b.*\s/app\b", value):
                found.append("runtime stage chowns /app")
        if keyword == "COPY":
            chown = re.search(r"--chown=(\S+)", value)
            if chown and chown.group(1).split(":")[0] not in {"root", "0"}:
                found.append(f"code copied as {chown.group(1)}")
    users = [value for keyword, value in final if keyword == "USER"]
    if not users or users[-1].split(":")[0] in {"root", "0"}:
        found.append("runtime user is root")
    return found


def _rel(path: Path) -> str:
    return path.relative_to(REPO).as_posix()


@pytest.mark.parametrize("path", CARTRIDGE_DOCKERFILES, ids=_rel)
def test_cartridge_images_are_multi_stage_non_root_with_root_owned_code(path):
    assert _violations(path) == []


@pytest.mark.parametrize("path", SERVICE_DOCKERFILES, ids=_rel)
def test_service_images_comply_or_are_declared_follow_ups(path):
    violations = _violations(path)
    if _rel(path) in FOLLOW_UP_EXCEPTIONS:
        assert violations, f"{_rel(path)} complies now: drop it from FOLLOW_UP_EXCEPTIONS"
    else:
        assert violations == []


@pytest.mark.parametrize("path", CARTRIDGE_DOCKERFILES, ids=_rel)
def test_cartridge_images_build_from_the_repo_root_with_shared_packages(path):
    cartridge = path.parent.name
    final = _final_stage(path)
    copies = [value for keyword, value in final if keyword == "COPY"]
    env = " ".join(value for keyword, value in final if keyword == "ENV")
    assert f"--chown=root:root cartridges/{cartridge}/app/ app/" in copies
    assert "--chown=root:root omega_lakehouse/ omega_lakehouse/" in copies
    assert ("--chown=root:root omega_cartridge_kit/ omega_cartridge_kit/" in copies) == (
        cartridge in SQL_CARTRIDGES
    )
    assert "--from=builder /install /usr/local" in copies
    assert "HOME=/home/appuser" in env
    builder = _instructions(path)[: len(_instructions(path)) - len(final)]
    assert any(
        keyword == "RUN" and "pip install --prefix=/install" in value and "--only-binary=:all:" in value
        for keyword, value in builder
    )
    assert (f"cartridges/{cartridge}/requirements.txt requirements.txt") in " ".join(
        value for keyword, value in builder if keyword == "COPY"
    )


@pytest.mark.parametrize(
    "path",
    [path for path in CARTRIDGE_DOCKERFILES if path.parent.name in SQL_CARTRIDGES],
    ids=_rel,
)
def test_duckdb_extensions_install_into_the_runtime_users_home(path):
    final = _final_stage(path)
    user_index = next(i for i, (keyword, value) in enumerate(final) if keyword == "USER" and value == "appuser")
    install_index = next(
        i for i, (keyword, value) in enumerate(final) if keyword == "RUN" and "INSTALL httpfs;" in value
    )
    assert user_index < install_index
    assert "INSTALL aws;" in final[install_index][1]


def test_policy_detects_the_regressions_it_guards_against(tmp_path):
    bad = tmp_path / "Dockerfile"
    bad.write_text(
        "FROM python:3.12-slim\n"
        "RUN apt-get update && apt-get install -y --no-install-recommends \\\n"
        "    ca-certificates curl build-essential && rm -rf /var/lib/apt/lists/*\n"
        "COPY --chown=appuser:appuser app /app/app\n"
        "RUN adduser appuser && chown -R appuser:appuser /app\n",
        encoding="utf-8",
    )
    violations = _violations(bad)
    for expected in (
        "single-stage build",
        "runtime stage installs curl",
        "runtime stage installs build-essential",
        "code copied as appuser:appuser",
        "runtime stage chowns /app",
        "runtime user is root",
    ):
        assert expected in violations
