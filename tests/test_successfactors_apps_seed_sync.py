"""Keep migration 87 immutable and reconcile current apps at runtime."""

from __future__ import annotations

import asyncio
import hashlib
import json
import re
import shutil
import subprocess
import sys
from pathlib import Path

from console.app.services import seed_packaged_apps as packaged_apps


REPO = Path(__file__).resolve().parents[1]
SEED = REPO / "infra" / "init" / "87_sap_successfactors_apps_seed.sql"
FORWARD = REPO / "infra" / "init" / "99zzv_sap_successfactors_apps_secure_refresh.sql"
APPS_DIR = REPO / "cartridges" / "sap_successfactors" / "apps"
BASELINE_MANIFEST = (
    REPO
    / "infra"
    / "migrations"
    / "manifests"
    / ("gcp-live-6b12883c5b5ea0537120279ccbee4947137998a2.json")
)
RELEASE_MANIFEST = REPO / "infra" / "migrations" / "manifests" / "v1.45.207-beta.json"
IMMUTABLE_SEED_SHA256 = (
    "bbd5407ca36c32aa8efd6e4c8d94190d4fd80ea5c867e831a88f54a40887845d"
)
APP_NAMES = (
    "sap_successfactors_workforce_overview",
    "sap_successfactors_talent_health",
)


class _Connection:
    def __init__(self) -> None:
        self.calls: list[tuple[str, tuple[object, ...]]] = []

    async def execute(self, sql: str, *args: object) -> None:
        self.calls.append((sql, args))


class _Acquire:
    def __init__(self, connection: _Connection) -> None:
        self.connection = connection

    async def __aenter__(self) -> _Connection:
        return self.connection

    async def __aexit__(self, *_args: object) -> None:
        return None


class _Pool:
    def __init__(self, connection: _Connection) -> None:
        self.connection = connection

    def acquire(self) -> _Acquire:
        return _Acquire(self.connection)


def test_historical_seed_is_byte_exact_in_both_migration_locks() -> None:
    checksum = hashlib.sha256(SEED.read_bytes()).hexdigest()
    assert checksum == IMMUTABLE_SEED_SHA256
    for path in (BASELINE_MANIFEST, RELEASE_MANIFEST):
        manifest = json.loads(path.read_text(encoding="utf-8"))
        assert (
            manifest["databases"]["operational"]["87_sap_successfactors_apps_seed.sql"]
            == IMMUTABLE_SEED_SHA256
        )


def test_runtime_seed_upserts_current_packaged_html(
    tmp_path: Path, monkeypatch
) -> None:
    registry = tmp_path / "registry"
    runtime_apps = registry / "sap_successfactors" / "apps"
    runtime_apps.mkdir(parents=True)
    for name in APP_NAMES:
        shutil.copy2(APPS_DIR / f"{name}.html", runtime_apps / f"{name}.html")
        shutil.copy2(APPS_DIR / f"{name}.json", runtime_apps / f"{name}.json")
    monkeypatch.setattr(packaged_apps, "_REGISTRY", registry)

    connection = _Connection()
    asyncio.run(packaged_apps.seed_packaged_apps(_Pool(connection)))
    upserts = {
        args[0]: (sql, args)
        for sql, args in connection.calls
        if sql.lstrip().startswith("INSERT INTO analytic_apps")
    }
    assert set(upserts) == set(APP_NAMES)
    for name in APP_NAMES:
        sql, args = upserts[name]
        sidecar = json.loads((APPS_DIR / f"{name}.json").read_text(encoding="utf-8"))
        assert "ON CONFLICT (name) DO UPDATE" in sql
        assert args[2] == (APPS_DIR / f"{name}.html").read_text(encoding="utf-8")
        assert args[5] == sorted(sidecar["datasets_used"])


def test_historical_seed_registers_exactly_the_two_published_apps() -> None:
    sql = SEED.read_text(encoding="utf-8")
    inserted = re.findall(r"VALUES \(\$seed\$([a-z_]+)\$seed\$", sql)
    assert inserted == list(APP_NAMES)


def test_forward_migration_is_generated_from_exact_packaged_html() -> None:
    subprocess.run(
        [sys.executable, "scripts/generate_successfactors_apps_seed.py", "--check"],
        cwd=REPO,
        check=True,
    )
    sql = FORWARD.read_text(encoding="utf-8")
    release = json.loads(RELEASE_MANIFEST.read_text(encoding="utf-8"))
    assert (
        hashlib.sha256(FORWARD.read_bytes()).hexdigest()
        == release["databases"]["operational"][FORWARD.name]
    )
    assert hashlib.sha256(SEED.read_bytes()).hexdigest() == IMMUTABLE_SEED_SHA256
    for name in APP_NAMES:
        html = (APPS_DIR / f"{name}.html").read_text(encoding="utf-8")
        delimiter = f"$omega_{name}$"
        assert f"{delimiter}{html}{delimiter}" in sql
        assert hashlib.sha256(html.encode("utf-8")).hexdigest() in sql
    assert "public.digest(convert_to(app.html, 'UTF8'), 'sha256')" in sql
    assert "manifest.html_sha256 IS DISTINCT FROM expected.html_sha256" in sql
    assert "scope_status = 'platform_template'" in sql
