"""Sprint v1.44.2 — static guards for the Next.js scaffold + the
v1.44.1 backend hooks that it consumes.

Browser-level runtime exercise (login → dashboard → KPI poll) is
out of scope for the CI sandbox; this file is the static contract
that:
  * The Next.js scaffold has the deps + static-export config the brief
    documents.
  * No API key / secret leaks into a NEXT_PUBLIC_* env name.
  * FastAPI serves the static export and protects routes with its
    existing auth/RBAC dependencies.
  * The dashboard hook + components consume the v1.44.1 KpiPayload
    shape.

`next build` succeeding is the additional CI-sandbox-runnable check
(captured by a shell smoke in scripts/smoke_test.sh — wired in a
later commit so the gate can fail loudly on real regressions).
"""
from __future__ import annotations

import json
import re
from pathlib import Path


REPO        = Path(__file__).resolve().parents[1]
NEXT_ROOT   = REPO / "console-next"
SRC         = NEXT_ROOT / "src"
COMPOSE     = REPO / "infra/docker-compose.yml"


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


# ── Project layout ───────────────────────────────────────────────────────


def test_console_next_directory_exists():
    assert NEXT_ROOT.is_dir(), "console-next/ scaffold missing"


def test_package_json_present_and_parseable():
    pkg = json.loads(_read(NEXT_ROOT / "package.json"))
    assert pkg["name"] == "console-next"
    # Standard Next.js + companion deps.
    for dep in ("next", "react", "react-dom"):
        assert dep in pkg["dependencies"], f"missing dep {dep}"
    for dep in ("@tanstack/react-query", "sonner", "lucide-react",
                "tailwind-merge", "clsx", "zustand"):
        assert dep in pkg["dependencies"], (
            f"v1.44.2 brief deps: missing {dep!r}"
        )
    # next must pin a vetted production release. v1.44.2 started on
    # Next 14; the go-live security audit moved the frontend to 16.2.6
    # because npm audit no longer provided a safe patched 14.x floor.
    next_pin = pkg["dependencies"]["next"]
    assert next_pin.lstrip("^~").startswith("16."), (
        f"next pin should be 16.x; got {next_pin}"
    )
    m = re.match(r"[\^~]?16\.(\d+)\.(\d+)", next_pin)
    assert m, f"unparseable next pin: {next_pin}"
    minor, patch = int(m.group(1)), int(m.group(2))
    assert (minor, patch) >= (2, 6), (
        f"next pin must be >= 16.2.6 (go-live npm audit hardening). "
        f"Got {next_pin}"
    )


def test_tsconfig_has_path_alias():
    tsc = json.loads(_read(NEXT_ROOT / "tsconfig.json"))
    paths = tsc["compilerOptions"]["paths"]
    assert paths.get("@/*") == ["./src/*"], (
        "tsconfig must declare @/* → ./src/* (the brief's import alias)"
    )
    # Strict-mode TS — the brief doesn't say it, but the audit will.
    assert tsc["compilerOptions"]["strict"] is True


def test_next_config_uses_static_export_output():
    src = _read(NEXT_ROOT / "next.config.mjs")
    assert 'output: "export"' in src, (
        "next.config.mjs must use output:'export' so FastAPI can serve "
        "the compiled files without a Node.js runtime"
    )
    assert 'assetPrefix: "/static/console-next"' in src


def test_next_config_disables_powered_by_header():
    src = _read(NEXT_ROOT / "next.config.mjs")
    assert "poweredByHeader: false" in src, (
        "X-Powered-By: Next.js is a fingerprinting gift; disable it"
    )


def test_next_config_does_not_emit_runtime_headers():
    """Static export cannot depend on Next.js runtime header hooks."""
    src = _read(NEXT_ROOT / "next.config.mjs")
    assert "headers(" not in src
    assert "rewrites(" not in src


# ── Tailwind / styles ────────────────────────────────────────────────────


def test_tailwind_config_has_sap_blue_primary():
    """The brief documents a SAP-blue primary scale. Tailwind config
    pulls from the --primary HSL variable; globals.css declares it
    at the SAP-blue hue (~208deg, 92% sat, 43% lum = #0a6ed1)."""
    css = _read(SRC / "styles/globals.css")
    assert re.search(r"--primary:\s*208\s+92%\s+43%", css), (
        "globals.css :root must declare --primary at SAP-blue hue (208 92% 43%)"
    )
    assert re.search(r"--primary:\s*207\s+71%\s+60%", css), (
        ".dark must override --primary to the dark-mode variant (#4ea3e0)"
    )


def test_tailwind_config_uses_class_dark_mode():
    cfg = _read(NEXT_ROOT / "tailwind.config.ts")
    assert 'darkMode: "class"' in cfg, (
        "tailwind config must use class-based dark mode so toggling is "
        "deterministic (no prefers-color-scheme surprises)"
    )


# ── Static FastAPI serving ───────────────────────────────────────────────


def test_next_proxy_is_absent_for_static_export():
    assert not (SRC / "proxy.ts").exists()
    assert not (SRC / "lib/proxy.ts").exists()


def test_next_route_handlers_are_absent_for_static_export():
    for path in (
        SRC / "app/api/[...path]/route.ts",
        SRC / "app/auth/[...path]/route.ts",
        SRC / "app/login-proxy/route.ts",
        SRC / "app/legacy/[[...path]]/route.ts",
        SRC / "app/security/[...path]/route.ts",
    ):
        assert not path.exists(), f"{path.relative_to(REPO)} must not exist"


def test_fastapi_pages_router_serves_console_next_export():
    src = _read(REPO / "console/app/routers/pages.py")
    assert "CONSOLE_NEXT_STATIC" in src
    assert "_console_next_response" in src
    assert "set_csrf_cookie" in src
    for route in (
        '"/dashboard"',
        '"/cartridges"',
        '"/copilot"',
        '"/copilot/actions"',
        '"/monitor"',
        '"/operational-intelligence"',
        '"/supervised-actions"',
        '"/viewer"',
    ):
        assert route in src


def test_fastapi_workspace_route_serves_workspace_shell_not_copilot_layout():
    src = _read(REPO / "console/app/routers/pages.py")
    start = src.index('"/workspace"')
    end = src.index('@router.post(\n    "/workspace/chat"', start)
    workspace_route = src[start:end]
    assert 'FileResponse(STATIC / "workspace.html")' in workspace_route
    assert 'set_csrf_cookie' in workspace_route
    assert '"workspace/index.html"' not in workspace_route


def test_fastapi_workspace_proxy_forwards_csrf_header_to_workspace_service():
    src = _read(REPO / "console/app/routers/pages.py")
    start = src.index("def _workspace_headers")
    end = src.index("async def _workspace_proxy", start)
    workspace_headers = src[start:end]
    assert '"x-csrf-token"' in workspace_headers


def test_fastapi_console_next_csp_hashes_inline_next_scripts():
    src = _read(REPO / "console/app/routers/pages.py")
    assert "_INLINE_SCRIPT_RE" in src
    assert "hashlib.sha256" in src
    assert "'sha256-" in src
    assert "frame-ancestors" in src


def test_copilot_initial_prompt_is_visible_and_sendable():
    src = _read(SRC / "components/workspace/ChatLayout.tsx")
    assert "preparedPrompt" in src
    assert "Prompt preparado" in src
    assert "Enviar al copiloto" in src


def test_copilot_actions_are_on_dedicated_screen_not_chat_surface():
    chat = _read(SRC / "components/workspace/ChatLayout.tsx")
    copilot_page = _read(SRC / "app/(shell)/copilot/page.tsx")
    workspace_page = _read(SRC / "app/(shell)/workspace/page.tsx")
    actions_page = SRC / "app/(shell)/copilot/actions/page.tsx"
    panel = _read(SRC / "components/workspace/CopilotActionsConsole.tsx")
    client = _read(SRC / "lib/copilot/client.ts")

    assert actions_page.exists()
    assert "actionsHref" in chat
    assert "Acciones" in chat
    assert "<CopilotActionsConsole" not in chat
    assert 'actionsHref="/copilot/actions"' in copilot_page
    assert "actionsHref" not in workspace_page
    assert "CopilotActionsConsole" in _read(actions_page)
    for endpoint in (
        "/api/copilot/goals",
        "/api/copilot/briefing/v2",
        "/api/copilot/watchdogs",
        "/api/copilot/lessons",
        "/api/copilot/ask-with-context",
    ):
        assert endpoint in client
    for visible_label in (
        "Acciones",
        "Crear + diagnosticar",
        "Analizar contexto",
        "Buscar vigilancias",
        "Contexto Vivo",
        "Aprendizajes",
    ):
        assert visible_label in panel


# ── lib/api.ts — server vs browser base URL ──────────────────────────────


def test_api_client_has_no_server_side_base_url():
    """Static export has no server-side data plane; all browser calls are
    relative to the FastAPI origin serving the HTML."""
    src = _read(SRC / "lib/api.ts")
    assert "BACKEND_INTERNAL_URL" not in src
    assert "API_INTERNAL_URL" not in src


def test_api_client_browser_uses_same_origin_fastapi_fetch():
    """The browser client must not point at a public backend origin or
    rely on a Next.js proxy. Relative fetches hit FastAPI directly."""
    src = _read(SRC / "lib/api.ts")
    assert 'NEXT_PUBLIC_API_BASE' not in src, (
        "lib/api.ts must not reference NEXT_PUBLIC_API_BASE."
    )
    assert 'NEXT_PUBLIC_BACKEND_URL' not in src, (
        "lib/api.ts must not reference NEXT_PUBLIC_BACKEND_URL."
    )
    assert "export async function apiFetch" in src
    assert "fetch(path" in src


def test_api_client_sends_credentials_and_request_id():
    """The JWT is in an httpOnly cookie and every request must be
    auditable through X-Request-ID."""
    src = _read(SRC / "lib/api.ts")
    assert 'credentials: init.credentials ?? "include"' in src
    assert '"X-Request-ID"' in src
    assert '"X-CSRF-Token"' in src


# ── NEXT_PUBLIC_* leak guard ─────────────────────────────────────────────


def test_no_secrets_in_next_public_envs():
    """Any env name prefixed with NEXT_PUBLIC_ ships to the browser
    bundle. INTERNAL_API_KEY / DATABASE_URL / JWT_SECRET / etc. must
    never appear as NEXT_PUBLIC_*. Pre-flight check on the source
    files + the compose env block.

    Searches every TS/TSX/JS file in console-next/src for the
    forbidden patterns.
    """
    forbidden_substrings = (
        "INTERNAL_API_KEY",
        "JWT_SECRET",
        "DATABASE_URL",
        "OMEGA_",                # role passwords
        "FIELD_ENCRYPTION_KEY",
        "ANTHROPIC_API_KEY",
        "MINIO_SECRET_KEY",
        "SUPERSET_SECRET_KEY",
    )
    bad: list[str] = []
    for file in SRC.rglob("*"):
        if not file.is_file() or file.suffix not in {".ts", ".tsx", ".js", ".mjs"}:
            continue
        text = file.read_text(encoding="utf-8")
        for forbidden in forbidden_substrings:
            if f"NEXT_PUBLIC_{forbidden}" in text:
                bad.append(f"{file.relative_to(REPO)}: NEXT_PUBLIC_{forbidden}")

    assert not bad, (
        "v1.44.2 Security R1: NEXT_PUBLIC_* must never carry a secret "
        "name. Offenders:\n  " + "\n  ".join(bad)
    )


# ── Runtime removal ──────────────────────────────────────────────────────


def test_console_next_has_no_runtime_dockerfile():
    assert not (NEXT_ROOT / "Dockerfile").exists()


# ── Static assets ────────────────────────────────────────────────────────


def test_public_directory_exists_for_dockerfile_copy():
    """Keep public/ tracked for Next static assets even without a runtime
    container."""
    pub = NEXT_ROOT / "public"
    assert pub.is_dir(), (
        "console-next/public/ must exist on disk"
    )
    # The COPY targets the directory itself — even a single
    # placeholder file (.gitkeep) is enough to make git track it.
    contents = list(pub.iterdir())
    assert contents, (
        "console-next/public/ must contain at least one file "
        "(e.g. .gitkeep) so git tracks the directory"
    )


# ── docker-compose entry ─────────────────────────────────────────────────


def test_compose_does_not_declare_console_next_service():
    src = _read(COMPOSE)
    assert "console_next:" not in src
    assert "omega_console_next" not in src
    assert "3000:3000" not in src


def test_package_has_export_copy_script_for_fastapi_static_mount():
    pkg = json.loads(_read(NEXT_ROOT / "package.json"))
    script = pkg["scripts"]["export:copy"]
    assert "npm run build" in script or "next build" in script
    assert "../console/app/static/console-next" in script
    assert "rm -rf ../console/app/static/console-next/studio" in script


# ── Hook + components: shape contracts ───────────────────────────────────


def test_use_kpis_hook_matches_backend_shape():
    """The dashboard hook's KpiPayload interface must mirror the
    payload returned by console/app/routers/dashboard.py::dashboard_kpis.
    Drift here = silent UI breakage."""
    src = _read(SRC / "lib/hooks/useKpis.ts")
    for key in ("cartridges", "extractions", "data_freshness",
                "users", "copilot", "audit"):
        assert key in src, f"useKpis KpiPayload missing key {key!r}"
    # Polling matches the brief's 30 s cadence.
    assert "refetchInterval" in src
    assert "30_000" in src or "30000" in src


def test_dashboard_page_renders_kpi_grid():
    src = _read(SRC / "app/(shell)/dashboard/page.tsx")
    assert "useKpis" in src
    # Loading + error states present.
    assert "isLoading" in src
    assert "isError" in src or "isError" in src
    # Freshness table consumed.
    assert "FreshnessTable" in src


def test_login_page_avoids_next_navigation_bundle():
    """Login is public and carries a strict first-load JS budget.
    It must parse ?next= without next/navigation so it does not pull
    route-shell chunks into the gateway page."""
    src = _read(SRC / "app/login/page.tsx")
    assert "next/navigation" not in src
    assert "useSearchParams" not in src
    assert "new URLSearchParams" in src
    assert "startsWith(\"//\")" in src


# ── Compose tests this branch doesn't break existing parity ──────────────


def test_existing_aws_compose_consistency_still_passes():
    """The AWS compose no longer ships a console_next service."""
    src = _read(REPO / "infra/terraform/deploy/docker-compose.aws.yml")
    assert "console_next:" not in src
    assert "ghcr.io/${GHCR_OWNER:-emmanuelnavaromero02-commits}/console-next" not in src
    assert "BACKEND_INTERNAL_URL" not in src
    assert "API_INTERNAL_URL" not in src
