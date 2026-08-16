"""
HubSpot CRM client.

Talks to the HubSpot CRM v3 REST APIs at https://api.hubapi.com.
Examples (whitelisted in app/config/entities.yaml):

  GET /crm/v3/objects/deals?limit=100&after=<cursor>&properties=...
  GET /crm/v3/objects/companies
  GET /crm/v3/owners
  GET /crm/v3/pipelines/deals

Auth: Private App bearer token (Authorization: Bearer <token>). Resolved from
the Console Vault connection (auth_method=bearer_token) with a settings/env
fallback. If credentials are missing the client refuses to fetch and returns a
structured "degraded" status — it never invents data.

Pagination: cursor-based. Each list response carries
``paging.next.after`` until the last page, which omits it. The extraction
service loops the cursor and applies a client-side watermark filter for
incremental loads (HubSpot list endpoints don't take a server-side
``updatedAt`` filter — the same client-side approach Replicon uses for its
async export).

Environment variables (canonical):
    HUBSPOT_BASE_URL (optional, default api.hubapi.com),
    HUBSPOT_API_TOKEN  (Private App token).
Legacy short names HUBSPOT_TOKEN / HUBSPOT_PRIVATE_APP_TOKEN are accepted
via fallback in ``app.core.config``.
"""
from __future__ import annotations

import logging
from typing import Any

import requests
from urllib3.util.retry import Retry

from app.core.auth_factory import auth_trace, build_auth_headers
from app.core.config import settings
from app.core.egress_guard import guarded_session
from app.core.vault_client import get_hubspot_connection

logger = logging.getLogger(__name__)
logging.getLogger("urllib3.util.retry").setLevel(logging.INFO)

_RETRYABLE_STATUSES = {429, 500, 502, 503, 504}


# Exponential backoff for transient errors so a 429 (HubSpot rate limit) or a
# 5xx no longer kills the extraction. Sleep before retry N is
# backoff_factor * (2 ** (N-1)) seconds: 2s, 4s, 8s with the defaults.
# urllib3 honours any Retry-After header HubSpot returns.
def _make_retry_session(max_retries: int = 3, backoff_factor: float = 2.0) -> requests.Session:
    retry = Retry(
        total=max_retries,
        backoff_factor=backoff_factor,
        status_forcelist=tuple(_RETRYABLE_STATUSES),
        allowed_methods=frozenset(["GET", "POST"]),
        respect_retry_after_header=True,
        raise_on_status=False,
    )
    return guarded_session(retries=retry)


class HubSpotClientError(RuntimeError):
    pass


class HubSpotClient:
    """HubSpot CRM v3 REST client (cursor pagination, bearer auth)."""

    CARTRIDGE_ID = "hubspot"
    _RETRY_MAX = 3
    _RETRY_BACKOFF_FACTOR = 2.0

    def __init__(self, security_context: str | None = None) -> None:
        self._session = _make_retry_session(self._RETRY_MAX, self._RETRY_BACKOFF_FACTOR)
        if settings.use_demo_data:
            connection = {
                "base_url": settings.hubspot_base_url,
                "auth_method": "bearer_token",
                "token": settings.hubspot_api_token or "",
            }
        else:
            connection = get_hubspot_connection(security_context=security_context)
        self.base_url = str(
            connection.get("base_url") or settings.hubspot_base_url or ""
        ).rstrip("/")
        self._auth_connection = {
            **connection,
            "auth_method": connection.get("auth_method") or "bearer_token",
            "token": connection.get("token") or settings.hubspot_api_token,
        }

    # ------------------------------------------------------------------
    # Auth header
    # ------------------------------------------------------------------

    @property
    def _auth_headers(self) -> dict[str, str]:
        headers, _, _ = build_auth_headers(
            self._auth_connection,
            default_method="bearer_token",
            default_api_key_header="Authorization",
            base_headers={"Content-Type": "application/json", "Accept": "application/json"},
        )
        return headers

    def _log_auth(self, status_code: int | None = None) -> None:
        _, method, header_names = build_auth_headers(
            self._auth_connection,
            default_method="bearer_token",
            default_api_key_header="Authorization",
            base_headers={"Content-Type": "application/json", "Accept": "application/json"},
        )
        suffix = f" -> Respuesta del servidor {status_code}" if status_code is not None else ""
        logger.warning("%s%s", auth_trace(method, header_names), suffix)

    # ------------------------------------------------------------------
    # Configuration
    # ------------------------------------------------------------------

    def configuration_status(self) -> dict[str, Any]:
        token = self._auth_connection.get("token")
        missing = []
        if not self.base_url:
            missing.append("HUBSPOT_BASE_URL")
        if not token:
            missing.append("HUBSPOT_API_TOKEN")
        return {
            "cartridge": self.CARTRIDGE_ID,
            "configured": not missing,
            "missing": missing,
            "base_url": self.base_url or None,
        }

    def _require_configured(self) -> None:
        status = self.configuration_status()
        if not status["configured"]:
            raise HubSpotClientError(
                f"hubspot not configured; missing env: {status['missing']}"
            )

    # ------------------------------------------------------------------
    # HTTP
    # ------------------------------------------------------------------

    def _get(self, path: str, params: dict[str, Any]) -> dict[str, Any]:
        url = f"{self.base_url}{path}"
        try:
            logger.warning("HubSpot outbound GET %s", url)
            resp = self._session.get(
                url,
                params=params,
                headers=self._auth_headers,
                timeout=settings.hubspot_timeout,
            )
            self._log_auth(resp.status_code)
            resp.raise_for_status()
            return resp.json()
        except requests.RequestException as exc:
            raise HubSpotClientError(f"GET {url} failed: {exc}") from exc

    # ------------------------------------------------------------------
    # Discovery
    # ------------------------------------------------------------------

    def list_tables(self) -> list[dict[str, Any]]:
        from app.services.catalog_service import get_all_entities
        return [
            {"id": e.get("entity"), "name": e.get("entity"),
             "description": e.get("description", "")}
            for e in get_all_entities() if e.get("entity")
        ]

    def get_table_schema(self, table_id: str) -> dict[str, Any]:
        from app.services.catalog_service import get_entity_config
        cfg = get_entity_config(table_id)
        if not cfg:
            return {"error": f"Entity '{table_id}' not in local catalog"}
        return {
            "entity": cfg.get("entity"),
            "fields": cfg.get("properties", []),
            "watermark_field": cfg.get("watermark_field"),
        }

    # ------------------------------------------------------------------
    # Fetch — one page. Returns (rows, next_after). next_after=None ends the loop.
    # ------------------------------------------------------------------

    def fetch_page(
        self,
        config: dict[str, Any],
        after: str | None = None,
    ) -> tuple[list[dict[str, Any]], str | None]:
        self._require_configured()
        shape = config.get("result_shape", "objects")
        if shape == "owners":
            return self._fetch_owners(config, after)
        if shape == "pipelines":
            return self._fetch_pipelines(config)
        return self._fetch_objects(config, after)

    def _fetch_objects(
        self, config: dict[str, Any], after: str | None
    ) -> tuple[list[dict[str, Any]], str | None]:
        path = config.get("api_path") or f"/crm/v3/objects/{config['entity']}"
        params: dict[str, Any] = {
            "limit": config.get("page_size", settings.hubspot_page_size),
            "archived": "false",
        }
        props = config.get("properties") or config.get("select_fields") or []
        if isinstance(props, str):
            import json as _json
            try:
                props = _json.loads(props)
            except Exception:
                props = []
        if props:
            params["properties"] = ",".join(props)
        if after:
            params["after"] = after
        payload = self._get(path, params)
        rows = [self._flatten_object(r) for r in (payload.get("results") or [])]
        next_after = (((payload.get("paging") or {}).get("next") or {}).get("after"))
        return rows, next_after

    @staticmethod
    def _flatten_object(record: dict[str, Any]) -> dict[str, Any]:
        """HubSpot objects wrap business fields in ``properties``. Flatten to a
        single dict and lift id/createdAt/updatedAt to top level."""
        row: dict[str, Any] = {
            "hubspot_id": record.get("id"),
            "created_at": record.get("createdAt"),
            "updated_at": record.get("updatedAt"),
            "archived": record.get("archived"),
        }
        for key, value in (record.get("properties") or {}).items():
            # never let a stray "id"/"createdAt" property clobber the lifted ones
            if key in ("hubspot_id", "created_at", "updated_at", "archived"):
                continue
            row[key] = value
        return row

    def _fetch_owners(
        self, config: dict[str, Any], after: str | None
    ) -> tuple[list[dict[str, Any]], str | None]:
        params: dict[str, Any] = {"limit": config.get("page_size", 100)}
        if after:
            params["after"] = after
        payload = self._get(config.get("api_path", "/crm/v3/owners"), params)
        rows = [
            {
                "hubspot_id": o.get("id"),
                "email": o.get("email"),
                "first_name": o.get("firstName"),
                "last_name": o.get("lastName"),
                "user_id": o.get("userId"),
                "archived": o.get("archived"),
                "created_at": o.get("createdAt"),
                "updated_at": o.get("updatedAt"),
            }
            for o in (payload.get("results") or [])
        ]
        next_after = (((payload.get("paging") or {}).get("next") or {}).get("after"))
        return rows, next_after

    def _fetch_pipelines(
        self, config: dict[str, Any]
    ) -> tuple[list[dict[str, Any]], str | None]:
        """Pipelines come as one document with nested stages. Flatten to one
        row per (pipeline, stage) — this is the stage→probability lookup the
        gold forecast layer joins against. No pagination."""
        payload = self._get(config.get("api_path", "/crm/v3/pipelines/deals"), {})
        rows: list[dict[str, Any]] = []
        for p in (payload.get("results") or []):
            for stage in (p.get("stages") or []):
                meta = stage.get("metadata") or {}
                rows.append({
                    "pipeline_id": p.get("id"),
                    "pipeline_label": p.get("label"),
                    "pipeline_display_order": p.get("displayOrder"),
                    "stage_id": stage.get("id"),
                    "stage_label": stage.get("label"),
                    "stage_display_order": stage.get("displayOrder"),
                    "probability": meta.get("probability"),
                    "is_closed": meta.get("isClosed"),
                    "archived": p.get("archived"),
                })
        return rows, None

    # ------------------------------------------------------------------
    # Connection test
    # ------------------------------------------------------------------

    def test_connection(self) -> dict[str, Any]:
        status = self.configuration_status()
        if not status["configured"]:
            return {"status": "degraded", **status}
        try:
            self._get("/crm/v3/owners", {"limit": 1})
            return {"status": "ok", "configured": True, "base_url": self.base_url}
        except Exception as exc:  # noqa: BLE001
            return {"status": "error", "configured": True, "error": str(exc)[:200]}
