from __future__ import annotations

import json
import subprocess
import time
import uuid
from dataclasses import dataclass
from pathlib import Path

import boto3
import psycopg2
from botocore.config import Config

from tests.staged_publication_canaries import (
    TENANT_A,
    TENANT_B,
    WORKSPACE_A,
    WORKSPACE_B,
)

PASSWORD = "staged-publication-postgres"
READER_PASSWORD = "staged-publication-reader"
PUBLISHER_PASSWORD = "staged-publication-publisher"


def valid_lineage() -> str:
    return json.dumps(
        {
            "source_entity": "real",
            "source_load_date": "2026-07-31",
            "source_batch_id": "test",
            "sql_digest": "e" * 64,
            "column_mapping_digest": "f" * 64,
        }
    )


def valid_catalog() -> str:
    return json.dumps(
        [
            {"name": "tenant_id", "type": "TEXT"},
            {"name": "workspace_id", "type": "TEXT"},
            {"name": "value", "type": "INTEGER"},
        ]
    )


def _docker(*args: str, check: bool = True) -> str:
    result = subprocess.run(
        ["docker", *args], check=False, text=True, capture_output=True
    )
    if check and result.returncode:
        raise RuntimeError(result.stderr or result.stdout)
    return result.stdout.strip()


def _port(container: str, internal: str) -> int:
    value = _docker("port", container, internal).splitlines()[0]
    return int(value.rsplit(":", 1)[1])


def _wait(dsn: str) -> None:
    deadline = time.monotonic() + 90
    while time.monotonic() < deadline:
        try:
            with psycopg2.connect(dsn) as conn, conn.cursor() as cur:
                cur.execute(
                    "SELECT to_regclass('omega_publication.dataset_publication_heads')"
                )
                if cur.fetchone()[0]:
                    return
        except psycopg2.OperationalError:
            time.sleep(0.25)
    raise RuntimeError("staged publication postgres did not become ready")


@dataclass
class LiveStack:
    admin_dsn: str
    reader_dsn: str
    publisher_dsn: str
    s3: object
    minio_endpoint: str
    gold_container: str
    repo_root: Path

    def sql(self, dsn: str, scope: tuple[str, str], query: str, params=(), fetch=True):
        with psycopg2.connect(dsn) as conn, conn.cursor() as cur:
            cur.execute("SELECT set_config('app.tenant_id',%s,true)", (scope[0],))
            cur.execute("SELECT set_config('app.workspace_id',%s,true)", (scope[1],))
            cur.execute(query, params)
            return cur.fetchall() if fetch and cur.description else None

    def reserve(
        self,
        dataset: str,
        run: uuid.UUID,
        scope=(TENANT_A, WORKSPACE_A),
        digest="a" * 64,
    ):
        head = self.head(dataset, scope)
        return self.sql(
            self.publisher_dsn,
            scope,
            "SELECT * FROM omega_publication.reserve_materialization(%s,%s,%s,%s,'gold',%s,%s,%s)",
            (str(run), *scope, dataset, digest, "b" * 64, head[0] if head else None),
        )[0]

    def stage(
        self, dataset: str, run: uuid.UUID, value: int, scope=(TENANT_A, WORKSPACE_A)
    ):
        stage = self.stage_gold(dataset, run, value, scope)
        uri, checksum = self.write_object(dataset, run, value, scope)
        return stage, uri, checksum

    def stage_gold(
        self, dataset: str, run: uuid.UUID, value: int, scope=(TENANT_A, WORKSPACE_A)
    ):
        columns = [
            {"name": "tenant_id", "type": "TEXT"},
            {"name": "workspace_id", "type": "TEXT"},
            {"name": "value", "type": "INTEGER"},
        ]
        stage = self.sql(
            self.publisher_dsn,
            scope,
            "SELECT omega_publication.create_gold_stage(%s,%s::jsonb)",
            (str(run), json.dumps(columns)),
        )[0][0]
        self.sql(
            self.publisher_dsn,
            scope,
            f'INSERT INTO omega_publication_stage."{stage}" VALUES (%s,%s,%s)',
            (*scope, value),
            fetch=False,
        )
        return stage

    def write_object(
        self, dataset: str, run: uuid.UUID, value: int, scope=(TENANT_A, WORKSPACE_A)
    ):
        body = f"{dataset}:{value}".encode()
        import hashlib

        checksum = hashlib.sha256(body).hexdigest()
        key = f"gold/test/{dataset}/tenant_id={scope[0]}/workspace_id={scope[1]}/_pending/{run.hex}/{checksum}.parquet"
        self.s3.put_object(
            Bucket="lakehouse", Key=key, Body=body, Metadata={"omega-sha256": checksum}
        )
        return f"s3://lakehouse/{key}", checksum

    def prepare(
        self, dataset: str, run: uuid.UUID, value: int, scope=(TENANT_A, WORKSPACE_A)
    ):
        stage, uri, checksum = self.stage(dataset, run, value, scope)
        lineage = self.bound_lineage(run, valid_lineage(), scope)
        self.attest(
            run,
            uri=uri,
            checksum=checksum,
            row_count=1,
            lineage=lineage,
            catalog=valid_catalog(),
            scope=scope,
        )
        self.sql(
            self.publisher_dsn,
            scope,
            "SELECT * FROM omega_publication.mark_prepared(%s,%s,%s,1,%s,%s,%s::jsonb,%s::jsonb)",
            (
                str(run),
                uri,
                checksum,
                stage,
                stage,
                lineage,
                valid_catalog(),
            ),
        )
        return uri

    def bound_lineage(
        self,
        run: uuid.UUID,
        lineage: str,
        scope=(TENANT_A, WORKSPACE_A),
    ) -> str:
        input_digest, contract_digest = self.sql(
            self.reader_dsn,
            scope,
            "SELECT input_digest,contract_digest "
            "FROM omega_publication.materialization_runs "
            "WHERE materialization_run_id=%s",
            (str(run),),
        )[0]
        value = json.loads(lineage)
        value.update(
            input_digest=input_digest,
            contract_digest=contract_digest,
        )
        return json.dumps(value, sort_keys=True)

    def attest(
        self,
        run: uuid.UUID,
        *,
        uri: str,
        checksum: str,
        row_count: int,
        lineage: str,
        catalog: str,
        scope=(TENANT_A, WORKSPACE_A),
    ) -> str:
        return str(
            self.sql(
                self.reader_dsn,
                scope,
                "SELECT omega_publication.record_attestation("
                "%s,%s,%s,%s,%s::jsonb,%s::jsonb)",
                (str(run), uri, checksum, row_count, lineage, catalog),
            )[0][0]
        )

    def publish(
        self, run: uuid.UUID, expected: uuid.UUID | None, scope=(TENANT_A, WORKSPACE_A)
    ):
        return self.sql(
            self.publisher_dsn,
            scope,
            "SELECT * FROM omega_publication.publish_materialization(%s,%s)",
            (str(run), str(expected) if expected else None),
        )[0]

    def head(self, dataset: str, scope=(TENANT_A, WORKSPACE_A)):
        rows = self.sql(
            self.reader_dsn,
            scope,
            "SELECT materialization_run_id,generation FROM omega_publication.dataset_publication_heads WHERE dataset=%s",
            (dataset,),
        )
        return rows[0] if rows else None

    def rows(self, dataset: str, scope=(TENANT_A, WORKSPACE_A)):
        return self.sql(
            self.reader_dsn,
            scope,
            f"SELECT value FROM {self.gold_table(dataset, scope)}",
        )

    def gold_table(self, dataset: str, scope=(TENANT_A, WORKSPACE_A)) -> str:
        rows = self.sql(
            self.reader_dsn,
            scope,
            """SELECT r.gold_table, r.status
                 FROM omega_publication.dataset_publication_heads h
                 JOIN omega_publication.materialization_runs r
                   ON r.materialization_run_id=h.materialization_run_id
                WHERE h.dataset=%s AND h.layer='gold'""",
            (dataset,),
        )
        schema = (
            "public" if rows[0][1] == "legacy_unverified" else "omega_publication_gold"
        )
        return f'"{schema}"."{rows[0][0]}"'

    def public_object(self, dataset: str, scope=(TENANT_A, WORKSPACE_A)):
        rows = self.sql(
            self.reader_dsn,
            scope,
            """SELECT r.object_uri FROM omega_publication.dataset_publication_heads h
                 JOIN omega_publication.materialization_runs r
                   ON r.materialization_run_id=h.materialization_run_id
                WHERE h.dataset=%s AND h.layer='gold'""",
            (dataset,),
        )
        return rows[0][0] if rows else None

    def evidence(self, dataset: str, scope=(TENANT_A, WORKSPACE_A)):
        return self.sql(
            self.reader_dsn,
            scope,
            """SELECT e.materialization_run_id::text,e.row_count,e.lineage,e.catalog
                 FROM omega_publication.dataset_publication_heads h
                 JOIN omega_publication.materialization_evidence e
                   ON e.materialization_run_id=h.materialization_run_id
                WHERE h.dataset=%s""",
            (dataset,),
        )

    def compatibility_rows(self, dataset: str, scope=(TENANT_A, WORKSPACE_A)):
        relation = self.sql(
            self.reader_dsn,
            scope,
            "SELECT relation_name FROM omega_publication.dataset_gold_relations WHERE dataset=%s",
            (dataset,),
        )[0][0]
        return self.sql(
            self.reader_dsn,
            scope,
            f'SELECT value FROM public."{relation}"',
        )

    def published_state(self, dataset: str):
        return {
            "head_cache_key": self.head(dataset),
            "gold_diagnostics": self.rows(dataset),
            "superset_compatibility": self.compatibility_rows(dataset),
            "object": self.public_object(dataset),
            "lineage_catalog_metadata": self.evidence(dataset),
        }

    def baseline(self, dataset: str):
        run = uuid.uuid4()
        self.reserve(dataset, run)
        self.prepare(dataset, run, 1)
        self.publish(run, None)
        return str(run)

    def rerun_gold_migration(self, filename: str) -> None:
        _docker(
            "exec",
            "-e",
            f"PGPASSWORD={PASSWORD}",
            "-e",
            f"PGOPTIONS=-c app.omega_refinement_gold_password={READER_PASSWORD} -c app.omega_gold_publisher_password={PUBLISHER_PASSWORD}",
            self.gold_container,
            "psql",
            "-v",
            "ON_ERROR_STOP=1",
            "-U",
            "postgres",
            "-d",
            "modecissions_gold",
            "-f",
            f"/docker-entrypoint-initdb.d/{filename}",
        )


from tests.staged_publication_canary_impl import CANARY_IMPLEMENTATIONS
