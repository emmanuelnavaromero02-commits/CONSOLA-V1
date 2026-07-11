from __future__ import annotations

import os

import psycopg2

from app.core.config import settings

CARTRIDGE_ID = "sec_edgar"


class WatermarkStore:
    def get_many(self, tenant_id: str, workspace_id: str, keys: list[str]) -> dict[str, str | None]:
        raise NotImplementedError

    def update_many(
        self,
        tenant_id: str,
        workspace_id: str,
        values: dict[str, str],
        *,
        run_id: str,
    ) -> None:
        raise NotImplementedError


class PostgresWatermarkStore(WatermarkStore):
    def __init__(self, database_url: str | None = None) -> None:
        self.database_url = _psycopg_dsn(database_url or settings.database_url)

    def get_many(self, tenant_id: str, workspace_id: str, keys: list[str]) -> dict[str, str | None]:
        result = {key: None for key in keys}
        with psycopg2.connect(self.database_url) as conn:
            with conn.cursor() as cur:
                _set_scope(cur, tenant_id, workspace_id)
                cur.execute(
                    """
                    SELECT entity_name, last_watermark_value
                    FROM entity_watermarks
                    WHERE cartridge_id = %s AND watermark_scope = %s
                      AND entity_name = ANY(%s)
                    """,
                    (CARTRIDGE_ID, _scope(tenant_id, workspace_id), [_entity_name(s) for s in keys]),
                )
                for entity_name, value in cur.fetchall():
                    result[str(entity_name).split(":", 1)[-1]] = value
        return result

    def update_many(
        self,
        tenant_id: str,
        workspace_id: str,
        values: dict[str, str],
        *,
        run_id: str,
    ) -> None:
        with psycopg2.connect(self.database_url) as conn:
            with conn.cursor() as cur:
                _set_scope(cur, tenant_id, workspace_id)
                for key, watermark in values.items():
                    cur.execute(
                        """
                        INSERT INTO entity_watermarks
                            (cartridge_id, entity_name, watermark_field, last_watermark_value,
                             last_run_id, tenant_id, workspace_id, watermark_scope)
                        VALUES (%s, %s, %s, %s, %s, %s::uuid, %s::uuid, %s)
                        ON CONFLICT (watermark_scope, cartridge_id, entity_name) DO UPDATE SET
                            watermark_field = EXCLUDED.watermark_field,
                            last_watermark_value = EXCLUDED.last_watermark_value,
                            last_run_id = EXCLUDED.last_run_id,
                            tenant_id = EXCLUDED.tenant_id,
                            workspace_id = EXCLUDED.workspace_id,
                            updated_at = NOW()
                        """,
                        (
                            CARTRIDGE_ID,
                            _entity_name(key),
                            "end_date",
                            watermark,
                            run_id,
                            tenant_id,
                            workspace_id,
                            _scope(tenant_id, workspace_id),
                        ),
                    )


def _entity_name(key: str) -> str:
    return f"company_facts:{key}"


def _scope(tenant_id: str, workspace_id: str) -> str:
    return f"tenant:{tenant_id}:workspace:{workspace_id}"


def _set_scope(cur, tenant_id: str, workspace_id: str) -> None:
    cur.execute("SELECT set_config('app.tenant_id', %s, true)", (tenant_id,))
    cur.execute("SELECT set_config('app.workspace_id', %s, true)", (workspace_id,))
    cur.execute("SELECT set_config('app.platform_admin', %s, true)", ("false",))


def _psycopg_dsn(database_url: str) -> str:
    return database_url.replace("postgresql+psycopg2://", "postgresql://", 1)


def default_store() -> WatermarkStore:
    if os.environ.get("SEC_EDGAR_DISABLE_DB_WATERMARKS") == "1":
        return NullWatermarkStore()
    return PostgresWatermarkStore()


class NullWatermarkStore(WatermarkStore):
    def get_many(self, tenant_id: str, workspace_id: str, keys: list[str]) -> dict[str, str | None]:
        return {key: None for key in keys}

    def update_many(
        self,
        tenant_id: str,
        workspace_id: str,
        values: dict[str, str],
        *,
        run_id: str,
    ) -> None:
        return None
