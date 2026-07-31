from __future__ import annotations

import time
import uuid
from pathlib import Path

import boto3
import pytest
from botocore.config import Config

from tests.staged_publication_live import (
    CANARY_IMPLEMENTATIONS,
    PASSWORD,
    PUBLISHER_PASSWORD,
    READER_PASSWORD,
    TENANT_A,
    WORKSPACE_A,
    LiveStack,
    _docker,
    _port,
    _wait,
)

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(scope="module")
def staged_publication_live_stack() -> LiveStack:
    if _docker("info", check=False) == "":
        pytest.skip("Docker is required for staged publication acceptance")
    suffix = uuid.uuid4().hex[:12]
    gold = f"omega-staged-gold-{suffix}"
    minio = f"omega-staged-minio-{suffix}"
    _docker(
        "run",
        "-d",
        "--rm",
        "--name",
        gold,
        "-e",
        "POSTGRES_DB=modecissions_gold",
        "-e",
        "POSTGRES_USER=postgres",
        "-e",
        f"POSTGRES_PASSWORD={PASSWORD}",
        "-e",
        f"PGOPTIONS=-c app.omega_refinement_gold_password={READER_PASSWORD} -c app.omega_gold_publisher_password={PUBLISHER_PASSWORD}",
        "-v",
        f"{ROOT / 'infra/init_gold'}:/docker-entrypoint-initdb.d:ro",
        "-P",
        "postgres:15",
    )
    _docker(
        "run",
        "-d",
        "--rm",
        "--name",
        minio,
        "-e",
        "MINIO_ROOT_USER=minio",
        "-e",
        "MINIO_ROOT_PASSWORD=minio-secret",
        "-P",
        "minio/minio:RELEASE.2024-12-18T13-15-44Z",
        "server",
        "/data",
    )
    try:
        gold_port = _port(gold, "5432/tcp")
        minio_port = _port(minio, "9000/tcp")
        admin = (
            f"postgresql://postgres:{PASSWORD}@127.0.0.1:{gold_port}/modecissions_gold"
        )
        _wait(admin)
        endpoint = f"http://127.0.0.1:{minio_port}"
        s3 = boto3.client(
            "s3",
            endpoint_url=endpoint,
            aws_access_key_id="minio",
            aws_secret_access_key="minio-secret",
            region_name="us-east-1",
            config=Config(
                connect_timeout=1, read_timeout=3, retries={"max_attempts": 2}
            ),
        )
        deadline = time.monotonic() + 60
        while True:
            try:
                s3.create_bucket(Bucket="lakehouse")
                break
            except Exception:
                if time.monotonic() >= deadline:
                    raise
                time.sleep(0.25)
        yield LiveStack(
            admin,
            f"postgresql://omega_refinement_gold:{READER_PASSWORD}@127.0.0.1:{gold_port}/modecissions_gold",
            f"postgresql://omega_gold_publisher:{PUBLISHER_PASSWORD}@127.0.0.1:{gold_port}/modecissions_gold",
            s3,
            endpoint,
            gold,
            ROOT,
        )
    finally:
        _docker("rm", "-f", minio, check=False)
        _docker("rm", "-f", gold, check=False)


@pytest.mark.parametrize("canary", sorted(CANARY_IMPLEMENTATIONS))
def test_staged_publication_canary(
    staged_publication_live_stack: LiveStack, canary: str
) -> None:
    CANARY_IMPLEMENTATIONS[canary](staged_publication_live_stack)


def test_real_engine_publishes_one_complete_silver_run(
    staged_publication_live_stack: LiveStack, monkeypatch: pytest.MonkeyPatch
) -> None:
    stack = staged_publication_live_stack
    for name, value in {
        "GOLD_DATABASE_URL": stack.reader_dsn,
        "GOLD_PUBLISHER_DATABASE_URL": stack.publisher_dsn,
        "MINIO_ENDPOINT": stack.minio_endpoint.removeprefix("http://"),
        "MINIO_ACCESS_KEY": "minio",
        "MINIO_SECRET_KEY": "minio-secret",
        "MINIO_BUCKET": "lakehouse",
        "MINIO_SECURE": "false",
    }.items():
        monkeypatch.setenv(name, value)
    from refinement.app.staged_publication_engine import StagedPublicationEngine

    engine = StagedPublicationEngine()
    result = engine.materialize(
        {
            "name": "engine_live_probe",
            "layer": "silver",
            "cartridge": "acceptance",
            "sql_def": "SELECT 7::INTEGER AS value",
            "sources": [],
        },
        {"tenant_id": TENANT_A, "workspace_id": WORKSPACE_A},
    )
    evidence = stack.evidence("engine_live_probe")
    assert result["row_count"] == 1 and len(evidence) == 1
    assert evidence[0][1] == 1
    assert {field["name"] for field in evidence[0][3]} == {
        "tenant_id",
        "workspace_id",
        "value",
    }
    if engine._con is not None:
        engine._con.close()


def test_real_engine_publishes_gold_only_at_the_cas(
    staged_publication_live_stack: LiveStack, monkeypatch: pytest.MonkeyPatch
) -> None:
    stack = staged_publication_live_stack
    for name, value in {
        "GOLD_DATABASE_URL": stack.reader_dsn,
        "GOLD_PUBLISHER_DATABASE_URL": stack.publisher_dsn,
        "MINIO_ENDPOINT": stack.minio_endpoint.removeprefix("http://"),
        "MINIO_ACCESS_KEY": "minio",
        "MINIO_SECRET_KEY": "minio-secret",
        "MINIO_BUCKET": "lakehouse",
        "MINIO_SECURE": "false",
    }.items():
        monkeypatch.setenv(name, value)
    from refinement.app.staged_publication_engine import StagedPublicationEngine

    engine = StagedPublicationEngine()
    result = engine.materialize(
        {
            "name": "engine_gold_probe",
            "layer": "gold",
            "cartridge": "acceptance",
            "sql_def": "SELECT 9::INTEGER AS value",
            "sources": [],
        },
        {"tenant_id": TENANT_A, "workspace_id": WORKSPACE_A},
    )
    assert result["row_count"] == 1
    assert stack.rows("engine_gold_probe") == [(9,)]
    assert stack.head("engine_gold_probe")[1] == 1
    replay = engine.materialize(
        {
            "name": "engine_gold_probe",
            "layer": "gold",
            "cartridge": "acceptance",
            "sql_def": "SELECT 9::INTEGER AS value",
            "sources": [],
        },
        {"tenant_id": TENANT_A, "workspace_id": WORKSPACE_A},
    )
    assert replay["row_count"] == 1 and stack.head("engine_gold_probe")[1] == 1
    if engine._con is not None:
        engine._con.close()
