from __future__ import annotations

from pathlib import Path
import hashlib

import yaml

from cartridges.replicon.app.services.kb_config_reconciliation import (
    CURRENT_PACKAGE_VERSIONS,
    LEGACY_PACKAGE_SQL_DIGESTS,
    blocked_kb_runtime_result,
    classify_managed_kb,
    is_kb_runtime_safe,
    reconcile_packaged_kbs,
)


ROOT = Path(__file__).resolve().parents[1]
KBS_PATH = ROOT / "cartridges/replicon/app/config/knowledge_bits.yaml"
MANAGED_IDS = {"kb_wip_mensual", "kb_wip_resumen"}


def _packaged() -> dict[str, dict]:
    payload = yaml.safe_load(KBS_PATH.read_text(encoding="utf-8"))
    definitions = {}
    for item in payload["knowledge_bits"]:
        kb_id = str(item.get("id") or "")
        if kb_id not in MANAGED_IDS:
            continue
        sql = (KBS_PATH.parent / item["sql_file"]).read_text(encoding="utf-8").strip()
        if "__WIP_MENSUAL_V3__" in sql:
            monthly = (
                (KBS_PATH.parent / "sql/kb_wip_mensual_v3.sql")
                .read_text(encoding="utf-8")
                .strip()
                .removesuffix(";")
            )
            sql = sql.replace("__WIP_MENSUAL_V3__", monthly)
        definitions[kb_id] = {
            **item,
            "sql": sql,
            "package_version": CURRENT_PACKAGE_VERSIONS[kb_id],
        }
    return definitions


def test_only_exact_legacy_wip_digests_are_package_owned() -> None:
    assert LEGACY_PACKAGE_SQL_DIGESTS == {
        "kb_wip_mensual": {
            "2bf0d0456874fd068c7885e6c0397d3e54241922e67b8cd3cb8a34fa1ab7f558",
        },
        "kb_wip_resumen": {
            "39af8cd6ffe21ef63e1373377ef7903b82b64992066cf8b30617d6af5978439d",
        },
    }
    fixtures = {
        "kb_wip_mensual": "replicon_wip_mensual_legacy_v1.sql",
        "kb_wip_resumen": "replicon_wip_resumen_legacy_v1.sql",
    }
    for kb_id, filename in fixtures.items():
        payload = (ROOT / "tests/fixtures" / filename).read_bytes()
        assert {hashlib.sha256(payload).hexdigest()} == LEGACY_PACKAGE_SQL_DIGESTS[
            kb_id
        ]


def test_current_and_legacy_package_rows_reconcile_but_unknown_fails_closed() -> None:
    packaged = _packaged()
    assert set(packaged) == MANAGED_IDS
    assert set(CURRENT_PACKAGE_VERSIONS) == MANAGED_IDS

    for kb_id, definition in packaged.items():
        assert definition["package_version"] == CURRENT_PACKAGE_VERSIONS[kb_id]
        current_sql = str(definition["sql"])
        assert classify_managed_kb(kb_id, current_sql, current_sql) == "current"

        legacy_digest = next(iter(LEGACY_PACKAGE_SQL_DIGESTS[kb_id]))
        assert (
            classify_managed_kb(
                kb_id,
                "legacy placeholder",
                current_sql,
                stored_digest=legacy_digest,
            )
            == "upgrade"
        )
        assert (
            classify_managed_kb(kb_id, "SELECT 'custom'", current_sql)
            == "block_unknown"
        )
        assert is_kb_runtime_safe(kb_id, current_sql, list(packaged.values()))
        assert not is_kb_runtime_safe(kb_id, "SELECT 'custom'", list(packaged.values()))


def test_non_reserved_custom_kb_is_never_claimed_by_package() -> None:
    assert (
        classify_managed_kb(
            "kb_customer_owned",
            "SELECT 1",
            "SELECT 2",
        )
        == "custom"
    )


class _Rows:
    def __init__(self, rows: list[dict]) -> None:
        self._rows = rows

    def mappings(self) -> _Rows:
        return self

    def all(self) -> list[dict]:
        return self._rows

    def first(self):
        return self._rows[0] if self._rows else None

    def scalar(self):
        return 1


class _Connection:
    def __init__(self, rows: dict[str, dict]) -> None:
        self.rows = rows
        self.writes: list[str] = []

    def execute(self, statement, values=None):
        sql = str(statement).strip()
        values = values or {}
        if "COUNT(*) FROM entity_config" in sql:
            return _Rows([])
        if sql.startswith("SELECT kb_id"):
            return _Rows(list(self.rows.values()))
        if sql.startswith("SELECT * FROM kb_config"):
            row = self.rows.get(str(values["kid"]))
            return _Rows([row] if row and row.get("enabled", True) else [])
        self.writes.append(sql)
        kb_id = str(values["kid"])
        if sql.startswith("INSERT"):
            self.rows[kb_id] = {"kb_id": kb_id, "sql": values["sql"]}
        elif "SET sql" in sql:
            self.rows[kb_id]["sql"] = values["sql"]
        return _Rows([])

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return None


def test_reconciliation_harness_upgrades_only_owned_legacy(monkeypatch) -> None:
    from cartridges.replicon.app.services import kb_config_reconciliation as module

    packaged = {
        "id": "kb_wip_mensual",
        "package_version": CURRENT_PACKAGE_VERSIONS["kb_wip_mensual"],
        "sql": "SELECT 'current'",
    }
    legacy_sql = "SELECT 'legacy'"
    monkeypatch.setitem(
        module.LEGACY_PACKAGE_SQL_DIGESTS,
        "kb_wip_mensual",
        {hashlib.sha256(legacy_sql.encode()).hexdigest()},
    )
    connection = _Connection(
        {"kb_wip_mensual": {"kb_id": "kb_wip_mensual", "sql": legacy_sql}}
    )

    first = reconcile_packaged_kbs(connection, [packaged], "replicon")
    writes_after_upgrade = len(connection.writes)
    second = reconcile_packaged_kbs(connection, [packaged], "replicon")

    assert first == {"inserted": 0, "upgraded": 1, "blocked": 0, "unchanged": 0}
    assert second == {"inserted": 0, "upgraded": 0, "blocked": 0, "unchanged": 1}
    assert connection.rows["kb_wip_mensual"]["sql"] == packaged["sql"]
    assert len(connection.writes) == writes_after_upgrade


def test_legacy_upgrade_preserves_customer_metadata_destinations_and_disabled_state(
    monkeypatch,
) -> None:
    from cartridges.replicon.app.services import kb_config_reconciliation as module

    legacy_sql = "SELECT 'legacy-owned'"
    monkeypatch.setitem(
        module.LEGACY_PACKAGE_SQL_DIGESTS,
        "kb_wip_mensual",
        {hashlib.sha256(legacy_sql.encode()).hexdigest()},
    )
    original = {
        "kb_id": "kb_wip_mensual",
        "sql": legacy_sql,
        "name": "Nombre del cliente",
        "description": "Descripción preservada",
        "pg_table": "customer_destination",
        "output_path": "customer/output",
        "enabled": False,
    }
    connection = _Connection({"kb_wip_mensual": dict(original)})
    packaged = {
        "id": "kb_wip_mensual",
        "package_version": CURRENT_PACKAGE_VERSIONS["kb_wip_mensual"],
        "sql": "SELECT 'v3'",
        "name": "Package name",
        "description": "Package description",
        "pg_table": "package_destination",
        "output_path": "package/output",
    }

    assert reconcile_packaged_kbs(connection, [packaged], "replicon")["upgraded"] == 1
    upgraded = connection.rows["kb_wip_mensual"]
    assert upgraded["sql"] == packaged["sql"]
    for key in ("name", "description", "pg_table", "output_path", "enabled"):
        assert upgraded[key] == original[key]
    update_sql = connection.writes[-1]
    assert "enabled = TRUE" not in update_sql
    assert "name =" not in update_sql
    assert "description =" not in update_sql
    assert "pg_table =" not in update_sql
    assert "output_path =" not in update_sql


def test_unknown_reserved_row_is_byte_stable_and_blocked_before_execution() -> None:
    packaged = {"id": "kb_wip_resumen", "sql": "SELECT 'current'"}
    original = {"kb_id": "kb_wip_resumen", "sql": "SELECT 'customer override'"}
    connection = _Connection({"kb_wip_resumen": dict(original)})

    result = reconcile_packaged_kbs(connection, [packaged], "replicon")
    blocked = blocked_kb_runtime_result(
        {
            **connection.rows["kb_wip_resumen"],
            "enabled": False,
            "runtime_status": "insufficient_data",
            "blocked_reason": "reserved_kb_package_digest_unknown",
        }
    )

    assert result["blocked"] == 1
    assert connection.rows["kb_wip_resumen"] == original
    assert connection.writes == []
    assert blocked == {
        "status": "partial",
        "data_status": "insufficient_data",
        "error": "Knowledge Bit blocked: reserved_kb_package_digest_unknown",
    }
