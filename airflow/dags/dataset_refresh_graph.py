from __future__ import annotations

import json
from collections import defaultdict
from typing import Any


def _required_scope(conf: dict[str, Any]) -> tuple[str, str]:
    tenant = str(conf.get("tenant_id") or "").strip()
    workspace = str(conf.get("workspace_id") or "").strip()
    if not tenant or not workspace:
        raise ValueError(
            "tenant_id and workspace_id are required for dataset_refresh_chain"
        )
    return tenant, workspace


def _cartridge_from_seed_raw(seed_raw: str) -> str:
    parts = str(seed_raw or "").strip("/").split("/")
    return parts[1] if len(parts) >= 3 and parts[0].lower() == "raw" else ""


def _raw_key(source: object) -> str | None:
    parts = str(source or "").strip().strip("/").lower().split("/")
    if len(parts) >= 3 and parts[0] == "raw" and parts[1] and parts[2]:
        return f"{parts[1]}/{parts[2]}"
    return None


def _extracted_raw(
    cur: Any, tenant_id: str, workspace_id: str, keys: set[str]
) -> set[str]:
    if not keys:
        return set()
    cur.execute(
        """
        SELECT DISTINCT lower(cartridge_id), lower(entity)
          FROM pipeline_runs
         WHERE tenant_id = %s::uuid
           AND workspace_id = %s::uuid
           AND lower(cartridge_id) = ANY(%s::text[])
           AND lower(entity) = ANY(%s::text[])
           AND dag_id <> 'dataset_refresh_chain'
           AND lower(status) IN ('success', 'partial')
           AND (COALESCE(record_count, 0) > 0 OR COALESCE(storage_uri, '') <> '')
        """,
        (
            tenant_id,
            workspace_id,
            sorted({key.split("/", 1)[0] for key in keys}),
            sorted({key.split("/", 1)[1] for key in keys}),
        ),
    )
    return {f"{cartridge}/{entity}" for cartridge, entity in cur.fetchall()} & keys


def _load_graph(
    dsn: str,
    *,
    tenant_id: str,
    workspace_id: str,
) -> dict[str, dict[str, Any]]:
    import psycopg2

    with psycopg2.connect(dsn) as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT set_config('app.tenant_id', %s, true), "
            "set_config('app.workspace_id', %s, true)",
            (tenant_id, workspace_id),
        )
        cur.execute(
            """
            SELECT name, layer, cartridge, COALESCE(sources, '[]'::jsonb),
                   last_refresh IS NOT NULL
              FROM datasets
             WHERE tenant_id = %s::uuid
               AND workspace_id = %s::uuid
             ORDER BY name
            """,
            (tenant_id, workspace_id),
        )
        rows = cur.fetchall()
        graph: dict[str, dict[str, Any]] = {}
        for name, layer, cartridge, sources, materialized in rows:
            if isinstance(sources, str):
                try:
                    sources = json.loads(sources)
                except (TypeError, json.JSONDecodeError):
                    sources = []
            graph[str(name)] = {
                "layer": str(layer),
                "cartridge": str(cartridge or ""),
                "sources": list(sources or []),
                "materialized": materialized is True,
            }
        raw_keys = {
            key
            for info in graph.values()
            for source in info["sources"]
            if (key := _raw_key(source))
        }
        extracted = _extracted_raw(cur, tenant_id, workspace_id, raw_keys)
    for info in graph.values():
        info["unextracted_raw"] = sorted(
            {
                f"raw/{key}"
                for source in info["sources"]
                if (key := _raw_key(source)) and key not in extracted
            }
        )
    return graph


def _source_matches(source: str, name: str, cartridge: str) -> bool:
    value = str(source or "").strip().lower()
    dataset = name.lower()
    owner = cartridge.lower()
    return value in {
        dataset,
        f"silver_{dataset}",
        f"gold_{dataset}",
        f"silver/{owner}/{dataset}",
        f"gold/{owner}/{dataset}",
    }


def _upstream_datasets(graph: dict[str, dict[str, Any]], name: str) -> list[str]:
    upstreams: list[str] = []
    for source in graph[name]["sources"]:
        if str(source).strip().lower().startswith("raw/"):
            continue
        for candidate, candidate_info in graph.items():
            if _source_matches(str(source), candidate, candidate_info["cartridge"]):
                upstreams.append(candidate)
                break
    return upstreams


def _reverse_index(graph: dict[str, dict[str, Any]]) -> dict[str, list[str]]:
    reverse: dict[str, list[str]] = defaultdict(list)
    for downstream, info in graph.items():
        for source in info["sources"]:
            source_text = str(source).strip().lower()
            if source_text.startswith("raw/"):
                reverse[source_text].append(downstream)
        for upstream in _upstream_datasets(graph, downstream):
            reverse[upstream].append(downstream)
    return reverse


def _never_materialized_upstreams(
    graph: dict[str, dict[str, Any]], name: str, upstreams: list[str]
) -> list[str]:
    datasets = [u for u in upstreams if graph[u].get("materialized") is False]
    return sorted(set(datasets)) + list(graph[name].get("unextracted_raw") or [])


def _resolve_cartridge(
    conf: dict[str, Any],
    graph: dict[str, dict[str, Any]],
    seed_raw: str,
    seed_dataset: str,
) -> str:
    requested = str(conf.get("cartridge_id") or "").strip()
    inferred = _cartridge_from_seed_raw(seed_raw)
    if seed_dataset and seed_dataset in graph:
        inferred = str(graph[seed_dataset].get("cartridge") or "").strip()
    if requested and inferred and requested != inferred:
        raise ValueError("cartridge_id does not match the scoped seed")
    cartridge = requested or inferred
    if not cartridge:
        raise ValueError("cartridge_id is required or must be inferable from the seed")
    return cartridge


def _validate_plan(plan: list[dict[str, Any]], cartridge: str) -> None:
    if any(str(item.get("cartridge") or "") != cartridge for item in plan):
        raise ValueError("dataset_refresh_chain crossed a cartridge boundary")


def _build_plan(
    graph: dict[str, dict[str, Any]],
    *,
    seed_raw: str,
    seed_dataset: str,
    maximum_depth: int,
) -> list[dict[str, Any]]:
    reverse = _reverse_index(graph)
    if seed_raw:
        raw_key = seed_raw.strip().strip("/").lower()
        if raw_key.endswith("/*"):
            cartridge_prefix = raw_key[:-1]
            if cartridge_prefix.count("/") != 2:
                raise ValueError("cartridge raw seed must be raw/<cartridge>/*")
            roots = sorted(
                {
                    child
                    for key, children in reverse.items()
                    if key.startswith(cartridge_prefix)
                    for child in children
                }
            )
            if not roots:
                raise ValueError("raw seed does not exist in the active workspace graph")
        elif raw_key not in reverse:
            raise ValueError("raw seed does not exist in the active workspace graph")
        else:
            roots = list(reverse[raw_key])
        ranks: dict[str, int] = {}
        root_depth = 1
    else:
        if seed_dataset not in graph:
            raise ValueError("seed dataset does not exist in the active workspace")
        roots = [seed_dataset]
        ranks = {seed_dataset: 0}
        root_depth = 0

    visiting: set[str] = set()
    visited: set[str] = set()

    def visit(name: str) -> None:
        if name in visiting:
            raise ValueError("dataset dependency cycle detected")
        if name in visited:
            return
        visiting.add(name)
        for child in reverse.get(name, []):
            visit(child)
        visiting.remove(name)
        visited.add(name)

    for root in roots:
        visit(root)

    frontier = [(name, root_depth) for name in roots]
    while frontier:
        name, depth = frontier.pop(0)
        if depth > maximum_depth:
            raise ValueError("dataset dependency plan exceeded max_depth")
        ranks[name] = max(ranks.get(name, -1), depth)
        for child in reverse.get(name, []):
            frontier.append((child, depth + 1))

    plan = []
    for name, rank in sorted(ranks.items(), key=lambda item: (item[1], item[0])):
        upstreams = sorted(set(_upstream_datasets(graph, name)))
        plan.append(
            {
                "name": name,
                "layer": graph[name]["layer"],
                "cartridge": graph[name]["cartridge"],
                "rank": rank,
                "upstreams": upstreams,
                "materialized": graph[name].get("materialized") is not False,
                "never_materialized_upstreams": _never_materialized_upstreams(
                    graph, name, upstreams
                ),
            }
        )
    return plan


def resolve_chain(
    context: dict[str, Any],
    *,
    postgres_dsn: str,
    admitted_conf: dict[str, Any] | None = None,
) -> int:
    conf = admitted_conf or {}
    tenant_id, workspace_id = _required_scope(conf)
    seed_raw = str(conf.get("seed_raw") or "").strip()
    seed_dataset = str(conf.get("seed_dataset") or "").strip()
    if not seed_raw and not seed_dataset:
        raise ValueError("conf must include seed_raw or seed_dataset")
    graph = _load_graph(
        postgres_dsn,
        tenant_id=tenant_id,
        workspace_id=workspace_id,
    )
    cartridge = _resolve_cartridge(conf, graph, seed_raw, seed_dataset)
    maximum_depth = max(1, min(int(conf.get("max_depth") or 10), 50))
    plan = _build_plan(
        graph,
        seed_raw=seed_raw,
        seed_dataset=seed_dataset,
        maximum_depth=maximum_depth,
    )
    _validate_plan(plan, cartridge)
    context["ti"].xcom_push(key="plan", value=plan)
    context["ti"].xcom_push(key="cartridge_id", value=cartridge)
    return len(plan)


__all__ = ["_build_plan", "_required_scope", "_validate_plan", "resolve_chain"]
