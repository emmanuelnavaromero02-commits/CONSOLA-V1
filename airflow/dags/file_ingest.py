from __future__ import annotations

import fnmatch
import hashlib
import io
import os
import re
import uuid
from datetime import datetime, timezone

from airflow.decorators import dag, task
from airflow.models import Variable
from runtime_security_context import build_pipeline_run_context
from dataset_refresh_admission import build_dataset_refresh_trigger

MCP_INFRA_URL = os.environ.get("MCP_INFRA_URL", "http://mcp-infra:8010")


def _is_production() -> bool:
    return os.environ.get("APP_ENV", "production").strip().lower() in {
        "production",
        "prod",
    }


def _internal_key(env_name: str) -> str:
    key = os.environ.get(env_name, "")
    if key:
        return key
    if not _is_production():
        legacy = os.environ.get("INTERNAL_API_KEY", "")
        if legacy:
            return legacy
    raise RuntimeError(
        f"{env_name} missing; legacy INTERNAL_API_KEY fallback is disabled in production"
    )


def _mcp_headers() -> dict[str, str]:
    return {
        "X-Internal-Service": "airflow",
        "X-API-Key": _internal_key("INTERNAL_API_KEY_AIRFLOW_TO_MCP_INFRA"),
    }


def _minio_cfg() -> dict:
    provider = os.environ.get("LAKEHOUSE_PROVIDER", "").strip().lower()
    endpoint = os.environ.get("LAKEHOUSE_ENDPOINT", "").strip()
    if provider == "gcs" or "storage.googleapis.com" in endpoint.lower():
        config = {
            "endpoint": endpoint or "storage.googleapis.com",
            "access_key": os.environ.get("GCS_ACCESS_KEY_ID", "").strip(),
            "secret_key": os.environ.get("GCS_SECRET_ACCESS_KEY", "").strip(),
            "bucket": (
                os.environ.get("GCS_BUCKET")
                or os.environ.get("LAKEHOUSE_BUCKET")
                or ""
            ).strip(),
            "secure": True,
            "region": "auto",
        }
        if not all(config[key] for key in ("access_key", "secret_key", "bucket")):
            raise RuntimeError("complete GCS lakehouse credentials are required")
        return config
    return {
        "endpoint": Variable.get("minio_endpoint"),
        "access_key": Variable.get("minio_access_key"),
        "secret_key": Variable.get("minio_secret_key"),
        "bucket": Variable.get("minio_bucket"),
        "secure": Variable.get("minio_secure", default_var="false").lower() == "true",
        "region": None,
    }


def _minio_client():
    from minio import Minio

    cfg = _minio_cfg()
    return Minio(
        cfg["endpoint"],
        access_key=cfg["access_key"],
        secret_key=cfg["secret_key"],
        secure=cfg["secure"],
        region=cfg["region"],
    )


def _safe_scope_segment(value: str | None, label: str) -> str:
    value = str(value or "").strip()
    if not value:
        return ""
    if not re.fullmatch(r"[A-Za-z0-9_.:-]+", value):
        raise ValueError(f"{label} inválido para partición SaaS")
    return value


def _upload_prefix(
    cartridge_id: str, tenant_id: str | None, workspace_id: str | None
) -> str:
    tenant = _safe_scope_segment(tenant_id, "tenant_id")
    workspace = _safe_scope_segment(workspace_id, "workspace_id")
    if tenant and workspace:
        return f"uploads/{cartridge_id}/tenant_id={tenant}/workspace_id={workspace}/in/"
    return f"uploads/{cartridge_id}/in/"


def _raw_prefix(
    cartridge_id: str,
    entity: str,
    tenant_id: str | None,
    workspace_id: str | None,
    load_date: str,
) -> str:
    tenant = _safe_scope_segment(tenant_id, "tenant_id")
    workspace = _safe_scope_segment(workspace_id, "workspace_id")
    base = f"raw/{cartridge_id}/{entity}/"
    if tenant and workspace:
        base += f"tenant_id={tenant}/workspace_id={workspace}/"
    return f"{base}load_date={load_date}/"


def _parse_default(data: bytes, conf: dict):
    import pandas as pd

    fmt = (conf.get("format") or "csv").lower()
    skiprows = int(conf.get("skiprows") or 0)
    if fmt == "csv":
        return pd.read_csv(
            io.BytesIO(data),
            sep=conf.get("delimiter") or ",",
            encoding=conf.get("encoding") or "utf-8",
            header=0,
            skiprows=skiprows or None,
        )
    if fmt in ("excel", "xlsx", "xls"):
        return pd.read_excel(
            io.BytesIO(data),
            sheet_name=conf.get("sheet", 0),
            header=0,
            skiprows=skiprows or None,
            engine="openpyxl",
        )
    raise ValueError(f"format no soportado: {fmt!r} (use csv | excel)")


def _parse_csv_latin1(data: bytes, conf: dict):
    import pandas as pd

    return pd.read_csv(io.BytesIO(data), sep=";", encoding="latin1", header=0)


def _parse_excel_skip_decor(data: bytes, conf: dict):
    import pandas as pd

    return pd.read_excel(
        io.BytesIO(data),
        sheet_name=conf.get("sheet", 0),
        skiprows=4,
        header=0,
        engine="openpyxl",
    )


PARSERS = {
    "default": _parse_default,
    "csv_latin1": _parse_csv_latin1,
    "excel_skip_decor": _parse_excel_skip_decor,
}


def _save_run(cartridge_id: str, entity: str, run_id: str, **kwargs) -> None:
    import requests

    allowed = {
        "airflow_dag_run_id",
        "mode",
        "status",
        "started_at",
        "finished_at",
        "duration_seconds",
        "record_count",
        "bytes_written",
        "storage_uri",
        "watermark_updated_to",
        "error_message",
        "tenant_id",
        "workspace_id",
        "project_id",
    }
    args = {
        "dag_id": "file_ingest",
        "cartridge_id": cartridge_id,
        "entity": entity,
        "run_id": run_id,
    }
    extra = dict(kwargs.pop("extra", {}) or {})
    for key, value in kwargs.items():
        if key in allowed:
            args[key] = value
        else:
            extra[key] = value
    if extra:
        args["extra"] = extra

    r = requests.post(
        f"{MCP_INFRA_URL}/mcp/invoke",
        headers=_mcp_headers(),
        json={
            "tool": "pipeline_run_save",
            "args": args,
            "security_context": build_pipeline_run_context(args),
        },
        timeout=10,
    )
    r.raise_for_status()
    body = r.json()
    if body.get("error"):
        raise RuntimeError(str(body["error"]))


def _require_saas_scope(conf: dict, cartridge_id: str) -> tuple[str, str]:
    tenant_id = str(conf.get("tenant_id") or "").strip()
    workspace_id = str(conf.get("workspace_id") or "").strip()
    if tenant_id and workspace_id:
        return tenant_id, workspace_id
    if cartridge_id == "platform" and conf.get("allow_unscoped_platform"):
        return tenant_id, workspace_id
    raise ValueError("tenant_id and workspace_id are required for file_ingest")


def _trigger_refresh_chain(
    *,
    admission: dict,
) -> None:
    import os as _os
    import requests as _req

    url = _os.environ.get("AIRFLOW_URL", "http://airflow:8080")
    user = (
        _os.environ.get("AIRFLOW_USER")
        or _os.environ.get("AIRFLOW_ADMIN_USER")
        or "admin"
    )
    pw = (
        _os.environ.get("AIRFLOW_PASSWORD")
        or _os.environ.get("AIRFLOW_ADMIN_PASSWORD")
        or "admin"
    )
    if not isinstance(admission, dict) or not isinstance(admission.get("conf"), dict):
        raise RuntimeError("refresh chain admission authority is required")
    dag_run_id = str(admission.get("dag_run_id") or "")
    conf = dict(admission["conf"])
    try:
        r = _req.post(
            f"{url}/api/v1/dags/dataset_refresh_chain/dagRuns",
            auth=(user, pw),
            json={"conf": conf, "dag_run_id": dag_run_id},
            timeout=15,
        )
        r.raise_for_status()
    except Exception as exc:  # noqa: BLE001
        raise RuntimeError(f"refresh_chain trigger falló: {exc}") from exc


@dag(
    dag_id="file_ingest",
    description="Ingesta genérica de archivos del lake → parquet bronze. Parametrizado por entity_config.dag_params.",
    schedule=None,
    start_date=datetime(2026, 5, 1),
    catchup=False,
    tags=["platform", "ingest", "bronze", "generic"],
    params={
        "entity": {"type": "string", "default": ""},
        "cartridge_id": {"type": "string", "default": "replicon"},
        "mode": {"type": "string", "default": "full"},
        "file_pattern": {"type": "string", "default": ""},
        "format": {"type": "string", "default": "csv"},
        "delimiter": {"type": "string", "default": ","},
        "encoding": {"type": "string", "default": "utf-8"},
        "sheet": {"type": ["string", "integer"], "default": 0},
        "skiprows": {"type": "integer", "default": 0},
        "parser": {"type": "string", "default": "default"},
    },
)
def file_ingest():
    @task
    def authorize_refresh_chain(**ctx) -> dict:
        dag_run = ctx.get("dag_run")
        conf = (dag_run.conf if dag_run else {}) or {}
        cartridge_id = str(conf.get("cartridge_id") or "replicon")
        entity = str(conf.get("entity") or "").strip()
        tenant_id, workspace_id = _require_saas_scope(conf, cartridge_id)
        upstream = conf.get("security_context")
        if not entity or not isinstance(upstream, dict):
            raise RuntimeError("refresh chain admission authority is required")
        refresh_conf = {
            "seed_raw": f"raw/{cartridge_id}/{entity}",
            "cartridge_id": cartridge_id,
            "triggered_by": "file_ingest",
            "max_depth": 10,
            "tenant_id": tenant_id,
            "workspace_id": workspace_id,
        }
        return build_dataset_refresh_trigger(
            upstream_context=upstream,
            conf=refresh_conf,
            source_dag_run_id=str(getattr(dag_run, "run_id", "") or ""),
            prefix="file_ingest",
        )

    @task
    def list_matching_files(admission: dict, **ctx) -> list[dict]:
        if not isinstance(admission, dict):
            raise RuntimeError("refresh chain admission is unavailable")
        conf = (ctx.get("dag_run").conf if ctx.get("dag_run") else {}) or {}
        cartridge_id = conf.get("cartridge_id") or "replicon"
        tenant_id, workspace_id = _require_saas_scope(conf, cartridge_id)
        pattern = conf.get("file_pattern") or ""
        if not pattern:
            raise ValueError("dag_params.file_pattern es obligatorio")

        cfg = _minio_cfg()
        bucket = cfg["bucket"]
        prefix = _upload_prefix(
            cartridge_id,
            tenant_id,
            workspace_id,
        )
        client = _minio_client()
        matches: list[dict] = []
        for obj in client.list_objects(bucket, prefix=prefix, recursive=False):
            name = obj.object_name.removeprefix(prefix)
            if not name or name.endswith("/"):
                continue
            if fnmatch.fnmatch(name, pattern):
                matches.append(
                    {
                        "key": obj.object_name,
                        "name": name,
                        "bytes": obj.size,
                    }
                )
        print(f"[file_ingest] {len(matches)} archivos matchean '{pattern}' en {prefix}")
        return matches

    @task
    def ingest_and_archive(files: list[dict], admission: dict, **ctx) -> dict:
        if not isinstance(admission, dict):
            raise RuntimeError("refresh chain admission is unavailable")
        import pandas as pd
        import pyarrow as pa
        import pyarrow.parquet as pq
        from minio.commonconfig import CopySource

        conf = (ctx.get("dag_run").conf if ctx.get("dag_run") else {}) or {}
        cartridge_id = conf.get("cartridge_id") or "replicon"
        tenant_id, workspace_id = _require_saas_scope(conf, cartridge_id)
        entity = conf.get("entity")
        if not entity:
            raise ValueError("conf.entity es obligatorio")

        airflow_run_id = (ctx.get("dag_run").run_id if ctx.get("dag_run") else "") or ""

        parser_name = conf.get("parser") or "default"
        parser = PARSERS.get(parser_name)
        if not parser:
            raise ValueError(
                f"parser desconocido: {parser_name!r}. Disponibles: {list(PARSERS)}"
            )

        cfg = _minio_cfg()
        bucket = cfg["bucket"]
        client = _minio_client()
        batch_id = str(uuid.uuid4())
        started = datetime.now(timezone.utc)
        today = started.strftime("%Y-%m-%d")
        out_pref = _raw_prefix(cartridge_id, entity, tenant_id, workspace_id, today)

        if not files:
            print(f"[file_ingest] no hay archivos para {cartridge_id}.{entity} — noop")
            _save_run(
                cartridge_id,
                entity,
                batch_id,
                status="success",
                record_count=0,
                bytes_written=0,
                started_at=started.isoformat(),
                finished_at=datetime.now(timezone.utc).isoformat(),
                airflow_dag_run_id=airflow_run_id,
                tenant_id=tenant_id,
                workspace_id=workspace_id,
                project_id=conf.get("project_id"),
                note="no files matched",
            )
            return {"row_count": 0, "files": 0, "storage_uri": None}

        frames = []
        for f in files:
            resp = client.get_object(bucket, f["key"])
            try:
                raw = resp.read()
            finally:
                resp.close()
                resp.release_conn()
            df = parser(raw, conf)
            df["_source_file"] = f["name"]
            frames.append(df)
        df = pd.concat(frames, ignore_index=True)
        if tenant_id:
            df["tenant_id"] = str(tenant_id)
        if workspace_id:
            df["workspace_id"] = str(workspace_id)
        print(
            f"[file_ingest] {cartridge_id}.{entity} → {len(df)} filas de {len(files)} archivo(s)"
        )

        size = 0
        for f, frame in zip(files, frames):
            if tenant_id:
                frame["tenant_id"] = str(tenant_id)
            if workspace_id:
                frame["workspace_id"] = str(workspace_id)
            table = pa.Table.from_pandas(frame)
            buf = io.BytesIO()
            pq.write_table(table, buf)
            buf.seek(0)
            part_size = buf.getbuffer().nbytes
            digest = hashlib.sha256(f["name"].encode("utf-8")).hexdigest()[:12]
            out_key = f"{out_pref}data-{digest}.parquet"
            client.put_object(
                bucket, out_key, buf, part_size,
                content_type="application/octet-stream",
            )
            size += part_size
        legacy_key = f"{out_pref}data.parquet"
        try:
            client.remove_object(bucket, legacy_key)
        except Exception:  # noqa: BLE001
            pass
        storage_uri = f"s3://{bucket}/{out_pref}"

        ts = started.strftime("%Y%m%d_%H%M%S")
        for f in files:
            dst_key = (
                f["key"].replace("/in/", "/bak/").rsplit("/", 1)[0]
                + f"/{ts}_{f['name']}"
            )
            try:
                client.copy_object(bucket, dst_key, CopySource(bucket, f["key"]))
                client.remove_object(bucket, f["key"])
            except Exception as exc:  # noqa: BLE001
                print(f"[file_ingest] WARN archivado falló para {f['key']}: {exc}")

        finished = datetime.now(timezone.utc)
        _save_run(
            cartridge_id,
            entity,
            batch_id,
            status="success",
            record_count=len(df),
            bytes_written=size,
            storage_uri=storage_uri,
            started_at=started.isoformat(),
            finished_at=finished.isoformat(),
            airflow_dag_run_id=airflow_run_id,
            tenant_id=tenant_id,
            workspace_id=workspace_id,
            project_id=conf.get("project_id"),
            files_processed=len(files),
        )

        _trigger_refresh_chain(
            admission=admission,
        )
        return {
            "row_count": len(df),
            "files": len(files),
            "bytes_written": size,
            "storage_uri": storage_uri,
            "batch_id": batch_id,
        }

    authority = authorize_refresh_chain()
    ingest_and_archive(list_matching_files(authority), authority)


dag = file_ingest()
