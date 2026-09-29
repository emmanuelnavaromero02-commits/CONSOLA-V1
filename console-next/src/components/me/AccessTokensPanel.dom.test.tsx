// @vitest-environment jsdom

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type { AccessToken, AccessTokensResponse } from "@/lib/access-tokens";
import { forbiddenTermsIn } from "@/lib/glossary";

import { AccessTokensPanel } from "./AccessTokensPanel";

const api = vi.hoisted(() => ({
  listAccessTokens: vi.fn(),
  createAccessToken: vi.fn(),
  revokeAccessToken: vi.fn(),
}));
const clipboard = vi.hoisted(() => ({ copyText: vi.fn() }));

vi.mock("@/lib/access-tokens", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/access-tokens")>();
  return { ...actual, ...api };
});
vi.mock("@/lib/clipboard", () => clipboard);

const ALPHABET = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789";

function runtimeToken(): string {
  let body = "";
  for (let index = 0; index < 43; index += 1) {
    body += ALPHABET[Math.floor(Math.random() * ALPHABET.length)];
  }
  return `omega_pat_${body}`;
}

function token(overrides: Partial<AccessToken> = {}): AccessToken {
  return {
    id: "tok-1",
    nombre: "Asistente de finanzas",
    espacio_de_trabajo: { id: "w-1", nombre: "Operaciones" },
    token_prefix: "omega_pat_Ab12", // gitleaks:allow
    alcances: ["lectura"],
    creado_en: "2026-09-01T10:00:00Z",
    vence_en: "2026-10-01T10:00:00Z",
    ultimo_uso_en: null,
    revocado_en: null,
    motivo_revocacion: null,
    estado: "activo",
    ...overrides,
  };
}

function listing(overrides: Partial<AccessTokensResponse> = {}): AccessTokensResponse {
  return {
    tokens: [token(), token({ id: "tok-2", nombre: "Viejo", estado: "revocado", motivo_revocacion: "usuario" })],
    puede_crear: true,
    alcances_permitidos: ["lectura"],
    limite_activos: 10,
    tokens_activos: 1,
    dias_permitidos: [7, 30, 90],
    dias_predeterminados: 30,
    ...overrides,
  };
}

let container: HTMLDivElement;
let root: Root;

async function settle() {
  for (let index = 0; index < 6; index += 1) {
    await act(async () => {
      await new Promise((resolve) => setTimeout(resolve, 0));
    });
  }
}

async function render() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } });
  await act(async () => {
    root.render(
      <QueryClientProvider client={client}>
        <AccessTokensPanel />
      </QueryClientProvider>,
    );
  });
  await settle();
}

function button(text: string): HTMLButtonElement | undefined {
  return [...container.querySelectorAll<HTMLButtonElement>("button")].find(
    (element) => element.textContent?.trim() === text,
  );
}

async function click(element: HTMLElement | undefined) {
  expect(element).toBeTruthy();
  await act(async () => {
    element?.dispatchEvent(new MouseEvent("click", { bubbles: true }));
  });
  await settle();
}

async function typeName(value: string) {
  const input = container.querySelector<HTMLInputElement>("input");
  expect(input).toBeTruthy();
  await act(async () => {
    const setter = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, "value")?.set;
    setter?.call(input, value);
    input?.dispatchEvent(new Event("input", { bubbles: true }));
  });
}

beforeEach(() => {
  (globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;
  vi.clearAllMocks();
  api.listAccessTokens.mockResolvedValue(listing());
  container = document.createElement("div");
  document.body.append(container);
  root = createRoot(container);
});

afterEach(async () => {
  await act(async () => root.unmount());
  container.remove();
  vi.restoreAllMocks();
});

describe("AccessTokensPanel", () => {
  it("lists tokens with Spanish columns, honest empty values and no technical terms", async () => {
    await render();
    const text = container.textContent ?? "";
    expect(text).toContain("Conectores de IA");
    for (const column of ["Nombre", "Espacio", "Alcance", "Creado", "Vence", "Último uso", "Estado"]) {
      expect(text).toContain(column);
    }
    expect(text).toContain("Asistente de finanzas");
    expect(text).toContain("Operaciones");
    expect(text).toContain("Solo lectura");
    expect(text).toContain("Sin uso registrado");
    expect(text).toContain("Revocado (revocado por ti)");
    expect(container.querySelectorAll('[data-testid="access-token-row"]')).toHaveLength(2);
    expect(forbiddenTermsIn(text)).toEqual([]);
    expect(text).not.toMatch(/\bMCP\b/);
    expect(text).toContain(`${window.location.origin}/api/ia/v1/openapi.json`);
    expect(text).toContain(`${window.location.origin}/api/ia/v1/mcp`);
  });

  it("only offers the action scope when the backend allows it", async () => {
    await render();
    const option = container.querySelector<HTMLOptionElement>('option[value="acciones"]');
    expect(option?.disabled).toBe(true);
    expect(option?.textContent).toBe("Lectura y acciones");
    expect(container.textContent).toContain("Tu usuario solo puede crear tokens de solo lectura.");
  });

  it("shows the secret once and forgets it after confirmation", async () => {
    const secret = runtimeToken();
    api.createAccessToken.mockResolvedValue({ token: secret, token_info: token({ id: "tok-3" }) });
    await render();
    await typeName("Asistente nuevo");
    await click(button("Crear token"));
    expect(api.createAccessToken.mock.calls[0][0]).toEqual({ nombre: "Asistente nuevo", alcance: "lectura", dias: 30 });
    expect(container.textContent).toContain("Copia este token ahora. Por seguridad no volverá a mostrarse.");
    expect(container.querySelector('[data-testid="access-token-secret"]')?.textContent).toBe(secret);
    await click(button("Copiar"));
    expect(clipboard.copyText).toHaveBeenCalledWith(secret);
    await click(button("Ya lo guardé"));
    expect(container.textContent).not.toContain(secret);
    expect(api.listAccessTokens.mock.calls.length).toBeGreaterThanOrEqual(2);
  });

  it("asks for confirmation before revoking", async () => {
    api.revokeAccessToken.mockResolvedValue({ revocado: true, id: "tok-1" });
    const confirm = vi.spyOn(window, "confirm").mockReturnValueOnce(false).mockReturnValueOnce(true);
    await render();
    await click(button("Revocar"));
    expect(api.revokeAccessToken).not.toHaveBeenCalled();
    await click(button("Revocar"));
    expect(confirm).toHaveBeenLastCalledWith(
      "¿Revocar este token? Los asistentes que lo usen perderán acceso de inmediato.",
    );
    expect(api.revokeAccessToken.mock.calls[0][0]).toBe("tok-1");
    expect(container.textContent).toContain("Token revocado.");
  });

  it("labels tokens that lost workspace access and still lets the owner revoke them", async () => {
    api.revokeAccessToken.mockResolvedValue({ revocado: true, id: "tok-4" });
    vi.spyOn(window, "confirm").mockReturnValue(true);
    api.listAccessTokens.mockResolvedValue(
      listing({ tokens: [token({ id: "tok-4", estado: "sin_acceso" }), token({ id: "tok-5", estado: "vencido" })] }),
    );
    await render();
    expect(container.textContent).toContain("Sin acceso al espacio de trabajo");
    const buttons = [...container.querySelectorAll<HTMLButtonElement>("button")].filter(
      (element) => element.textContent?.trim() === "Revocar",
    );
    expect(buttons).toHaveLength(1);
    await click(buttons[0]);
    expect(api.revokeAccessToken.mock.calls[0][0]).toBe("tok-4");
  });

  it("disables creation at the active token limit", async () => {
    api.listAccessTokens.mockResolvedValue(listing({ puede_crear: false }));
    await render();
    await typeName("Otro");
    expect(button("Crear token")?.disabled).toBe(true);
    expect(container.textContent).toContain("Alcanzaste el máximo de 10 tokens activos");
  });

  it("shows a retry when the listing fails", async () => {
    api.listAccessTokens.mockRejectedValue(new Error("fallo"));
    await render();
    expect(container.textContent).toContain("No se pudieron cargar tus tokens.");
    expect(button("Reintentar")).toBeTruthy();
  });
});
