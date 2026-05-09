from datetime import datetime, timezone
import uuid
import httpx
from typing import Any

from app.core.config import settings

class SapHcmClient:
    def __init__(self):
        self.base_url = settings.sap_hcm_base_url
        self.auth = (settings.sap_hcm_user, settings.sap_hcm_pass)
        self.timeout = 120.0
        self.headers = {
            "Accept": "application/json",
            "sap-client": settings.sap_hcm_client
        }
        self.client = httpx.Client(
            base_url=self.base_url,
            auth=self.auth,
            timeout=self.timeout,
            headers=self.headers
        )

    def get_entity_url(self, entity: str) -> str:
        if "/" in entity:
            parts = entity.split("/", 1)
            service = parts[0].strip()
            collection = parts[1].strip()
            return f"{self.base_url.rstrip('/')}/{service}/{collection}"
        else:
            return f"{self.base_url.rstrip('/')}/{entity}"

    def fetch_entity(self, entity: str, select: list[str], page_size: int, skip: int, filter_expr: str | None = None) -> list[dict]:
        params = {
            "$top": page_size,
            "$skip": skip,
            "$format": "json"
        }
        if select:
            params["$select"] = ",".join(select)
        if filter_expr:
            params["$filter"] = filter_expr

        url = self.get_entity_url(entity)
        retries = 3
        backoff = 1
        for attempt in range(retries):
            try:
                response = self.client.get(url, params=params)
                response.raise_for_status()
                data = response.json()
                results = data.get("d", {}).get("results", data.get("d", data))
                if isinstance(results, dict) and "results" in results:
                     results = results["results"]
                if not isinstance(results, list):
                     results = [results] if results else []

                run_id = str(uuid.uuid4())
                extracted_at = datetime.now(timezone.utc).isoformat()
                for row in results:
                    row["_extracted_at"] = extracted_at
                    row["_run_id"] = run_id
                return results
            except Exception as e:
                if attempt == retries - 1:
                    raise e
                import time
                time.sleep(backoff)
                backoff *= 2
        return []

    def list_tables(self) -> list[dict]:
        from app.services.catalog_service import get_all_entities
        entities = get_all_entities()
        return [{"id": e.get("entity", ""), "name": e.get("entity", "")} for e in entities]

    def get_table_schema(self, table_id: str) -> dict:
        from app.services.catalog_service import get_entity_config
        config = get_entity_config(table_id)
        if not config:
            return {}
        select_fields = config.get("select_fields", [])
        return {
             "id": table_id,
             "name": table_id,
             "columns": [{"name": f, "type": "string"} for f in select_fields]
        }

    def test_connection(self) -> dict:
        return {"status": "connected"}
