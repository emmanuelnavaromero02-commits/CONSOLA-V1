from __future__ import annotations

import hashlib
from typing import Any, Literal

from sqlalchemy import text


CURRENT_PACKAGE_VERSIONS = {
    "kb_wip_mensual": "replicon.wip_mensual.v3",
    "kb_wip_resumen": "replicon.wip_resumen.v3",
}
LEGACY_PACKAGE_SQL_DIGESTS = {
    "kb_wip_mensual": {
        "2bf0d0456874fd068c7885e6c0397d3e54241922e67b8cd3cb8a34fa1ab7f558",
    },
    "kb_wip_resumen": {
        "39af8cd6ffe21ef63e1373377ef7903b82b64992066cf8b30617d6af5978439d",
    },
}

ReconciliationAction = Literal["current", "upgrade", "block_unknown", "custom"]


def _digest(sql: str) -> str:
    return hashlib.sha256(sql.encode("utf-8")).hexdigest()


def classify_managed_kb(
    kb_id: str,
    stored_sql: str,
    packaged_sql: str,
    *,
    stored_digest: str | None = None,
) -> ReconciliationAction:

    if kb_id not in CURRENT_PACKAGE_VERSIONS:
        return "custom"
    digest = stored_digest or _digest(stored_sql)
    if digest == _digest(packaged_sql):
        return "current"
    if digest in LEGACY_PACKAGE_SQL_DIGESTS[kb_id]:
        return "upgrade"
    return "block_unknown"


def _values(kb: dict[str, Any], cartridge_id: str) -> dict[str, Any]:
    return {
        "cid": cartridge_id,
        "kid": str(kb.get("id") or ""),
        "name": kb.get("name", kb.get("id")),
        "desc": kb.get("description", ""),
        "sql": kb.get("sql", ""),
        "pg": kb.get("pg_table", kb.get("id")),
        "out": kb.get("output_path", ""),
        "version": kb.get("package_version"),
        "digest": _digest(str(kb.get("sql") or "")),
    }


def reconcile_packaged_kbs(
    conn: Any,
    packaged_kbs: list[dict[str, Any]],
    cartridge_id: str,
) -> dict[str, int]:

    rows = (
        conn.execute(
            text(
                "SELECT kb_id, sql, enabled, name, description, pg_table, output_path "
                "FROM kb_config WHERE cartridge_id = :cid FOR UPDATE"
            ),
            {"cid": cartridge_id},
        )
        .mappings()
        .all()
    )
    existing = {str(row["kb_id"]): dict(row) for row in rows}
    counts = {"inserted": 0, "upgraded": 0, "blocked": 0, "unchanged": 0}

    for kb in packaged_kbs:
        values = _values(kb, cartridge_id)
        kb_id = values["kid"]
        if not kb_id:
            continue
        if kb_id not in existing:
            conn.execute(
                text(
                    """INSERT INTO kb_config (
                           cartridge_id, kb_id, name, description, sql,
                           pg_table, output_path, enabled
                           , package_version, package_sql_digest,
                           materialization_status, invalid_reason
                       ) VALUES (:cid, :kid, :name, :desc, :sql, :pg, :out, TRUE,
                                 :version, :digest, 'quarantined', 'rerun_required')
                       ON CONFLICT (cartridge_id, kb_id) DO NOTHING"""
                ),
                values,
            )
            counts["inserted"] += 1
            continue

        observed = existing[kb_id]
        action = classify_managed_kb(
            kb_id, str(observed.get("sql") or ""), str(values["sql"])
        )
        if action == "upgrade":
            updated = conn.execute(
                text(
                    """UPDATE kb_config
                          SET sql = :sql, package_version = :version,
                              package_sql_digest = :digest,
                              materialization_status = 'quarantined',
                              current_run_id = NULL,
                              invalid_reason = 'invalid_legacy_fx'
                        WHERE cartridge_id = :cid AND kb_id = :kid
                          AND sql = :observed_sql"""
                ),
                {**values, "observed_sql": observed.get("sql")},
            )
            if getattr(updated, "rowcount", 1) == 0:
                counts["blocked"] += 1
            else:
                counts["upgraded"] += 1
        elif action == "block_unknown":
            counts["blocked"] += 1
        else:
            counts["unchanged"] += 1
    return counts


def is_kb_runtime_safe(
    kb_id: str,
    stored_sql: str,
    packaged_kbs: list[dict[str, Any]],
) -> bool:

    if kb_id not in CURRENT_PACKAGE_VERSIONS:
        return True
    packaged = next(
        (kb for kb in packaged_kbs if str(kb.get("id") or "") == kb_id),
        None,
    )
    if packaged is None:
        return False
    return (
        classify_managed_kb(kb_id, stored_sql, str(packaged.get("sql") or ""))
        == "current"
    )


def blocked_kb_runtime_result(config: dict[str, Any]) -> dict[str, Any] | None:
    if config.get("enabled") is not False:
        return None
    return {
        "status": "partial",
        "data_status": config.get("runtime_status", "insufficient_data"),
        "error": f"Knowledge Bit blocked: {config.get('blocked_reason', 'disabled')}",
    }
