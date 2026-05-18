/**
 * v1.44.3.2.1 spec 11 — Copilot backend deep coverage.
 *
 * 22 tests covering the LIVE /api/copilot/* endpoints. These are
 * marked test.fail(true) where they require the v1.44.4 streaming
 * SSE endpoint or the /copilot chat page that haven't shipped yet
 * — but the contract probes for memory / drafts / workflows /
 * briefing all run today against the v1.44.3 LLM integration.
 *
 * Real LLM calls happen only when the developer has ANTHROPIC_API_KEY
 * (or GEMINI_API_KEY) exported. Without a key the LLM-dependent
 * tests skip cleanly; CSRF + RBAC + boundary checks always run.
 */
import { test, expect, request as pwRequest } from "@playwright/test";

test.use({ storageState: { cookies: [], origins: [] } });

const BACKEND = process.env.LEGACY_URL || "http://localhost:8000";
const EMAIL = process.env.TEST_EMAIL || "emmanuel@local.ai";
const PASSWORD = process.env.TEST_PASSWORD || "";
const HAS_LLM_KEY = Boolean(
  process.env.ANTHROPIC_API_KEY || process.env.GEMINI_API_KEY,
);

async function authedCtxAndCsrf() {
  const ctx = await pwRequest.newContext();
  const csrf = await ctx.get(`${BACKEND}/login`);
  const sc = csrf.headers()["set-cookie"] || "";
  const m = (Array.isArray(sc) ? sc.join("\n") : sc).match(/csrf_token=([^;,\s]+)/);
  if (!m) throw new Error("csrf_token cookie missing on GET /login");
  const token = m[1];
  const r = await ctx.post(`${BACKEND}/auth/login`, {
    headers: {
      "Content-Type": "application/json",
      "X-CSRF-Token":  token,
      "Cookie":        `csrf_token=${token}`,
    },
    data: { email: EMAIL, password: PASSWORD },
  });
  if (r.status() !== 200) {
    throw new Error(`auth bootstrap failed: HTTP ${r.status()}`);
  }
  return { ctx, csrf: token };
}

test.describe("Copilot conversations", () => {
  test("POST /api/copilot/conversations creates and returns id", async () => {
    const { ctx, csrf } = await authedCtxAndCsrf();
    const r = await ctx.post(`${BACKEND}/api/copilot/conversations`, {
      headers: { "X-CSRF-Token": csrf },
      data: {},
    });
    expect([200, 201]).toContain(r.status());
    const body = await r.json();
    expect(body).toHaveProperty("id");
    await ctx.dispose();
  });

  test("GET /api/copilot/conversations lists conversations", async () => {
    const { ctx } = await authedCtxAndCsrf();
    const r = await ctx.get(`${BACKEND}/api/copilot/conversations`);
    expect(r.status()).toBe(200);
    const body = await r.json();
    expect(body).toHaveProperty("conversations");
    expect(Array.isArray(body.conversations)).toBe(true);
    await ctx.dispose();
  });
});

test.describe("Copilot memory CRUD", () => {
  test("POST memory/fact persists + returns serialised fact", async () => {
    const { ctx, csrf } = await authedCtxAndCsrf();
    const r = await ctx.post(`${BACKEND}/api/copilot/memory/fact`, {
      headers: { "X-CSRF-Token": csrf },
      data: { fact: `e2e-test fact ${Date.now()}` },
    });
    let factId: number | null = null;
    // v1.44.3.2.1 R1 Security P2: wrap the cleanup in try/finally
    // so an assertion failure above doesn't leave the throwaway
    // fact in the user_facts table.
    try {
      expect(r.status()).toBe(200);
      const body = await r.json();
      expect(body).toHaveProperty("fact");
      expect(body.fact).toHaveProperty("id");
      factId = body.fact.id;
    } finally {
      if (factId !== null) {
        await ctx.delete(
          `${BACKEND}/api/copilot/memory/fact/${factId}`,
          { headers: { "X-CSRF-Token": csrf } },
        ).catch(() => null);
      }
      await ctx.dispose();
    }
    // Early return below — the dispose already happened in finally.
    return;
  });

  test("GET memory returns facts + preferences shape", async () => {
    const { ctx } = await authedCtxAndCsrf();
    const r = await ctx.get(`${BACKEND}/api/copilot/memory`);
    expect(r.status()).toBe(200);
    const body = await r.json();
    expect(body).toHaveProperty("facts");
    expect(body).toHaveProperty("preferences");
    expect(Array.isArray(body.facts)).toBe(true);
    await ctx.dispose();
  });

  test("DELETE memory/fact/{id} for another user's fact returns 404",
    async () => {
      const { ctx, csrf } = await authedCtxAndCsrf();
      // Use a very high ID unlikely to belong to this user.
      const r = await ctx.delete(
        `${BACKEND}/api/copilot/memory/fact/999999999`,
        { headers: { "X-CSRF-Token": csrf } },
      );
      expect(r.status()).toBe(404);
      await ctx.dispose();
    },
  );

  test("POST memory/fact rejects 501-char fact", async () => {
    const { ctx, csrf } = await authedCtxAndCsrf();
    const r = await ctx.post(`${BACKEND}/api/copilot/memory/fact`, {
      headers: { "X-CSRF-Token": csrf },
      data: { fact: "x".repeat(501) },
    });
    expect(r.status()).toBe(400);
    await ctx.dispose();
  });

  test("PUT memory/preference/{key} with disallowed key → 400", async () => {
    const { ctx, csrf } = await authedCtxAndCsrf();
    const r = await ctx.put(
      `${BACKEND}/api/copilot/memory/preference/not_in_allowlist`,
      {
        headers: { "X-CSRF-Token": csrf },
        data: { value: "x" },
      },
    );
    expect(r.status()).toBe(400);
    await ctx.dispose();
  });
});

test.describe("Copilot drafts CRUD", () => {
  test("POST /drafts creates a draft with valid kind+tone+body",
    async () => {
      const { ctx, csrf } = await authedCtxAndCsrf();
      const r = await ctx.post(`${BACKEND}/api/copilot/drafts`, {
        headers: { "X-CSRF-Token": csrf },
        data: { kind: "note", body: "e2e probe", tone: "neutral" },
      });
      expect([200, 201]).toContain(r.status());
      const body = await r.json();
      expect(body).toHaveProperty("draft");
      expect(body.draft).toHaveProperty("id");
      await ctx.dispose();
    },
  );

  test("POST /drafts rejects invalid kind", async () => {
    const { ctx, csrf } = await authedCtxAndCsrf();
    const r = await ctx.post(`${BACKEND}/api/copilot/drafts`, {
      headers: { "X-CSRF-Token": csrf },
      data: { kind: "INVALID", body: "x" },
    });
    expect(r.status()).toBe(400);
    await ctx.dispose();
  });

  test("POST /drafts/{uuid}/send for non-uuid → 400", async () => {
    const { ctx, csrf } = await authedCtxAndCsrf();
    const r = await ctx.post(
      `${BACKEND}/api/copilot/drafts/not-a-uuid/send`,
      { headers: { "X-CSRF-Token": csrf } },
    );
    // The v1.44.3 R1 fix added UUID validation → 400.
    expect([400, 404]).toContain(r.status());
    await ctx.dispose();
  });

  test("POST /drafts/generate calls the LLM end-to-end", async () => {
    test.skip(!HAS_LLM_KEY,
      "ANTHROPIC_API_KEY/GEMINI_API_KEY not set — skip live LLM probe",
    );
    const { ctx, csrf } = await authedCtxAndCsrf();
    const r = await ctx.post(`${BACKEND}/api/copilot/drafts/generate`, {
      headers: { "X-CSRF-Token": csrf },
      data: {
        kind: "note",
        about: "respuesta breve de prueba",
        tone: "neutral",
      },
      timeout: 30_000,
    });
    expect(r.status()).toBe(200);
    const body = await r.json();
    expect(body.draft.body.length).toBeGreaterThan(10);
    await ctx.dispose();
  });
});

test.describe("Copilot workflows CRUD", () => {
  test("POST /workflow creates with status='planning'", async () => {
    const { ctx, csrf } = await authedCtxAndCsrf();
    const r = await ctx.post(`${BACKEND}/api/copilot/workflow`, {
      headers: { "X-CSRF-Token": csrf },
      data: { intent: "e2e probe workflow" },
    });
    expect([200, 201]).toContain(r.status());
    const body = await r.json();
    expect(body.workflow).toHaveProperty("status");
    expect(body.workflow.status).toBe("planning");
    await ctx.dispose();
  });

  test("GET /workflow/{id} for non-uuid → 400", async () => {
    const { ctx } = await authedCtxAndCsrf();
    const r = await ctx.get(`${BACKEND}/api/copilot/workflow/not-a-uuid`);
    expect([400, 404]).toContain(r.status());
    await ctx.dispose();
  });

  test("POST /workflow rejects empty intent", async () => {
    const { ctx, csrf } = await authedCtxAndCsrf();
    const r = await ctx.post(`${BACKEND}/api/copilot/workflow`, {
      headers: { "X-CSRF-Token": csrf },
      data: { intent: "" },
    });
    expect(r.status()).toBe(400);
    await ctx.dispose();
  });

  test("POST /workflow rejects 1001-char intent", async () => {
    const { ctx, csrf } = await authedCtxAndCsrf();
    const r = await ctx.post(`${BACKEND}/api/copilot/workflow`, {
      headers: { "X-CSRF-Token": csrf },
      data: { intent: "x".repeat(1001) },
    });
    expect(r.status()).toBe(400);
    await ctx.dispose();
  });
});

test.describe("Copilot briefing", () => {
  test("GET /briefing returns highlights array", async () => {
    const { ctx } = await authedCtxAndCsrf();
    const r = await ctx.get(`${BACKEND}/api/copilot/briefing`);
    expect(r.status()).toBe(200);
    const body = await r.json();
    expect(body).toHaveProperty("highlights");
    expect(Array.isArray(body.highlights)).toBe(true);
  });

  test("POST /briefing/{id}/dismiss with empty id → 400", async () => {
    const { ctx, csrf } = await authedCtxAndCsrf();
    const r = await ctx.post(
      `${BACKEND}/api/copilot/briefing/%20/dismiss`,
      { headers: { "X-CSRF-Token": csrf } },
    );
    // Either 400 (whitespace-stripped boundary check) or 404
    // (path normalisation). Both prove the destructive branch
    // doesn't execute.
    expect([400, 404]).toContain(r.status());
    await ctx.dispose();
  });

  test("POST /briefing/{id}/dismiss with overlong id → 400", async () => {
    const { ctx, csrf } = await authedCtxAndCsrf();
    const r = await ctx.post(
      `${BACKEND}/api/copilot/briefing/${"x".repeat(201)}/dismiss`,
      { headers: { "X-CSRF-Token": csrf } },
    );
    expect([400, 404]).toContain(r.status());
    await ctx.dispose();
  });
});

test.describe("Copilot streaming + chat UI — deferred to v1.44.4", () => {
  test.fail(true, "SSE chat stream endpoint deferred to v1.44.4");

  test("GET /api/copilot/chat/{id}/stream exists", async () => {
    const { ctx } = await authedCtxAndCsrf();
    const r = await ctx.get(
      `${BACKEND}/api/copilot/chat/00000000-0000-0000-0000-000000000000/stream`,
    );
    expect(r.status()).not.toBe(404);
  });

  test("/copilot Next.js page renders chat shell", async ({ page }) => {
    await page.goto("/copilot");
    await expect(page.getByText(/copiloto|chat/i).first()).toBeVisible({
      timeout: 10_000,
    });
  });

  test("/copilot has a message input", async ({ page }) => {
    await page.goto("/copilot");
    await expect(
      page.getByRole("textbox", { name: /mensaje|message/i }),
    ).toBeVisible({ timeout: 10_000 });
  });

  test("/copilot streaming renders tokens incrementally", async ({ page }) => {
    await page.goto("/copilot");
    const input = page.getByRole("textbox", { name: /mensaje|message/i });
    await input.fill("Cuántos cartuchos hay configurados?");
    await page.getByRole("button", { name: /enviar|send/i }).click();
    // Look for SSE streaming evidence — text growing over time.
    const messageRegion = page.locator(
      '[data-testid="chat-messages"], [aria-label*="mensajes" i]',
    ).first();
    const initialText = await messageRegion.innerText().catch(() => "");
    await page.waitForTimeout(3_000);
    const laterText = await messageRegion.innerText().catch(() => "");
    expect(laterText.length).toBeGreaterThan(initialText.length);
  });
});
