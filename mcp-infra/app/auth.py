
import os
from fastapi import Request, HTTPException

INTERNAL_API_KEY = os.environ.get("INTERNAL_API_KEY", "modecissions-internal-key")

def verify_internal_api_key(request: Request):
    key = request.headers.get("X-Internal-Api-Key")
    if key != INTERNAL_API_KEY:
        raise HTTPException(status_code=401, detail="Invalid internal API key")
