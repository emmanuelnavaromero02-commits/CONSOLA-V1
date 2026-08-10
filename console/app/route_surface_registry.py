from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

RouteSurface = Literal["frontend", "admin_only", "internal", "legacy", "deprecated"]


@dataclass(frozen=True)
class RouteSurfaceRule:
    prefix: str
    surface: RouteSurface
    note: str


ROUTE_SURFACE_REGISTRY: tuple[RouteSurfaceRule, ...] = (
    RouteSurfaceRule(
        "/api/control-room/internal", "internal", "server-side Control Room reader"
    ),
    RouteSurfaceRule("/monitoring/mcp", "internal", "MCP monitoring transport"),
    RouteSurfaceRule("/studio_ops/mcp", "internal", "Studio MCP transport"),
    RouteSurfaceRule("/internal", "internal", "internal service-to-service API"),
    RouteSurfaceRule("/api/v1", "legacy", "legacy compatibility API"),
    RouteSurfaceRule("/monitoring/tools", "legacy", "legacy tool discovery"),
    RouteSurfaceRule("/monitoring/invoke", "legacy", "legacy tool invocation"),
    RouteSurfaceRule("/api/intelligence", "frontend", "Operational Intelligence API"),
    RouteSurfaceRule("/api/actions", "frontend", "Supervised Actions API"),
    RouteSurfaceRule("/api/copilot/context", "frontend", "Copilot live context API"),
    RouteSurfaceRule(
        "/api/copilot/recommendations", "frontend", "Copilot recommendation API"
    ),
    RouteSurfaceRule("/api/copilot", "frontend", "Copilot API"),
    RouteSurfaceRule(
        "/api/control-room/decision-intelligence",
        "legacy",
        "Control Room legacy intelligence facade",
    ),
    RouteSurfaceRule("/api/control-room", "frontend", "Control Room API"),
    RouteSurfaceRule("/api/admin", "admin_only", "administration API"),
    RouteSurfaceRule("/api/settings", "admin_only", "settings API"),
    RouteSurfaceRule("/api/operations", "admin_only", "operations API"),
    RouteSurfaceRule("/api/mcp", "admin_only", "MCP administration API"),
    RouteSurfaceRule("/api/vault", "admin_only", "Vault administration API"),
    RouteSurfaceRule("/api/security", "admin_only", "security API"),
    RouteSurfaceRule("/security", "admin_only", "security UI/API"),
    RouteSurfaceRule("/operations", "admin_only", "operations UI"),
    RouteSurfaceRule("/settings", "admin_only", "settings UI"),
    RouteSurfaceRule("/iam", "admin_only", "identity administration UI"),
    RouteSurfaceRule("/api/agents", "frontend", "agents API"),
    RouteSurfaceRule("/api/agent-runs", "frontend", "agent runs API"),
    RouteSurfaceRule("/api/apps", "frontend", "analytic apps API"),
    RouteSurfaceRule("/api/auth", "frontend", "auth API"),
    RouteSurfaceRule("/api/bronze", "frontend", "bronze query API"),
    RouteSurfaceRule("/api/cartridges", "frontend", "cartridge API"),
    RouteSurfaceRule("/api/catalog", "frontend", "catalog API"),
    RouteSurfaceRule("/api/config", "frontend", "console config API"),
    RouteSurfaceRule("/api/customer", "frontend", "customer marketplace API"),
    RouteSurfaceRule("/api/dag_templates", "frontend", "DAG templates API"),
    RouteSurfaceRule("/api/dags", "frontend", "DAG API"),
    RouteSurfaceRule("/api/dashboard", "frontend", "dashboard API"),
    RouteSurfaceRule("/api/data", "frontend", "data API"),
    RouteSurfaceRule("/api/datasets", "frontend", "datasets API"),
    RouteSurfaceRule("/api/decisions", "frontend", "decisions API"),
    RouteSurfaceRule("/api/explorer", "frontend", "explorer API"),
    RouteSurfaceRule("/api/freshness", "frontend", "freshness API"),
    RouteSurfaceRule("/api/jobs", "frontend", "jobs API"),
    RouteSurfaceRule("/api/lineage", "frontend", "lineage API"),
    RouteSurfaceRule("/api/marketplace", "frontend", "marketplace API"),
    RouteSurfaceRule("/api/me", "frontend", "current user API"),
    RouteSurfaceRule("/api/metrics", "frontend", "metrics API"),
    RouteSurfaceRule("/api/pipeline", "frontend", "pipeline API"),
    RouteSurfaceRule("/api/pipeline_runs", "frontend", "pipeline run API"),
    RouteSurfaceRule("/api/rag", "frontend", "RAG API"),
    RouteSurfaceRule("/api/schema", "frontend", "schema API"),
    RouteSurfaceRule("/api/semantic", "frontend", "semantic API"),
    RouteSurfaceRule("/api/sources", "frontend", "sources API"),
    RouteSurfaceRule("/api/studio", "frontend", "Studio API"),
    RouteSurfaceRule("/api/system", "frontend", "system API"),
    RouteSurfaceRule("/api/tools", "frontend", "tool manifest API"),
    RouteSurfaceRule("/api/users", "admin_only", "user administration API"),
    RouteSurfaceRule("/activate", "frontend", "activation UI"),
    RouteSurfaceRule("/admin", "admin_only", "legacy administration UI"),
    RouteSurfaceRule("/agents", "frontend", "agents UI"),
    RouteSurfaceRule("/analytics", "frontend", "analytics UI"),
    RouteSurfaceRule("/apps", "frontend", "analytic app UI"),
    RouteSurfaceRule("/apps-gallery", "frontend", "analytic app gallery"),
    RouteSurfaceRule("/assistant", "legacy", "legacy assistant UI"),
    RouteSurfaceRule("/auth", "frontend", "auth UI"),
    RouteSurfaceRule("/cartridges", "frontend", "cartridge UI"),
    RouteSurfaceRule("/control-room", "frontend", "Control Room UI"),
    RouteSurfaceRule("/copilot", "frontend", "Copilot UI"),
    RouteSurfaceRule("/customer", "frontend", "customer UI"),
    RouteSurfaceRule("/dashboard", "frontend", "dashboard UI"),
    RouteSurfaceRule("/data", "frontend", "data UI"),
    RouteSurfaceRule("/datasets", "frontend", "dataset UI"),
    RouteSurfaceRule("/decisions", "frontend", "decisions UI"),
    RouteSurfaceRule("/docs", "admin_only", "API documentation"),
    RouteSurfaceRule("/explorer", "frontend", "explorer UI"),
    RouteSurfaceRule("/forgot-password", "frontend", "password reset UI"),
    RouteSurfaceRule("/healthz", "internal", "health probe"),
    RouteSurfaceRule("/jobs", "frontend", "jobs UI"),
    RouteSurfaceRule("/linaje", "frontend", "lineage UI"),
    RouteSurfaceRule("/lineage", "frontend", "lineage UI"),
    RouteSurfaceRule("/login", "frontend", "login UI"),
    RouteSurfaceRule("/marketplace", "frontend", "marketplace UI"),
    RouteSurfaceRule("/me", "frontend", "current user UI"),
    RouteSurfaceRule("/mis-accesos", "frontend", "access UI"),
    RouteSurfaceRule("/monitor", "frontend", "monitor UI"),
    RouteSurfaceRule("/my-access", "frontend", "access UI"),
    RouteSurfaceRule("/openapi.json", "admin_only", "OpenAPI schema"),
    RouteSurfaceRule(
        "/operational-intelligence", "frontend", "operational intelligence UI"
    ),
    RouteSurfaceRule("/rag", "frontend", "RAG UI"),
    RouteSurfaceRule("/readyz", "internal", "readiness probe"),
    RouteSurfaceRule("/redoc", "admin_only", "API documentation"),
    RouteSurfaceRule("/reset-password", "frontend", "password reset UI"),
    RouteSurfaceRule("/static", "frontend", "static assets"),
    RouteSurfaceRule("/studio", "frontend", "Studio UI"),
    RouteSurfaceRule("/supervised-actions", "frontend", "supervised actions UI"),
    RouteSurfaceRule("/tokens", "frontend", "tokens UI"),
    RouteSurfaceRule("/viewer", "frontend", "technical catalog viewer UI"),
    RouteSurfaceRule("/vpn-config", "frontend", "VPN configuration download"),
    RouteSurfaceRule("/workspace", "frontend", "workspace UI"),
    RouteSurfaceRule("/", "frontend", "root UI"),
)


def _matches_route(rule: RouteSurfaceRule, path: str) -> bool:
    if rule.prefix == "/":
        return path == "/"
    prefix = rule.prefix.rstrip("/")
    return path == prefix or path.startswith(f"{prefix}/")


def classify_route_surface(path: str) -> RouteSurface | None:
    normalized = (path or "").strip() or "/"
    for rule in sorted(
        ROUTE_SURFACE_REGISTRY, key=lambda item: len(item.prefix), reverse=True
    ):
        if _matches_route(rule, normalized):
            return rule.surface
    return None


def route_surface_rule(path: str) -> RouteSurfaceRule | None:
    normalized = (path or "").strip() or "/"
    for rule in sorted(
        ROUTE_SURFACE_REGISTRY, key=lambda item: len(item.prefix), reverse=True
    ):
        if _matches_route(rule, normalized):
            return rule
    return None
