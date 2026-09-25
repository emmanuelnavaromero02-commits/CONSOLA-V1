from __future__ import annotations

import re
from typing import Any


_PII_NAME_HINTS = (
    "email", "mail", "phone", "telefono", "celular", "rfc", "curp", "ssn",
    "passport", "pasaporte", "iban", "clabe", "tarjeta", "card", "cuenta",
    "account", "birth", "nacimiento", "gbdat", "address", "direccion",
    "salary", "salario", "sueldo", "compensation", "pernr",
    "dni", "nss", "dob", "zip", "postal", "ip", "national", "voter",
)
_MONEY_NAME_HINTS = (
    "amount", "monto", "importe", "price", "precio", "cost", "costo", "revenue",
    "ingreso", "betrg", "dmbtr", "netpr", "total", "value", "valor", "mrr",
    "arr", "billing", "facturacion", "margin", "margen", "budget", "presupuesto",
)
_DATE_TYPES = {"date", "timestamp", "datetime"}
_KEY_NAME_HINTS = ("id", "code", "codigo", "key", "uuid", "guid")


def classify_field(field: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(field, dict):
        return {"name": "", "role": "dimension", "protected": False}
    name = str(field.get("name") or "").strip()
    lname = name.lower()
    ftype = str(field.get("type") or "string").lower()
    is_pk = bool(field.get("primary_key"))

    camel = re.sub(r"(?<=[a-z0-9])(?=[A-Z])", "_", str(name)).lower()
    tokens = {t for t in re.split(r"[^a-z0-9]+", camel) if t}

    def _hint_hit(hints: tuple[str, ...]) -> bool:
        return any(h in tokens for h in hints)

    role = "dimension"
    protect = False

    if _hint_hit(_PII_NAME_HINTS):
        role = "pii"
        protect = True
    elif is_pk or lname == "id" or lname.endswith("_id") or _hint_hit(_KEY_NAME_HINTS):
        role = "key"
    elif ftype in _DATE_TYPES:
        role = "date"
    elif ftype in {"int", "float", "number", "decimal"} and _hint_hit(_MONEY_NAME_HINTS):
        role = "money"
    elif ftype in {"int", "float", "number", "decimal"}:
        role = "metric"

    out = dict(field)
    out["name"] = name
    out["role"] = role
    out["protected"] = protect
    return out


def _slug_identifier(value: str, fallback: str = "entity") -> str:
    s = re.sub(r"[^A-Za-z0-9_]", "_", str(value or "").strip())
    if not s or not (s[0].isalpha() or s[0] == "_"):
        s = "e_" + s
    return s[:127] or fallback


def _classify_entity(entity: dict[str, Any]) -> dict[str, Any]:
    fields = [c for c in (classify_field(f) for f in (entity.get("fields") or []) if isinstance(f, dict)) if c["name"]]
    pk = entity.get("primary_key") or next(
        (f["name"] for f in fields if f["role"] == "key"), None
    )
    watermark = entity.get("watermark_field") or next(
        (
            f["name"]
            for f in fields
            if f["role"] == "date"
            and any(w in f["name"].lower() for w in ("modif", "updated", "change"))
        ),
        next(
            (
                f["name"]
                for f in fields
                if f["role"] == "date"
                and any(w in f["name"].lower() for w in ("fecha", "date"))
            ),
            next((f["name"] for f in fields if f["role"] == "date"), None),
        ),
    )
    def _typed(role_types: set[str], money: bool = False) -> list[str]:
        out: list[str] = []
        for f in fields:
            ft = str(f.get("type") or "").lower()
            if ft in role_types and (not money or _money_named(f["name"])):
                out.append(f["name"])
        return out

    date_fields = _typed(_DATE_TYPES)
    pii_names = {f["name"] for f in fields if f["role"] == "pii"}
    money_fields = [f["name"] for f in fields if f["role"] == "money"] or [
        n for n in _typed({"int", "float", "number", "decimal"}, money=True)
        if n not in pii_names
    ]
    if not watermark and date_fields:
        wm_cands = [d for d in date_fields if d not in pii_names]
        watermark = next(
            (d for d in wm_cands if any(w in d.lower() for w in ("modif", "updated", "change"))),
            next(
                (d for d in wm_cands if any(w in d.lower() for w in ("fecha", "date"))),
                next((d for d in wm_cands), None),
            ),
        )
    return {
        "name": _slug_identifier(entity.get("name") or entity.get("entity") or "entity"),
        "fields": fields,
        "primary_key": pk,
        "watermark_field": watermark,
        "money_fields": list(dict.fromkeys(money_fields)),
        "metric_fields": [f["name"] for f in fields if f["role"] == "metric"],
        "date_fields": list(dict.fromkeys(date_fields)),
        "pii_fields": [f["name"] for f in fields if f["role"] == "pii"],
    }


def _money_named(name: str) -> bool:
    camel = re.sub(r"(?<=[a-z0-9])(?=[A-Z])", "_", str(name)).lower()
    tokens = {t for t in re.split(r"[^a-z0-9]+", camel) if t}
    return any(h in tokens for h in _MONEY_NAME_HINTS)


def _qi(name: str) -> str:
    return '"' + str(name).replace('"', '""') + '"'


def _silver_sql(cartridge_id: str, ent: dict[str, Any]) -> str:
    name = ent["name"]
    src = f"s3://lakehouse/raw/{cartridge_id}/{name}/**/*.parquet"
    pk = ent["primary_key"]
    order = ent["watermark_field"] or "load_date"
    if pk:
        return (
            f"SELECT * EXCLUDE (rn) FROM (\n"
            f"  SELECT *, ROW_NUMBER() OVER (PARTITION BY {_qi(pk)} ORDER BY {_qi(order)} DESC) AS rn\n"
            f"  FROM read_parquet('{src}', hive_partitioning=true, union_by_name=true)\n"
            f"  WHERE load_date = '{{latest_date}}'\n"
            f") _dedup WHERE rn = 1"
        )
    return (
        f"SELECT * FROM read_parquet('{src}', hive_partitioning=true, union_by_name=true)\n"
        f"WHERE load_date = '{{latest_date}}'"
    )


def _gold_sql(ent: dict[str, Any]) -> str | None:
    silver = _qi(f"silver_{ent['name']}")
    money = list(dict.fromkeys(ent["money_fields"]))
    pii = set(ent.get("pii_fields", []))
    dates = [d for d in ent["date_fields"] if d not in pii]
    if not money:
        return None
    sums = ",\n  ".join(f"SUM({_qi(m)}) AS {_qi('total_' + m)}" for m in money)
    if dates:
        d = dates[0]
        return (
            f"SELECT date_trunc('month', {_qi(d)}) AS periodo,\n"
            f"  COUNT(*) AS registros,\n  {sums}\n"
            f"FROM {silver}\nGROUP BY 1\nORDER BY 1"
        )
    return f"SELECT COUNT(*) AS registros,\n  {sums}\nFROM {silver}"


def build_blueprint(
    *,
    cartridge_id: str,
    name: str,
    entities: list[dict[str, Any]],
    description: str = "",
    pattern: str = "rest",
    category: str = "custom",
) -> dict[str, Any]:
    cid = re.sub(r"[^a-z0-9_]", "_", str(cartridge_id).strip().lower())
    if not cid or not cid[0].isalpha():
        cid = "c_" + cid
    cid = cid[:80]
    classified = [_classify_entity(e) for e in entities if isinstance(e, dict) and (e.get("name") or e.get("entity"))]
    if not classified:
        raise ValueError("autopilot requires at least one entity with fields")

    _seen: dict[str, int] = {}
    for ent in classified:
        base = ent["name"]
        if base in _seen:
            _seen[base] += 1
            ent["name"] = f"{base}_{_seen[base]}"
        else:
            _seen[base] = 1

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
            "kb_id": f"kb_{en}",
            "name": f"kb_{en}",
            "sql": f"SELECT * FROM {_qi('silver_' + en)} LIMIT 100",
            "description": f"Conocimiento base sobre {en}: estructura, claves y campos.",
        })

        for f in ent["fields"]:
            if f["role"] in {"money", "metric", "key"}:
                semantic_terms.append({
                    "term": f"{en}.{f['name']}",
                    "definition": f"{f['name']} ({f['role']}) en {en}.",
                    "maps_to": f"{en}.{f['name']}",
                })

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
            "params": '["entity", "mode"]',
        }],
        "entities": out_entities,
        "datasets": out_datasets,
        "kbs": out_kbs,
        "agents": [agent],
        "semantic_model": {"vocabulary": semantic_terms},
    }


def summarize_blueprint(blueprint: dict[str, Any]) -> dict[str, Any]:
    bp = blueprint if isinstance(blueprint, dict) else {}
    datasets = bp.get("datasets") or []
    vocab = (bp.get("semantic_model") or {}).get("vocabulary") or []
    return {
        "cartridge_id": bp.get("id"),
        "name": bp.get("name"),
        "entities": len(bp.get("entities") or []),
        "datasets": len(datasets),
        "silver": sum(1 for d in datasets if d.get("layer") == "silver"),
        "gold": sum(1 for d in datasets if d.get("layer") == "gold"),
        "kbs": len(bp.get("kbs") or []),
        "agents": len(bp.get("agents") or []),
        "semantic_terms": len(vocab),
        "pii_protected": sorted({
            col for e in (bp.get("entities") or [])
            for col in (e.get("protection", {}).get("encrypt") or [])
        }),
    }
