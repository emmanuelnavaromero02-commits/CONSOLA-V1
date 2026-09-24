"""Pin ``import app`` to the SAP Business One cartridge and provide the
Business One-shaped Postgres test bed to the cartridge tests."""
from __future__ import annotations

import importlib
import importlib.util
import os
import subprocess
import sys
import time
import uuid
from datetime import date
from pathlib import Path

import pytest

_CARTRIDGE_ROOT = os.path.dirname(os.path.dirname(__file__))
REPO_ROOT = Path(_CARTRIDGE_ROOT).resolve().parents[1]
FIXTURE_DIR = REPO_ROOT / "tests" / "fixtures" / "sap_b1"
POSTGRES_IMAGE = os.getenv("SAP_B1_FAKE_POSTGRES_IMAGE", "postgres:15")
POSTGRES_PASSWORD = "test_sap_b1_fake_password"

os.environ.setdefault("APP_ENV", "test")
os.environ.setdefault("INTERNAL_API_KEY", "test-secret-key-not-default")
os.environ.setdefault("SECURITY_CONTEXT_SIGNING_KEY", "test-security-context-signing-key-12345")
os.environ.setdefault("DATABASE_URL", "postgresql+psycopg2://test:test@postgres:5432/modecissions")
os.environ.setdefault("MINIO_ACCESS_KEY", "test-minio-access")
os.environ.setdefault("MINIO_SECRET_KEY", "test-minio-secret")
# protection_service requires a valid Fernet key at import time; set it here so
# every test file in this directory can safely import app.* modules.
try:
    from cryptography.fernet import Fernet

    os.environ.setdefault("FIELD_ENCRYPTION_KEY", Fernet.generate_key().decode())
except ImportError:
    pass


def _use_this_cartridge() -> None:
    if _CARTRIDGE_ROOT in sys.path:
        sys.path.remove(_CARTRIDGE_ROOT)
    sys.path.insert(0, _CARTRIDGE_ROOT)
    for name in [n for n in sys.modules if n == "app" or n.startswith("app.")]:
        del sys.modules[name]


_use_this_cartridge()


@pytest.fixture(autouse=True)
def _isolate_cartridge_app():
    _use_this_cartridge()
    yield


# ── the Business One-shaped fake ───────────────────────────────────────────


def load_fixture_package():
    """Import tests/fixtures/sap_b1 as a package without touching sys.path globally."""
    name = "sap_b1_fake"
    if name in sys.modules:
        return sys.modules[name]
    spec = importlib.util.spec_from_file_location(
        name, FIXTURE_DIR / "__init__.py", submodule_search_locations=[str(FIXTURE_DIR)]
    )
    package = importlib.util.module_from_spec(spec)
    sys.modules[name] = package
    spec.loader.exec_module(package)
    return package


load_fixture_package()
b1_schema = importlib.import_module("sap_b1_fake.schema")
b1_generator = importlib.import_module("sap_b1_fake.generator")
b1_loader = importlib.import_module("sap_b1_fake.loader")


@pytest.fixture(scope="session")
def dataset():
    return b1_generator.generate(seed=7, start_month=date(2024, 10, 1), months=24)


def _docker(*args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    result = subprocess.run(["docker", *args], check=False, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    if check and result.returncode != 0:
        raise RuntimeError(f"docker {' '.join(args)} failed:\n{result.stdout}\n{result.stderr}")
    return result


def _mapped_port(container_id: str) -> int:
    for _ in range(60):
        result = _docker("port", container_id, "5432/tcp", check=False)
        if result.returncode == 0 and result.stdout.strip():
            return int(result.stdout.strip().splitlines()[0].rsplit(":", 1)[1])
        time.sleep(0.5)
    raise RuntimeError("postgres port was not published")


def _wait_ready(dsn: str) -> None:
    import psycopg2

    last: Exception | None = None
    for _ in range(120):
        try:
            with psycopg2.connect(dsn) as conn:
                with conn.cursor() as cur:
                    cur.execute("SELECT 1")
            return
        except Exception as exc:  # noqa: BLE001 - startup race, retried
            last = exc
            time.sleep(0.5)
    raise RuntimeError(f"postgres never became ready: {last}")


@pytest.fixture(scope="session")
def fake_postgres(dataset):
    """A postgres:15 container loaded with the fake; yields connection facts."""
    if _docker("info", check=False).returncode != 0:
        pytest.skip("Docker is required to run the Postgres-backed B1 fake")
    if _docker("image", "inspect", POSTGRES_IMAGE, check=False).returncode != 0:
        pytest.skip(f"{POSTGRES_IMAGE} is not present locally (docker pull it first)")
    name = f"consola-sap-b1-cartridge-{uuid.uuid4().hex[:12]}"
    container = _docker(
        "run", "--pull=never", "-d", "--rm", "--name", name,
        "-e", "POSTGRES_DB=b1fake", "-e", "POSTGRES_USER=postgres", "-e", f"POSTGRES_PASSWORD={POSTGRES_PASSWORD}",
        "-P", POSTGRES_IMAGE,
    ).stdout.strip()
    try:
        port = _mapped_port(container)
        dsn = f"postgresql://postgres:{POSTGRES_PASSWORD}@127.0.0.1:{port}/b1fake"
        _wait_ready(dsn)
        b1_loader.load(dsn, dataset)
        yield {
            "dsn": dsn,
            "host": "127.0.0.1",
            "port": port,
            "user": "postgres",
            "password": POSTGRES_PASSWORD,
            "database": "b1fake",
            "companies": ",".join(f"{c.alias}={c.schema}" for c in dataset.companies),
        }
    finally:
        _docker("rm", "-f", container, check=False)


@pytest.fixture
def b1_env(fake_postgres, monkeypatch):
    """Point the cartridge at the fake through the same variables production uses."""
    monkeypatch.setenv("SAP_B1_DIALECT", "postgres")
    monkeypatch.setenv("SAP_B1_HOST", fake_postgres["host"])
    monkeypatch.setenv("SAP_B1_PORT", str(fake_postgres["port"]))
    monkeypatch.setenv("SAP_B1_USER", fake_postgres["user"])
    monkeypatch.setenv("SAP_B1_PASSWORD", fake_postgres["password"])
    monkeypatch.setenv("SAP_B1_DATABASE", fake_postgres["database"])
    monkeypatch.setenv("SAP_B1_COMPANIES", fake_postgres["companies"])
    return fake_postgres
