// @vitest-environment jsdom

import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type { CouncilProposal } from "@/lib/decisions/council-client";
import { toApiError } from "@/lib/api";

import { ActionCouncil, COUNCIL_EMPTY_TITLE, COUNCIL_FOCUS_MISSING } from "./ActionCouncil";
import { APPROVE_EXPLANATION } from "./CouncilDialogs";

const council = vi.hoisted(() => ({
  getActionCouncil: vi.fn(),
  approveCouncilProposal: vi.fn(),
  discardCouncilProposal: vi.fn(),
  renewCouncilProposal: vi.fn(),
}));
const freshness = vi.hoisted(() => ({ getControlRoomFreshness: vi.fn() }));

vi.mock("@/lib/decisions/council-client", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/decisions/council-client")>();
  return { ...actual, ...council };
});
vi.mock("@/lib/control-room/experience-client", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/control-room/experience-client")>();
  return { ...actual, ...freshness };
});
vi.mock("next/link", () => ({
  default: ({ href, children, ...rest }: { href: string; children: React.ReactNode }) => (
    <a href={href} {...rest}>
      {children}
    </a>
  ),
}));

(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

let container: HTMLDivElement;
let root: Root;

const SYSTEM: CouncilProposal = {
  proposal_id: "a".repeat(64),
  origin: "system",
  authored_by_you: false,
  title: "Empleado terminado con acceso activo",
  section_title: "Personas",
  severity: "critical",
  observed_at: "2026-09-20T00:00:00Z",
  state: "pending_approval",
  impact: {
    kind: "money",
    value: 12600,
    currency: "USD",
    basis: "rule",
    formula: "monthly_cost_usd * 3 meses de exposicion",
    label: "Regla: costo mensual × 3 meses",
  },
  evidence: [{ label: "Entidad", value: "Ana Gómez" }],
  can_approve: true,
  can_discard: true,
  can_renew: false,
};

const OWN: CouncilProposal = {
  proposal_id: "b".repeat(64),
  origin: "person",
  authored_by_you: true,
  decision_id: 41,
  title: "Proveedor duplicado",
  severity: "medium",
  state: "needs_other_approver",
  impact: { kind: "none", label: "Sin estimación" },
  evidence: [],
  can_approve: false,
  can_discard: false,
  can_renew: false,
  disabled_reason: "Requiere la aprobación de otra persona del equipo.",
};

const EXPIRED: CouncilProposal = {
  ...OWN,
  proposal_id: "c".repeat(64),
  decision_id: 42,
  title: "Margen bajo en proyecto",
  state: "expired",
  can_renew: true,
  disabled_reason: "La propuesta venció; su autor puede renovarla.",
};

function payload(proposals: CouncilProposal[]) {
  return {
    schema_version: "control-room-council/v1" as const,
    generated_at: "2026-09-26T10:00:00Z",
    proposals,
  };
}

async function flush() {
  for (let index = 0; index < 5; index += 1) {
    await act(async () => {
      await new Promise((resolve) => setTimeout(resolve, 0));
    });
  }
}

async function render(focusDecisionId: number | null = null) {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  await act(async () => {
    root.render(
      <QueryClientProvider client={queryClient}>
        <ActionCouncil focusDecisionId={focusDecisionId} />
      </QueryClientProvider>,
    );
  });
  await flush();
}

function buttons(label: string) {
  return [...container.querySelectorAll("button")].filter(
    (node) => node.textContent?.trim() === label,
  );
}

function dialog() {
  return document.querySelector('[role="dialog"]');
}

async function click(node: Element | undefined) {
  expect(node).toBeDefined();
  await act(async () => (node as HTMLElement).click());
  await flush();
}

async function type(textarea: HTMLTextAreaElement, value: string) {
  const setter = Object.getOwnPropertyDescriptor(HTMLTextAreaElement.prototype, "value")?.set;
  await act(async () => {
    setter?.call(textarea, value);
    textarea.dispatchEvent(new Event("input", { bubbles: true }));
  });
}

beforeEach(() => {
  vi.clearAllMocks();
  freshness.getControlRoomFreshness.mockResolvedValue({
    schema_version: "control-room-freshness/v1",
    fingerprint: "f".repeat(64),
    checked_at: "2026-09-26T10:00:00Z",
    data_refreshed_at: null,
  });
  council.getActionCouncil.mockResolvedValue(payload([SYSTEM, OWN, EXPIRED]));
  container = document.createElement("div");
  document.body.append(container);
  root = createRoot(container);
});

afterEach(async () => {
  await act(async () => root.unmount());
  container.remove();
  document.body.style.overflow = "";
});

describe("ActionCouncil", () => {
  it("shows the shared empty state with a way back to the Control Room", async () => {
    council.getActionCouncil.mockResolvedValue(payload([]));
    await render();
    const empty = container.querySelector('[data-testid="empty-state"]');
    expect(empty?.textContent).toContain(COUNCIL_EMPTY_TITLE);
    expect(empty?.querySelector("a")?.getAttribute("href")).toBe("/control-room");
  });

  it("labels origin, impact with its formula and the reason an action is unavailable", async () => {
    await render();
    const [system, own, expired] = [...container.querySelectorAll("article")];
    expect(system.textContent).toContain("Sugerida por el sistema");
    expect(system.textContent).toContain("Impacto estimado:");
    expect(system.textContent).toContain("Regla: costo mensual × 3 meses");
    expect(system.textContent).toContain("Fórmula: monthly_cost_usd * 3 meses de exposicion");
    expect(own.textContent).toContain("Propuesta por ti");
    expect(own.textContent).toContain("Impacto: Sin estimación");
    expect(own.textContent).toContain("Requiere la aprobación de otra persona del equipo.");
    const ownApprove = [...own.querySelectorAll("button")].find(
      (node) => node.textContent?.trim() === "Aprobar e implementar",
    );
    expect(ownApprove?.disabled).toBe(true);
    expect(own.textContent).not.toContain("Descartar con motivo");
    expect(expired.textContent).toContain("Renovar propuesta");
    expect(container.textContent).not.toMatch(/cartucho/i);
  });

  it("approves once, explains that no external system changes and refreshes", async () => {
    let resolve: (value: unknown) => void = () => undefined;
    council.approveCouncilProposal.mockReturnValue(
      new Promise((done) => {
        resolve = done;
      }),
    );
    await render();
    await click(buttons("Aprobar e implementar")[0]);
    expect(dialog()?.textContent).toContain(APPROVE_EXPLANATION);
    const confirm = [...(dialog()?.querySelectorAll("button") ?? [])].find(
      (node) => node.textContent?.trim() === "Aprobar e implementar",
    );
    await act(async () => {
      confirm?.click();
      confirm?.click();
    });
    expect(council.approveCouncilProposal).toHaveBeenCalledTimes(1);
    expect(council.approveCouncilProposal.mock.calls[0][0]).toBe(SYSTEM.proposal_id);
    expect(String(council.approveCouncilProposal.mock.calls[0][1]).length).toBeGreaterThanOrEqual(8);
    expect(dialog()?.getAttribute("aria-busy")).toBe("true");
    await act(async () => {
      resolve({
        status: "approved_with_followup",
        decision_id: 77,
        followup_created: true,
        message: "Decisión aprobada y tarea de seguimiento interna registrada.",
      });
    });
    await flush();
    expect(dialog()).toBeNull();
    const status = container.querySelector('[role="status"]');
    expect(status?.textContent).toContain("tarea de seguimiento interna registrada");
    expect(status?.querySelector("a")).toBeNull();
    expect(council.getActionCouncil.mock.calls.length).toBeGreaterThanOrEqual(2);
  });

  it("requires a visible reason before discarding and closes with Escape", async () => {
    council.discardCouncilProposal.mockResolvedValue({
      status: "discarded",
      message: "Propuesta descartada; el motivo quedó registrado.",
    });
    await render();
    await click(buttons("Descartar con motivo")[0]);
    await act(async () => {
      document.dispatchEvent(new KeyboardEvent("keydown", { key: "Escape" }));
    });
    expect(dialog()).toBeNull();

    await click(buttons("Descartar con motivo")[0]);
    const textarea = dialog()?.querySelector("textarea") as HTMLTextAreaElement;
    const confirm = () =>
      [...(dialog()?.querySelectorAll("button") ?? [])].find(
        (node) => node.textContent?.trim() === "Descartar con motivo",
      ) as HTMLButtonElement;
    await type(textarea, "corto");
    expect(confirm().disabled).toBe(true);
    await type(textarea, "Motivo\u200b con carácter invisible");
    expect(confirm().disabled).toBe(true);
    expect(dialog()?.textContent).toContain("caracteres invisibles");
    await type(textarea, "Motivo \u3164\u3164 de relleno visible");
    expect(confirm().disabled).toBe(true);
    await type(textarea, "- - - - - - - - - - . . .");
    expect(confirm().disabled).toBe(true);
    expect(dialog()?.textContent).toContain("0/10 letras o números · 25/500 caracteres");
    expect(dialog()?.textContent).toContain(
      "Faltan 10 letras o números; los espacios y signos no cuentan.",
    );
    await type(textarea, "  La causa ya\nse corrigió en origen  ");
    expect(confirm().disabled).toBe(false);
    expect(dialog()?.textContent).toContain("10/10 letras o números · 33/500 caracteres");
    expect(dialog()?.textContent).not.toContain("Faltan");
    await click(confirm());
    expect(council.discardCouncilProposal).toHaveBeenCalledWith(
      SYSTEM.proposal_id,
      "La causa ya se corrigió en origen",
      expect.any(String),
    );
    expect(container.querySelector('[role="status"]')?.textContent).toContain(
      "el motivo quedó registrado",
    );
    expect(container.querySelector('[role="status"] a')).toBeNull();
  });

  it("explains a source change without claiming success", async () => {
    council.approveCouncilProposal.mockRejectedValue(
      toApiError("conflict", 409, { detail: { code: "proposal_source_changed" } }),
    );
    await render();
    await click(buttons("Aprobar e implementar")[0]);
    const confirm = [...(dialog()?.querySelectorAll("button") ?? [])].find(
      (node) => node.textContent?.trim() === "Aprobar e implementar",
    );
    await click(confirm);
    const alert = container.querySelector('[role="alert"]');
    expect(alert?.textContent).toContain("Los datos de origen cambiaron");
    expect(container.querySelector('[role="status"] a')).toBeNull();
  });

  it("renews through its own confirmation and highlights the linked proposal", async () => {
    council.renewCouncilProposal.mockResolvedValue({
      status: "renewed",
      decision_id: 42,
      message: "Propuesta renovada; espera la aprobación de otra persona del equipo.",
    });
    await render(42);
    const highlighted = container.querySelector("#propuesta-42");
    expect(highlighted?.className).toContain("ring-2");
    await click(buttons("Renovar propuesta")[0]);
    const confirm = [...(dialog()?.querySelectorAll("button") ?? [])].find(
      (node) => node.textContent?.trim() === "Renovar propuesta",
    );
    await click(confirm);
    expect(council.renewCouncilProposal).toHaveBeenCalledWith(EXPIRED.proposal_id);
  });

  it("says when the linked proposal is no longer pending", async () => {
    await render(999);
    expect(container.textContent).toContain(COUNCIL_FOCUS_MISSING);
  });
});
