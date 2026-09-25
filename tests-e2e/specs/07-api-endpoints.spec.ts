/**
 * v1.44.3.2 spec 07 — Backend API contract probes.
 *
 * For each documented endpoint:
 *   1. Without auth → expects 401/403 (NOT 200 — that would be a
 *      RBAC regression; NOT 5xx — that would be a routing bug).
 *   2. With auth → expects 200/201 + a shape sanity check.
 *
 * Diagnostics confirmed the backend currently returns 401
 * for every protected endpoint without auth, which is the desired
 * baseline. These tests pin that baseline as a regression guard.
 */
import { test, expect, request as pwRequest } from "@playwright/test";
import { loginViaApi } from "../fixtures/auth";

const LEGACY = process.env.LEGACY_URL || "http://localhost:8000";

function extractCsrf(setCookie: string | undefined): string {
  const match = (setCookie || "").match(/csrf_token=([^;,\s]+)/);
  if (!match?.[1]) {
    throw new Error("GET /login did not seed csrf_token for /api/auth/login");
  }
  return match[1];
}

async function postApiAuthLogin(
  ctx: import("@playwright/test").APIRequestContext,
  data: { email: string; password: string },
) {
  const csrfResponse = await ctx.get(`${LEGACY}/login`);
  const csrf = extractCsrf(csrfResponse.headers()["set-cookie"]);
  return ctx.fetch(`${LEGACY}/api/auth/login`, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      "X-CSRF-Token": csrf,
      Cookie: `csrf_token=${csrf}`,
    },
    data,
  });
}

// v1.44.3.2.1: this spec mixes unauth + authed checks. Force the
// unauth surface for the WHOLE file by clearing storage state; the
// authed tests inside re-mint a session via loginViaApi.
test.use({ storageState: { cookies: [], origins: [] } });

interface ApiCheck {
  method:    "GET" | "POST" | "PUT" | "DELETE";
  path:      string;
  body?:     unknown;
  /**
   * Shape validator for the authed response. The validator receives
   * the parsed JSON body and must return true. Throw or return false
   * to fail the test.
   */
  shape?:    (data: unknown) => boolean;
  /** Endpoints that may legitimately return 404/409 instead of 200 */
  authedOk?: number[];
}

const PROTECTED: ApiCheck[] = [
  {
    method: "GET", path: "/api/dashboard/kpis",
    shape: (d) => typeof d === "object" && d !== null &&
                  "cartridges" in d && "extractions" in d &&
                  "data_freshness" in d && "users" in d &&
                  "copilot" in d && "audit" in d,
  },
  {
    method: "GET", path: "/api/cartridges",
    shape: (d) => typeof d === "object" && d !== null && "cartridges" in d &&
                  Array.isArray((d as { cartridges: unknown[] }).cartridges),
  },
  {
    method: "GET", path: "/api/cartridges/hubspot/connector_schema",
    shape: (d) => typeof d === "object" && d !== null,
  },
  {
    method: "GET", path: "/api/cartridges/replicon/connector_schema",
    shape: (d) => typeof d === "object" && d !== null,
  },
  {
    method: "GET", path: "/api/copilot/conversations",
    shape: (d) => typeof d === "object" && d !== null,
  },
  {
    method: "GET", path: "/api/copilot/memory",
    shape: (d) => typeof d === "object" && d !== null &&
                  "facts" in d && "preferences" in d,
  },
  {
    method: "GET", path: "/api/copilot/briefing",
    shape: (d) => typeof d === "object" && d !== null && "highlights" in d,
  },
  {
    method: "GET", path: "/api/system/onboarding/state",
    shape: (d) => typeof d === "object" && d !== null &&
                  "completed" in d && "total_steps" in d,
  },
];

test.describe("Backend API — auth gate (unauth ⇒ 401/403)", () => {
  for (const check of PROTECTED) {
    test(`${check.method} ${check.path} rejects unauthenticated`, async () => {
      const ctx = await pwRequest.newContext();
      const response = await ctx.fetch(`${LEGACY}${check.path}`, {
        method: check.method,
        data: check.body,
      });
      const status = response.status();
      expect([401, 403],
        `${check.method} ${check.path} returned ${status} unauthenticated — ` +
        "the auth gate is the load-bearing security boundary; any other " +
        "status (200 = bypass, 404 = route gone, 5xx = crash) is a P0.",
      ).toContain(status);
      await ctx.dispose();
    });
  }
});

test.describe("Backend API — authenticated", () => {
  for (const check of PROTECTED) {
    test(`${check.method} ${check.path} returns expected shape`, async ({
      page,
    }) => {
      // v1.44.3.2.1: fixture signature changed — loginViaApi now
      // takes an APIRequestContext (cookies persist on the context)
      // and returns the response. Authed page.request follows.
      await loginViaApi(page.request);
      const response = await page.request.fetch(`${LEGACY}${check.path}`, {
        method: check.method,
        data: check.body,
      });
      const status = response.status();
      const okList = check.authedOk ?? [200, 201];
      expect(okList,
        `${check.method} ${check.path} returned ${status} with valid auth — expected one of ${okList.join("/")}`,
      ).toContain(status);
      if (check.shape) {
        const body = await response.json();
        expect(check.shape(body),
          `${check.path} response shape failed the validator`,
        ).toBe(true);
      }
    });
  }
});

test.describe("Backend API — POST /api/auth/login round-trip", () => {
  test("invalid creds return 401", async () => {
    const ctx = await pwRequest.newContext();
    const response = await postApiAuthLogin(ctx, {
      email: "nobody@invalid.local",
      password: "wrong",
    });
    expect([401, 429],
      "Invalid credentials must be rejected. In a full-suite run the " +
      "shared test IP may legitimately hit the brute-force limiter first.",
    ).toContain(response.status());
    await ctx.dispose();
  });

  test("valid creds return 200 + set a cookie", async () => {
    // v1.44.3.2 R1 Testing F4 follow-up: fail loud (not silent
    // skip) when env vars are missing. The whole-suite contract
    // (fixtures/auth.ts:readCreds) already throws on missing
    // creds; the previous test.skip here contradicted that
    // contract and let a misconfigured run silently green-pass.
    const email = process.env.TEST_EMAIL;
    const password = process.env.TEST_PASSWORD;
    if (!email || !password) {
      throw new Error(
        "TEST_EMAIL + TEST_PASSWORD must be set in tests-e2e/.env. " +
        "The suite refuses to run a login round-trip without real " +
        "credentials so a misconfigured CI can't silently green-pass.",
      );
    }
    const ctx = await pwRequest.newContext();
    const response = await postApiAuthLogin(ctx, { email, password });
    expect(response.status()).toBe(200);
    // The Set-Cookie header must include httpOnly + at least one
    // recognised auth cookie name.
    const setCookie = response.headers()["set-cookie"] || "";
    expect(setCookie).toMatch(/httponly/i);
    expect(setCookie).toMatch(/access_token|session|jwt|auth_token/i);
    await ctx.dispose();
  });
});
