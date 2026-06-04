# OpenTelemetry Plan

Status: planned implementation for v1.0 public readiness.

CONSOLA-BETA already propagates `x-request-id` through Console, MCP Infra,
Vault, Refinement, Workspace, and cartridges. That correlation remains the
fallback. The OpenTelemetry rollout adds W3C `traceparent` propagation and
exportable spans without changing existing request-id logs.

## Goals

- Correlate one user action across Console -> MCP Infra -> Cartridge ->
  Refinement/Vault.
- Preserve `x-request-id` in every JSON log and add `trace_id`/`span_id`
  once instrumentation is active.
- Export traces to an OTLP collector in staging and production.
- Keep tracing disabled by default in local dev unless
  `OTEL_ENABLED=true`.
- Never record request bodies, secrets, tokens, Vault values, or cartridge
  credentials as span attributes.

## Environment

| Variable | Purpose | Default |
|---|---|---|
| `OTEL_ENABLED` | Enables instrumentation and propagation | `false` |
| `OTEL_SERVICE_NAME` | Service name override | service-specific |
| `OTEL_EXPORTER_OTLP_ENDPOINT` | OTLP collector URL | unset |
| `OTEL_TRACES_SAMPLER` | Sampler name | `parentbased_traceidratio` |
| `OTEL_TRACES_SAMPLER_ARG` | Sample ratio | `0.10` |

## Propagation Contract

- Incoming `traceparent` is accepted if syntactically valid.
- If `traceparent` is missing, the service starts a new trace and still
  preserves `x-request-id`.
- Outbound HTTP calls include both `traceparent` and `x-request-id`.
- Existing middleware in `app/middleware/request_id.py` remains in place.
- Logs include `request_id` always; `trace_id` and `span_id` are added when
  a current span exists.

## Instrumentation Points

| Hop | Span name | Required attributes |
|---|---|---|
| Console HTTP request | `console.http.request` | route, method, status_code |
| Console -> MCP Infra | `console.mcp.invoke` | tool_name, approval_required |
| MCP Infra tool execution | `mcp.tool.invoke` | tool_name, mutating, allowed |
| MCP Infra -> Cartridge | `mcp.cartridge.call` | cartridge_id, endpoint |
| MCP Infra -> Vault | `mcp.vault.call` | operation, scope_type |
| Console -> Refinement | `console.refinement.query` | dataset, read_only |
| Refinement SQL guard | `refinement.sql.guard` | parse_ok, rls_injected |
| Cartridge extraction | `cartridge.extract` | cartridge_id, entity, result |

Attributes must be low-cardinality and redacted. Tenant/workspace IDs may be
hashed before export if the deployment treats them as sensitive metadata.

## Implementation Steps

1. Add shared helper modules per service namespace:
   `app/observability.py` with `setup_tracing(service_name)` and
   `inject_trace_headers(headers)`.
2. Wire `setup_tracing()` during app startup after logging setup in Console,
   MCP Infra, Vault, Refinement, Workspace, and cartridges.
3. Add ASGI instrumentation for FastAPI when `OTEL_ENABLED=true`.
4. Wrap outbound `httpx` calls with trace propagation helpers.
5. Add logger enrichment for `trace_id` and `span_id` without removing
   `request_id`.
6. Add Docker Compose optional `otel-collector` profile for local/staging.
7. Gate CI with unit tests for propagation and redaction.

## Acceptance

With `OTEL_ENABLED=true` and an OTLP collector running:

```bash
curl -H 'traceparent: 00-11111111111111111111111111111111-2222222222222222-01' \
  http://localhost:8000/readyz
docker compose logs console mcp-infra refinement vault \
  | grep '11111111111111111111111111111111'
```

Expected result:

- The same trace id appears in Console and every downstream service touched by
  the request.
- `x-request-id` still appears in logs and responses.
- No secret-shaped values appear in span attributes or logs.

## Blockers Before DONE

- Choose the production collector target and retention policy.
- Decide whether tenant/workspace IDs are exported raw or hashed.
- Run the acceptance command against staging with real Console -> MCP ->
  Cartridge -> Refinement/Vault traffic.
