
from typing import List, Dict, Any
from ..client.sap_client import SAPClient, SAPClientError
import logging

logger = logging.getLogger(__name__)

class SyncService:
    def __init__(self, client: SAPClient):
        self.client = client

    def list_entities(self) -> List[str]:
        # Implementation depends on exact SAP product, returning mock/standard for now
        return ["Employee", "CostCenter", "OrganizationUnit"]

    def sync_entity(self, entity_name: str, sync_mode: str = "incremental") -> Dict[str, Any]:
        logger.info(f"Starting {sync_mode} sync for {entity_name}")

        try:
            # SAP Pagination logic would go here
            data = self.client.get(f"/{entity_name}")

            return {
                "status": "success",
                "entity": entity_name,
                "records_processed": len(data.get("d", {}).get("results", data.get("value", []))), # Handles OData v2 and v4
                "mode": sync_mode
            }
        except SAPClientError as e:
            logger.error(f"Sync failed for {entity_name}: {str(e)}")
            return {
                "status": "error",
                "entity": entity_name,
                "error": str(e)
            }
