import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { apiFetch } from "@/lib/api";
import { deleteCookie, readCookie } from "@/lib/cookies";
import { loginUser } from "./auth-flow";

vi.mock("@/lib/api", () => ({
  apiFetch: vi.fn(),
}));

vi.mock("@/lib/cookies", () => ({
  deleteCookie: vi.fn(),
  readCookie: vi.fn(),
}));

const apiFetchMock = vi.mocked(apiFetch);
const deleteCookieMock = vi.mocked(deleteCookie);
const readCookieMock = vi.mocked(readCookie);

beforeEach(() => {
  apiFetchMock.mockReset();
  deleteCookieMock.mockReset();
  readCookieMock.mockReset();
});

afterEach(() => {
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
});

function stubWindow() {
  vi.stubGlobal("window", {
    setTimeout,
    clearTimeout,
  });
}

describe("loginUser", () => {
  it("runs the same-origin CSRF login flow and returns parsed body", async () => {
    stubWindow();
    apiFetchMock
      .mockResolvedValueOnce(new Response("", { status: 200 }))
      .mockResolvedValueOnce(new Response(JSON.stringify({ ok: true, user: { email: "user@example.com" } }), {
        status: 200,
        headers: { "content-type": "application/json" },
      }));
    readCookieMock.mockReturnValue("csrf-token");

    const body = await loginUser("user@example.com", "secret");

    expect(body).toEqual({ ok: true, user: { email: "user@example.com" } });
    expect(apiFetchMock).toHaveBeenNthCalledWith(1, "/login", expect.objectContaining({ method: "GET" }));
    expect(apiFetchMock).toHaveBeenNthCalledWith(2, "/auth/login", expect.objectContaining({
      method: "POST",
      headers: { "X-CSRF-Token": "csrf-token" },
      json: { email: "user@example.com", password: "secret" },
    }));
    expect(deleteCookieMock).toHaveBeenCalledWith("omega_active_workspace_id");
    expect(deleteCookieMock).toHaveBeenCalledTimes(2);
  });

  it("rejects when /login did not seed the CSRF cookie", async () => {
    stubWindow();
    apiFetchMock.mockResolvedValueOnce(new Response("", { status: 200 }));
    readCookieMock.mockReturnValue(null);

    await expect(loginUser("user@example.com", "secret")).rejects.toMatchObject({
      message: "El backend no devolvió el token CSRF. Verifica que /login responde 200.",
    });
    expect(apiFetchMock).toHaveBeenCalledTimes(1);
  });

  it("maps backend auth failures to useful user-facing messages", async () => {
    stubWindow();
    apiFetchMock
      .mockResolvedValueOnce(new Response("", { status: 200 }))
      .mockResolvedValueOnce(new Response(JSON.stringify({ detail: "bad creds" }), {
        status: 401,
        headers: { "content-type": "application/json" },
      }));
    readCookieMock.mockReturnValue("csrf-token");

    await expect(loginUser("user@example.com", "wrong")).rejects.toMatchObject({
      message: "Email o contraseña incorrectos.",
      status: 401,
      detail: "bad creds",
    });
  });
});
