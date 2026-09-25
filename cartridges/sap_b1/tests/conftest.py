from __future__ import annotations

import importlib
import importlib.util
import os
import secrets
import shutil
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
MSSQL_IMAGE = os.getenv("SAP_B1_FAKE_MSSQL_IMAGE", "mcr.microsoft.com/mssql/server:2022-latest")
MSSQL_ODBC_DRIVER = "ODBC Driver 18 for SQL Server"
MSSQL_READER = "omega_b1_reader"
MSSQL_UNAVAILABLE = (
    "SQL Server fake unavailable: needs Docker, the mssql/server:2022-latest image, pyodbc and ODBC Driver 18"
)
DOCKER_NETWORK = os.getenv("SAP_B1_FAKE_DOCKER_NETWORK", "").strip()

os.environ.setdefault("APP_ENV", "test")
os.environ.setdefault("INTERNAL_API_KEY", "test-secret-key-not-default")
os.environ.setdefault("SECURITY_CONTEXT_SIGNING_KEY", "test-security-context-signing-key-12345")
os.environ.setdefault("DATABASE_URL", "postgresql+psycopg2://test:test@postgres:5432/modecissions")
os.environ.setdefault("MINIO_ACCESS_KEY", "test-minio-access")
os.environ.setdefault("MINIO_SECRET_KEY", "test-minio-secret")
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


def load_fixture_package():
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
b1_mssql_loader = importlib.import_module("sap_b1_fake.mssql_loader")


@pytest.fixture(scope="session")
def dataset():
    return b1_generator.generate(seed=7, start_month=date(2024, 10, 1), months=24)


def _docker(*args: str, check: bool = True, env: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
    if shutil.which("docker") is None:
        return subprocess.CompletedProcess(["docker", *args], returncode=127, stdout="", stderr="docker: not found")
    result = subprocess.run(
        ["docker", *args], check=False, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=env
    )
    if check and result.returncode != 0:
        raise RuntimeError(f"docker {' '.join(args)} failed:\n{result.stdout}\n{result.stderr}")
    return result


def _mapped_port(container_id: str, container_port: str = "5432/tcp") -> int:
    for _ in range(60):
        result = _docker("port", container_id, container_port, check=False)
        if result.returncode == 0 and result.stdout.strip():
            return int(result.stdout.strip().splitlines()[0].rsplit(":", 1)[1])
        time.sleep(0.5)
    raise RuntimeError(f"{container_port} was not published")


def _publish_args() -> list[str]:
    # inside a runner container the fakes are reached over a shared docker network
    return ["--network", DOCKER_NETWORK] if DOCKER_NETWORK else ["-P"]


def _address(container: str, name: str, container_port: int) -> tuple[str, int]:
    if DOCKER_NETWORK:
        return name, container_port
    return "127.0.0.1", _mapped_port(container, f"{container_port}/tcp")


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
    if _docker("info", check=False).returncode != 0:
        pytest.skip("Docker is required to run the Postgres-backed B1 fake")
    if _docker("image", "inspect", POSTGRES_IMAGE, check=False).returncode != 0:
        pytest.skip(f"{POSTGRES_IMAGE} is not present locally (docker pull it first)")
    name = f"consola-sap-b1-cartridge-{uuid.uuid4().hex[:12]}"
    container = _docker(
        "run", "--pull=never", "-d", "--rm", "--name", name,
        "-e", "POSTGRES_DB=b1fake", "-e", "POSTGRES_USER=postgres", "-e", f"POSTGRES_PASSWORD={POSTGRES_PASSWORD}",
        *_publish_args(), POSTGRES_IMAGE,
    ).stdout.strip()
    try:
        host, port = _address(container, name, 5432)
        dsn = f"postgresql://postgres:{POSTGRES_PASSWORD}@{host}:{port}/b1fake"
        _wait_ready(dsn)
        b1_loader.load(dsn, dataset)
        yield {
            "dialect": "postgres",
            "dsn": dsn,
            "host": host,
            "port": port,
            "user": "postgres",
            "password": POSTGRES_PASSWORD,
            "database": "b1fake",
            "companies": ",".join(f"{c.alias}={c.schema}" for c in dataset.companies),
            "extra_env": {},
        }
    finally:
        _docker("rm", "-f", "-v", container, check=False)


@pytest.fixture
def b1_env(fake_postgres, monkeypatch):
    monkeypatch.setenv("SAP_B1_DIALECT", "postgres")
    monkeypatch.setenv("SAP_B1_HOST", fake_postgres["host"])
    monkeypatch.setenv("SAP_B1_PORT", str(fake_postgres["port"]))
    monkeypatch.setenv("SAP_B1_USER", fake_postgres["user"])
    monkeypatch.setenv("SAP_B1_PASSWORD", fake_postgres["password"])
    monkeypatch.setenv("SAP_B1_DATABASE", fake_postgres["database"])
    monkeypatch.setenv("SAP_B1_COMPANIES", fake_postgres["companies"])
    return fake_postgres


def _pyodbc_with_driver():
    try:
        import pyodbc
    except ImportError:
        return None
    return pyodbc if MSSQL_ODBC_DRIVER in pyodbc.drivers() else None


def _mssql_password() -> str:
    # meets the SQL Server complexity policy; generated per session, never stored
    return f"{secrets.token_urlsafe(18)}aZ9!"


def _odbc(value: object) -> str:
    return "{" + str(value).replace("}", "}}") + "}"


def _mssql_connect(pyodbc, host: str, port: int, user: str, password: str, timeout: int = 10):
    return pyodbc.connect(
        f"DRIVER={_odbc(MSSQL_ODBC_DRIVER)};SERVER={_odbc(f'tcp:{host},{port}')};UID={_odbc(user)};"
        f"PWD={_odbc(password)};Encrypt=yes;TrustServerCertificate=yes;",
        autocommit=True,
        timeout=timeout,
    )


def _wait_mssql(pyodbc, container: str, host: str, port: int, password: str) -> None:
    last: Exception | None = None
    for _ in range(180):
        try:
            conn = _mssql_connect(pyodbc, host, port, "sa", password, timeout=5)
            try:
                conn.cursor().execute("SELECT 1").fetchall()
            finally:
                conn.close()
            return
        except Exception as exc:  # noqa: BLE001 - startup race, retried
            last = exc
        state = _docker("inspect", "--format", "{{.State.Status}}", container, check=False).stdout.strip()
        if state in {"exited", "dead", ""}:
            logs = _docker("logs", "--tail", "40", container, check=False)
            raise RuntimeError(f"SQL Server container stopped ({state}):\n{logs.stdout}\n{logs.stderr}")
        time.sleep(2)
    raise RuntimeError(f"SQL Server never became ready: {type(last).__name__}")


@pytest.fixture(scope="session")
def fake_mssql(dataset):
    pyodbc = _pyodbc_with_driver()
    if (
        pyodbc is None
        or _docker("info", check=False).returncode != 0
        or _docker("image", "inspect", MSSQL_IMAGE, check=False).returncode != 0
    ):
        pytest.skip(MSSQL_UNAVAILABLE)
    sa_password, reader_password = _mssql_password(), _mssql_password()
    name = f"consola-sap-b1-mssql-{uuid.uuid4().hex[:12]}"
    env = {**os.environ, "MSSQL_SA_PASSWORD": sa_password}
    container = _docker(
        "run", "--pull=never", "-d", "--rm", "--platform", "linux/amd64", "--name", name,
        "-e", "ACCEPT_EULA=Y", "-e", "MSSQL_PID=Developer", "-e", "MSSQL_SA_PASSWORD",
        *_publish_args(), MSSQL_IMAGE,
        env=env,
    ).stdout.strip()
    try:
        host, port = _address(container, name, 1433)
        _wait_mssql(pyodbc, container, host, port, sa_password)

        def admin():
            return _mssql_connect(pyodbc, host, port, "sa", sa_password, timeout=30)

        b1_mssql_loader.load(admin, dataset, reader=(MSSQL_READER, reader_password))
        yield {
            "dialect": "mssql",
            "host": host,
            "port": port,
            "user": MSSQL_READER,
            "password": reader_password,
            "database": "",
            "companies": ",".join(f"{c.alias}={c.schema}" for c in dataset.companies),
            "extra_env": {"SAP_B1_ENCRYPT": "true", "SAP_B1_SSL_VALIDATE_CERTIFICATE": "false"},
            "admin": admin,
            "sa_password": sa_password,
        }
    finally:
        _docker("rm", "-f", "-v", container, check=False)


BACKENDS = ("postgres", "mssql")


def backend_env(fake: dict) -> dict[str, str]:
    env = {
        "SAP_B1_DIALECT": fake["dialect"],
        "SAP_B1_HOST": str(fake["host"]),
        "SAP_B1_PORT": str(fake["port"]),
        "SAP_B1_USER": fake["user"],
        "SAP_B1_PASSWORD": fake["password"],
        "SAP_B1_DATABASE": fake["database"],
        "SAP_B1_COMPANIES": fake["companies"],
    }
    env.update(fake["extra_env"])
    return env


@pytest.fixture
def switch_backend(monkeypatch):
    def apply(fake: dict) -> dict:
        for name in ("SAP_B1_DATABASE", "SAP_B1_ENCRYPT", "SAP_B1_SSL_VALIDATE_CERTIFICATE"):
            monkeypatch.delenv(name, raising=False)
        for name, value in backend_env(fake).items():
            monkeypatch.setenv(name, value)
        return fake

    return apply


@pytest.fixture(params=BACKENDS)
def b1_backend(request, switch_backend):
    return switch_backend(request.getfixturevalue(f"fake_{request.param}"))
