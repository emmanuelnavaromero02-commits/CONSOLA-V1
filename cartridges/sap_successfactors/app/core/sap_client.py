import time
import uuid
import httpx
from datetime import datetime, timezone

from app.core.config import settings

class SapSfClient:
    def __init__(self):
        self.base_url = settings.sf_base_url
        self.company_id = settings.sf_company_id
        self.client_id = settings.sf_client_id
        self.client_secret = settings.sf_client_secret
        self.token_url = settings.sf_token_url
        self.timeout = 120.0
        self._token = None
        self._token_expires_at = 0

    def get_access_token(self) -> str:
        now = time.time()
        if self._token and now < self._token_expires_at:
            return self._token

        data = {
            "grant_type": "client_credentials",
            "client_id": self.client_id,
            "client_secret": self.client_secret,
            "company_id": self.company_id,
        }
        response = httpx.post(self.token_url, data=data, timeout=self.timeout)
        response.raise_for_status()
        res_json = response.json()
        self._token = res_json.get("access_token")
        expires_in = int(res_json.get("expires_in", 3600))
        self._token_expires_at = now + expires_in - 60
        return self._token

    def fetch_entity(self, entity: str, select: list[str], page_size: int, skip: int, filter_expr: str | None = None) -> list[dict]:
        token = self.get_access_token()
        headers = {
            "Authorization": f"Bearer {token}",
            "companyId": self.company_id,
            "Accept": "application/json"
        }

        limit = min(page_size, 200)
        params = {
            "$format": "json",
            "$top": limit,
            "$skip": skip
        }
        if select:
            params["$select"] = ",".join(select)
        if filter_expr:
            params["$filter"] = filter_expr

        url = f"{self.base_url.rstrip('/')}/{entity}"
        retries = 3
        backoff = 1
        for attempt in range(retries):
            try:
                response = httpx.get(url, headers=headers, params=params, timeout=self.timeout)
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
