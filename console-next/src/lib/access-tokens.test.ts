import { beforeEach, describe, expect, it, vi } from "vitest";

const apiMock = vi.hoisted(() => ({
  get: vi.fn(),
  post: vi.fn(),
  delete: vi.fn(),
}));

vi.mock("@/lib/api", () => ({ api: apiMock }));

import {
  canRevoke,
  createAccessToken,
  formatDate,
  gatewayUrls,
  listAccessTokens,
  revokeAccessToken,
  scopeLabel,
  statusLabel,
} from "./access-tokens";

beforeEach(() => {
  vi.clearAllMocks();
});

describe("access token client", () => {
  it("talks only to the session management routes", async () => {
    apiMock.get.mockResolvedValue({ data: { tokens: [] } });
    apiMock.post.mockResolvedValue({ data: { token: "x", token_info: {} } });
    apiMock.delete.mockResolvedValue({ data: { revocado: true, id: "a/b" } });
    await listAccessTokens();
    await createAccessToken({ nombre: "Asistente", alcance: "lectura", dias: 30 });
    await revokeAccessToken("a/b");
    expect(apiMock.get).toHaveBeenCalledWith("/api/me/access-tokens");
    expect(apiMock.post).toHaveBeenCalledWith("/api/me/access-tokens", {
      nombre: "Asistente",
      alcance: "lectura",
      dias: 30,
    });
    expect(apiMock.delete).toHaveBeenCalledWith("/api/me/access-tokens/a%2Fb");
  });
});

describe("labels", () => {
  it("describes scopes in business Spanish", () => {
    expect(scopeLabel(["lectura"])).toBe("Solo lectura");
    expect(scopeLabel(["acciones", "lectura"])).toBe("Lectura y acciones");
    expect(scopeLabel([])).toBe("Sin información");
    expect(scopeLabel(undefined)).toBe("Sin información");
  });

  it("describes statuses and revocation reasons", () => {
    expect(statusLabel({ estado: "activo", motivo_revocacion: null })).toBe("Activo");
    expect(statusLabel({ estado: "vencido", motivo_revocacion: null })).toBe("Vencido");
    expect(statusLabel({ estado: "revocado", motivo_revocacion: "credenciales_restablecidas" })).toBe(
      "Revocado (revocado al restablecer la contraseña)",
    );
    expect(statusLabel({ estado: "sin_acceso", motivo_revocacion: null })).toBe(
      "Sin acceso al espacio de trabajo",
    );
    expect(statusLabel({ estado: "otro", motivo_revocacion: null })).toBe("Sin información");
  });

  it("keeps tokens without workspace access revocable", () => {
    expect(canRevoke({ estado: "activo" })).toBe(true);
    expect(canRevoke({ estado: "sin_acceso" })).toBe(true);
    expect(canRevoke({ estado: "vencido" })).toBe(false);
    expect(canRevoke({ estado: "revocado" })).toBe(false);
  });

  it("formats dates and never invents missing ones", () => {
    expect(formatDate(null)).toBe("Sin información");
    expect(formatDate(null, "Sin uso registrado")).toBe("Sin uso registrado");
    expect(formatDate("no-es-fecha")).toBe("Sin información");
    expect(formatDate("2026-09-29T12:00:00Z")).toMatch(/2026/);
  });

  it("builds connection addresses from the current origin", () => {
    expect(gatewayUrls("https://consola.example.test/")).toEqual({
      base: "https://consola.example.test",
      esquema: "https://consola.example.test/api/ia/v1/openapi.json",
      conectorRemoto: "https://consola.example.test/api/ia/v1/mcp",
    });
  });
});
