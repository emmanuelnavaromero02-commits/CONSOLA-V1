
import os
import requests
import logging
from typing import Optional, Dict, Any, List
import time

logger = logging.getLogger(__name__)

class SAPClientError(Exception):
    pass

class SAPClient:
    def __init__(self, cartridge_id: str):
        self.cartridge_id = cartridge_id
        # Dynamic env variable resolution based on cartridge_id
        prefix = f"{cartridge_id.upper()}"
        self.base_url = os.environ.get(f"{prefix}_BASE_URL")
        self.user = os.environ.get(f"{prefix}_USER")
        self.password = os.environ.get(f"{prefix}_PASSWORD")
        self.client_id = os.environ.get(f"{prefix}_CLIENT_ID")
        self.client_secret = os.environ.get(f"{prefix}_CLIENT_SECRET")
        self.token_url = os.environ.get(f"{prefix}_TOKEN_URL")

        self.auth_mode = "basic" if self.user and self.password else "oauth" if self.client_id and self.client_secret else "none"
        self._token = None
        self._token_expires_at = 0

    def _get_token(self) -> str:
        if self._token and time.time() < self._token_expires_at:
            return self._token

        if not self.token_url:
            raise SAPClientError(f"Missing TOKEN_URL for {self.cartridge_id}")

        try:
            resp = requests.post(
                self.token_url,
                data={"grant_type": "client_credentials"},
                auth=(self.client_id, self.client_secret),
                timeout=30
            )
            resp.raise_for_status()
            data = resp.json()
            self._token = data.get("access_token")
            self._token_expires_at = time.time() + data.get("expires_in", 3600) - 60
            return self._token
        except Exception as e:
            raise SAPClientError(f"OAuth auth failed: {str(e)}")

    def _get_headers(self) -> Dict[str, str]:
        headers = {"Accept": "application/json"}
        if self.auth_mode == "oauth":
            headers["Authorization"] = f"Bearer {self._get_token()}"
        return headers

    def _get_auth(self):
        if self.auth_mode == "basic":
            return (self.user, self.password)
        return None

    def list_tables(self) -> List[Dict[str, Any]]:
        # Usually SAP entities are static via connector.yaml, but can be dynamic via OData $metadata
        # For this base implementation we return a standard mock if no actual base_url is present to allow startup
        if not self.base_url:
            return [{"id": "MockEntity", "name": "Mock Entity"}]

        try:
            resp = requests.get(
                f"{self.base_url}/$metadata",
                headers=self._get_headers(),
                auth=self._get_auth(),
                timeout=30
            )
            resp.raise_for_status()
            return [{"id": "ODataEntity", "name": "OData Entity"}]
        except Exception as e:
             logger.warning(f"Could not fetch metadata for {self.cartridge_id}, returning mock tables: {str(e)}")
             return [{"id": "MockEntity", "name": "Mock Entity"}]

    def get_table_schema(self, table_id: str) -> Dict[str, Any]:
        return {"fields": [{"name": "id", "type": "string"}]}

    def fetch_entity(self, entity: str, select: List[str] = None, page_size: int = 100, skip: int = 0, filter_expr: str = None) -> List[Dict[str, Any]]:
        if not self.base_url:
            return []

        params = {"$top": page_size, "$skip": skip}
        if select:
            params["$select"] = ",".join(select)
        if filter_expr:
            params["$filter"] = filter_expr

        url = f"{self.base_url.rstrip('/')}/{entity}"
        try:
            resp = requests.get(url, params=params, headers=self._get_headers(), auth=self._get_auth(), timeout=60)
            resp.raise_for_status()
            data = resp.json()
            # Handle standard OData v2 and v4 responses
            if "d" in data and "results" in data["d"]:
                return data["d"]["results"]
            elif "value" in data:
                return data["value"]
            return [data]
        except Exception as e:
            logger.error(f"Failed to fetch entity {entity} from {url}: {e}")
            raise SAPClientError(str(e))
