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


CARTRIDGE_ID    = "platform"
ENTITY          = "DatasetRefreshChain"
REFINEMENT_URL  = os.environ.get("REFINEMENT_URL",  "http://refinement:8500")
MCP_INFRA_URL   = os.environ.get("MCP_INFRA_URL",   "http://mcp-infra:8010")
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


def _internal_headers(target: str) -> dict[str, str]:
    env_name = f"INTERNAL_API_KEY_AIRFLOW_TO_{target}"
    return {
        "X-Internal-Service": "airflow",
        "X-API-Key": _internal_key(env_name),
    }


def _load_graph() -> dict[str, dict]:
    """Devuelve {dataset_name: {layer, cartridge, sources:[str]}}."""
    conn = _pg()
    try:
        with conn.cursor() as cur:
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
            try:    sources = _json.loads(sources)
            except: sources = []
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

    graph = _load_graph()
    rev   = _build_reverse_index(graph)

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
        {"name": n, "layer": graph[n]["layer"], "rank": r}
        for n, r in ordered if n in graph
    ]
    print(f"[refresh_chain] seed={seed_raw or seed_dataset} → {len(plan)} datasets:")
    for p in plan:
        print(f"  rank={p['rank']}  {p['layer']}.{p['name']}")
    ctx["ti"].xcom_push(key="plan", value=plan)
    return len(plan)


# ── Task 2 · materialize_in_order ────────────────────────────────────────────

def materialize_in_order(**ctx):
    plan = ctx["ti"].xcom_pull(task_ids="resolve_chain", key="plan") or []
    if not plan:
        print("[refresh_chain] nada que materializar")
        return {"materialized": 0, "results": []}

    results = []
    for item in plan:
        name = item["name"]
        try:
            r = requests.post(
                f"{REFINEMENT_URL}/mcp/invoke",
                headers=_internal_headers("REFINEMENT"),
                json={"tool": "materialize", "args": {"name": name}},
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
    print(f"[refresh_chain] materialized {ok}/{len(results)}")
    return {"materialized": ok, "results": results}


# ── Task 3 · record_run ──────────────────────────────────────────────────────

def record_run(**ctx):
    inv     = ctx["ti"].xcom_pull(task_ids="materialize_in_order") or {}
    started = ctx["logical_date"].isoformat()
    ended   = datetime.now(timezone.utc).isoformat()
    materialized = int(inv.get("materialized") or 0)
    results = inv.get("results") or []
    total = len(results)
    status = "success" if total == materialized else "partial" if materialized else "failed"
    if total == 0:
        status = "success"
    try:
        r = requests.post(
            f"{MCP_INFRA_URL}/mcp/invoke",
            headers=_internal_headers("MCP_INFRA"),
            json={"tool": "pipeline_run_save",
                  "args": {"dag_id": "dataset_refresh_chain",
                           "cartridge_id": CARTRIDGE_ID,
                           "entity":       ENTITY,
                           "run_id":       f"dataset_refresh_chain:{ctx['run_id']}",
                           "airflow_dag_run_id": ctx["run_id"],
                           "mode":         "refresh",
                           "status":       status,
                           "started_at":   started,
                           "finished_at":  ended,
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


t_resolve = PythonOperator(task_id="resolve_chain",          python_callable=resolve_chain,        dag=dag)
t_mat     = PythonOperator(task_id="materialize_in_order",   python_callable=materialize_in_order, dag=dag)
t_rec     = PythonOperator(task_id="record_run",             python_callable=record_run,           dag=dag)

t_resolve >> t_mat >> t_rec
