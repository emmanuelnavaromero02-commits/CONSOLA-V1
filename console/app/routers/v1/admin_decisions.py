from __future__ import annotations

from fastapi import APIRouter
import types

import app.main as _console_main

# Import the current console runtime namespace, including private helper
# functions used by legacy handlers. Handlers are rebound to app.main's
# namespace before registration so existing tests and monkeypatches that
# patch app.main.<helper> continue to affect the handler at runtime.
globals().update(_console_main.__dict__)
router = APIRouter()


def _bind_to_main(fn):
    rebound = types.FunctionType(
        fn.__code__,
        _console_main.__dict__,
        fn.__name__,
        fn.__defaults__,
        fn.__closure__,
    )
    rebound.__kwdefaults__ = fn.__kwdefaults__
    rebound.__annotations__ = dict(getattr(fn, "__annotations__", {}))
    rebound.__dict__.update(getattr(fn, "__dict__", {}))
    rebound.__doc__ = fn.__doc__
    rebound.__module__ = _console_main.__name__
    _console_main.__dict__[fn.__name__] = rebound
    return rebound

# /decisions
@router.get("/decisions", dependencies=[Depends(require_admin)])
@_bind_to_main
async def viewer_decisions(request: Request):
    from app.routers.pages import _console_next_response

    return _console_next_response(request, "decisions/index.html")

# /api/decisions
@router.get("/api/decisions", dependencies=[Depends(require_permission("datasets.read"))])
@_bind_to_main
async def api_decisions_list(status: str = "", overdue: str = "", user: dict = Depends(require_permission("datasets.read"))):
    workspace_id = _current_workspace_id(user)
    if not workspace_id:
        return {"decisions": []}
    sql, params = _dec_list_query(
        user=user,
        workspace_id=workspace_id,
        status=status,
        overdue=overdue,
    )
    pool = await _dec_pool()
    async with scoped_db_for_user(pool, user) as (conn, _tenant_id, _workspace_id):
        rows = await conn.fetch(sql, *params)
    return {"decisions": [_dec_row_to_dict(r) for r in rows]}

# /api/decisions
@router.post("/api/decisions", dependencies=[Depends(require_csrf), Depends(require_permission("control_room.write"))])
@_bind_to_main
async def api_decisions_create(body: dict, user: dict = Depends(require_permission("control_room.write"))):
    title = (body.get("title") or "").strip()
    if not title:
        raise HTTPException(400, "title is required")
    # Sprint v1.37 (audit B7 P0): every decision belongs to the user's
    # active workspace. Without this, console.POST /api/decisions
    # silently created rows with workspace_id=NULL and the list
    # endpoint then leaked them as "shared" across tenants on the
    # legacy fallback in 33_decisions_workspace_id.sql.
    workspace_id = _current_workspace_id(user)
    if not workspace_id:
        raise HTTPException(400, "active workspace is required to create a decision")
    assignee_id = _decision_reference_id(body.get("assignee_id"), "assignee_id")
    pool = await _dec_pool()
    async with scoped_db_for_user(pool, user) as (conn, tenant_id, _workspace_id):
        await _validate_decision_references(
            conn,
            tenant_id=tenant_id,
            workspace_id=workspace_id,
            assignee_id=assignee_id,
        )
        row = await conn.fetchrow(
            """INSERT INTO decisions
                  (title, description, commitment_date, kpis, created_by_id, assignee_id, visibility, workspace_id)
               VALUES ($1, $2, $3, $4::jsonb, $5, $6, $7, $8)
               RETURNING *""",
            title,
            body.get("description") or "",
            _coerce_date(body.get("commitment_date")),
            _json_dec.dumps(body.get("kpis") or []),
            user["id"],
            assignee_id,
            body.get("visibility") if body.get("visibility") in ("private", "shared") else "private",
            workspace_id,
        )
    return _dec_row_to_dict(row)

# /api/decisions/{decision_id}
@router.get("/api/decisions/{decision_id}", dependencies=[Depends(require_permission("datasets.read"))])
@_bind_to_main
async def api_decisions_get(decision_id: int, user: dict = Depends(require_permission("datasets.read"))):
    row = await _dec_load_with_visibility(decision_id, user)
    if not row:
        raise HTTPException(404, f"Decision {decision_id} not found")
    pool = await _dec_pool()
    async with scoped_db_for_user(pool, user) as (conn, _tenant_id, _workspace_id):
        actions = await conn.fetch(
            "SELECT * FROM decision_actions WHERE decision_id = $1 ORDER BY ts DESC",
            decision_id,
        )
    out = _dec_row_to_dict(row)
    out["actions"] = [
        {**dict(a), "ts": a["ts"].isoformat() if a["ts"] else None} for a in actions
    ]
    return out

# /api/decisions/{decision_id}
@router.patch("/api/decisions/{decision_id}", dependencies=[Depends(require_csrf), Depends(require_permission("control_room.write"))])
@_bind_to_main
async def api_decisions_update(decision_id: int, body: dict, user: dict = Depends(require_permission("control_room.write"))):
    """Patch any subset of: title, description, commitment_date, kpis, status, outcome,
    closed_at, follow_up_decision_id, assignee_id, visibility."""
    normalized_body = dict(body)
    for field in ("assignee_id", "follow_up_decision_id"):
        if field in normalized_body:
            normalized_body[field] = _decision_reference_id(
                normalized_body[field], field
            )
    workspace_id = _current_workspace_id(user)
    if not workspace_id:
        raise HTTPException(403, "active workspace is required")
    pool = await _dec_pool()
    async with scoped_db_for_user(pool, user) as (conn, tenant_id, _workspace_id):
        existing = await _dec_load_on_conn(
            conn,
            decision_id=decision_id,
            user=user,
            workspace_id=workspace_id,
            tenant_id=tenant_id,
            for_update=True,
        )
        if not existing:
            raise HTTPException(404, f"Decision {decision_id} not found")
        if not _dec_can_edit(existing, user):
            raise HTTPException(403, "you can only edit decisions you created or are assigned to")
        await _validate_decision_references(
            conn,
            tenant_id=tenant_id,
            workspace_id=workspace_id,
            assignee_id=normalized_body.get("assignee_id"),
            follow_up_decision_id=normalized_body.get("follow_up_decision_id"),
        )
        sets, params = _decision_update_assignments(normalized_body)
        if not sets:
            raise HTTPException(400, "no updatable fields supplied")
        sql, params = _decision_update_sql_and_params(
            sets=sets,
            params=params,
            decision_id=decision_id,
            existing=existing,
        )
        row = await conn.fetchrow(sql, *params)
    if not row:
        # The visibility check passed but the row vanished between
        # SELECT and UPDATE (e.g. a concurrent delete, or the row was
        # moved to a different workspace). Treat as not-found rather
        # than 500.
        raise HTTPException(404, f"Decision {decision_id} not found")
    return _dec_row_to_dict(row)

# /api/decisions/{decision_id}
@router.delete("/api/decisions/{decision_id}", dependencies=[Depends(require_csrf), Depends(require_permission("control_room.write"))])
@_bind_to_main
async def api_decisions_delete(decision_id: int, user: dict = Depends(require_permission("control_room.write"))):
    workspace_id = _current_workspace_id(user)
    if not workspace_id:
        raise HTTPException(403, "active workspace is required")
    pool = await _dec_pool()
    # Sprint v1.37: pin DELETE to (id, workspace_id) — same rationale
    # as the UPDATE above. ``existing["workspace_id"]`` comes from the
    # workspace-scoped SELECT FOR UPDATE in this same transaction.
    async with scoped_db_for_user(pool, user) as (conn, tenant_id, _workspace_id):
        existing = await _dec_load_on_conn(
            conn,
            decision_id=decision_id,
            user=user,
            workspace_id=workspace_id,
            tenant_id=tenant_id,
            for_update=True,
        )
        if not existing:
            raise HTTPException(404, f"Decision {decision_id} not found")
        if not _dec_can_delete(existing, user):
            raise HTTPException(403, "only the creator or an admin can delete a decision")
        await conn.execute(
            "DELETE FROM decisions WHERE id = $1 AND workspace_id = $2",
            decision_id,
            existing["workspace_id"],
        )
    return {"deleted": True, "id": decision_id}

# /api/decisions/{decision_id}/actions
@router.post("/api/decisions/{decision_id}/actions", dependencies=[Depends(require_csrf), Depends(require_permission("control_room.write"))])
@_bind_to_main
async def api_decisions_add_action(
    decision_id: int,
    body: dict,
    request: Request,
    user: dict = Depends(require_permission("control_room.write")),
):
    key_hash = _decision_idempotency_key(request)
    raw_action_text = body.get("action_text")
    if not isinstance(raw_action_text, str):
        raise HTTPException(400, "action_text must be a string")
    action_text = raw_action_text.strip()
    if not action_text:
        raise HTTPException(400, "action_text is required")
    note = body.get("note")
    if note is not None and not isinstance(note, str):
        raise HTTPException(400, "note must be a string or null")
    pool = await _dec_pool()
    workspace_id = _current_workspace_id(user)
    if not workspace_id:
        raise HTTPException(403, "active workspace is required")
    async with scoped_db_for_user(pool, user) as (conn, tenant_id, _workspace_id):
        if not tenant_id:
            raise HTTPException(403, "active tenant and workspace are required")
        existing = await _dec_load_on_conn(
            conn,
            decision_id=decision_id,
            user=user,
            workspace_id=workspace_id,
            tenant_id=tenant_id,
            for_update=True,
        )
        if not existing:
            raise HTTPException(404, f"Decision {decision_id} not found")
        if not _dec_can_edit(existing, user):
            raise HTTPException(403, "only creator/assignee/admin can add to bitácora")
        return await _create_decision_action_idempotently(
            conn,
            decision_id=decision_id,
            user=user,
            tenant_id=tenant_id,
            workspace_id=workspace_id,
            action_text=action_text,
            note=note,
            key_hash=key_hash,
        )

# /api/users
@router.get("/api/users")
@_bind_to_main
async def api_users_list(user: dict = Depends(require_permission("iam.users.read"))):
    return await _list_user_picker_payload_impl(
        user=user,
        auth_list_users=_auth.list_users,
        is_global_iam_admin=_is_global_iam_admin,
        visible_user_ids_for_admin=_visible_user_ids_for_admin,
    )

# /admin/users
@router.get("/admin/users", dependencies=[Depends(require_permission("iam.users.read"))])
@_bind_to_main
async def viewer_admin_users(request: Request, user: dict = Depends(require_permission("iam.users.read"))):
    # Compatibility URL, but not a separate users app anymore:
    # /admin/users now enters the IAM ecosystem and opens the Users tab.
    return RedirectResponse(url="/operations/users", status_code=307)

# /api/admin/users
@router.get("/api/admin/users")
@_bind_to_main
async def api_admin_users_list(admin_user: dict = Depends(require_permission("iam.users.read"))):
    users = await _auth.list_users(active_only=False)
    if _is_global_iam_admin(admin_user):
        return {"users": users}
    visible_ids = await _visible_user_ids_for_admin(admin_user, users)
    return {"users": [u for u in users if u.get("id") in visible_ids]}

# /api/admin/users
@router.post(
    "/api/admin/users",
    dependencies=[Depends(require_csrf), Depends(require_permission("iam.users.write"))],
)
@_bind_to_main
async def api_admin_users_create(body: dict, request: Request, admin_user: dict = Depends(require_permission("iam.users.write"))):
    email_raw = body.get("email")
    pw = body.get("password") or ""
    if not email_raw or not pw:
        raise HTTPException(400, "email and password are required")
    email = _normalize_email_or_400(email_raw)
    pw = _validate_password_or_400(pw)
    if await _auth.get_user_by_email(email):
        raise HTTPException(409, f"user with email {email} already exists")
    requested_role = _assignable_role(body.get("role"), admin_user)
    platform_role = requested_role if _is_global_iam_admin(admin_user) else "user"
    workspace_role = requested_role if not _is_global_iam_admin(admin_user) else None
    workspace_id = (
        (body.get("workspace_id") or admin_user.get("active_workspace_id") or "").strip()
        or None
    )
    if not workspace_id:
        raise HTTPException(400, "workspace_id is required")
    await _assert_can_use_workspace(admin_user, workspace_id)
    try:
        create_user_kwargs = {
            "email": email,
            "password": pw,
            "name": body.get("name"),
            "role": platform_role,
        }
        # Test doubles from older auth contracts may not expose workspace_id;
        # production auth.create_user does and assigns the membership in the
        # same transaction after the route has validated workspace scope.
        if "workspace_id" in inspect.signature(_auth.create_user).parameters:
            create_user_kwargs["workspace_id"] = workspace_id
        target_user = await _auth.create_user(**create_user_kwargs)
        if workspace_role and target_user.get("id"):
            try:
                await _set_workspace_role_for_user(int(target_user["id"]), workspace_id, workspace_role)
            except (RuntimeError, AttributeError) as exc:
                if not _workspace_scope_db_unavailable(exc):
                    raise
    except RuntimeError:
        # create_user assigns workspace membership in the same transaction;
        # let the global 500 handler log + sanitize internal details.
        raise
    await _audit.record_event(
        admin_user.get("id"), admin_user.get("email"), "user.created", "user", str(target_user["id"]),
        ip=_client_ip(request), user_agent=request.headers.get("user-agent"),
        metadata={"role": platform_role, "workspace_role": workspace_role, "workspace_id": workspace_id},
    )
    return target_user

# /api/admin/users/{user_id}
@router.patch(
    "/api/admin/users/{user_id}",
    dependencies=[Depends(require_csrf), Depends(require_permission("iam.users.write"))],
)
@_bind_to_main
async def api_admin_users_update(user_id: int, body: dict, request: Request, admin_user: dict = Depends(require_permission("iam.users.write"))):
    # Don't let an admin demote / disable themselves accidentally
    if user_id == admin_user["id"] and (body.get("role") not in (None, admin_user.get("role")) or body.get("is_active") is False):
        raise HTTPException(400, "you cannot demote or disable your own account")
    await _assert_can_manage_target_user(admin_user, user_id)
    before = await _auth.get_user_by_id(user_id)
    password = None
    if "password" in body:
        password = _validate_password_or_400(body.get("password"))
    password_changed = password is not None
    role_update = body.get("role")
    workspace_role = None
    platform_role_update = None
    if role_update:
        requested_role = _assignable_role(role_update, admin_user)
        if _is_global_iam_admin(admin_user):
            platform_role_update = requested_role
        else:
            workspace_role = requested_role
    target_user = await _auth.update_user(
        user_id,
        name=body.get("name"),
        role=platform_role_update,
        is_active=body.get("is_active"),
        password=password,
        escalation_notify=body.get("escalation_notify") if "escalation_notify" in body else None,
    )
    if not target_user:
        raise HTTPException(404, "user not found")
    if workspace_role:
        workspace_id = str(admin_user.get("active_workspace_id") or "")
        if not workspace_id:
            raise HTTPException(400, "active workspace is required")
        try:
            await _set_workspace_role_for_user(user_id, workspace_id, workspace_role)
        except (RuntimeError, AttributeError) as exc:
            if not _workspace_scope_db_unavailable(exc):
                raise
        target_user["workspace_role"] = workspace_role
    action = "user.updated"
    if before and before.get("role") != target_user.get("role"):
        action = "user.role_changed"
    elif before and before.get("is_active") and not target_user.get("is_active"):
        action = "user.disabled"
    elif before and not before.get("is_active") and target_user.get("is_active"):
        action = "user.enabled"
    await _audit.record_event(
        admin_user.get("id"),
        admin_user.get("email"),
        action,
        "user",
        str(user_id),
        ip=_client_ip(request),
        user_agent=request.headers.get("user-agent"),
        metadata={
            "role": target_user.get("role"),
            "is_active": target_user.get("is_active"),
            "password_changed": password_changed,
        },
    )
    if password_changed:
        # Password change is independently auditable: an admin overriding a
        # user's credential is privileged enough to warrant its own row, even
        # when bundled with other field updates in the same request.
        await _audit.record_event(
            admin_user.get("id"),
            admin_user.get("email"),
            "user.password_changed",
            "user",
            str(user_id),
            ip=_client_ip(request),
            user_agent=request.headers.get("user-agent"),
            metadata={"target_email": target_user.get("email")},
        )
    return target_user

# /api/admin/users/{user_id}
@router.delete(
    "/api/admin/users/{user_id}",
    dependencies=[Depends(require_csrf), Depends(require_permission("iam.users.write"))],
)
@_bind_to_main
async def api_admin_users_delete(
    user_id: int,
    request: Request,
    admin_user: dict = Depends(require_permission("iam.users.write")),
):
    if user_id == admin_user["id"]:
        raise HTTPException(400, "you cannot delete your own account")
    await _assert_can_manage_target_user(admin_user, user_id)
    try:
        ok = await _auth.delete_user(user_id)
    except RuntimeError as exc:
        raise HTTPException(409, "user delete conflict") from exc
    if not ok:
        raise HTTPException(404, "user not found")
    await _audit.record_event(
        admin_user.get("id"),
        admin_user.get("email"),
        "user.deleted",
        "user",
        str(user_id),
        ip=_client_ip(request),
        user_agent=request.headers.get("user-agent"),
    )
    return {"deleted": True, "id": user_id}

# /vpn-config/{token}
@router.get("/vpn-config/{token}")
@_bind_to_main
async def get_vpn_config(token: str, user: dict | None = Depends(current_user)):
    return await _vpn_config_download_response_impl(
        token=token,
        tokens_service=_tokens,
        vpn_service=_vpn,
        safe_filename=_safe_filename,
        response_cls=Response,
        vpn_error_cls=_vpn.VPNError,
    )

# /api/admin/users/{user_id}/vpn-reissue
@router.post(
    "/api/admin/users/{user_id}/vpn-reissue",
    dependencies=[Depends(require_csrf), Depends(require_permission("iam.users.write"))],
)
@_bind_to_main
async def api_admin_users_vpn_reissue(
    user_id: int,
    request: Request,
    admin_user: dict = Depends(require_permission("iam.users.write")),
):
    target_user = await _auth.get_user_by_id(user_id)
    if not target_user:
        raise HTTPException(404, "user not found")
    await _assert_can_manage_target_user(admin_user, user_id)
    res = await _issue_vpn_for_user(user_id, target_user["email"], target_user.get("name"))
    await _audit.record_event(
        admin_user.get("id"),
        admin_user.get("email"),
        "vpn.reissued",
        "user",
        str(user_id),
        ip=_client_ip(request),
        user_agent=request.headers.get("user-agent"),
        metadata={"issued": bool(res.get("issued")), "email_sent": res.get("email_sent")},
    )
    return {"reissued": res.get("issued", False), **res}

# /api/admin/users/invite
@router.post(
    "/api/admin/users/invite",
    dependencies=[Depends(require_csrf), Depends(require_permission("iam.users.write"))],
)
@_bind_to_main
async def api_admin_users_invite(body: dict, request: Request, admin_user: dict = Depends(require_permission("iam.users.write"))):
    """Invite a new user by email. Creates an inactive user with no password,
    issues an invitation token, and emails the activation link."""
    email = _normalize_email_or_400(body.get("email"))
    existing = await _auth.get_user_by_email(email)
    if existing:
        raise HTTPException(409, f"user with email {email} already exists")
    role = _assignable_role(body.get("role"), admin_user)
    workspace_id = (
        (body.get("workspace_id") or admin_user.get("active_workspace_id") or "").strip()
        or None
    )
    if workspace_id:
        await _assert_can_use_workspace(admin_user, workspace_id)
    else:
        raise HTTPException(400, "workspace_id is required")
    target_user = await _auth.create_invited_user(
        email=email,
        name=body.get("name"),
        role=role,
        workspace_id=workspace_id,
    )
    tok, _ = await _tokens.create(target_user["id"], "invite")
    activation_link = _activation_link(tok)

    vpn_result: dict = {"issued": False}
    if body.get("with_vpn", True):
        vpn_result = await _create_vpn_config_link(target_user["id"], email)

    attachments: list[tuple[str, bytes, str]] = []
    vpn_password: str | None = None
    if vpn_result.get("issued") and vpn_result.get("conf_text"):
        zip_bytes, vpn_password = _pack_vpn_conf(vpn_result["conf_text"], email)
        attachments.append((f"{_safe_filename(email)}.zip", zip_bytes, "application/zip"))

    if vpn_result.get("issued"):
        subject, html = _email.render_invitation_with_vpn(
            target_user.get("name"),
            email,
            activation_link,
            vpn_result["link"],
            INVITE_TTL_HOURS,
            VPN_TTL_HOURS,
            vpn_password,
        )
        sent = await _email.send_email(email, subject, html, attachments=attachments)
        vpn_result["email_sent"] = sent
    else:
        subject, html = _email.render_invitation(target_user.get("name"), email, activation_link, INVITE_TTL_HOURS)
        sent = await _email.send_email(email, subject, html)
    await _audit.record_event(
        admin_user.get("id"), admin_user.get("email"), "user.invited", "user", str(target_user["id"]),
        ip=_client_ip(request), user_agent=request.headers.get("user-agent"),
        metadata={"role": role, "workspace_id": workspace_id, "email_sent": sent, "vpn": vpn_result},
    )
    return {"invited": True, "user": target_user, "email_sent": sent, "vpn": vpn_result}

# /api/admin/users/{user_id}/reinvite
@router.post(
    "/api/admin/users/{user_id}/reinvite",
    dependencies=[Depends(require_csrf), Depends(require_permission("iam.users.write"))],
)
@_bind_to_main
async def api_admin_users_reinvite(
    user_id: int,
    request: Request,
    body: dict | None = None,
    admin_user: dict = Depends(require_permission("iam.users.write")),
):
    """Re-issue an invitation email (only for users that have not activated yet)."""
    target_user = await _auth.get_user_by_id(user_id)
    if not target_user:
        raise HTTPException(404, "user not found")
    await _assert_can_manage_target_user(admin_user, user_id)
    if target_user.get("is_active"):
        raise HTTPException(400, "user already active; use password reset instead")
    tok, _ = await _tokens.create(user_id, "invite")
    activation_link = _activation_link(tok)
    payload = body or {}
    vpn_result: dict = {"issued": False}
    if payload.get("with_vpn", True):
        vpn_result = await _create_vpn_config_link(user_id, target_user["email"])

    attachments: list[tuple[str, bytes, str]] = []
    vpn_password: str | None = None
    if vpn_result.get("issued") and vpn_result.get("conf_text"):
        zip_bytes, vpn_password = _pack_vpn_conf(vpn_result["conf_text"], target_user["email"])
        attachments.append((f"{_safe_filename(target_user['email'])}.zip", zip_bytes, "application/zip"))

    if vpn_result.get("issued"):
        subject, html = _email.render_invitation_with_vpn(
            target_user.get("name"),
            target_user["email"],
            activation_link,
            vpn_result["link"],
            INVITE_TTL_HOURS,
            VPN_TTL_HOURS,
            vpn_password,
        )
        sent = await _email.send_email(target_user["email"], subject, html, attachments=attachments)
        vpn_result["email_sent"] = sent
    else:
        subject, html = _email.render_invitation(
            target_user.get("name"), target_user["email"], activation_link, INVITE_TTL_HOURS,
        )
        sent = await _email.send_email(target_user["email"], subject, html)
    await _audit.record_event(
        admin_user.get("id"),
        admin_user.get("email"),
        "user.reinvited",
        "user",
        str(user_id),
        ip=_client_ip(request),
        user_agent=request.headers.get("user-agent"),
        metadata={"email_sent": sent, "vpn": vpn_result},
    )
    return {"reinvited": True, "email_sent": sent, "vpn": vpn_result}

# /api/admin/users/{user_id}/send-reset
@router.post(
    "/api/admin/users/{user_id}/send-reset",
    dependencies=[Depends(require_csrf), Depends(require_permission("iam.users.write"))],
)
@_bind_to_main
async def api_admin_users_send_reset(user_id: int, request: Request, admin: dict = Depends(require_permission("iam.users.write"))):
    """Email a password reset link and issue a one-time admin temporary password."""
    return await _admin_send_reset_payload_impl(
        user_id=user_id,
        admin=admin,
        request_ip=_client_ip(request),
        user_agent=request.headers.get("user-agent"),
        auth_service=_auth,
        audit_service=_audit,
        tokens_service=_tokens,
        email_service=_email,
        reset_link_for_token=_reset_link,
        reset_ttl_hours=RESET_TTL_HOURS,
        assert_can_manage_target_user=_assert_can_manage_target_user,
    )
