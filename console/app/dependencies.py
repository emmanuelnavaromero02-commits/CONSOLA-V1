from fastapi import Request, HTTPException

def current_user(request: Request) -> dict | None:
    return getattr(request.state, "user", None)

def require_user(request: Request) -> dict:
    u = current_user(request)
    if not u:
        raise HTTPException(401, "authentication required")
    return u

def require_admin(request: Request) -> dict:
    u = require_user(request)
    if u.get("role") != "admin":
        raise HTTPException(403, "admin role required")
    return u
