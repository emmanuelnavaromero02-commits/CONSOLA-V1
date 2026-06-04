from __future__ import annotations

from pathlib import Path


REPO = Path(__file__).resolve().parents[1]
PLAN = REPO / "docs/observability/opentelemetry-plan.md"


def test_opentelemetry_plan_maps_services_propagation_and_acceptance():
    text = PLAN.read_text(encoding="utf-8")

    assert "Status: planned implementation" in text
    for service in ("Console", "MCP Infra", "Cartridge", "Refinement", "Vault"):
        assert service in text
    for required in (
        "traceparent",
        "x-request-id",
        "OTEL_ENABLED",
        "OTEL_EXPORTER_OTLP_ENDPOINT",
        "trace_id",
        "span_id",
        "No secret-shaped values",
    ):
        assert required in text
    for span in (
        "console.mcp.invoke",
        "mcp.tool.invoke",
        "mcp.cartridge.call",
        "console.refinement.query",
        "refinement.sql.guard",
        "cartridge.extract",
    ):
        assert span in text
    assert "curl -H 'traceparent:" in text
    assert "Blockers Before DONE" in text
