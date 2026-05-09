from fastapi import APIRouter, Depends, HTTPException, Request
from app.dependencies import require_admin
from app.services import auth as _auth
import json

router = APIRouter(prefix="/security", tags=["Security Center"])

@router.get("/sessions")
async def get_sessions(user: dict = Depends(require_admin)):
    p = await _auth.pool()
    rows = await p.fetch(
        """SELECT s.token, s.user_id, u.email as user_email, s.ip, s.last_seen, s.user_agent, s.created_at, s.expires_at
           FROM user_sessions s
           JOIN users u ON u.id = s.user_id
           ORDER BY s.last_seen DESC NULLS LAST"""
    )
    res = []
    for r in rows:
        d = dict(r)
        token = d.pop("token")
        d["token_preview"] = token[:8] + "..." if token and len(token) > 8 else "***"
        res.append(d)
    return res

@router.delete("/sessions/{token}")
async def revoke_session(token: str, user: dict = Depends(require_admin)):
    p = await _auth.pool()
    res = await p.execute("DELETE FROM user_sessions WHERE token = $1", token)
    if res == "DELETE 0":
        raise HTTPException(status_code=404, detail="Session not found")
    return {"status": "ok"}

@router.get("/audit")
async def get_audit_events(user: dict = Depends(require_admin)):
    p = await _auth.pool()
    rows = await p.fetch(
        """SELECT a.id, a.user_id, u.email as user_email, a.action, a.metadata as details, a.ip, a.created_at
           FROM audit_events a
           LEFT JOIN users u ON u.id = a.user_id
           ORDER BY a.created_at DESC
           LIMIT 100"""
    )
    res = []
    for r in rows:
        d = dict(r)
        if isinstance(d.get("details"), str):
            try:
                d["details"] = json.loads(d["details"])
            except:
                pass
        res.append(d)
    return res
