import { readFileSync } from "node:fs";
import { join } from "node:path";

import { describe, expect, it } from "vitest";

/**
 * Frontera del ApprovalGateDialog del Copilot.
 *
 * Esta compuerta es un consentimiento humano para herramientas del copiloto,
 * DISTINTA del circuito de acciones supervisadas de PR-A/PR-B:
 * - el backend decide cuándo se requiere (`requires_approval` en la
 *   respuesta) y aplica la autorización real al recibir el approve;
 * - requiere un clic humano explícito (nunca auto-run);
 * - su endpoint es exclusivo del copiloto
 *   (`/api/copilot/conversations/{cid}/approve/{mid}`) y no toca
 *   `/api/actions/*` ni ningún cliente de acciones supervisadas.
 * Estos asserts de contrato de superficie mantienen esa frontera visible.
 */

const chatLayoutSource = readFileSync(
  join(__dirname, "ChatLayout.tsx"),
  "utf8",
);
const dialogSource = readFileSync(
  join(__dirname, "ApprovalGateDialog.tsx"),
  "utf8",
);
const copilotClientSource = readFileSync(
  join(__dirname, "..", "..", "lib", "copilot", "client.ts"),
  "utf8",
);

describe("frontera Copilot: compuerta de consentimiento ≠ acciones supervisadas", () => {
  it("el chat no importa el cliente de acciones supervisadas", () => {
    expect(chatLayoutSource).not.toContain("supervised-actions");
    expect(dialogSource).not.toContain("supervised-actions");
  });

  it("el approve del copiloto usa exclusivamente su endpoint de conversación", () => {
    expect(copilotClientSource).toContain("/api/copilot/conversations/");
    expect(copilotClientSource).not.toMatch(/\/api\/actions\//);
  });

  it("la compuerta solo se abre cuando el backend lo exige (requires_approval)", () => {
    expect(chatLayoutSource).toMatch(/requires_approval\s*&&/);
  });

  it("no hay auto-run: la aprobación exige un clic humano en el diálogo", () => {
    // El diálogo nunca invoca onApprove por efecto propio; solo el botón.
    expect(dialogSource).not.toMatch(/useEffect[\s\S]{0,400}onApprove\(\)/);
    expect(dialogSource).toMatch(/onClick=\{\(\) => void onApprove\(\)\}/);
  });
});
