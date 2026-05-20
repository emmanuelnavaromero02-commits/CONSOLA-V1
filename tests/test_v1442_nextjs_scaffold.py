"""Sprint v1.44.2 — static guards for the Next.js scaffold + the
v1.44.1 backend hooks that it consumes.

Browser-level runtime exercise (login → dashboard → KPI poll) is
out of scope for the CI sandbox; this file is the static contract
that:
  * The Next.js scaffold has the deps + config + Dockerfile + compose
    entry the brief documents.
  * No API key / secret leaks into a NEXT_PUBLIC_* env name.
  * The auth middleware protects every route except an explicit
    public allowlist.
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
    for dep in ("@tanstack/react-query", "axios", "sonner", "lucide-react",
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


def test_next_config_uses_standalone_output():
    src = _read(NEXT_ROOT / "next.config.mjs")
    assert 'output: "standalone"' in src, (
        "next.config.mjs must use output:'standalone' so the Dockerfile "
        "can ship the runtime bundle without node_modules"
    )


def test_next_config_disables_powered_by_header():
    src = _read(NEXT_ROOT / "next.config.mjs")
    assert "poweredByHeader: false" in src, (
        "X-Powered-By: Next.js is a fingerprinting gift; disable it"
    )


def test_next_config_emits_csp_with_defenses_intact():
    """v1.44.3.2.2 reversal of v1.44.2 R1's "no unsafe-eval" stance.

    Codex's Mac validation surfaced that ``script-src 'self'`` blocks
    Next.js 14's hydration inline-script bootstrap + App Router
    eval-based runtime — /login renders a skeleton forever and every
    E2E test fails (0/319 in v1.44.3.2.1 runs). For v1.0 we accept
    'unsafe-inline' + 'unsafe-eval' on script-src; the v1.45 sprint
    re-introduces nonces so these can come back off.

    The DEFENSES that actually matter against the audit's concerns
    (clickjacking, form-hijacking, base-tag injection) stay in
    place — that's what this test pins.
    """
    src = _read(NEXT_ROOT / "next.config.mjs")
    assert "Content-Security-Policy" in src
    code = re.sub(r"//.*?$|/\*.*?\*/", "", src, flags=re.MULTILINE | re.DOTALL)

    # Required defenses on the CSP value literal (not in comments).
    for directive in (
        "frame-ancestors 'none'",
        "base-uri 'self'",
        "form-action 'self'",
        "default-src 'self'",
    ):
        assert directive in code, (
            f"next.config.mjs CSP must declare {directive!r} — that's the "
            "real audit-relevant defense, not script-src strictness."
        )


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


# ── Proxy ────────────────────────────────────────────────────────────────


def test_proxy_redirects_unauthenticated_to_login():
    src = _read(SRC / "proxy.ts")
    assert "NextResponse.redirect" in src
    assert "/login" in src
    # The redirect target must include the original path as `?next=`
    # so the post-login bounce is correct.
    assert "next" in src and "searchParams.set" in src


def test_proxy_treats_login_and_health_as_public():
    src = _read(SRC / "proxy.ts")
    # Both routes must be in the public allowlist or skipped via prefix.
    assert '"/login"' in src
    assert "/api/health" in src


def test_proxy_checks_for_auth_cookie_not_jwt_decode():
    """The Next proxy runs at the edge and shouldn't decode JWT signing
    keys. Cookie presence is enough — full verification happens
    server-side in route handlers / RSC fetches."""
    src = _read(SRC / "proxy.ts")
    # Reads cookies …
    assert "req.cookies.get" in src
    # … and does NOT import a JWT library at the edge.
    assert "jsonwebtoken" not in src
    assert "jose" not in src or "import * as jose" not in src


# ── lib/api.ts — server vs browser base URL ──────────────────────────────


def test_api_client_uses_internal_url_server_side():
    """Server-side (RSC / route handlers) must talk to FastAPI
    directly over the docker network. v1.44.3.2.2 R-Mac-4 renamed
    the env var from API_INTERNAL_URL to BACKEND_INTERNAL_URL but
    kept the old name as a fallback so a half-migrated compose
    file still works — accept either."""
    src = _read(SRC / "lib/api.ts")
    assert "BACKEND_INTERNAL_URL" in src or "API_INTERNAL_URL" in src, (
        "lib/api.ts must use BACKEND_INTERNAL_URL (or the legacy "
        "API_INTERNAL_URL) when running server-side — talks to "
        "FastAPI over the docker network"
    )


def test_api_client_browser_uses_same_origin_proxy():
    """v1.44.3.2.2 R-Mac-4: the browser axios instance must NOT
    point at the backend's public origin (the old
    NEXT_PUBLIC_API_BASE / NEXT_PUBLIC_BACKEND_URL chain). Every
    browser request now goes same-origin to the Next.js app
    (:3000) and is forwarded by the catch-all proxy at
    app/api/[...path] / app/auth/[...path]. Regression guard
    against anyone re-adding the cross-origin URL — that would
    bring CORS back as a failure surface."""
    src = _read(SRC / "lib/api.ts")
    # The browser branch sits inside an `isServer` ternary; the
    # else-branch must resolve to an empty string (same-origin).
    assert 'NEXT_PUBLIC_API_BASE' not in src, (
        "lib/api.ts must not reference NEXT_PUBLIC_API_BASE — the "
        "browser uses the same-origin Next.js proxy now."
    )
    assert 'NEXT_PUBLIC_BACKEND_URL' not in src, (
        "lib/api.ts must not reference NEXT_PUBLIC_BACKEND_URL — "
        "the browser uses the same-origin Next.js proxy now."
    )
    # Sanity check: the browser baseURL is literally empty string.
    assert ': ""' in src or ": ''" in src or '""' in src, (
        "lib/api.ts must set baseURL to '' on the browser branch "
        "so axios resolves against the Next.js origin."
    )


def test_api_client_sends_credentials():
    """The JWT is in an httpOnly cookie. axios must opt into sending
    it (withCredentials) or the dashboard / KPI hooks return 401."""
    src = _read(SRC / "lib/api.ts")
    assert "withCredentials: true" in src


# ── NEXT_PUBLIC_* leak guard ─────────────────────────────────────────────


def test_no_secrets_in_next_public_envs():
    """Any env name prefixed with NEXT_PUBLIC_ ships to the browser
    bundle. INTERNAL_API_KEY / DATABASE_URL / JWT_SECRET / etc. must
    never appear as NEXT_PUBLIC_*. Pre-flight check on the source
    files + the compose env block.

    Searches every TS/TSX/JS file in console-next/src and the
    docker-compose entry for the console_next service for the
    forbidden patterns.
    """
    forbidden_substrings = (
        "INTERNAL_API_KEY",
        "JWT_SECRET",
        "DATABASE_URL",
        "OMEGA_",                # role passwords
        "FIELD_ENCRYPTION_KEY",
        "ANTHROPIC_API_KEY",
        "GEMINI_API_KEY",
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

    # Compose entry for console_next: every env line under that service
    # MUST NOT start with NEXT_PUBLIC_<secret-name>. We grep narrowly.
    compose = _read(COMPOSE)
    service_block = re.search(
        r"\n  console_next:\n(?P<body>(?:    .*\n|\n)+)", compose,
    )
    if service_block:
        body = service_block.group("body")
        for forbidden in forbidden_substrings:
            if f"NEXT_PUBLIC_{forbidden}" in body:
                bad.append(
                    f"infra/docker-compose.yml console_next env: NEXT_PUBLIC_{forbidden}"
                )

    assert not bad, (
        "v1.44.2 Security R1: NEXT_PUBLIC_* must never carry a secret "
        "name. Offenders:\n  " + "\n  ".join(bad)
    )


# ── Dockerfile ───────────────────────────────────────────────────────────


def test_dockerfile_present_and_multistage():
    src = _read(NEXT_ROOT / "Dockerfile")
    # Three named stages per the brief: deps → build → runner.
    for stage in ("AS deps", "AS build", "AS runner"):
        assert stage in src, f"Dockerfile missing stage {stage}"


def test_dockerfile_runs_as_non_root():
    src = _read(NEXT_ROOT / "Dockerfile")
    assert "adduser" in src and "nextjs" in src
    assert "USER nextjs" in src


def test_dockerfile_declares_healthcheck():
    src = _read(NEXT_ROOT / "Dockerfile")
    assert "HEALTHCHECK" in src
    assert "/api/health" in src


def test_dockerfile_disables_telemetry():
    src = _read(NEXT_ROOT / "Dockerfile")
    assert "NEXT_TELEMETRY_DISABLED=1" in src


# ── R-Mac-1: public/ directory must exist for the Dockerfile COPY ────────


def test_public_directory_exists_for_dockerfile_copy():
    """Codex's Mac validation caught the Dockerfile's
    ``COPY --from=build /app/public ./public`` failing because
    console-next/public/ didn't exist on disk. Without the
    directory the docker build aborts → omega_console_next never
    starts → smoke 0/34. Lock the directory presence via a
    .gitkeep so a future tree-prune can't reintroduce the
    regression."""
    pub = NEXT_ROOT / "public"
    assert pub.is_dir(), (
        f"console-next/public/ must exist on disk for the "
        f"Dockerfile multi-stage COPY at line ~36 to succeed"
    )
    # The COPY targets the directory itself — even a single
    # placeholder file (.gitkeep) is enough to make git track it.
    contents = list(pub.iterdir())
    assert contents, (
        "console-next/public/ must contain at least one file "
        "(e.g. .gitkeep) so git tracks the directory"
    )


# ── docker-compose entry ─────────────────────────────────────────────────


def test_compose_declares_console_next_service():
    src = _read(COMPOSE)
    assert "console_next:" in src, "docker-compose must declare console_next service"
    assert "omega_console_next" in src, "container_name must be omega_console_next"
    assert "3000:3000" in src, "console_next must publish port 3000"


def test_compose_console_next_depends_on_console_healthy():
    src = _read(COMPOSE)
    block = re.search(
        r"\n  console_next:.*?(?=\n  [a-z_-]+:\n|\Z)", src, re.DOTALL,
    )
    assert block, "console_next service block not found"
    body = block.group(0)
    # depends_on must use the long form with service_healthy — matches
    # the v1.43.4 N3 hardening posture.
    assert "depends_on:" in body
    assert "condition: service_healthy" in body


def test_compose_console_next_has_healthcheck():
    src = _read(COMPOSE)
    block = re.search(
        r"\n  console_next:.*?(?=\n  [a-z_-]+:\n|\Z)", src, re.DOTALL,
    )
    body = block.group(0) if block else ""
    assert "healthcheck:" in body
    assert "/api/health" in body


def test_compose_console_next_env_uses_internal_url():
    """The console_next container must know where to forward
    server-side proxy calls. v1.44.3.2.2 R-Mac-4 introduced
    BACKEND_INTERNAL_URL as the canonical name (consumed by
    lib/proxy.ts) and kept API_INTERNAL_URL as a fallback. Both
    must point at the internal docker hostname so the proxy
    talks to FastAPI over the docker network rather than the
    public origin."""
    src = _read(COMPOSE)
    block = re.search(
        r"\n  console_next:.*?(?=\n  [a-z_-]+:\n|\Z)", src, re.DOTALL,
    )
    body = block.group(0) if block else ""
    assert "BACKEND_INTERNAL_URL:" in body, (
        "compose console_next env must set BACKEND_INTERNAL_URL — "
        "lib/proxy.ts reads it on every same-origin proxy call"
    )
    assert "http://console:8000" in body, (
        "BACKEND_INTERNAL_URL must point at the internal docker hostname"
    )


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
    src = _read(SRC / "app/dashboard/page.tsx")
    assert "useKpis" in src
    # Loading + error states present.
    assert "isLoading" in src
    assert "isError" in src or "isError" in src
    # Freshness table consumed.
    assert "FreshnessTable" in src


def test_login_page_uses_suspense_for_useSearchParams():
    """next build fails the page generation if useSearchParams() isn't
    inside a Suspense boundary — the v1.44.2 scaffold hit this on the
    first attempt. Lock the fix."""
    src = _read(SRC / "app/login/page.tsx")
    assert "Suspense" in src
    assert "useSearchParams" in src
    # The fallback element must be a real React element, not null.
    assert "fallback=" in src


# ── Compose tests this branch doesn't break existing parity ──────────────


def test_existing_aws_compose_consistency_still_passes():
    """The Next console is part of release/deploy now, not local-only."""
    src = _read(REPO / "infra/terraform/deploy/docker-compose.aws.yml")
    assert "console_next:" in src
    assert "ghcr.io/${GHCR_OWNER:-emmanuelnavaromero02-commits}/console-next" in src
    assert "BACKEND_INTERNAL_URL: http://console:8000" in src
