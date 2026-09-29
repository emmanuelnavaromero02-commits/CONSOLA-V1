from __future__ import annotations

import base64
import hashlib
import re
from functools import lru_cache
from pathlib import Path
from urllib.parse import urlencode

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import FileResponse, RedirectResponse

from app.dependencies import ROLE_ADMIN, ROLE_WORKSPACE_ADMIN, require_admin, require_global_any_role
from app.dependencies import require_authenticated
from app.services.csrf import CSRF_COOKIE_NAME, set_csrf_cookie
from app.services.permission_roles import PLATFORM_ADMIN_ROLES
from app.services.permissions import has_permission, require_permission


STATIC = Path(__file__).resolve().parents[1] / "static"
CONSOLE_NEXT_STATIC = STATIC / "console-next"
_INLINE_SCRIPT_RE = re.compile(
    r"<script(?![^>]*\bsrc=)[^>]*>(.*?)</script>", re.IGNORECASE | re.DOTALL
)
_DATA_VIEWERS = {
    "schema",
    "datasets",
    "dataset",
    "semantic",
    "semantic-layer",
    "lineage",
}
_VAULT_VIEWERS = {"vault"}

router = APIRouter(tags=["Pages"])
PLATFORM_ADMIN = require_global_any_role("owner", "super_admin", ROLE_ADMIN)


def _console_next_file(path: str = "index.html") -> Path:
    root = CONSOLE_NEXT_STATIC.resolve()
    if not root.is_dir():
        raise HTTPException(
            status_code=503, detail="console-next frontend is not built"
        )
    candidate = (root / path).resolve()
    try:
        candidate.relative_to(root)
    except ValueError as exc:
        raise HTTPException(
            status_code=404, detail="console-next asset not found"
        ) from exc
    if candidate.is_dir():
        candidate = candidate / "index.html"
    if not candidate.is_file():
        raise HTTPException(status_code=404, detail="console-next asset not found")
    return candidate


@lru_cache(maxsize=128)
def _console_next_csp_cached(
    path: str,
    mtime_ns: int,
    size: int,
    frame_ancestors: str = "'none'",
) -> str:
    html = Path(path).read_text(encoding="utf-8")
    hashes = []
    for body in _INLINE_SCRIPT_RE.findall(html):
        if not body.strip():
            continue
        digest = hashlib.sha256(body.encode("utf-8")).digest()
        hashes.append(base64.b64encode(digest).decode("ascii"))
    script_src = "script-src 'self'" + "".join(f" 'sha256-{value}'" for value in hashes)
    return (
        "default-src 'self'; "
        f"{script_src}; "
        "style-src 'self' 'unsafe-inline'; "
        "img-src 'self' data: blob:; "
        "font-src 'self' data:; "
        "connect-src 'self'; "
        f"frame-ancestors {frame_ancestors}; "
        "base-uri 'self'; "
        "form-action 'self'"
    )


def _console_next_csp(path: str, frame_ancestors: str = "'none'") -> str:
    stat = Path(path).stat()
    return _console_next_csp_cached(
        path, stat.st_mtime_ns, stat.st_size, frame_ancestors
    )


def _console_next_response(
    request: Request, path: str = "index.html", *, frame_ancestors: str = "'none'"
) -> FileResponse:
    page = _console_next_file(path)
    response = FileResponse(
        page,
        headers={
            "Content-Security-Policy": _console_next_csp(str(page), frame_ancestors)
        },
    )
    set_csrf_cookie(response, request.cookies.get(CSRF_COOKIE_NAME))
    return response


def _viewer_permission(viewer_type: str | None) -> str:
    normalized = (viewer_type or "jobs").strip().lower()
    if normalized in _DATA_VIEWERS:
        return "datasets.read"
    if normalized in _VAULT_VIEWERS:
        return "vault.connections.read"
    return "monitor.read"


async def _require_viewer_permission(request: Request) -> dict:
    user = getattr(request.state, "user", None)
    if not user:
        raise HTTPException(status_code=401, detail="authentication required")
    permission = _viewer_permission(request.query_params.get("type"))
    if not has_permission(user, permission):
        raise HTTPException(
            status_code=403, detail=f"permission required: {permission}"
        )
    return user


def _viewer_redirect(
    request: Request, viewer_type: str, **params: str
) -> RedirectResponse:
    query = dict(request.query_params)
    query["type"] = viewer_type
    for key, value in params.items():
        if value:
            query[key] = value
    return RedirectResponse(url=f"/viewer?{urlencode(query)}", status_code=307)


@router.get("/")
async def index():
    return RedirectResponse(url="/dashboard", status_code=307)


@router.get("/monitor", dependencies=[Depends(require_permission("monitor.read"))])
async def monitor_page(request: Request):
    return _console_next_response(request, "monitor/index.html")


@router.get("/dashboard", dependencies=[Depends(require_authenticated)])
async def dashboard_page(request: Request):
    return _console_next_response(request, "dashboard/index.html")


@router.get(
    "/security",
    dependencies=[
        Depends(require_permission("security.audit.read")),
        Depends(require_admin),
    ],
)
async def security_page(request: Request):
    return _console_next_response(request, "security/index.html")


@router.get(
    "/control-room", dependencies=[Depends(require_permission("datasets.read"))]
)
async def control_room_page(request: Request):
    return _console_next_response(request, "control-room/index.html")


@router.get(
    "/control-room/", dependencies=[Depends(require_permission("datasets.read"))]
)
async def control_room_page_slash(request: Request):
    return _console_next_response(request, "control-room/index.html")


@router.get(
    "/control-room/talent",
    dependencies=[
        Depends(require_permission("operations.read")),
        Depends(require_permission("datasets.read")),
    ],
)
async def control_room_talent_page(request: Request):
    return _console_next_response(request, "control-room/talent/index.html")


@router.get(
    "/control-room/talent/",
    dependencies=[
        Depends(require_permission("operations.read")),
        Depends(require_permission("datasets.read")),
    ],
)
async def control_room_talent_page_slash(request: Request):
    return _console_next_response(request, "control-room/talent/index.html")


@router.get(
    "/control-room/sap-b1",
    dependencies=[
        Depends(require_permission("operations.read")),
        Depends(require_permission("datasets.read")),
    ],
)
async def control_room_sap_b1_page(request: Request):
    return _console_next_response(request, "control-room/sap-b1/index.html")


@router.get(
    "/control-room/sap-b1/",
    dependencies=[
        Depends(require_permission("operations.read")),
        Depends(require_permission("datasets.read")),
    ],
)
async def control_room_sap_b1_page_slash(request: Request):
    return _console_next_response(request, "control-room/sap-b1/index.html")


@router.get(
    "/operational-intelligence",
    dependencies=[Depends(require_permission("datasets.read"))],
)
async def operational_intelligence_page(request: Request):
    return _console_next_response(request, "operational-intelligence/index.html")


@router.get(
    "/operational-intelligence/",
    dependencies=[Depends(require_permission("datasets.read"))],
)
async def operational_intelligence_page_slash(request: Request):
    return _console_next_response(request, "operational-intelligence/index.html")


@router.get(
    "/supervised-actions",
    dependencies=[Depends(require_permission("control_room.write"))],
)
async def supervised_actions_page(request: Request):
    return _console_next_response(request, "supervised-actions/index.html")


@router.get(
    "/supervised-actions/",
    dependencies=[Depends(require_permission("control_room.write"))],
)
async def supervised_actions_page_slash(request: Request):
    return _console_next_response(request, "supervised-actions/index.html")


@router.get("/my-access", dependencies=[Depends(require_authenticated)])
async def my_access_page(request: Request):
    return _console_next_response(request, "my-access/index.html")


@router.get("/mis-accesos", dependencies=[Depends(require_authenticated)])
async def mis_accesos_page():
    return RedirectResponse(url="/my-access", status_code=307)


@router.get(
    "/iam",
    dependencies=[
        Depends(require_permission("iam.users.read")),
        Depends(require_admin),
    ],
)
async def iam_page():
    return RedirectResponse(url="/operations/users", status_code=307)


@router.get(
    "/settings",
    dependencies=[Depends(require_permission("settings.read")), Depends(require_admin)],
)
async def settings_page(request: Request):
    return _console_next_response(request, "settings/index.html")


@router.get(
    "/operations",
    dependencies=[Depends(require_permission("operations.read"))],
)
async def operations_page(request: Request):
    return _console_next_response(request, "operations/index.html")


@router.get(
    "/operations/companies",
    dependencies=[Depends(PLATFORM_ADMIN)],
)
async def operations_companies_page(request: Request):
    return _console_next_response(request, "operations/companies/index.html")


@router.get(
    "/operations/users",
    dependencies=[Depends(require_permission("iam.users.read"))],
)
async def operations_users_page(request: Request):
    return _console_next_response(request, "operations/users/index.html")


@router.get(
    "/operations/audit",
    dependencies=[Depends(require_permission("security.audit.read"))],
)
async def operations_audit_page(request: Request):
    return _console_next_response(request, "operations/audit/index.html")


@router.get(
    "/operations/vault",
    dependencies=[Depends(require_permission("vault.connections.read"))],
)
async def operations_vault_page(request: Request):
    return _console_next_response(request, "operations/vault/index.html")


@router.get(
    "/operations/workflows",
    dependencies=[
        Depends(require_permission("operations.read")),
        Depends(require_admin),
    ],
)
@router.get(
    "/operations/workflows/",
    dependencies=[
        Depends(require_permission("operations.read")),
        Depends(require_admin),
    ],
)
async def operations_workflows_page(request: Request):
    return _console_next_response(request, "operations/workflows/index.html")


@router.get(
    "/operations/metrics",
    dependencies=[Depends(require_permission("operations.read"))],
)
@router.get(
    "/operations/metrics/",
    dependencies=[Depends(require_permission("operations.read"))],
)
async def operations_metrics_page(request: Request):
    return _console_next_response(request, "operations/metrics/index.html")


@router.get("/viewer/jobs", dependencies=[Depends(require_permission("monitor.read"))])
async def viewer_jobs(request: Request):
    return _viewer_redirect(request, "jobs")


@router.get(
    "/viewer/jobs/{job_id}", dependencies=[Depends(require_permission("monitor.read"))]
)
async def viewer_job(job_id: str, request: Request):
    return _viewer_redirect(request, "job", id=job_id)


@router.get(
    "/viewer/schema", dependencies=[Depends(require_permission("datasets.read"))]
)
async def viewer_schema(request: Request):
    return _viewer_redirect(request, "schema")


@router.get(
    "/viewer/datasets", dependencies=[Depends(require_permission("datasets.read"))]
)
async def viewer_datasets(request: Request):
    return _viewer_redirect(request, "datasets")


@router.get(
    "/viewer/datasets/{name}",
    dependencies=[Depends(require_permission("datasets.read"))],
)
async def viewer_dataset(name: str, request: Request):
    return _viewer_redirect(request, "dataset", name=name)


@router.get("/data", dependencies=[Depends(require_permission("datasets.read"))])
async def data_page(request: Request):
    return _console_next_response(request, "data/index.html")


@router.get("/data/", dependencies=[Depends(require_permission("datasets.read"))])
async def data_page_slash(request: Request):
    return _console_next_response(request, "data/index.html")


@router.get(
    "/data/catalog", dependencies=[Depends(require_permission("datasets.read"))]
)
@router.get(
    "/data/catalog/", dependencies=[Depends(require_permission("datasets.read"))]
)
async def data_catalog_page(request: Request):
    return _console_next_response(request, "data/catalog/index.html")


@router.get(
    "/data/inventory", dependencies=[Depends(require_permission("datasets.read"))]
)
@router.get(
    "/data/inventory/", dependencies=[Depends(require_permission("datasets.read"))]
)
async def data_inventory_page(request: Request):
    return _console_next_response(request, "data/inventory/index.html")


@router.get(
    "/data/lineage", dependencies=[Depends(require_permission("datasets.read"))]
)
@router.get(
    "/data/lineage/", dependencies=[Depends(require_permission("datasets.read"))]
)
async def data_lineage_page(request: Request):
    return _console_next_response(request, "data/lineage/index.html")


@router.get(
    "/data/bronze",
    dependencies=[
        Depends(require_permission("datasets.write")),
        Depends(require_admin),
    ],
)
@router.get(
    "/data/bronze/",
    dependencies=[
        Depends(require_permission("datasets.write")),
        Depends(require_admin),
    ],
)
async def data_bronze_page(request: Request):
    return _console_next_response(request, "data/bronze/index.html")


@router.get(
    "/studio",
    dependencies=[
        Depends(require_permission("studio.read")),
        Depends(require_admin),
    ],
)
@router.get(
    "/studio/",
    dependencies=[
        Depends(require_permission("studio.read")),
        Depends(require_admin),
    ],
)
async def studio_page(request: Request):
    return _console_next_response(request, "studio/index.html")


@router.get("/lineage", dependencies=[Depends(require_permission("datasets.read"))])
async def lineage_page(request: Request):
    return _viewer_redirect(request, "lineage")


@router.get("/linaje", dependencies=[Depends(require_permission("datasets.read"))])
async def linaje_page(request: Request):
    return _viewer_redirect(request, "lineage")


@router.get(
    "/viewer/semantic", dependencies=[Depends(require_permission("datasets.read"))]
)
async def viewer_semantic(request: Request):
    return _viewer_redirect(request, "semantic")


@router.get("/apps-gallery", dependencies=[Depends(require_permission("apps.read"))])
async def apps_gallery(request: Request):
    return _console_next_response(request, "apps-gallery/index.html")


@router.get("/analytics", dependencies=[Depends(require_permission("apps.read"))])
async def analytics_page(request: Request):
    return _console_next_response(request, "analytics/index.html")


@router.get(
    "/analytics/viewer", dependencies=[Depends(require_permission("apps.read"))]
)
async def analytics_viewer_page(request: Request):
    return _console_next_response(request, "analytics/viewer/index.html")


@router.get(
    "/cartridges",
    dependencies=[Depends(require_permission("cartridges.read"))],
)
async def cartridges_page(request: Request):
    return _console_next_response(request, "cartridges/index.html")


@router.get(
    "/cartridges/viewer",
    dependencies=[Depends(require_permission("cartridges.read"))],
)
async def cartridges_viewer_page(request: Request):
    return _console_next_response(request, "cartridges/viewer/index.html")


@router.get(
    "/workspace",
    dependencies=[Depends(require_permission("workspace.access"))],
)
async def workspace_page(request: Request):
    """Legacy surface merged into /copilot; preserves ?prompt= deep links."""
    prompt = request.query_params.get("prompt")
    target = "/copilot"
    if prompt:
        target += f"?{urlencode({'prompt': prompt})}"
    return RedirectResponse(url=target, status_code=303)


@router.get(
    "/copilot",
    dependencies=[Depends(require_permission("copilot.use"))],
)
async def copilot_page(request: Request):
    return _console_next_response(request, "copilot/index.html")


@router.get(
    "/copilot/actions",
    dependencies=[Depends(require_permission("copilot.use"))],
)
@router.get(
    "/copilot/actions/",
    dependencies=[Depends(require_permission("copilot.use"))],
)
async def copilot_actions_page(request: Request):
    return _console_next_response(request, "copilot/actions/index.html")


def _require_knowledge_manager_role(request: Request) -> dict:
    """Mirrors the /api/rag write guard: platform admin or workspace admin."""
    user = getattr(request.state, "user", None)
    if not user:
        raise HTTPException(401, "authentication required")
    role = str(user.get("role") or "")
    roles = {role, str(user.get("workspace_role") or "")}
    if role in PLATFORM_ADMIN_ROLES or roles.intersection({ROLE_ADMIN, ROLE_WORKSPACE_ADMIN}):
        return user
    raise HTTPException(403, "required role missing")


@router.get(
    "/copilot/knowledge",
    dependencies=[
        Depends(require_permission("datasets.write")),
        Depends(_require_knowledge_manager_role),
    ],
)
@router.get(
    "/copilot/knowledge/",
    dependencies=[
        Depends(require_permission("datasets.write")),
        Depends(_require_knowledge_manager_role),
    ],
)
async def copilot_knowledge_page(request: Request):
    return _console_next_response(request, "copilot/knowledge/index.html")


@router.get(
    "/copilot/tokens",
    dependencies=[Depends(require_permission("copilot.use"))],
)
@router.get(
    "/copilot/tokens/",
    dependencies=[Depends(require_permission("copilot.use"))],
)
async def copilot_tokens_page(request: Request):
    return _console_next_response(request, "copilot/tokens/index.html")


@router.get("/viewer", dependencies=[Depends(_require_viewer_permission)])
async def viewer_page(request: Request):
    return _console_next_response(
        request, "viewer/index.html", frame_ancestors="'self'"
    )
