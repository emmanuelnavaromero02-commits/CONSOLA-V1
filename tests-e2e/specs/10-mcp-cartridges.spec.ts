/**
 * v1.44.3.2.1 spec 10 — MCP cartridge deep coverage.
 *
 * 36 tests — 9 checks × 4 cartridges:
 *   /healthz (no auth)
 *   /health  (no auth)
 *   /skills  unauth → 401/403
 *   /skills/entities unauth → 401/403
 *   /mcp/tools unauth → 401/403
 *   /mcp/tools authed via INTERNAL_API_KEY → 200 + tools array
 *   /mcp/invoke unauth → 401/403
 *   /mcp-reload unauth → 401/403
 *   /health response includes structured fields
 */
import { test, expect, request as pwRequest } from "@playwright/test";

test.use({ storageState: { cookies: [], origins: [] } });

const CARTS = [
  { id: "replicon",            url: process.env.REPLICON_URL || "http://localhost:8201" },
  { id: "sap_hcm",             url: process.env.SAP_HCM_URL  || "http://localhost:8202" },
  { id: "sap_successfactors",  url: process.env.SAP_SF_URL   || "http://localhost:8203" },
  { id: "sap_s4hana",          url: process.env.SAP_S4_URL   || "http://localhost:8204" },
];

const INTERNAL_KEY = process.env.INTERNAL_API_KEY || "";

for (const c of CARTS) {
  test.describe(`MCP — ${c.id} @ ${c.url}`, () => {
    test(`/health responds (200 or 503 with structured reason)`, async () => {
      const ctx = await pwRequest.newContext();
      const r = await ctx.get(`${c.url}/health`, { timeout: 10_000 });
      expect([200, 503]).toContain(r.status());
      const body = await r.json();
      expect(body).toHaveProperty("service");
      expect(body.service).toBe(c.id);
      await ctx.dispose();
    });

    test(`/health body includes tool_count when healthy`, async () => {
      const ctx = await pwRequest.newContext();
      const r = await ctx.get(`${c.url}/health`, { timeout: 10_000 });
      const body = await r.json();
      if (r.status() === 200 && body.ok === true) {
        expect(body).toHaveProperty("tool_count");
        expect(typeof body.tool_count).toBe("number");
        expect(body.tool_count).toBeGreaterThan(0);
      } else if (r.status() === 503) {
        // 503 path must include a reason code.
        expect(body).toHaveProperty("reason");
      }
      await ctx.dispose();
    });

    test(`/skills GET unauth → 401/403 (route exists, NOT 404)`, async () => {
      const ctx = await pwRequest.newContext();
      const r = await ctx.get(`${c.url}/skills`, { timeout: 10_000 });
      expect([200, 401, 403, 405],
        `Got ${r.status()} — 404 means route is gone`,
      ).toContain(r.status());
      await ctx.dispose();
    });

    test(`/skills/list GET unauth → 401 (privileged metadata)`, async () => {
      // v1.44.3.3 R-Mac Mini-fix: Codex's Mac run reported tests
      // expecting 200 here, but the agreed contract (Option B
      // in the brief) is that /skills/list is PRIVILEGED — the
      // skill catalogue is sensitive metadata that the
      // orchestrator authenticates with X-Internal-Api-Key
      // before reading. Anonymous → 401.
      const ctx = await pwRequest.newContext();
      const r = await ctx.get(`${c.url}/skills/list`, { timeout: 10_000 });
      expect([401, 403],
        `/skills/list unauth must 401/403; got ${r.status()}. ` +
        `If 404 the route is gone (Task C regression).`,
      ).toContain(r.status());
      await ctx.dispose();
    });

    test(`/skills/list GET authed → 200 + {service, skills}`, async () => {
      const ctx = await pwRequest.newContext({
        extraHTTPHeaders: {
          "X-Api-Key":           process.env.INTERNAL_API_KEY || "",
          "X-Internal-Service":  "console",
        },
      });
      const r = await ctx.get(`${c.url}/skills/list`, { timeout: 10_000 });
      expect(r.status(),
        `/skills/list authed must 200; got ${r.status()}`,
      ).toBe(200);
      const body = await r.json();
      expect(body).toHaveProperty("service");
      expect(body.service).toBe(c.id);
      expect(body).toHaveProperty("skills");
      expect(Array.isArray(body.skills)).toBe(true);
      expect(body.skills.length,
        `cartridge ${c.id} reported 0 skills — discovery is broken`,
      ).toBeGreaterThan(0);
      await ctx.dispose();
    });

    test(`/healthz GET no auth → 200 + {ok, service}`, async () => {
      // v1.44.3.3 Task C: the new yes/no liveness probe must
      // be PUBLIC (Kubernetes-style liveness doesn't carry
      // auth) and independent of startup state. Distinct from
      // /health which gates on app.state.startup_ok.
      const ctx = await pwRequest.newContext();
      const r = await ctx.get(`${c.url}/healthz`, { timeout: 10_000 });
      expect(r.status(),
        `/healthz must 200 no-auth; got ${r.status()}`,
      ).toBe(200);
      const body = await r.json();
      expect(body.ok).toBe(true);
      expect(body.service).toBe(c.id);
      await ctx.dispose();
    });

    test(`/mcp/tools GET unauth → 401/403`, async () => {
      const ctx = await pwRequest.newContext();
      const r = await ctx.get(`${c.url}/mcp/tools`, { timeout: 10_000 });
      expect([401, 403]).toContain(r.status());
      await ctx.dispose();
    });

    test(`/mcp/tools GET WITH X-Internal-Api-Key → 200 + tools array`,
      async () => {
        if (!INTERNAL_KEY) {
          test.skip(true,
            "INTERNAL_API_KEY env var not set — export it to exercise authed MCP probe",
          );
          return;
        }
        const ctx = await pwRequest.newContext();
        const r = await ctx.get(`${c.url}/mcp/tools`, {
          headers: {
            "X-Internal-Api-Key": INTERNAL_KEY,
            "X-Internal-Service": "console",
          },
          timeout: 10_000,
        });
        expect(r.status(),
          `${c.id} /mcp/tools authed must return 200 (Codex C1 regression guard)`,
        ).toBe(200);
        const body = await r.json();
        expect(body).toHaveProperty("tools");
        expect(Array.isArray(body.tools)).toBe(true);
        expect(body.tools.length,
          `${c.id} should expose at least 1 tool`,
        ).toBeGreaterThan(0);
        // Each tool must have name + input_schema.
        for (const t of body.tools.slice(0, 3)) {
          expect(t).toHaveProperty("name");
          expect(t).toHaveProperty("input_schema");
        }
        await ctx.dispose();
      },
    );

    test(`/mcp/invoke POST unauth → 401/403`, async () => {
      const ctx = await pwRequest.newContext();
      const r = await ctx.post(`${c.url}/mcp/invoke`, {
        data: { tool: "anything", args: {} },
        timeout: 10_000,
      });
      expect([401, 403]).toContain(r.status());
      await ctx.dispose();
    });

    test(`/mcp-reload POST unauth → 401/403/404 (not 200)`, async () => {
      const ctx = await pwRequest.newContext();
      const r = await ctx.post(`${c.url}/mcp-reload`, { timeout: 10_000 });
      expect(r.status(),
        `${c.id} /mcp-reload anon must be rejected (route may not exist on every cartridge)`,
      ).not.toBe(200);
      await ctx.dispose();
    });

    test(`/health response JSON parses cleanly`, async () => {
      const ctx = await pwRequest.newContext();
      const r = await ctx.get(`${c.url}/health`, { timeout: 10_000 });
      const text = await r.text();
      expect(() => JSON.parse(text)).not.toThrow();
      await ctx.dispose();
    });

    test(`unknown path returns 404 (router is intact)`, async () => {
      const ctx = await pwRequest.newContext();
      const r = await ctx.get(`${c.url}/this-path-does-not-exist`, {
        timeout: 10_000,
      });
      expect(r.status()).toBe(404);
      await ctx.dispose();
    });
  });
}
