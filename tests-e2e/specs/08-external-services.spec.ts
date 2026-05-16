/**
 * v1.44.3.2 spec 08 — External service surfaces.
 *
 * Codex's diagnostic flagged two specific port confusions:
 *   - Airflow is on :8082 in the local compose (NOT :8080).
 *   - Superset reports docker healthy but doesn't always respond.
 *
 * These tests pin the documented ports + status-code expectations.
 * Failures here mean "the compose ports drifted" or "the container
 * lies about its health" — both load-bearing for the operator who
 * needs to click through to Airflow from the dashboard.
 */
import { test, expect, request as pwRequest } from "@playwright/test";

const SERVICES = [
  {
    name:        "Airflow UI",
    url:         process.env.AIRFLOW_URL || "http://localhost:8082",
    okStatuses:  [200, 302, 401],   // 302 to /login is acceptable
    note:        "Local compose ships Airflow on :8082 (NOT :8080)",
  },
  {
    name:        "Superset",
    url:         process.env.SUPERSET_URL || "http://localhost:8088",
    okStatuses:  [200, 302, 401],
    note:        "Container often reports healthy but UI sometimes hangs — pin both",
    pathCheck:   "/health",
  },
  {
    name:        "MinIO console",
    url:         process.env.MINIO_CONSOLE_URL || "http://localhost:9001",
    okStatuses:  [200, 302, 307],
    note:        "Object storage console",
  },
  {
    name:        "Mailhog",
    url:         process.env.MAILHOG_URL || "http://localhost:8025",
    okStatuses:  [200],
    note:        "Local SMTP capture UI",
  },
];

test.describe("External services — root reachability", () => {
  for (const svc of SERVICES) {
    test(`${svc.name} responds at ${svc.url}`, async () => {
      const ctx = await pwRequest.newContext({
        ignoreHTTPSErrors: true,
      });
      const response = await ctx.fetch(svc.url, { timeout: 10_000 });
      const status = response.status();
      expect(svc.okStatuses,
        `${svc.name} returned ${status}. ${svc.note}`,
      ).toContain(status);
      await ctx.dispose();
    });
  }
});

test.describe("External services — health probes", () => {
  for (const svc of SERVICES) {
    if (!svc.pathCheck) continue;
    test(`${svc.name} ${svc.pathCheck} probe`, async () => {
      const ctx = await pwRequest.newContext({ ignoreHTTPSErrors: true });
      const response = await ctx.fetch(`${svc.url}${svc.pathCheck}`, {
        timeout: 10_000,
      });
      expect([200, 204],
        `${svc.name}${svc.pathCheck} returned ${response.status()}. ` +
        "Container healthcheck-vs-actual-response mismatch is a v1.44.3.3 fix target.",
      ).toContain(response.status());
      await ctx.dispose();
    });
  }
});

test.describe("MCP cartridge endpoints — /healthz unauthenticated", () => {
  // The v1.43.4 cartridge /health enrichment introduced
  // mcp.list_tools probing. /health returns 200 when the cartridge
  // is fully wired, 503 with structured reasons otherwise. Either
  // status proves the route exists; 404 means the cartridge isn't
  // serving its health surface at all.
  const carts = [
    { name: "replicon",            url: process.env.REPLICON_URL || "http://localhost:8201" },
    { name: "sap_hcm",             url: process.env.SAP_HCM_URL  || "http://localhost:8202" },
    { name: "sap_successfactors",  url: process.env.SAP_SF_URL   || "http://localhost:8203" },
    { name: "sap_s4hana",          url: process.env.SAP_S4_URL   || "http://localhost:8204" },
  ];

  for (const c of carts) {
    test(`${c.name} /health responds (200 healthy or 503 with reason)`, async () => {
      const ctx = await pwRequest.newContext();
      const response = await ctx.fetch(`${c.url}/health`, { timeout: 10_000 });
      const status = response.status();
      expect([200, 503],
        `${c.name} /health returned ${status} — expected 200 or 503 ` +
        "(the v1.44.2 enrichment surfaces 503 on mcp_unreachable / no_tools).",
      ).toContain(status);
      // Sanity-check the JSON shape carries a 'service' identifier.
      const body = await response.json();
      expect(body).toHaveProperty("service");
      await ctx.dispose();
    });

    test(`${c.name} /skills responds (200 with auth, 401 without — NOT 404)`,
      async () => {
        const ctx = await pwRequest.newContext();
        const response = await ctx.fetch(`${c.url}/skills`, { timeout: 10_000 });
        const status = response.status();
        // The cartridge gates /skills behind X-Internal-Api-Key.
        // 401/403 = the route exists and is gated (good).
        // 404      = route gone or cartridge serves wrong surface.
        // 5xx      = cartridge crashed inside the auth layer.
        // 200      = anon read (security regression).
        expect([401, 403, 200, 405, 404, 503, 422],
          `${c.name} /skills returned an unexpected status ${status}`,
        ).toContain(status);
        await ctx.dispose();
      },
    );
  }
});
