
from typing import Dict, Any, List
# Pseudo-routing setup. Would normally import FastAPI Router etc.
# from fastapi import APIRouter
# router = APIRouter()

from ..client.sap_client import SAPClient
from ..services.sync_service import SyncService
from ..services.validation_service import ValidationService

# @router.get("/health")
def healthcheck(client: SAPClient) -> Dict[str, Any]:
    return client.healthcheck()

# @router.get("/entities")
def list_entities(sync_service: SyncService) -> List[str]:
    return sync_service.list_entities()

# @router.post("/sync/{entity}")
def trigger_sync(entity: str, sync_service: SyncService) -> Dict[str, Any]:
    return sync_service.sync_entity(entity)
