from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from app.dependencies import require_interactive_session
from app.domains.data_platform.rag_requests import read_json_body_capped
from app.services import access_tokens, audit_service
from app.services.csrf import require_csrf
from app.services.permissions import get_effective_permissions
from app.services.rate_limiter import get_rate_limiter
from app.services.request_rate_limits import (
    client_ip,
    rate_limit_authenticated_action,
)


router = APIRouter(tags=["Access tokens"])

MAX_BODY_BYTES = 4_096
_NO_STORE = {"Cache-Control": "no-store", "Pragma": "no-cache"}


class CreateAccessTokenRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    nombre: str = Field(min_length=1, max_length=access_tokens.MAX_NAME_CHARS)
    alcance: Literal["lectura", "acciones"] = "lectura"
    dias: Literal[7, 30, 90] = access_tokens.DEFAULT_EXPIRY_DAYS


def _workspace(user: dict) -> tuple[str, str | None]:
    workspace_id = str(user.get("active_workspace_id") or "").strip()
    if not workspace_id:
        raise HTTPException(403, "Se requiere un espacio de trabajo activo.")
    name = next(
        (
            str(item.get("workspace_name") or "") or None
            for item in user.get("workspaces") or []
            if str(item.get("workspace_id") or "") == workspace_id
        ),
        None,
    )
    return workspace_id, name


def _allowed_scopes(user: dict) -> list[str]:
    return access_tokens.allowed_scopes(frozenset(get_effective_permissions(user)))


def _raise_token_error(exc: access_tokens.AccessTokenError) -> None:
    raise HTTPException(exc.status_code, exc.message) from None


async def _audit(request: Request, user: dict, action: str, token: dict) -> None:
    await audit_service.record_event(
        user_id=user.get("id"),
        email=user.get("email"),
        action=action,
        resource_type="access_token",
        resource_id=str(token.get("id") or ""),
        ip=client_ip(request),
        user_agent=(request.headers.get("user-agent") or "")[:200] or None,
        status="success",
        metadata={
            "token_id": str(token.get("id") or ""),
            "token_prefix": token.get("token_prefix"),
            "scopes": list(token.get("alcances") or []),
            "workspace_id": (token.get("espacio_de_trabajo") or {}).get("id"),
            "expires_at": token.get("vence_en"),
        },
    )


@router.get(
    "/api/me/access-tokens",
    dependencies=[Depends(require_interactive_session)],
)
async def list_access_tokens(user: dict = Depends(require_interactive_session)):
    tokens = await access_tokens.list_tokens(int(user["id"]))
    active = sum(
        1 for token in tokens if token.get("estado") in access_tokens.UNEXPIRED_LIST_STATUSES
    )
    workspace_id = str(user.get("active_workspace_id") or "")
    return JSONResponse(
        {
            "tokens": tokens,
            "puede_crear": bool(workspace_id) and active < access_tokens.MAX_ACTIVE_TOKENS,
            "alcances_permitidos": _allowed_scopes(user),
            "limite_activos": access_tokens.MAX_ACTIVE_TOKENS,
            "tokens_activos": active,
            "dias_permitidos": list(access_tokens.EXPIRY_DAYS),
            "dias_predeterminados": access_tokens.DEFAULT_EXPIRY_DAYS,
        },
        headers=_NO_STORE,
    )


@router.post(
    "/api/me/access-tokens",
    dependencies=[Depends(require_csrf), Depends(require_interactive_session)],
)
async def create_access_token(
    request: Request,
    user: dict = Depends(require_interactive_session),
):
    raw = await read_json_body_capped(request, MAX_BODY_BYTES)
    raw.pop("_csrf", None)
    try:
        body = CreateAccessTokenRequest.model_validate(raw)
    except ValidationError:
        raise HTTPException(
            400, "Revisa el nombre (1 a 80 caracteres), el alcance y la vigencia."
        ) from None
    if body.alcance not in _allowed_scopes(user):
        raise HTTPException(
            403, "Tu usuario no puede crear tokens con alcance de acciones."
        )
    workspace_id, workspace_name = _workspace(user)
    await rate_limit_authenticated_action(
        "access_token:create",
        user_id=user.get("id"),
        workspace_id=workspace_id,
        limiter_factory=get_rate_limiter,
    )
    try:
        created = await access_tokens.create_token(
            user_id=int(user["id"]),
            workspace_id=workspace_id,
            workspace_name=workspace_name,
            name=body.nombre,
            scopes=access_tokens.normalize_scopes(body.alcance),
            days=body.dias,
        )
    except access_tokens.AccessTokenError as exc:
        _raise_token_error(exc)
    await _audit(request, user, "access_token.create", created.token)
    return JSONResponse(
        {"token": created.secret, "token_info": created.token},
        status_code=201,
        headers=_NO_STORE,
    )


@router.delete(
    "/api/me/access-tokens/{token_id}",
    dependencies=[Depends(require_csrf), Depends(require_interactive_session)],
)
async def revoke_access_token(
    token_id: str,
    request: Request,
    user: dict = Depends(require_interactive_session),
):
    revoked = await access_tokens.revoke_token(int(user["id"]), token_id)
    if revoked is None:
        raise HTTPException(404, "No se encontró un token activo con ese identificador.")
    await _audit(request, user, "access_token.revoke", revoked)
    return JSONResponse({"revocado": True, "id": token_id}, headers=_NO_STORE)


__all__ = ("router",)
