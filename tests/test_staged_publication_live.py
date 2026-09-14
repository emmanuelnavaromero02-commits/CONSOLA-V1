from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import time
import uuid
from pathlib import Path

import boto3
import psycopg2
import pytest
from botocore.config import Config

from tests.staged_publication_live import (
    CANARY_IMPLEMENTATIONS,
    PASSWORD,
    PUBLISHER_PASSWORD,
    READER_PASSWORD,
    VERIFIER_PASSWORD,
    TENANT_A,
    TENANT_B,
    WORKSPACE_A,
    WORKSPACE_B,
    LiveStack,
    _docker,
    _port,
    _wait,
    valid_catalog,
    valid_lineage,
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
        "--pull=never",
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
        f"PGOPTIONS=-c app.omega_refinement_gold_password={READER_PASSWORD} "
        f"-c app.omega_gold_publisher_password={PUBLISHER_PASSWORD} "
        f"-c app.omega_gold_verifier_password={VERIFIER_PASSWORD}",
        "-v",
        f"{ROOT / 'infra/init_gold'}:/docker-entrypoint-initdb.d:ro",
        "-P",
        "postgres:15.18",
    )
    _docker(
        "run",
        "--pull=never",
        "-d",
        "--rm",
        "--name",
        minio,
        "-e",
        "MINIO_ROOT_USER=minio",
        "-e",
        "MINIO_ROOT_PASSWORD=minio-secret",
        "-P",
        "quay.io/minio/minio:RELEASE.2024-12-18T13-15-44Z",
        "server",
        "/data",
    )
    verifier = None
    original_socket = os.environ.get("PUBLICATION_VERIFIER_SOCKET")
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
                s3.put_bucket_versioning(
                    Bucket="lakehouse", VersioningConfiguration={"Status": "Enabled"}
                )
                break
            except Exception:
                if time.monotonic() >= deadline:
                    raise
                time.sleep(0.25)
        verifier_dir = tempfile.TemporaryDirectory(prefix="omega-verifier-")
        verifier_socket = str(Path(verifier_dir.name) / "verifier.sock")
        verifier_env = {
            **os.environ,
            "PYTHONPATH": str(ROOT),
            "GOLD_VERIFIER_DATABASE_URL": (
                f"postgresql://omega_gold_verifier:{VERIFIER_PASSWORD}"
                f"@127.0.0.1:{gold_port}/modecissions_gold"
            ),
            "PUBLICATION_VERIFIER_SOCKET": verifier_socket,
            "PUBLICATION_APP_UID": str(os.getuid()),
            "MINIO_ENDPOINT": endpoint.removeprefix("http://"),
            "MINIO_ACCESS_KEY": "minio",
            "MINIO_SECRET_KEY": "minio-secret",
            "MINIO_BUCKET": "lakehouse",
            "MINIO_SECURE": "false",
        }
        verifier = subprocess.Popen(
            [sys.executable, "refinement/app/publication_verifier_worker.py"],
            cwd=ROOT,
            env=verifier_env,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            text=True,
        )
        deadline = time.monotonic() + 20
        while not Path(verifier_socket).exists():
            if verifier.poll() is not None or time.monotonic() >= deadline:
                if verifier.poll() is None:
                    verifier.terminate()
                    verifier.wait(timeout=5)
                detail = verifier.stderr.read() if verifier.stderr else ""
                raise RuntimeError(
                    f"publication verifier did not become ready: {detail[-1000:]}"
                )
            time.sleep(0.05)
        os.environ["PUBLICATION_VERIFIER_SOCKET"] = verifier_socket
        yield LiveStack(
            admin,
            f"postgresql://omega_refinement_gold:{READER_PASSWORD}@127.0.0.1:{gold_port}/modecissions_gold",
            f"postgresql://omega_gold_publisher:{PUBLISHER_PASSWORD}@127.0.0.1:{gold_port}/modecissions_gold",
            f"postgresql://omega_gold_verifier:{VERIFIER_PASSWORD}@127.0.0.1:{gold_port}/modecissions_gold",
            s3,
            endpoint,
            gold,
            ROOT,
            verifier_socket,
        )
    finally:
        if verifier is not None:
            verifier.terminate()
            verifier.wait(timeout=10)
        if original_socket is None:
            os.environ.pop("PUBLICATION_VERIFIER_SOCKET", None)
        else:
            os.environ["PUBLICATION_VERIFIER_SOCKET"] = original_socket
        if "verifier_dir" in locals():
            verifier_dir.cleanup()
        _docker("rm", "-f", "-v", minio, check=False)
        _docker("rm", "-f", "-v", gold, check=False)


@pytest.mark.parametrize("canary", sorted(CANARY_IMPLEMENTATIONS))
def test_staged_publication_canary(
    staged_publication_live_stack: LiveStack, canary: str
) -> None:
    CANARY_IMPLEMENTATIONS[canary](staged_publication_live_stack)


def test_real_engine_publishes_one_complete_silver_run(
    staged_publication_live_stack: LiveStack, monkeypatch: pytest.MonkeyPatch
) -> None:
    stack = staged_publication_live_stack
    monkeypatch.delenv("DATABASE_URL", raising=False)
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
    monkeypatch.delenv("DATABASE_URL", raising=False)
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


def _empty_gold_stage(stack: LiveStack, dataset: str, run: uuid.UUID) -> str:
    stack.reserve(dataset, run)
    columns = [
        {"name": "tenant_id", "type": "TEXT"},
        {"name": "workspace_id", "type": "TEXT"},
        {"name": "value", "type": "INTEGER"},
    ]
    import json

    return stack.sql(
        stack.publisher_dsn,
        (TENANT_A, WORKSPACE_A),
        "SELECT omega_publication.create_gold_stage(%s,%s::jsonb)",
        (str(run), json.dumps(columns)),
    )[0][0]


def test_gold_stage_scope_is_not_null_while_empty_dataset_remains_valid(
    staged_publication_live_stack: LiveStack,
) -> None:
    stack = staged_publication_live_stack
    run = uuid.uuid4()
    stage = _empty_gold_stage(stack, "scope_not_null", run)
    nullability = stack.sql(
        stack.admin_dsn,
        (TENANT_A, WORKSPACE_A),
        "SELECT column_name,is_nullable FROM information_schema.columns "
        "WHERE table_schema='omega_publication_stage' AND table_name=%s "
        "AND column_name IN ('tenant_id','workspace_id') ORDER BY column_name",
        (stage,),
    )
    assert nullability == [("tenant_id", "NO"), ("workspace_id", "NO")]
    with pytest.raises(psycopg2.Error):
        stack.sql(
            stack.admin_dsn,
            (TENANT_A, WORKSPACE_A),
            f'INSERT INTO omega_publication_stage."{stage}" VALUES (NULL,%s,1)',
            (WORKSPACE_A,),
            fetch=False,
        )

    uri, checksum = stack.write_object("scope_not_null", run, 0)
    lineage = stack.bound_lineage(run, valid_lineage())
    stack.attest(
        run,
        uri=uri,
        checksum=checksum,
        row_count=0,
        lineage=lineage,
        catalog=valid_catalog(),
    )
    stack.sql(
        stack.publisher_dsn,
        (TENANT_A, WORKSPACE_A),
        "SELECT * FROM omega_publication.mark_prepared(%s,%s,%s,%s,0,%s,%s,%s::jsonb,%s::jsonb)",
        (
            str(run),
            uri,
            stack.object_version(uri),
            checksum,
            stage,
            stage,
            lineage,
            valid_catalog(),
        ),
    )


def test_gold_stage_is_bound_to_the_reserving_scope(
    staged_publication_live_stack: LiveStack,
) -> None:
    stack = staged_publication_live_stack
    run = uuid.uuid4()
    stage = _empty_gold_stage(stack, "scope_binding", run)

    with pytest.raises(psycopg2.Error):
        stack.sql(
            stack.publisher_dsn,
            (TENANT_B, WORKSPACE_B),
            f'INSERT INTO omega_publication_stage."{stage}" VALUES (%s,%s,1)',
            (TENANT_B, WORKSPACE_B),
            fetch=False,
        )

    stack.sql(
        stack.publisher_dsn,
        (TENANT_A, WORKSPACE_A),
        f'INSERT INTO omega_publication_stage."{stage}" VALUES (%s,%s,1)',
        (TENANT_A, WORKSPACE_A),
        fetch=False,
    )
    assert stack.sql(
        stack.publisher_dsn,
        (TENANT_A, WORKSPACE_A),
        f'SELECT count(*) FROM omega_publication_stage."{stage}"',
    ) == [(1,)]
