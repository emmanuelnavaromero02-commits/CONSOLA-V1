from __future__ import annotations


RUNTIME_SCRIPT = r"""
import json, os, shutil
from datetime import datetime, timezone
from pathlib import Path
from sqlalchemy import create_engine, text
from app.core.request_context import _sign_security_context, set_security_context
from app.services import duckdb_service
from app.services.kb_config_reconciliation import reconcile_packaged_kbs
from app.services.kb_materialization import (
    create_kb_run, fail_kb_run, finish_kb_run, load_base_currency_config,
)

canonical = Path(os.environ["CANONICAL_SQL"]).read_text()
resolved = Path(os.environ["RESOLVED_SQL"]).read_text()
config = {
    "id": "kb_wip_mensual", "name": "WIP Mensual", "sql": canonical,
    "pg_table": "customer_table", "output_path": "customer/path",
    "package_version": "replicon.wip_mensual.v3",
}
engine = create_engine(os.environ["DATABASE_URL"])
with engine.begin() as conn:
    counts = reconcile_packaged_kbs(conn, [config], "replicon")
assert counts["upgraded"] == 1
ctx = _sign_security_context({
    "trusted": True, "source": "live-test", "role": "admin",
    "tenant_id": os.environ["TENANT_ID"], "workspace_id": os.environ["WORKSPACE_ID"],
})
set_security_context(ctx)
base_currency, input_digest = load_base_currency_config(ctx)
frame = duckdb_service.run_kb_sql(
    resolved, runtime_tables={"replicon_base_currency": base_currency}
)
run = create_kb_run(
    "kb_wip_mensual", datetime.now(timezone.utc), ctx, config, input_digest
)
duckdb_service.upload_file_to_minio = lambda *, local_path, object_name: shutil.copy2(
    local_path, os.environ["ARTIFACT_PATH"]
)
storage_uri = duckdb_service.write_kb_parquet(
    frame, config["output_path"], config["id"], run.run_id, ctx,
    package_version=run.package_version, sql_digest=run.sql_digest,
    input_digest=run.input_digest,
)
duckdb_service.write_kb_to_postgres(
    frame, config["pg_table"], ctx, materialization_run=run
)
finish_kb_run(run, len(frame), storage_uri, datetime.now(timezone.utc))

stale = create_kb_run(
    "kb_wip_mensual", datetime.now(timezone.utc), ctx, config, input_digest
)
stale_frame = frame.copy()
stale_frame["projectname"] = "STALE-PACKAGE-RACE"
duckdb_service.write_kb_to_postgres(
    stale_frame, config["pg_table"], ctx, materialization_run=stale
)
with engine.begin() as conn:
    conn.execute(text("UPDATE kb_config SET package_sql_digest='changed-during-run' "
        "WHERE cartridge_id='replicon' AND kb_id='kb_wip_mensual'"))
try:
    finish_kb_run(stale, len(stale_frame), "s3://stale", datetime.now(timezone.utc))
except RuntimeError:
    fail_kb_run(stale, "package provenance changed", datetime.now(timezone.utc))
else:
    raise AssertionError("stale package run was published")
with engine.begin() as conn:
    conn.execute(text("UPDATE kb_config SET package_sql_digest=:digest "
        "WHERE cartridge_id='replicon' AND kb_id='kb_wip_mensual'"),
        {"digest": run.sql_digest})
engine.dispose()
Path(os.environ["RESULT_JSON"]).write_text(json.dumps({
    "current_run": run.run_id, "stale_run": stale.run_id, "rows": len(frame),
    "package_version": run.package_version, "sql_digest": run.sql_digest,
    "input_digest": run.input_digest,
}))
"""


__all__ = ("RUNTIME_SCRIPT",)
