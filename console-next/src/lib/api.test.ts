import { afterEach, describe, expect, it, vi } from "vitest";

import { api, apiFetch, isApiError } from "./api";

afterEach(() => {
  vi.useRealTimers();
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

function mockCookie(cookie: string) {
  vi.stubGlobal("document", { cookie });
}

describe("apiFetch", () => {
  it("uses same-origin credentials, JSON encoding, request ids and CSRF for mutations", async () => {
    mockCookie("csrf_token=csrf%20123");
    const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      void input;
      void init;
      return new Response(JSON.stringify({ ok: true }));
    });
    vi.stubGlobal("fetch", fetchMock);
    vi.stubGlobal("crypto", { randomUUID: () => "req-fixed" });

    await apiFetch("/api/example", {
      method: "POST",
      json: { answer: 42 },
    });

    expect(fetchMock).toHaveBeenCalledTimes(1);
    const [, init] = fetchMock.mock.calls[0] as [string, RequestInit];
    const headers = init?.headers as Headers;
    expect(init?.credentials).toBe("include");
    expect(init?.body).toBe(JSON.stringify({ answer: 42 }));
    expect(headers.get("Accept")).toBe("application/json");
    expect(headers.get("Content-Type")).toBe("application/json");
    expect(headers.get("X-Request-ID")).toBe("req-fixed");
    expect(headers.get("X-CSRF-Token")).toBe("csrf 123");
  });

  it("does not add CSRF to safe GET requests", async () => {
    mockCookie("csrf_token=csrf");
    const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      void input;
      void init;
      return new Response("{}");
    });
    vi.stubGlobal("fetch", fetchMock);

    await apiFetch("/api/example");

    const [, init] = fetchMock.mock.calls[0] as [string, RequestInit];
    const headers = init?.headers as Headers;
    expect(headers.get("X-CSRF-Token")).toBeNull();
  });

  it("sends the active workspace header from the workspace cookie", async () => {
    mockCookie("csrf_token=csrf; omega_active_workspace_id=workspace%201");
    const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      void input;
      void init;
      return new Response("{}");
    });
    vi.stubGlobal("fetch", fetchMock);

    await apiFetch("/api/me/access");

    const [, init] = fetchMock.mock.calls[0] as [string, RequestInit];
    const headers = init?.headers as Headers;
    expect(headers.get("X-Workspace-Id")).toBe("workspace 1");
  });

  it("aborts slow requests with a localized timeout error and request id", async () => {
    vi.useFakeTimers();
    mockCookie("");
    vi.stubGlobal("crypto", { randomUUID: () => "req-timeout" });
    vi.stubGlobal("fetch", vi.fn((_input: RequestInfo | URL, init?: RequestInit) => new Promise((_resolve, reject) => {
      const signal = init?.signal as AbortSignal | undefined;
      signal?.addEventListener("abort", () => reject(new DOMException("aborted", "AbortError")), { once: true });
    })));

    const request = apiFetch("/api/slow", { timeoutMs: 25 });
    const assertion = expect(request).rejects.toMatchObject({
      message: "La consulta tardó demasiado",
      status: 408,
      data: { timeout_ms: 25 },
      requestId: "req-timeout",
    });
    await vi.advanceTimersByTimeAsync(25);

    await assertion;
    vi.useRealTimers();
  });

  it("allows callers to disable the timeout for controlled streaming requests", async () => {
    mockCookie("");
    const fetchMock = vi.fn(async (_input: RequestInfo | URL, init?: RequestInit) => {
      expect(init?.signal).toBeUndefined();
      return new Response("{}");
    });
    vi.stubGlobal("fetch", fetchMock);

    await apiFetch("/api/stream", { timeoutMs: 0 });

    expect(fetchMock).toHaveBeenCalledTimes(1);
  });
});

describe("api", () => {
  it("preserves caller headers for idempotent mutations", async () => {
    const fetchMock = vi.fn(
      async (_input: RequestInfo | URL, _init?: RequestInit) => new Response(
        JSON.stringify({ ok: true }),
        { status: 200, headers: { "content-type": "application/json" } },
      ),
    );
    vi.stubGlobal("fetch", fetchMock);

    await api.post("/api/decisions/42/actions", { action_text: "canary" }, {
      headers: { "Idempotency-Key": "synthetic-client-canary" },
    });

    const [, init] = fetchMock.mock.calls[0] as [string, RequestInit];
    const headers = init.headers as Headers;
    expect(headers.get("Idempotency-Key")).toBe("synthetic-client-canary");
    expect(headers.get("X-Request-ID")).toBeTruthy();
  });

  it("parses JSON success payloads and preserves backend request id", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => new Response(
      JSON.stringify({ users: [1, 2] }),
      { status: 200, headers: { "content-type": "application/json", "x-request-id": "backend-req" } },
    )));

    const response = await api.get<{ users: number[] }>("/api/users");

    expect(response.data.users).toEqual([1, 2]);
    expect(response.status).toBe(200);
    expect(response.requestId).toBe("backend-req");
  });

  it("throws typed ApiError with detail, status and request id", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => new Response(
      JSON.stringify({ detail: "Forbidden by RBAC" }),
      { status: 403, headers: { "content-type": "application/json", "x-request-id": "backend-deny" } },
    )));

    await expect(api.post("/api/admin", {})).rejects.toMatchObject({
      message: "Forbidden by RBAC",
      status: 403,
      requestId: "backend-deny",
    });
  });

  it("falls back to localized messages for plain 401 responses", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => new Response("", { status: 401 })));

    try {
      await api.get("/api/me");
      throw new Error("expected failure");
    } catch (error) {
      expect(isApiError(error)).toBe(true);
      expect((error as Error).message).toBe("Sesión expirada o no autenticada.");
    }
  });

  it("passes through a safe single-line 4xx detail", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => new Response(
      JSON.stringify({ detail: "El período ya está cerrado." }),
      { status: 409, headers: { "content-type": "application/json", "x-request-id": "req-safe" } },
    )));

    await expect(api.post("/api/periods/close", {})).rejects.toMatchObject({
      message: "El período ya está cerrado.",
      status: 409,
      requestId: "req-safe",
    });
  });

  it.each([
    ["multiline detail", "línea uno\nlínea dos"],
    ["traceback", "Traceback (most recent call last): ValueError"],
    ["SQL fragment", "error near SELECT id FROM users"],
    ["server path", 'File "/app/services/payroll.py", line 42'],
    ["hex pointer", "segfault at 0xdeadbeef01"],
    ["digest", `dup key ${"a1b2c3d4".repeat(4)}`],
    ["too long", "x".repeat(241)],
  ])("replaces unsafe 4xx detail (%s) with generic message + Ref", async (_label, detail) => {
    vi.stubGlobal("fetch", vi.fn(async () => new Response(
      JSON.stringify({ detail }),
      { status: 422, headers: { "content-type": "application/json", "x-request-id": "req-unsafe" } },
    )));

    await expect(api.post("/api/things", {})).rejects.toMatchObject({
      message: "No se pudo completar la solicitud (HTTP 422). Ref: req-unsafe",
      status: 422,
      requestId: "req-unsafe",
    });
  });

  it("never surfaces raw non-JSON string bodies such as proxy HTML", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => new Response(
      "<html><body><h1>502 Bad Gateway</h1><pre>upstream connect error</pre></body></html>",
      { status: 400, headers: { "content-type": "text/html", "x-request-id": "req-html" } },
    )));

    await expect(api.get("/api/things")).rejects.toMatchObject({
      message: "No se pudo completar la solicitud (HTTP 400). Ref: req-html",
      status: 400,
      requestId: "req-html",
    });
  });

  it("keeps 5xx generic even when the backend sends a detail string", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => new Response(
      JSON.stringify({ detail: "psycopg2.OperationalError: connection refused" }),
      { status: 500, headers: { "content-type": "application/json", "x-request-id": "req-500" } },
    )));

    await expect(api.get("/api/things")).rejects.toMatchObject({
      message: "El backend no pudo completar la solicitud. Ref: req-500",
      status: 500,
      requestId: "req-500",
    });
  });

  it("keeps 5xx generic for raw HTML bodies from proxies", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => new Response(
      "<html><body>Bad Gateway</body></html>",
      { status: 502, headers: { "content-type": "text/html", "x-request-id": "req-502" } },
    )));

    await expect(api.get("/api/things")).rejects.toMatchObject({
      message: "El backend no pudo completar la solicitud. Ref: req-502",
      status: 502,
      requestId: "req-502",
    });
  });

  it("adds request id context to plain backend failures", async () => {
    vi.stubGlobal("crypto", { randomUUID: () => "client-req" });
    vi.stubGlobal("fetch", vi.fn(async () => new Response("", {
      status: 500,
      headers: { "x-request-id": "backend-oops" },
    })));

    await expect(api.get("/api/copilot/actions")).rejects.toMatchObject({
      message: "El backend no pudo completar la solicitud. Ref: backend-oops",
      status: 500,
      requestId: "backend-oops",
    });
  });
});
