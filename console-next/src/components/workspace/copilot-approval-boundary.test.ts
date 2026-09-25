import { readFileSync } from "node:fs";
import { join } from "node:path";

import { describe, expect, it } from "vitest";


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
    expect(dialogSource).not.toMatch(/useEffect[\s\S]{0,400}onApprove\(\)/);
    expect(dialogSource).toMatch(/onClick=\{\(\) => void onApprove\(\)\}/);
  });
});
