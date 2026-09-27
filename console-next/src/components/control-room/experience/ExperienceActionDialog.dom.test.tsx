// @vitest-environment jsdom

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi, type Mock } from "vitest";

import type { ApiError } from "@/lib/api";
import type { ControlRoomExperienceV2 } from "@/lib/control-room/experience-contract";
import { controlRoomExperienceKey } from "@/lib/control-room/use-control-room-experience";
import { useControlRoomExperienceAction } from "@/lib/control-room/use-control-room-experience-action";
import { controlRoomFreshnessKey } from "@/lib/control-room/use-control-room-live";

import { ControlRoomExperienceContent } from "./ControlRoomExperiencePage";
import { ExperienceActionDialog } from "./ExperienceActionDialog";

(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

const client = vi.hoisted(() => ({
  approve: vi.fn(),
  reopen: vi.fn(),
  proposal: vi.fn(),
  studio: vi.fn(),
  preview: vi.fn(),
}));

vi.mock("@/lib/control-room/experience-client", async (importOriginal) => {
  const actual = await importOriginal<
    typeof import("@/lib/control-room/experience-client")
  >();
  return {
    ...actual,
    approveControlRoomException: client.approve,
    reopenControlRoomException: client.reopen,
    createControlRoomDecisionProposal: client.proposal,
    resolveControlRoomStudioTarget: client.studio,
    previewControlRoomExperienceAction: client.preview,
  };
});

const approveHandle = "a".repeat(64);
const proposalHandle = "b".repeat(64);
const studioHandle = "c".repeat(64);
const reopenHandle = "d".repeat(64);
const REASON = "Proveedor validado por auditoría interna";

const experience: ControlRoomExperienceV2 = {
  schema_version: "control-room-experience/v2",
  generated_at: "2026-09-26T12:00:00Z",
  sections: [
    {
      title: "Compras",
      facts: [
        {
          kind: "anomaly",
          title: "Proveedor duplicado",
          entity_label: "Proveedor 1001",
          severity: "high",
          observed_at: "2026-09-20T00:00:00Z",
          stale: false,
          actions: [
            {
              action_handle: approveHandle,
              kind: "exception_approval",
              label: "Aprobar Excepción",
              enabled: true,
              requires_approval: false,
            },
            {
              action_handle: proposalHandle,
              kind: "decision_proposal",
              label: "Crear Propuesta de Decisión",
              enabled: true,
              requires_approval: false,
            },
            {
              action_handle: studioHandle,
              kind: "studio_adjustment",
              label: "Ajustar en Estudio",
              enabled: true,
              requires_approval: false,
            },
          ],
        },
      ],
    },
  ],
  exceptions: [
    {
      title: "Pedido sin factura",
      observed_at: "2026-09-18T00:00:00Z",
      approved_at: "2026-09-19T10:00:00Z",
      reason: "Aprobado por el comité",
      approved_by_you: false,
      actions: [
        {
          action_handle: reopenHandle,
          kind: "exception_reopen",
          label: "Reabrir hallazgo",
          enabled: true,
          requires_approval: false,
        },
      ],
    },
  ],
};

let container: HTMLDivElement;
let root: Root;
let queryClient: QueryClient;
let navigate: Mock<(href: string) => void>;

function Harness({
  current = experience,
  workspaceId = "workspace-a",
}: {
  current?: ControlRoomExperienceV2;
  workspaceId?: string;
}) {
  const action = useControlRoomExperienceAction(current, workspaceId, navigate);
  return (
    <>
      <ControlRoomExperienceContent
        experience={current}
        refreshing={false}
        refreshFailed={false}
        onRefresh={vi.fn()}
        onAction={action.openAction}
      />
      <ExperienceActionDialog {...action} />
    </>
  );
}

async function render(props: Parameters<typeof Harness>[0] = {}) {
  await act(async () => {
    root.render(
      <QueryClientProvider client={queryClient}>
        <Harness {...props} />
      </QueryClientProvider>,
    );
  });
}

function button(name: string) {
  return [...container.querySelectorAll("button")].find(
    (candidate) => candidate.textContent?.trim() === name,
  );
}

function dialog() {
  return container.querySelector<HTMLElement>('[role="dialog"]');
}

async function typeReason(value: string) {
  const textarea = container.querySelector("textarea");
  if (!textarea) throw new Error("reason field missing");
  await act(async () => {
    const setter = Object.getOwnPropertyDescriptor(
      HTMLTextAreaElement.prototype,
      "value",
    )?.set;
    setter?.call(textarea, value);
    textarea.dispatchEvent(new Event("input", { bubbles: true }));
  });
}

function dialogButton(name: string) {
  return [...(dialog()?.querySelectorAll("button") ?? [])].find(
    (candidate) => candidate.textContent?.trim() === name,
  );
}

beforeEach(() => {
  Object.values(client).forEach((mock) => mock.mockReset());
  navigate = vi.fn<(href: string) => void>();
  queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  container = document.createElement("div");
  document.body.append(container);
  root = createRoot(container);
});

afterEach(async () => {
  await act(async () => root.unmount());
  queryClient.clear();
  container.remove();
});

describe("Aprobar Excepción", () => {
  it("requires a reason, shows a counter and sends the normalized reason once", async () => {
    client.approve.mockResolvedValue({
      action_handle: approveHandle,
      status: "exception_approved",
      reversible: true,
      message: "Hallazgo archivado como excepción aprobada.",
    });
    const invalidate = vi.spyOn(queryClient, "invalidateQueries");
    await render();
    const trigger = button("Aprobar Excepción");
    trigger?.focus();
    await act(async () => trigger?.click());

    expect(dialog()?.getAttribute("aria-modal")).toBe("true");
    expect(dialog()?.textContent).toContain(
      "El hallazgo se archivará como excepción aprobada; puedes reabrirlo.",
    );
    expect(dialog()?.textContent).toContain("Proveedor 1001");
    expect(document.activeElement).toBe(container.querySelector("textarea"));
    const confirm = dialogButton("Aprobar Excepción");
    expect(confirm?.hasAttribute("disabled")).toBe(true);
    await typeReason("corto");
    expect(dialog()?.textContent).toContain("5/10 letras o números · 5/500 caracteres");
    expect(dialog()?.textContent).toContain("Faltan 5 letras o números");
    expect(confirm?.hasAttribute("disabled")).toBe(true);
    await act(async () => confirm?.click());
    expect(client.approve).not.toHaveBeenCalled();

    await typeReason(`  ${REASON.replace(" ", "\n")}  `);
    expect(confirm?.hasAttribute("disabled")).toBe(false);
    await act(async () => {
      confirm?.click();
      confirm?.click();
    });

    expect(client.approve).toHaveBeenCalledTimes(1);
    const [handle, reason, key] = client.approve.mock.calls[0];
    expect(handle).toBe(approveHandle);
    expect(reason).toBe(REASON);
    expect(key).toMatch(/.{8,}/);
    expect(dialog()).toBeNull();
    expect(container.querySelector('[role="status"]')?.textContent).toContain(
      "Hallazgo archivado como excepción aprobada.",
    );
    expect(container.textContent).not.toContain(approveHandle);
    expect(invalidate).toHaveBeenCalledWith({
      queryKey: controlRoomExperienceKey("workspace-a"),
      exact: true,
      refetchType: "active",
    });
    expect(invalidate).toHaveBeenCalledWith({
      queryKey: controlRoomFreshnessKey("workspace-a"),
      exact: true,
      refetchType: "active",
    });
    expect(document.activeElement).toBe(trigger);
  });

  it.each([
    ["zero-width spaces", "\u200b".repeat(10) + "motivo válido"],
    ["a bidi override", "motivo válido \u202e al revés"],
    ["an Arabic letter mark", "motivo válido\u061c"],
    ["a line separator", "motivo válido\u2028otra"],
    ["Hangul fillers", "\u3164".repeat(12)],
    ["an embedded Hangul filler", "motivo\u115fválido"],
    ["a braille blank", "motivo válido\u2800"],
    ["a halfwidth filler", "motivo válido\uffa0"],
  ])("rejects reasons with %s before sending", async (_name, value) => {
    await render();
    await act(async () => button("Aprobar Excepción")?.click());
    await typeReason(value);

    expect(dialogButton("Aprobar Excepción")?.hasAttribute("disabled")).toBe(true);
    expect(dialog()?.textContent).toContain("caracteres invisibles o de control");
    await act(async () => dialogButton("Aprobar Excepción")?.click());
    expect(client.approve).not.toHaveBeenCalled();
  });

  it("requires ten letters or digits, not ten characters, and counts them", async () => {
    await render();
    await act(async () => button("Aprobar Excepción")?.click());
    await typeReason("! ! ! ! ! ! ! ! ! ! ! !");
    expect(dialogButton("Aprobar Excepción")?.hasAttribute("disabled")).toBe(true);
    expect(dialog()?.textContent).toContain("0/10 letras o números · 23/500 caracteres");
    await typeReason("Caso 1: ok");
    expect(dialogButton("Aprobar Excepción")?.hasAttribute("disabled")).toBe(true);
    expect(dialog()?.textContent).toContain("7/10 letras o números · 10/500 caracteres");
    expect(dialog()?.textContent).toContain(
      "Faltan 3 letras o números; los espacios y signos no cuentan.",
    );
    await typeReason("Caso 12345: ok");
    expect(dialogButton("Aprobar Excepción")?.hasAttribute("disabled")).toBe(false);
    expect(dialog()?.textContent).toContain("10/10 letras o números · 14/500 caracteres");
    expect(dialog()?.textContent).not.toContain("Faltan");
  });

  it("keeps focus inside the dialog including the reason field and closes on Escape", async () => {
    await render();
    await act(async () => button("Aprobar Excepción")?.click());
    await typeReason(REASON);
    const textarea = container.querySelector("textarea");
    expect(document.activeElement).toBe(textarea);

    await act(async () =>
      document.dispatchEvent(new KeyboardEvent("keydown", { key: "Tab", shiftKey: true })),
    );
    expect(document.activeElement).toBe(dialogButton("Aprobar Excepción"));
    await act(async () =>
      document.dispatchEvent(new KeyboardEvent("keydown", { key: "Tab" })),
    );
    expect(document.activeElement).toBe(textarea);

    await act(async () =>
      document.dispatchEvent(new KeyboardEvent("keydown", { key: "Escape" })),
    );
    expect(dialog()).toBeNull();
    expect(client.approve).not.toHaveBeenCalled();
  });

  it.each([
    [404, "La acción ya no está disponible."],
    [409, "La acción ya no está disponible."],
    [422, "Revisa el motivo e inténtalo nuevamente."],
    [500, "No se pudo completar la acción."],
  ])("maps HTTP %s to fixed safe copy", async (status, copy) => {
    client.approve.mockRejectedValue(
      Object.assign(new Error(`private ${approveHandle}`), {
        status,
        data: { detail: "item business-1 changed" },
      }) as ApiError,
    );
    await render();
    await act(async () => button("Aprobar Excepción")?.click());
    await typeReason(REASON);
    await act(async () => dialogButton("Aprobar Excepción")?.click());

    const alert = container.querySelector('[role="alert"]');
    expect(alert?.textContent).toContain(copy);
    expect(alert?.textContent).not.toContain("business-1");
    expect(alert?.textContent).not.toContain(approveHandle);
    expect(container.querySelector('[role="status"]')).toBeNull();
  });

  it("never publishes a workspace A result after switching to workspace B", async () => {
    let resolve!: (value: unknown) => void;
    client.approve.mockReturnValue(new Promise((next) => (resolve = next)));
    await render();
    await act(async () => button("Aprobar Excepción")?.click());
    await typeReason(REASON);
    await act(async () => dialogButton("Aprobar Excepción")?.click());
    await render({ workspaceId: "workspace-b" });

    expect(container.querySelector('[role="alert"]')?.textContent).toContain(
      "La acción ya no está disponible.",
    );
    resolve({
      action_handle: approveHandle,
      status: "exception_approved",
      reversible: true,
      message: "Hallazgo archivado.",
    });
    await act(async () => undefined);
    expect(container.querySelector('[role="status"]')).toBeNull();
  });
});

describe("Crear Propuesta de Decisión", () => {
  it("explains the 7-day commitment and links to the council proposal", async () => {
    client.proposal.mockResolvedValue({
      action_handle: proposalHandle,
      status: "proposal_created",
      decision_id: 41,
      href: "/decisions?tab=consejo&propuesta=41",
      message: "Propuesta de decisión creada; pasa al Consejo de Acciones para aprobación.",
    });
    await render();
    await act(async () => button("Crear Propuesta de Decisión")?.click());

    expect(dialog()?.textContent).toContain(
      "Se crea una decisión con compromiso a 7 días y pasa al Consejo para aprobación.",
    );
    expect(container.querySelector("textarea")).toBeNull();
    await act(async () => dialogButton("Crear Propuesta de Decisión")?.click());

    expect(client.proposal).toHaveBeenCalledWith(proposalHandle, expect.any(String));
    const status = container.querySelector('[role="status"]');
    expect(status?.textContent).toContain("Propuesta de decisión creada");
    const link = status?.querySelector("a");
    expect(link?.textContent).toBe("Ver en el Consejo de Acciones");
    expect(link?.getAttribute("href")).toBe("/decisions?tab=consejo&propuesta=41");
  });
});

describe("Ajustar en Estudio", () => {
  it("resolves the server target and navigates without a dialog or write copy", async () => {
    client.studio.mockResolvedValue({
      action_handle: studioHandle,
      href: "/studio?cartridge=sap_b1&tab=capas",
    });
    await render();
    await act(async () => button("Ajustar en Estudio")?.click());

    expect(dialog()).toBeNull();
    expect(client.studio).toHaveBeenCalledWith(studioHandle);
    expect(navigate).toHaveBeenCalledWith("/studio?cartridge=sap_b1&tab=capas");
    expect(container.querySelector('[role="status"]')).toBeNull();
  });

  it("shows fixed copy when the studio target is no longer available", async () => {
    client.studio.mockRejectedValue(Object.assign(new Error("gone"), { status: 404 }));
    await render();
    await act(async () => button("Ajustar en Estudio")?.click());

    expect(navigate).not.toHaveBeenCalled();
    expect(container.querySelector('[role="alert"]')?.textContent).toContain(
      "La acción ya no está disponible.",
    );
  });
});

describe("Excepciones aprobadas", () => {
  it("expands the collapsed list and reopens with a short mandatory reason", async () => {
    client.reopen.mockResolvedValue({
      action_handle: reopenHandle,
      status: "exception_reopened",
      message: "Hallazgo reabierto; vuelve a la lista de hallazgos.",
    });
    await render();
    const toggle = button("Excepciones aprobadas (1)");
    expect(toggle?.getAttribute("aria-expanded")).toBe("false");
    expect(container.textContent).not.toContain("Pedido sin factura");
    await act(async () => toggle?.click());

    expect(toggle?.getAttribute("aria-expanded")).toBe("true");
    expect(container.textContent).toContain("Pedido sin factura");
    expect(container.textContent).toContain("Aprobado por el comité");
    await act(async () => button("Reabrir hallazgo")?.click());
    expect(dialog()?.textContent).toContain(
      "El hallazgo volverá a la lista de hallazgos abiertos.",
    );
    await typeReason("no");
    expect(dialogButton("Reabrir hallazgo")?.hasAttribute("disabled")).toBe(true);
    await typeReason("Revisar");
    await act(async () => dialogButton("Reabrir hallazgo")?.click());

    expect(client.reopen).toHaveBeenCalledWith(reopenHandle, "Revisar", expect.any(String));
    expect(container.querySelector('[role="status"]')?.textContent).toContain(
      "Hallazgo reabierto",
    );
  });

  it("shows only present exception fields", async () => {
    await render({
      current: {
        ...experience,
        exceptions: [
          {
            title: "Pedido sin factura",
            observed_at: "2026-09-18T00:00:00Z",
            approved_by_you: true,
            actions: [],
          },
        ],
      },
    });
    await act(async () => button("Excepciones aprobadas (1)")?.click());

    expect(container.textContent).toContain("Motivo:Sin información");
    expect(container.textContent).toContain("Aprobada:Sin información · por ti");
    expect(button("Reabrir hallazgo")).toBeUndefined();
  });
});
