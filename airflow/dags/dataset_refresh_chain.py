"""
DAG: dataset_refresh_chain  (platform · orchestrator)
=====================================================
Propaga materializaciones aguas abajo a partir de una semilla (raw entity
o dataset). Recorre el grafo de dependencias declarado en
`datasets.sources` (JSONB), ordena topológicamente y dispara
`refinement.materialize` por cada dataset.

Trigger típico:
    conf = {"seed_raw": "raw/replicon/ProjectAudit"}            # tras una extracción
    conf = {"seed_dataset": "replicon_user_latest"}             # tras refresh manual de un silver
    conf = {"seed_raw": "raw/replicon/User", "max_depth": 5}    # opcional cap de profundidad

Formato de `datasets.sources` reconocido:
    "raw/<cartridge>/<Entity>"           — entrada bronze
    "silver/<cartridge>/<name>"          — otro silver (path-style)
    "silver_<name>" / "gold_<name>"      — otro silver/gold (tabla-style)
    "<name>"                              — bare name (silver o gold del mismo cartucho)

El orden topológico se calcula como rangos (BFS por niveles) — un dataset
se materializa solo cuando todos sus ancestros ya fueron procesados.
"""
from __future__ import annotations

import os
from collections import defaultdict, deque
from datetime import datetime, timedelta, timezone

import requests
from airflow import DAG
from airflow.operators.python import PythonOperator
from airflow.utils.trigger_rule import TriggerRule


CARTRIDGE_ID    = "platform"
ENTITY          = "DatasetRefreshChain"
REFINEMENT_URL  = os.environ.get("REFINEMENT_URL",  "http://refinement:8500")
MCP_INFRA_URL   = os.environ.get("MCP_INFRA_URL",   "http://mcp-infra:8010")
CONSOLE_URL     = (
    os.environ.get("CONSOLE_INTERNAL_URL")
    or os.environ.get("CONSOLE_URL", "http://console:8000")
)
POSTGRES_DSN    = os.environ.get(
    "DATABASE_URL",
    "postgresql://postgres:postgres@postgres:5432/modecissions",
).replace("postgresql+psycopg2://", "postgresql://")


default_args = {
    "owner":       "platform",
    "retries":     1,
    "retry_delay": timedelta(minutes=2),
}


dag = DAG(
    dag_id="dataset_refresh_chain",
    default_args=default_args,
    description="Propaga materializaciones por el grafo datasets.sources a partir de una semilla.",
    schedule_interval=None,
    start_date=datetime(2026, 5, 1),
    tags=["platform", "orchestrator", "refresh"],
    catchup=False,
    max_active_runs=4,
    params={
        "seed_raw":     {"type": "string", "default": ""},
        "seed_dataset": {"type": "string", "default": ""},
        "max_depth":    {"type": "integer", "default": 10},
    },
)


# ── Helpers ──────────────────────────────────────────────────────────────────

def _pg():
    import psycopg2
    return psycopg2.connect(POSTGRES_DSN)


def _datasets_has_column(column: str) -> bool:
    conn = _pg()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT 1
                  FROM information_schema.columns
                 WHERE table_schema = 'public'
                   AND table_name = 'datasets'
                   AND column_name = %s
                 LIMIT 1
                """,
                (column,),
            )
            return bool(cur.fetchone())
    finally:
        conn.close()


def _is_production() -> bool:
    return os.environ.get("APP_ENV", "production").strip().lower() in {"production", "prod"}


def _internal_key(env_name: str) -> str:
    key = os.environ.get(env_name, "")
    if key:
        return key
    if not _is_production():
        legacy = os.environ.get("INTERNAL_API_KEY", "")
        if legacy:
            return legacy
    raise RuntimeError(f"{env_name} missing; legacy INTERNAL_API_KEY fallback is disabled in production")


def _internal_headers(target: str, ctx: dict | None = None) -> dict[str, str]:
    env_name = f"INTERNAL_API_KEY_AIRFLOW_TO_{target}"
    headers = {
        "X-Internal-Service": "airflow",
        "X-API-Key": _internal_key(env_name),
    }
    if ctx:
        headers["X-Request-ID"] = f"airflow:dataset_refresh_chain:{ctx.get('run_id', 'manual')}"
    return headers


def _security_context_for(item: dict, conf: dict) -> dict:
    cartridge = str(item.get("cartridge") or conf.get("cartridge_id") or "").strip()
    tenant_id = str(conf.get("tenant_id") or "").strip()
    workspace_id = str(conf.get("workspace_id") or "").strip()
    if cartridge and tenant_id and workspace_id:
        scope = f"tenant_id={tenant_id}/workspace_id={workspace_id}/"
        prefixes = [
            f"raw/{cartridge}/{scope}",
            f"silver/{cartridge}/{scope}",
            f"gold/{cartridge}/{scope}",
            f"uploads/{cartridge}/{scope}",
            f"cartridges/{cartridge}/",
        ]
    elif cartridge:
        prefixes = [f"{layer}/{cartridge}/" for layer in ("raw", "silver", "gold", "uploads", "cartridges")]
    else:
        prefixes = []
    return {
        "trusted": True,
        "source": "airflow",
        "role": "admin",
        "permissions": ["datasets.read", "datasets.write"],
        "tenant_id": tenant_id,
        "workspace_id": workspace_id,
        "allowed_cartridges": [cartridge] if cartridge else [],
        "allowed_prefixes": prefixes,
    }


def _cartridge_from_seed_raw(seed_raw: str) -> str:
    parts = (seed_raw or "").strip("/").split("/")
    if len(parts) >= 3 and parts[0].lower() == "raw":
        return parts[1]
    return ""


def _scoped_cartridge(conf: dict, graph: dict[str, dict], seed_raw: str, seed_dataset: str) -> str:
    requested = str(conf.get("cartridge_id") or "").strip()
    inferred = ""
    if seed_raw:
        inferred = _cartridge_from_seed_raw(seed_raw)
    elif seed_dataset and seed_dataset in graph:
        inferred = str(graph[seed_dataset].get("cartridge") or "").strip()
    cartridge = requested or inferred
    if requested and inferred and requested != inferred:
        raise ValueError(
            f"cartridge_id mismatch: conf={requested!r} seed={inferred!r}"
        )
    if not cartridge:
        raise ValueError("cartridge_id is required or must be inferable from the seed")
    return cartridge


def _require_run_scope(conf: dict, cartridge: str) -> tuple[str, str]:
    tenant_id = str(conf.get("tenant_id") or "").strip()
    workspace_id = str(conf.get("workspace_id") or "").strip()
    if tenant_id and workspace_id:
        return tenant_id, workspace_id
    if cartridge == "platform" and conf.get("allow_unscoped_platform"):
        return tenant_id, workspace_id
    raise ValueError("tenant_id and workspace_id are required for dataset_refresh_chain")


def _validate_plan_scope(plan: list[dict], cartridge: str) -> None:
    mismatches = [
        f"{item.get('name')}:{item.get('cartridge') or '<none>'}"
        for item in plan
        if str(item.get("cartridge") or "").strip() != cartridge
    ]
    if mismatches:
        raise ValueError(
            f"dataset_refresh_chain crossed cartridge boundary for {cartridge}: "
            + ", ".join(mismatches[:10])
        )


def _load_graph(workspace_id: str | None = None) -> dict[str, dict]:
    """Devuelve {dataset_name: {layer, cartridge, sources:[str]}}."""
    conn = _pg()
    try:
        with conn.cursor() as cur:
            workspace_id = str(workspace_id or "").strip()
            if workspace_id:
                if not _datasets_has_column("workspace_id"):
                    raise RuntimeError("datasets.workspace_id column is required for scoped refresh")
                cur.execute(
                    "SELECT name, layer, cartridge, COALESCE(sources,'[]'::jsonb) "
                    "FROM datasets WHERE workspace_id = %s",
                    (workspace_id,),
                )
            else:
                cur.execute(
                    "SELECT name, layer, cartridge, COALESCE(sources,'[]'::jsonb) "
                    "FROM datasets"
                )
            rows = cur.fetchall()
    finally:
        conn.close()
    out = {}
    for name, layer, cartridge, sources in rows:
        if isinstance(sources, str):
            import json as _json
            try:
                sources = _json.loads(sources)
            except (_json.JSONDecodeError, TypeError):
                sources = []
        out[name] = {"layer": layer, "cartridge": cartridge or "", "sources": list(sources or [])}
    return out


def _source_matches(source: str, name: str, cartridge: str) -> bool:
    """¿La string `source` referencia al dataset `name` (cartridge=cartridge)?"""
    src = (source or "").strip().lower()
    nm  = name.lower()
    c   = (cartridge or "").lower()
    return src in (
        nm,
        f"silver_{nm}",
        f"gold_{nm}",
        f"silver/{c}/{nm}",
        f"gold/{c}/{nm}",
    )


def _build_reverse_index(graph: dict[str, dict]) -> dict[str, list[str]]:
    """name del dataset → lista de datasets que lo tienen como source."""
    rev = defaultdict(list)
    for downstream, info in graph.items():
        for src in info["sources"]:
            # Si el source es 'raw/...' lo dejamos como key tal cual; si es
            # referencia a otro dataset, lo registramos por su name canónico.
            src_low = src.strip().lower()
            if src_low.startswith("raw/"):
                rev[src_low].append(downstream)
                continue
            # Buscar al dataset que coincida con esta source
            for cand, info_c in graph.items():
                if _source_matches(src, cand, info_c["cartridge"]):
                    rev[cand].append(downstream)
                    break
    return rev


# ── Task 1 · resolve_chain ───────────────────────────────────────────────────

def resolve_chain(**ctx):
    conf = (ctx.get("dag_run").conf if ctx.get("dag_run") else {}) or {}
    seed_raw     = (conf.get("seed_raw") or "").strip()
    seed_dataset = (conf.get("seed_dataset") or "").strip()
    max_depth    = int(conf.get("max_depth") or 10)

    if not seed_raw and not seed_dataset:
        raise ValueError("conf debe incluir seed_raw o seed_dataset")

    workspace_id = str(conf.get("workspace_id") or "").strip()
    graph = _load_graph(workspace_id)
    rev   = _build_reverse_index(graph)
    cartridge = _scoped_cartridge(conf, graph, seed_raw, seed_dataset)
    _require_run_scope(conf, cartridge)

    # BFS por niveles → asignamos rank al dataset (mayor rank = más profundo)
    rank: dict[str, int] = {}
    if seed_raw:
        start_key = seed_raw.lower()
        # los downstream nivel 1 son los datasets que listan ese raw en sources
        frontier = list(rev.get(start_key, []))
    else:
        # seed es un dataset: él se materializa primero, luego sus descendientes
        if seed_dataset not in graph:
            raise ValueError(f"dataset '{seed_dataset}' no existe")
        rank[seed_dataset] = 0
        frontier = list(rev.get(seed_dataset, []))

    depth = 1
    while frontier and depth <= max_depth:
        next_frontier = []
        for ds in frontier:
            # Reasignamos rank si encontramos un camino más largo (toposort por niveles)
            if rank.get(ds, -1) < depth:
                rank[ds] = depth
            for child in rev.get(ds, []):
                if child != ds:
                    next_frontier.append(child)
        frontier = next_frontier
        depth   += 1

    ordered = sorted(rank.items(), key=lambda kv: (kv[1], kv[0]))
    plan = [
        {"name": n, "layer": graph[n]["layer"], "cartridge": graph[n]["cartridge"], "rank": r}
        for n, r in ordered if n in graph
    ]
    _validate_plan_scope(plan, cartridge)
    print(f"[refresh_chain] seed={seed_raw or seed_dataset} → {len(plan)} datasets:")
    for p in plan:
        print(f"  rank={p['rank']}  {p['layer']}.{p['name']}")
    ctx["ti"].xcom_push(key="plan", value=plan)
    ctx["ti"].xcom_push(key="cartridge_id", value=cartridge)
    return len(plan)


# ── Task 2 · materialize_in_order ────────────────────────────────────────────

def materialize_in_order(**ctx):
    plan = ctx["ti"].xcom_pull(task_ids="resolve_chain", key="plan") or []
    scoped_cartridge = ctx["ti"].xcom_pull(task_ids="resolve_chain", key="cartridge_id")
    conf = (ctx.get("dag_run").conf if ctx.get("dag_run") else {}) or {}
    if not scoped_cartridge:
        graph = _load_graph(str(conf.get("workspace_id") or "").strip())
        scoped_cartridge = _scoped_cartridge(
            conf,
            graph,
            str(conf.get("seed_raw") or ""),
            str(conf.get("seed_dataset") or ""),
        )
    tenant_id, workspace_id = _require_run_scope(conf, scoped_cartridge)
    allow_partial = bool(conf.get("allow_partial"))
    if not plan:
        print("[refresh_chain] nada que materializar")
        return {"materialized": 0, "results": []}
    _validate_plan_scope(plan, scoped_cartridge)
    scoped_conf = {**conf, "cartridge_id": scoped_cartridge, "tenant_id": tenant_id, "workspace_id": workspace_id}

    results = []
    for item in plan:
        name = item["name"]
        try:
            r = requests.post(
                f"{REFINEMENT_URL}/mcp/invoke",
                headers=_internal_headers("REFINEMENT", ctx),
                json={
                    "tool": "materialize",
                    "args": {"name": name},
                    "security_context": _security_context_for(item, scoped_conf),
                },
                timeout=600,
            )
            ok = r.status_code < 400
            body = r.json() if ok else {"http": r.status_code, "text": r.text[:300]}
            if ok and isinstance(body, dict) and body.get("error"):
                ok = False
            results.append({"name": name, "ok": ok, "result": body})
            print(f"[refresh_chain] {'✓' if ok else '✗'} {name}: {str(body)[:200]}")
        except Exception as exc:                                  # noqa: BLE001
            results.append({"name": name, "ok": False, "error": str(exc)})
            print(f"[refresh_chain] ✗ {name}: {exc}")
    ok = sum(1 for r in results if r["ok"])
    result = {"materialized": ok, "results": results}
    print(f"[refresh_chain] materialized {ok}/{len(results)}")
    ctx["ti"].xcom_push(key="result", value=result)
    if ok != len(results) and not allow_partial:
        failed = [str(r.get("name") or "?") for r in results if not r.get("ok")]
        raise RuntimeError(
            "dataset_refresh_chain failed for critical materialization(s): "
            + ", ".join(failed[:10])
        )
    return result


# ── Task 3 · record_run ──────────────────────────────────────────────────────

def _successful_materialized_datasets(results: list[dict]) -> list[str]:
    output: set[str] = set()
    for item in results:
        if not isinstance(item, dict) or not item.get("ok"):
            continue
        name = str(item.get("name") or "").strip()
        if name:
            output.add(name)
    return sorted(output)


def _trigger_gold_refresh_intelligence(
    *,
    ctx: dict,
    tenant_id: str,
    workspace_id: str,
    cartridge_id: str,
    pipeline_run_id: str,
    status: str,
    datasets: list[str],
    finished_at: str,
) -> None:
    conf = (ctx.get("dag_run").conf if ctx.get("dag_run") else {}) or {}
    if bool(conf.get("skip_intelligence")) or _skip_intelligence_for_cartridge(cartridge_id):
        print(
            "[refresh_chain] intelligence skipped: "
            f"skip_intelligence={bool(conf.get('skip_intelligence'))} cartridge={cartridge_id}"
        )
        return
    if not datasets:
        print("[refresh_chain] intelligence skipped: no materialized datasets")
        return
    payload = {
        "tenant_id": tenant_id,
        "workspace_id": workspace_id,
        "cartridge_id": cartridge_id,
        "airflow_dag_run_id": ctx["run_id"],
        "pipeline_run_id": pipeline_run_id,
        "materialization_status": status,
        "datasets": datasets,
        "finished_at": finished_at,
    }
    try:
        r = requests.post(
            f"{CONSOLE_URL.rstrip('/')}/internal/intelligence/gold-refresh",
            headers=_internal_headers("CONSOLE", ctx),
            json=payload,
            timeout=45,
        )
        try:
            body = r.json()
        except Exception:
            body = {"text": r.text[:300]}
        if r.status_code >= 400:
            print(f"[refresh_chain] intelligence trigger HTTP {r.status_code}: {body}")
            return
        print(
            "[refresh_chain] intelligence trigger "
            f"ok={body.get('ok')} run_ref={body.get('run_ref')} "
            f"signals={body.get('signals')} skipped={body.get('skipped')}"
        )
    except Exception as exc:                                      # noqa: BLE001
        print(f"[refresh_chain] intelligence trigger failed: {exc}")

def _skip_intelligence_for_cartridge(cartridge_id: str) -> bool:
    if cartridge_id == "banxico":
        return True
    if cartridge_id == "inegi":
        return True
    if cartridge_id == "sec_edgar":
        return True
    return False

def _chain_status(invocation: dict, *, materialize_failed: bool) -> str:
    """Status of one refresh chain run, decided fail-closed.

    `record_run` runs under ALL_DONE, so it is reached even when
    `materialize_in_order` died. The failure signals are therefore read
    *before* the counters: `total == materialized == 0` is not evidence of a
    clean run, it is also exactly what a hard failure before the XCom push
    looks like. Only an empty plan published by a task that ended clean is
    allowed to report success.
    """
    if not invocation:
        # materialize_in_order never published a result. Whether or not its
        # state could be read, there is no evidence any work happened.
        return "failed"
    materialized = int(invocation.get("materialized") or 0)
    total = len(invocation.get("results") or [])
    if materialize_failed or invocation.get("error"):
        return "partial" if materialized else "failed"
    if total == 0 or materialized == total:
        return "success"
    return "partial" if materialized else "failed"


def record_run(**ctx):
    conf = (ctx.get("dag_run").conf if ctx.get("dag_run") else {}) or {}
    allow_partial = bool(conf.get("allow_partial"))
    inv     = (
        ctx["ti"].xcom_pull(task_ids="materialize_in_order", key="result")
        or ctx["ti"].xcom_pull(task_ids="materialize_in_order")
        or {}
    )
    dag_run = ctx.get("dag_run")
    mat_ti = dag_run.get_task_instance("materialize_in_order") if dag_run else None
    mat_state = str(getattr(mat_ti, "state", "") or "")
    materialize_failed = bool(mat_state and mat_state != "success")
    if not inv and materialize_failed:
        inv = {
            "materialized": 0,
            "results": [],
            "error": f"materialize_in_order ended with state={mat_state}",
        }
    started = ctx["logical_date"].isoformat()
    ended   = datetime.now(timezone.utc).isoformat()
    run_cartridge = (
        ctx["ti"].xcom_pull(task_ids="resolve_chain", key="cartridge_id")
        or str(conf.get("cartridge_id") or CARTRIDGE_ID)
    )
    tenant_id, workspace_id = _require_run_scope(conf, str(run_cartridge))
    results = inv.get("results") or []
    status = _chain_status(inv, materialize_failed=materialize_failed)
    pipeline_run_id = f"dataset_refresh_chain:{ctx['run_id']}"
    try:
        r = requests.post(
            f"{MCP_INFRA_URL}/mcp/invoke",
            headers=_internal_headers("MCP_INFRA", ctx),
            json={"tool": "pipeline_run_save",
                  "args": {"dag_id": "dataset_refresh_chain",
                           "cartridge_id": run_cartridge,
                           "entity":       ENTITY,
                           "run_id":       pipeline_run_id,
                           "airflow_dag_run_id": ctx["run_id"],
                           "mode":         "refresh",
                           "status":       status,
                           "started_at":   started,
                           "finished_at":  ended,
                           "tenant_id":    tenant_id,
                           "workspace_id": workspace_id,
                           "project_id":   conf.get("project_id"),
                           "extra":        inv}},
            timeout=15,
        )
        r.raise_for_status()
        body = r.json()
        if body.get("error"):
            raise RuntimeError(str(body["error"]))
    except Exception as exc:                                      # noqa: BLE001
        print(f"[refresh_chain] pipeline_run_save fallido: {exc}")
        raise
    if status == "success" or (status == "partial" and allow_partial):
        _trigger_gold_refresh_intelligence(
            ctx=ctx,
            tenant_id=tenant_id,
            workspace_id=workspace_id,
            cartridge_id=str(run_cartridge),
            pipeline_run_id=pipeline_run_id,
            status=status,
            datasets=_successful_materialized_datasets(results),
            finished_at=ended,
        )
    if status != "success" and not allow_partial:
        raise RuntimeError(f"dataset_refresh_chain recorded {status}: {inv}")


t_resolve = PythonOperator(task_id="resolve_chain",          python_callable=resolve_chain,        dag=dag)
t_mat     = PythonOperator(task_id="materialize_in_order",   python_callable=materialize_in_order, dag=dag)
t_rec     = PythonOperator(
    task_id="record_run",
    python_callable=record_run,
    trigger_rule=TriggerRule.ALL_DONE,
    dag=dag,
)

t_resolve >> t_mat >> t_rec
