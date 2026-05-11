from fastapi import APIRouter

from app.core.replicon_client import RepliconClient

router = APIRouter(prefix="/health", tags=["health"])


@router.get("")
def health() -> dict:
    return {"ok": True, "service": "replicon"}


@router.get("/replicon")
def health_replicon() -> dict:
    client = RepliconClient()
    info = client.test_connection()
    return {"ok": True, "service": "replicon", **info}
