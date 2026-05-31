"""
Cartridge Autopilot — Studio "constructor" engine (Level 1+2 spine).
=====================================================================
Turns an *introspected schema* (entities + typed fields, as produced by
``schema_introspect``) into a COMPLETE cartridge blueprint ready for
``cartridge_service.create_full_cartridge`` — without the operator hand-
writing entities, datasets, KBs, semantics or an agent.

This is the autonomous-construction backbone:
    schema (entities+types)
        -> semantic auto-mapping (PII / money / key / date / metric)
        -> entities (with watermark + primary key inferred)
        -> Silver datasets (latest-partition dedup per entity)
        -> Gold datasets (domain metrics derived from field semantics)
        -> KB (per entity) + business vocabulary + a domain agent
        -> a single manifest validated downstream in one transaction.

Design rules (match the rest of Studio):
- Pure, deterministic, side-effect free: easy to unit-test, no DB / network.
- Emits the EXACT manifest shape ``create_full_cartridge`` consumes.
- Conservative: when a field's role is ambiguous it is left untyped rather
  than guessing a destructive rule (PII detection is opt-in by pattern).
- Gold SQL is layer-aware (no ``read_parquet`` / ``{latest_date}`` — those
  are Silver-only per ``refinement.llm_sql.validate_generated_sql``).
"""
from __future__ import annotations

import re
from typing import Any

# ── Semantic classification ──────────────────────────────────────────────────
# Field "roles" the autopilot can infer from name + canonical type. These drive
# which protection rules, metrics and keys the generated cartridge gets.

_PII_NAME_HINTS = (
    "email", "mail", "phone", "telefono", "celular", "rfc", "curp", "ssn",
    "passport", "pasaporte", "iban", "clabe", "tarjeta", "card", "cuenta",
    "account_number", "birth", "nacimiento", "gbdat", "address", "direccion",
    "salary", "salario", "sueldo", "compensation", "pernr",
)
_MONEY_NAME_HINTS = (
    "amount", "monto", "importe", "price", "precio", "cost", "costo", "revenue",
    "ingreso", "betrg", "dmbtr", "netpr", "total", "value", "valor", "mrr",
    "arr", "billing", "facturacion", "margin", "margen", "budget", "presupuesto",
)
_DATE_TYPES = {"date", "timestamp", "datetime"}
_KEY_NAME_HINTS = ("id", "_id", "code", "codigo", "key", "uuid", "guid", "number")


def classify_field(field: dict[str, Any]) -> dict[str, Any]:
    """Return the field enriched with a semantic ``role`` and protection flag.

    Roles: ``key`` | ``pii`` | ``money`` | ``date`` | ``metric`` | ``dimension``.
    Never raises; unknown fields fall back to ``dimension``.
    """
    name = str(field.get("name") or "").strip()
    lname = name.lower()
    ftype = str(field.get("type") or "string").lower()
    is_pk = bool(field.get("primary_key"))

    role = "dimension"
    protect = False

    if is_pk or lname == "id" or lname.endswith("_id"):
        role = "key"
    elif any(h in lname for h in _PII_NAME_HINTS):
        role = "pii"
        protect = True
    elif ftype in _DATE_TYPES:
        role = "date"
    elif ftype in {"int", "float", "number", "decimal"} and any(
        h in lname for h in _MONEY_NAME_HINTS
    ):
        role = "money"
    elif ftype in {"int", "float", "number", "decimal"}:
        role = "metric"

    out = dict(field)
    out["role"] = role
    out["protected"] = protect
    return out


def _classify_entity(entity: dict[str, Any]) -> dict[str, Any]:
    fields = [classify_field(f) for f in (entity.get("fields") or [])]
    pk = entity.get("primary_key") or next(
        (f["name"] for f in fields if f["role"] == "key"), None
    )
    watermark = entity.get("watermark_field") or next(
        (
            f["name"]
            for f in fields
            if f["role"] == "date"
            and any(w in f["name"].lower() for w in ("modif", "updated", "change", "fecha", "date"))
        ),
        next((f["name"] for f in fields if f["role"] == "date"), None),
    )
    return {
        "name": str(entity.get("name") or entity.get("entity") or "entity"),
        "fields": fields,
        "primary_key": pk,
        "watermark_field": watermark,
        "money_fields": [f["name"] for f in fields if f["role"] == "money"],
        "metric_fields": [f["name"] for f in fields if f["role"] == "metric"],
        "date_fields": [f["name"] for f in fields if f["role"] == "date"],
        "pii_fields": [f["name"] for f in fields if f["role"] == "pii"],
    }


# ── SQL builders (Silver = latest-partition dedup; Gold = domain metrics) ─────


def _silver_sql(cartridge_id: str, ent: dict[str, Any]) -> str:
    name = ent["name"]
    src = f"s3://lakehouse/raw/{cartridge_id}/{name}/**/*.parquet"
    pk = ent["primary_key"]
    order = ent["watermark_field"] or "load_date"
    if pk:
        # Incremental dedup: keep the latest row per key within the newest partition.
        return (
            f"SELECT * EXCLUDE (rn) FROM (\n"
            f"  SELECT *, ROW_NUMBER() OVER (PARTITION BY {pk} ORDER BY {order} DESC) AS rn\n"
            f"  FROM read_parquet('{src}', hive_partitioning=true, union_by_name=true)\n"
            f"  WHERE load_date = '{{latest_date}}'\n"
            f") WHERE rn = 1"
        )
    return (
        f"SELECT * FROM read_parquet('{src}', hive_partitioning=true, union_by_name=true)\n"
        f"WHERE load_date = '{{latest_date}}'"
    )


def _gold_sql(ent: dict[str, Any]) -> str | None:
    """Derive a Gold metric table from the entity's money/date fields.

    Gold reads the registered Silver dataset (no read_parquet / {latest_date}).
    Returns None when there's nothing meaningful to aggregate.
    """
    silver = f"silver_{ent['name']}"
    money = ent["money_fields"]
    dates = ent["date_fields"]
    if not money:
        return None
    sums = ",\n  ".join(f"SUM({m}) AS total_{m}" for m in money)
    if dates:
        d = dates[0]
        return (
            f"SELECT date_trunc('month', {d}) AS periodo,\n"
            f"  COUNT(*) AS registros,\n  {sums}\n"
            f"FROM {silver}\nGROUP BY 1\nORDER BY 1"
        )
    return f"SELECT COUNT(*) AS registros,\n  {sums}\nFROM {silver}"


# ── Blueprint assembly ───────────────────────────────────────────────────────


def build_blueprint(
    *,
    cartridge_id: str,
    name: str,
    entities: list[dict[str, Any]],
    description: str = "",
    pattern: str = "rest",
    category: str = "custom",
) -> dict[str, Any]:
    """Produce a full cartridge manifest from introspected entities.

    ``entities`` items: ``{"name": str, "fields": [Field], "primary_key"?, "watermark_field"?}``.
    Output is the payload shape consumed by ``create_full_cartridge``.
    """
    cid = re.sub(r"[^a-z0-9_]", "_", str(cartridge_id).strip().lower()) or "cartridge"
    classified = [_classify_entity(e) for e in entities if (e.get("name") or e.get("entity"))]
    if not classified:
        raise ValueError("autopilot requires at least one entity with fields")

    dag_id = f"{cid}_extract"
    out_entities: list[dict[str, Any]] = []
    out_datasets: list[dict[str, Any]] = []
    out_kbs: list[dict[str, Any]] = []
    semantic_terms: list[dict[str, Any]] = []

    for ent in classified:
        en = ent["name"]
        out_entities.append({
            "entity": en,
            "mode": "incremental" if ent["watermark_field"] else "full",
            "dag_id": dag_id,
            "display_name": en.replace("_", " ").title(),
            "primary_key": ent["primary_key"],
            "watermark_field": ent["watermark_field"],
            "fields": [
                {
                    "name": f["name"],
                    "type": f.get("type", "string"),
                    "nullable": f.get("nullable", True),
                    "primary_key": f.get("primary_key", False),
                }
                for f in ent["fields"]
            ],
            # PII-protected columns are surfaced so downstream encryption applies.
            "protection": {"encrypt": ent["pii_fields"]} if ent["pii_fields"] else {},
        })

        out_datasets.append({
            "name": f"silver_{en}",
            "layer": "silver",
            "entity": en,
            "sql": _silver_sql(cid, ent),
            "sources": [en],
            "description": f"Silver dedup latest-partition de {en}.",
        })
        gold = _gold_sql(ent)
        if gold:
            out_datasets.append({
                "name": f"gold_{en}_metrics",
                "layer": "gold",
                "entity": en,
                "sql": gold,
                "sources": [f"silver_{en}"],
                "description": f"Métricas Gold derivadas de {en} (sumas por periodo).",
            })

        out_kbs.append({
            "name": f"kb_{en}",
            "sql": f"SELECT * FROM silver_{en} LIMIT 100",
            "description": f"Conocimiento base sobre {en}: estructura, claves y campos.",
        })

        for f in ent["fields"]:
            if f["role"] in {"money", "metric", "key"}:
                semantic_terms.append({
                    "term": f["name"],
                    "definition": f"{f['name']} ({f['role']}) en {en}.",
                    "entity": en,
                })

    # One domain agent that watches the most business-relevant metric.
    money_entities = [e["name"] for e in classified if e["money_fields"]]
    watch = money_entities[0] if money_entities else classified[0]["name"]
    agent = {
        "slug": f"{cid}_watchdog",
        "name": f"Vigía de {name}",
        "description": f"Vigila anomalías y métricas clave del cartucho {name}.",
        "instructions": (
            f"Eres el vigía del cartucho {name}. Monitoreas {watch} y los datasets "
            f"Gold para detectar desviaciones, valores fuera de rango y tendencias "
            f"de riesgo. Siempre consulta las tools antes de afirmar; nunca inventes."
        ),
    }

    hints = (
        f"# Cartucho {name}\n\n"
        f"Generado por el Autopilot del Studio desde introspección de esquema.\n"
        f"Entidades: {', '.join(e['name'] for e in classified)}.\n"
        f"Patrón de extracción: {pattern}.\n"
    )

    return {
        "id": cid,
        "name": name,
        "version": "1.0",
        "description": description or f"Cartucho {name} autogenerado por OMEGA Studio.",
        "pattern": pattern,
        "category": category,
        "bronze_path": f"raw/{cid}",
        "assistant_hints": hints,
        "dags": [{
            "dag_id": dag_id,
            "file": f"{dag_id}.py",
            "description": f"Extracción {pattern} de {name} a Bronze.",
            "trigger": "on-demand",
            "params": '["entity","mode"]',
        }],
        "entities": out_entities,
        "datasets": out_datasets,
        "kbs": out_kbs,
        "agents": [agent],
        "semantic_terms": semantic_terms,
    }


def summarize_blueprint(blueprint: dict[str, Any]) -> dict[str, Any]:
    """Compact, UI-friendly summary of what the autopilot produced."""
    return {
        "cartridge_id": blueprint["id"],
        "name": blueprint["name"],
        "entities": len(blueprint["entities"]),
        "datasets": len(blueprint["datasets"]),
        "silver": sum(1 for d in blueprint["datasets"] if d["layer"] == "silver"),
        "gold": sum(1 for d in blueprint["datasets"] if d["layer"] == "gold"),
        "kbs": len(blueprint["kbs"]),
        "agents": len(blueprint["agents"]),
        "semantic_terms": len(blueprint["semantic_terms"]),
        "pii_protected": sorted({
            col for e in blueprint["entities"]
            for col in (e.get("protection", {}).get("encrypt") or [])
        }),
    }
