"""Dynamic Airflow DAG code generation for Studio."""
from __future__ import annotations

import ast
import importlib.util
import json
import re
import subprocess
import sys
import tempfile
import textwrap
from pathlib import Path
from typing import Any

import yaml


_IDENT_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,127}$")
_AIRFLOW_RUNTIME_IMPORTS = {
    "airflow",
    "minio",
    "pandas",
    "pyarrow",
    "requests",
    "urllib3",
}


def _safe_identifier(value: str, label: str) -> str:
    ident = (value or "").strip()
    if not _IDENT_RE.fullmatch(ident):
        raise ValueError(f"Invalid {label}: use letters, numbers and underscores only")
    return ident


def _schema_connector(schema: dict[str, Any]) -> dict[str, Any]:
    payload = schema.get("connector_schema") if isinstance(schema.get("connector_schema"), dict) else schema
    connector = payload.get("connector") if isinstance(payload.get("connector"), dict) else payload
    return connector if isinstance(connector, dict) else {}


def _load_entity_configs(cartridge_id: str) -> list[dict[str, Any]]:
    path = Path(__file__).resolve().parents[3] / "cartridges" / cartridge_id / "app" / "config" / "entities.yaml"
    if not path.exists():
        return []
    parsed = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    entities = parsed.get("entities") if isinstance(parsed, dict) else []
    return [item for item in entities if isinstance(item, dict)]


def _find_entity_config(cartridge_id: str, entity_name: str) -> dict[str, Any]:
    for item in _load_entity_configs(cartridge_id):
        if str(item.get("entity") or item.get("name") or "").strip() == entity_name:
            return item
    return {}


def _schema_entities(schema: dict[str, Any]) -> list[dict[str, Any]]:
    payload = schema.get("connector_schema") if isinstance(schema.get("connector_schema"), dict) else schema
    candidates = []
    if isinstance(schema.get("entities"), list):
        candidates.extend(item for item in schema["entities"] if isinstance(item, dict))
    if isinstance(payload, dict) and isinstance(payload.get("entities"), list):
        candidates.extend(item for item in payload["entities"] if isinstance(item, dict))
    connector = _schema_connector(schema)
    connector_entities = connector.get("entities") if isinstance(connector.get("entities"), list) else []
    candidates.extend(item for item in connector_entities if isinstance(item, dict))
    return candidates


def _schema_entity_metadata(schema: dict[str, Any], entity_name: str) -> dict[str, Any]:
    for item in _schema_entities(schema):
        name = str(item.get("entity") or item.get("name") or item.get("id") or "").strip()
        if name == entity_name:
            return item
    return {}


def _fields_from_metadata(metadata: dict[str, Any]) -> list[dict[str, Any]]:
    fields = metadata.get("fields")
    return [field for field in fields if isinstance(field, dict) and field.get("name")] if isinstance(fields, list) else []


def _primary_keys_from_fields(fields: list[dict[str, Any]], metadata: dict[str, Any], entity_config: dict[str, Any]) -> list[str]:
    keys: list[str] = []
    for value in (entity_config.get("primary_key"), metadata.get("primary_key"), metadata.get("id_field")):
        if isinstance(value, str) and value.strip():
            keys.append(value.strip())
        elif isinstance(value, list):
            keys.extend(str(item).strip() for item in value if str(item).strip())
    keys.extend(str(field.get("name")).strip() for field in fields if field.get("primary_key") and field.get("name"))
    return list(dict.fromkeys(keys))


def _select_fields_from_schema(fields: list[dict[str, Any]]) -> list[str]:
    names = [str(field.get("name")).strip() for field in fields if field.get("name")]
    return list(dict.fromkeys(name for name in names if _IDENT_RE.fullmatch(name)))


def _watermark_from_fields(fields: list[dict[str, Any]]) -> str:
    preferred_fragments = (
        "lastmodified",
        "last_change",
        "lastchange",
        "updated",
        "modified",
        "aedtm",
        "changed",
        "timestamp",
    )
    for field in fields:
        name = str(field.get("name") or "")
        canonical = str(field.get("type") or "").lower()
        source_type = str(field.get("source_type") or "").lower()
        if canonical not in {"timestamp", "date"} and not any(t in source_type for t in ("date", "time")):
            continue
        compact = name.replace("_", "").lower()
        if any(fragment.replace("_", "") in compact for fragment in preferred_fragments):
            return name
    return ""


def _connector_kind(connector: dict[str, Any], cartridge_id: str, entity_config: dict[str, Any], entity_meta: dict[str, Any] | None = None) -> str:
    auth_type = str((connector.get("auth") or {}).get("type") or "").lower()
    description = str(connector.get("description") or "").lower()
    entity_meta = entity_meta or {}
    if cartridge_id == "replicon" or "async export" in description:
        return "replicon_async"
    if (
        entity_config.get("odata_entity")
        or entity_meta.get("odata_entity")
        or cartridge_id.startswith("sap_")
        or auth_type in {"basic", "oauth2_client_credentials"}
    ):
        return "odata"
    return "rest_offset"


def validate_dag_code(code: str) -> dict[str, Any]:
    """Validate generated DAG code without executing it."""
    if not isinstance(code, str) or not code.strip():
        return {
            "valid": False,
            "stderr": "DAG code is empty",
            "checks": [],
        }

    checks: list[str] = []
    with tempfile.NamedTemporaryFile("w", suffix=".py", encoding="utf-8", delete=True) as tmp:
        tmp.write(code)
        tmp.flush()
        try:
            proc = subprocess.run(
                [sys.executable, "-m", "py_compile", tmp.name],
                capture_output=True,
                text=True,
                timeout=10,
                check=False,
            )
        except subprocess.TimeoutExpired as exc:
            return {
                "valid": False,
                "stderr": f"py_compile timed out: {exc}",
                "checks": checks,
            }
        if proc.returncode != 0:
            return {
                "valid": False,
                "stderr": (proc.stderr or proc.stdout or "py_compile failed").strip(),
                "checks": checks,
            }
    checks.append("python_syntax")

    try:
        tree = ast.parse(code)
    except SyntaxError as exc:
        return {
            "valid": False,
            "stderr": f"{exc.__class__.__name__}: {exc}",
            "checks": checks,
        }
    imports: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imports.update(alias.name.split(".", 1)[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imports.add(node.module.split(".", 1)[0])

    missing = [
        module
        for module in sorted(imports)
        if module not in _AIRFLOW_RUNTIME_IMPORTS
        and module not in sys.builtin_module_names
        and importlib.util.find_spec(module) is None
    ]
    if missing:
        return {
            "valid": False,
            "stderr": f"Missing import(s): {', '.join(missing)}",
            "checks": checks,
            "imports": sorted(imports),
        }
    checks.append("imports")
    return {
        "valid": True,
        "stderr": "",
        "checks": checks,
        "imports": sorted(imports),
    }


def generate_validated_dag_code(
    cartridge_id: str,
    entity_name: str,
    schema: dict[str, Any],
    *,
    max_attempts: int = 2,
    validation_error: str | None = None,
) -> dict[str, Any]:
    """Generate DAG code and retry with validation context before returning."""
    attempts: list[dict[str, Any]] = []
    result: dict[str, Any] | None = None
    for attempt in range(1, max(1, max_attempts) + 1):
        result = generate_dag_code(
            cartridge_id,
            entity_name,
            schema,
            validation_error=validation_error,
        )
        validation = validate_dag_code(result["code"])
        attempts.append({"attempt": attempt, **validation})
        result["validation"] = validation
        result["validation_attempts"] = attempts
        result["validated"] = bool(validation.get("valid"))
        if validation.get("valid"):
            return result
        validation_error = validation.get("stderr") or "Unknown DAG validation error"
    assert result is not None
    result["code"] = ""
    return result


def generate_dag_code(
    cartridge_id: str,
    entity_name: str,
    schema: dict[str, Any],
    *,
    validation_error: str | None = None,
) -> dict[str, Any]:
    """Generate a ready-to-deploy Airflow DAG from connector metadata."""
    safe_cartridge = _safe_identifier(cartridge_id, "cartridge_id")
    safe_entity = _safe_identifier(entity_name, "entity_name")
    connector = _schema_connector(schema)
    entity_meta = _schema_entity_metadata(schema, safe_entity)
    schema_fields = _fields_from_metadata(entity_meta)
    entity_config = _find_entity_config(safe_cartridge, safe_entity)
    auth = connector.get("auth") if isinstance(connector.get("auth"), dict) else {}
    api = connector.get("api") if isinstance(connector.get("api"), dict) else {}
    entities_meta = connector.get("entities") if isinstance(connector.get("entities"), dict) else {}

    kind = _connector_kind(connector, safe_cartridge, entity_config, entity_meta)
    mode = str(entity_config.get("mode") or "incremental").lower()
    watermark_field = (
        entity_config.get("watermark_field")
        or entity_meta.get("watermark_field")
        or entities_meta.get("watermark_field_default")
        or _watermark_from_fields(schema_fields)
        or ""
    )
    page_size = int(entity_config.get("page_size") or api.get("page_size_default") or 500)
    select_fields = entity_config.get("select_fields") if isinstance(entity_config.get("select_fields"), list) else []
    if not select_fields:
        select_fields = _select_fields_from_schema(schema_fields)
    primary_keys = _primary_keys_from_fields(schema_fields, entity_meta, entity_config)
    odata_entity = str(entity_config.get("odata_entity") or entity_meta.get("odata_entity") or safe_entity)
    odata_filter = str(entity_config.get("odata_filter") or "")
    dag_id = f"{safe_cartridge}_{safe_entity}_dynamic_extract"

    constants = {
        "CARTRIDGE_ID": safe_cartridge,
        "ENTITY_NAME": safe_entity,
        "DAG_ID": dag_id,
        "CONNECTOR_KIND": kind,
        "AUTH_TYPE": str(auth.get("type") or "bearer_token"),
        "BASE_URL_ENV": str(api.get("base_url_env") or f"{safe_cartridge.upper()}_BASE_URL"),
        "TOKEN_ENV": str(auth.get("env_var") or auth.get("env_var_api_key") or f"{safe_cartridge.upper()}_API_TOKEN"),
        "USER_ENV": str(auth.get("env_var_user") or f"{safe_cartridge.upper()}_USER"),
        "PASSWORD_ENV": str(auth.get("env_var_pass") or f"{safe_cartridge.upper()}_PASS"),
        "TOKEN_URL_ENV": str(auth.get("token_url_env") or ""),
        "CLIENT_ID_ENV": str(auth.get("client_id_env") or ""),
        "CLIENT_SECRET_ENV": str(auth.get("client_secret_env") or ""),
        "API_KEY_HEADER": str(auth.get("header") or "X-API-Key"),
        "HEADER_PREFIX": str(auth.get("prefix") or ""),
        "ODATA_ENTITY": odata_entity,
        "ODATA_FILTER": odata_filter,
        "SELECT_FIELDS": select_fields,
        "PRIMARY_KEYS": primary_keys,
        "WATERMARK_FIELD": str(watermark_field or ""),
        "MODE": mode,
        "PAGE_SIZE": page_size,
        "TIMEOUT_SECONDS": int(api.get("timeout_seconds") or 120),
        "RETRY_MAX": int(api.get("retry_max") or 3),
        "POLL_INTERVAL_SECONDS": int(api.get("extract_poll_interval_seconds") or 5),
        "POLL_MAX_ATTEMPTS": int(api.get("extract_poll_max_attempts") or 60),
    }
    code = _render_code(constants)
    if validation_error:
        code = _repair_generated_code(code, validation_error)
    return {
        "dag_id": dag_id,
        "cartridge_id": safe_cartridge,
        "entity_name": safe_entity,
        "connector_kind": kind,
        "auth_type": constants["AUTH_TYPE"],
        "watermark_field": constants["WATERMARK_FIELD"],
        "primary_keys": primary_keys,
        "select_fields": select_fields,
        "page_size": page_size,
        "code": code,
    }


def _repair_generated_code(code: str, validation_error: str) -> str:
    """Apply deterministic repairs based on validator feedback."""
    error = validation_error.lower()
    if "indentationerror" in error or "unexpected indent" in error:
        return textwrap.dedent(code).lstrip()
    return code


def _const(name: str, value: Any) -> str:
    return f"{name} = {json.dumps(value, ensure_ascii=False)}"


def _render_code(constants: dict[str, Any]) -> str:
    const_block = "\n".join(_const(name, value) for name, value in constants.items())
    code = textwrap.dedent(f'''\
    """
    Studio-generated Airflow DAG for {constants["CARTRIDGE_ID"]}.{constants["ENTITY_NAME"]}.
    Generated from connector_schema and entity metadata. Ready for review/deployment.
    """
    from __future__ import annotations

    import csv
    import io
    import os
    import time
    import uuid
    from datetime import datetime, timedelta, timezone
    from urllib.parse import urljoin

    import pandas as pd
    import requests
    from airflow.decorators import dag, task
    from airflow.models import Variable
    from minio import Minio
    from requests.adapters import HTTPAdapter
    from urllib3.util.retry import Retry


    {const_block}
    MCP_INFRA_URL = os.environ.get("MCP_INFRA_URL", "http://mcp-infra:8010")
    REFINEMENT_URL = os.environ.get("REFINEMENT_URL", "http://refinement:8500")
    MINIO_BUCKET = os.environ.get("MINIO_BUCKET", "lakehouse")


    def _internal_key(env_name: str) -> str:
        key = os.environ.get(env_name) or os.environ.get("INTERNAL_API_KEY")
        if not key:
            raise RuntimeError(f"Missing internal API key: {{env_name}}")
        return key


    def _session() -> requests.Session:
        retry = Retry(
            total=RETRY_MAX,
            connect=RETRY_MAX,
            read=RETRY_MAX,
            backoff_factor=1.5,
            status_forcelist=(429, 500, 502, 503, 504),
            allowed_methods=frozenset(["GET", "POST"]),
        )
        adapter = HTTPAdapter(max_retries=retry)
        session = requests.Session()
        session.mount("http://", adapter)
        session.mount("https://", adapter)
        return session


    def _base_url() -> str:
        value = os.environ.get(BASE_URL_ENV, "").rstrip("/")
        if not value:
            raise RuntimeError(f"Missing base URL env var: {{BASE_URL_ENV}}")
        return value


    def _auth_headers(session: requests.Session) -> dict[str, str]:
        auth_type = AUTH_TYPE.lower()
        if auth_type == "bearer_token":
            token = os.environ.get(TOKEN_ENV, "")
            if not token:
                raise RuntimeError(f"Missing bearer token env var: {{TOKEN_ENV}}")
            return {{API_KEY_HEADER: f"{{HEADER_PREFIX}}{{token}}"}}
        if auth_type == "api_key":
            token = os.environ.get(TOKEN_ENV, "")
            if not token:
                raise RuntimeError(f"Missing API key env var: {{TOKEN_ENV}}")
            return {{API_KEY_HEADER: token}}
        if auth_type == "oauth2_client_credentials":
            token_url = os.environ.get(TOKEN_URL_ENV, "")
            client_id = os.environ.get(CLIENT_ID_ENV, "")
            client_secret = os.environ.get(CLIENT_SECRET_ENV, "")
            if not token_url or not client_id or not client_secret:
                raise RuntimeError("Missing OAuth2 token URL/client credentials")
            response = session.post(
                token_url,
                data={{"grant_type": "client_credentials"}},
                auth=(client_id, client_secret),
                timeout=TIMEOUT_SECONDS,
            )
            response.raise_for_status()
            token = response.json().get("access_token")
            if not token:
                raise RuntimeError("OAuth2 token response did not include access_token")
            return {{"Authorization": f"Bearer {{token}}"}}
        return {{}}


    def _basic_auth() -> tuple[str, str] | None:
        if AUTH_TYPE.lower() != "basic":
            return None
        username = os.environ.get(USER_ENV, "")
        password = os.environ.get(PASSWORD_ENV, "")
        if not username or not password:
            raise RuntimeError(f"Missing basic auth env vars: {{USER_ENV}}/{{PASSWORD_ENV}}")
        return username, password


    def _mcp_headers() -> dict[str, str]:
        return {{
            "x-api-key": _internal_key("INTERNAL_API_KEY_AIRFLOW_TO_MCP_INFRA"),
            "x-internal-service": "airflow",
        }}


    def _watermark_get() -> str | None:
        try:
            response = requests.post(
                f"{{MCP_INFRA_URL}}/mcp/invoke",
                headers=_mcp_headers(),
                json={{
                    "tool": "watermark_get",
                    "args": {{"cartridge_id": CARTRIDGE_ID, "entity": ENTITY_NAME}},
                }},
                timeout=15,
            )
            response.raise_for_status()
            return (response.json().get("result") or {{}}).get("last_value")
        except Exception:
            return None


    def _watermark_set(value: str, run_id: str) -> None:
        if not WATERMARK_FIELD or not value:
            return
        response = requests.post(
            f"{{MCP_INFRA_URL}}/mcp/invoke",
            headers=_mcp_headers(),
            json={{
                "tool": "watermark_set",
                "args": {{
                    "cartridge_id": CARTRIDGE_ID,
                    "entity": ENTITY_NAME,
                    "watermark_field": WATERMARK_FIELD,
                    "value": value,
                    "run_id": run_id,
                }},
            }},
            timeout=15,
        )
        response.raise_for_status()


    def _pipeline_run_save(run_id: str, status: str, record_count: int, path: str | None, error: str | None = None) -> None:
        response = requests.post(
            f"{{MCP_INFRA_URL}}/mcp/invoke",
            headers=_mcp_headers(),
            json={{
                "tool": "pipeline_run_save",
                "args": {{
                    "dag_id": DAG_ID,
                    "cartridge_id": CARTRIDGE_ID,
                    "entity": ENTITY_NAME,
                    "run_id": run_id,
                    "status": status,
                    "record_count": record_count,
                    "bronze_path": path,
                    "error": error,
                }},
            }},
            timeout=15,
        )
        response.raise_for_status()


    def _upload_parquet(rows: list[dict], run_id: str) -> tuple[str | None, int]:
        if not rows:
            return None, 0
        df = pd.DataFrame(rows)
        load_date = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        object_name = f"raw/{{CARTRIDGE_ID}}/{{ENTITY_NAME}}/load_date={{load_date}}/batch_id={{run_id}}/data.parquet"
        buffer = io.BytesIO()
        df.to_parquet(buffer, index=False)
        buffer.seek(0)
        size = buffer.getbuffer().nbytes
        client = Minio(
            os.environ.get("MINIO_ENDPOINT", Variable.get("minio_endpoint")),
            access_key=os.environ.get("MINIO_ACCESS_KEY", Variable.get("minio_access_key")),
            secret_key=os.environ.get("MINIO_SECRET_KEY", Variable.get("minio_secret_key")),
            secure=os.environ.get("MINIO_SECURE", "false").lower() == "true",
        )
        if not client.bucket_exists(MINIO_BUCKET):
            client.make_bucket(MINIO_BUCKET)
        client.put_object(MINIO_BUCKET, object_name, buffer, size, content_type="application/octet-stream")
        return f"s3://{{MINIO_BUCKET}}/{{object_name}}", len(rows)


    def _max_watermark(rows: list[dict]) -> str | None:
        if not WATERMARK_FIELD:
            return None
        values = [str(row.get(WATERMARK_FIELD)) for row in rows if row.get(WATERMARK_FIELD) not in (None, "")]
        return max(values) if values else None


    def _extract_odata(session: requests.Session, watermark: str | None) -> list[dict]:
        rows: list[dict] = []
        base = _base_url()
        endpoint = urljoin(f"{{base}}/", ODATA_ENTITY.lstrip("/"))
        headers = {{"Accept": "application/json", **_auth_headers(session)}}
        auth = _basic_auth()
        skip = 0
        next_url: str | None = endpoint
        while next_url:
            params = {{"$top": str(PAGE_SIZE), "$skip": str(skip)}}
            if SELECT_FIELDS:
                params["$select"] = ",".join(SELECT_FIELDS)
            filters = []
            if ODATA_FILTER:
                filters.append(f"({{ODATA_FILTER}})")
            if MODE == "incremental" and WATERMARK_FIELD and watermark:
                filters.append(f"{{WATERMARK_FIELD}} gt '{{watermark}}'")
            if filters:
                params["$filter"] = " and ".join(filters)
            response = session.get(next_url, headers=headers, params=params, auth=auth, timeout=TIMEOUT_SECONDS)
            response.raise_for_status()
            payload = response.json()
            batch = payload.get("value")
            if batch is None:
                batch = (payload.get("d") or {{}}).get("results")
            if batch is None:
                batch = payload.get("results") or []
            rows.extend(batch)
            next_url = payload.get("@odata.nextLink") or (payload.get("d") or {{}}).get("__next")
            if next_url:
                continue
            if len(batch) < PAGE_SIZE:
                break
            skip += PAGE_SIZE
        return rows


    def _extract_replicon_async(session: requests.Session, watermark: str | None) -> list[dict]:
        base = _base_url()
        headers = {{"Accept": "application/json", "Content-Type": "application/json", **_auth_headers(session)}}
        payload = {{"entity": ENTITY_NAME, "mode": MODE, "page_size": PAGE_SIZE}}
        if MODE == "incremental" and WATERMARK_FIELD and watermark:
            payload["watermark"] = {{"field": WATERMARK_FIELD, "value": watermark}}
        start = session.post(f"{{base}}/extracts", json=payload, headers=headers, timeout=TIMEOUT_SECONDS)
        start.raise_for_status()
        data = start.json()
        extract_id = data.get("id") or data.get("extract_id") or data.get("job_id")
        status_url = data.get("status_url") or (f"{{base}}/extracts/{{extract_id}}" if extract_id else None)
        if not status_url:
            raise RuntimeError("Replicon export did not return extract id or status_url")
        final_payload = data
        for _ in range(POLL_MAX_ATTEMPTS):
            poll = session.get(status_url, headers=headers, timeout=TIMEOUT_SECONDS)
            poll.raise_for_status()
            final_payload = poll.json()
            status = str(final_payload.get("status") or final_payload.get("state") or "").lower()
            if status in {{"complete", "completed", "success", "succeeded", "done"}}:
                break
            if status in {{"failed", "error", "cancelled", "canceled"}}:
                raise RuntimeError(f"Replicon export failed: {{final_payload}}")
            time.sleep(POLL_INTERVAL_SECONDS)
        else:
            raise TimeoutError("Replicon export polling timed out")
        download_url = final_payload.get("download_url") or final_payload.get("result_url")
        if not download_url and extract_id:
            download_url = f"{{base}}/extracts/{{extract_id}}/download"
        response = session.get(download_url, headers=headers, timeout=TIMEOUT_SECONDS)
        response.raise_for_status()
        content_type = response.headers.get("content-type", "")
        if "json" in content_type:
            parsed = response.json()
            return parsed if isinstance(parsed, list) else parsed.get("records", [])
        return list(csv.DictReader(io.StringIO(response.text)))


    def _extract_rest_offset(session: requests.Session, watermark: str | None) -> list[dict]:
        rows: list[dict] = []
        endpoint = urljoin(f"{{_base_url()}}/", ENTITY_NAME)
        headers = {{"Accept": "application/json", **_auth_headers(session)}}
        auth = _basic_auth()
        offset = 0
        while True:
            params = {{"limit": PAGE_SIZE, "offset": offset}}
            if MODE == "incremental" and WATERMARK_FIELD and watermark:
                params[WATERMARK_FIELD] = watermark
            response = session.get(endpoint, headers=headers, params=params, auth=auth, timeout=TIMEOUT_SECONDS)
            response.raise_for_status()
            payload = response.json()
            batch = payload if isinstance(payload, list) else payload.get("data") or payload.get("records") or []
            rows.extend(batch)
            if len(batch) < PAGE_SIZE:
                break
            offset += PAGE_SIZE
        return rows


    @dag(
        dag_id=DAG_ID,
        start_date=datetime(2024, 1, 1),
        schedule=None,
        catchup=False,
        default_args={{"retries": RETRY_MAX, "retry_delay": timedelta(minutes=2)}},
        tags=["mode", CARTRIDGE_ID, "bronze", "studio-generated", CONNECTOR_KIND],
    )
    def dynamic_extract_dag():
        @task(retries=RETRY_MAX, retry_delay=timedelta(minutes=2))
        def extract() -> dict:
            run_id = str(uuid.uuid4())
            watermark = _watermark_get() if MODE == "incremental" else None
            try:
                session = _session()
                if CONNECTOR_KIND == "odata":
                    rows = _extract_odata(session, watermark)
                elif CONNECTOR_KIND == "replicon_async":
                    rows = _extract_replicon_async(session, watermark)
                else:
                    rows = _extract_rest_offset(session, watermark)
                path, count = _upload_parquet(rows, run_id)
                new_watermark = _max_watermark(rows)
                if new_watermark:
                    _watermark_set(new_watermark, run_id)
                _pipeline_run_save(run_id, "success", count, path)
                return {{"run_id": run_id, "record_count": count, "bronze_path": path, "watermark": new_watermark}}
            except Exception as exc:
                _pipeline_run_save(run_id, "failed", 0, None, str(exc))
                raise

        extract()


    dag = dynamic_extract_dag()
    ''')
    return "\n".join(line[4:] if line.startswith("    ") else line for line in code.splitlines()).lstrip()
