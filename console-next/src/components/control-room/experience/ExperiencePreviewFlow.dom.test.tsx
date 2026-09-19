// @vitest-environment jsdom

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type { ApiError } from "@/lib/api";
import type { ControlRoomExperienceV2 } from "@/lib/control-room/experience-contract";
import { controlRoomExperienceKey } from "@/lib/control-room/use-control-room-experience";
import { useControlRoomExperiencePreview } from "@/lib/control-room/use-control-room-experience-preview";

import { ControlRoomExperienceContent } from "./ControlRoomExperiencePage";
import { ExperiencePreviewFlow } from "./ExperiencePreviewFlow";

const clientBoundary = vi.hoisted(() => ({ preview: vi.fn() }));

vi.mock("@/lib/control-room/experience-client", async (importOriginal) => {
  const actual = await importOriginal<
    typeof import("@/lib/control-room/experience-client")
  >();
  return { ...actual, previewControlRoomExperienceAction: clientBoundary.preview };
});

const actionHandle = "a".repeat(64);
const success = {
  action_handle: actionHandle,
  operation: "preview" as const,
  status: "generated" as const,
  message: "Preview generado; no se ejecuto ningun cambio externo." as const,
};
const experience: ControlRoomExperienceV2 = {
  schema_version: "control-room-experience/v2",
  generated_at: "2026-07-25T12:30:00Z",
  sections: [
    {
      title: "Performance",
      facts: [
        {
          kind: "kpi",
          title: "Cobertura crítica",
          entity_label: "Región Norte",
          severity: "low",
          observed_at: "2026-07-24T00:00:00Z",
          stale: false,
          actions: [
            {
              action_handle: actionHandle,
              label: "Solicitar revisión de owner",
              enabled: true,
              requires_approval: true,
            },
          ],
        },
      ],
    },
  ],
};

let container: HTMLDivElement;
let root: Root;
let queryClient: QueryClient;

function Harness({
  currentExperience = experience,
  workspaceId = "workspace-a",
}: {
  currentExperience?: ControlRoomExperienceV2;
  workspaceId?: string;
}) {
  const preview = useControlRoomExperiencePreview(currentExperience, workspaceId);
  return (
    <>
      <ControlRoomExperienceContent
        experience={currentExperience}
        refreshing={false}
        refreshFailed={false}
        onRefresh={vi.fn()}
        onPreviewAction={preview.openPreview}
      />
      <ExperiencePreviewFlow {...preview} />
    </>
  );
}

async function renderHarness(props = {}) {
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

beforeEach(() => {
  clientBoundary.preview.mockReset();
  queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  container = document.createElement("div");
  document.body.append(container);
  root = createRoot(container);
});

afterEach(async () => {
  await act(async () => root.unmount());
  queryClient.clear();
  container.remove();
  localStorage.clear();
  sessionStorage.clear();
});

describe("Experience preview confirmation", () => {
  it("opens without POST, exposes only business copy and restores focus on cancel", async () => {
    await renderHarness();
    const trigger = button("Generar preview");
    expect(trigger).toBeDefined();
    trigger?.focus();

    await act(async () => trigger?.click());
    const dialog = container.querySelector('[role="dialog"]');
    expect(dialog?.getAttribute("aria-modal")).toBe("true");
    expect(dialog?.getAttribute("aria-labelledby")).toBeTruthy();
    expect(dialog?.getAttribute("aria-describedby")).toBeTruthy();
    expect(container.textContent).toContain("Cobertura crítica");
    expect(container.textContent).toContain("Región Norte");
    expect(container.textContent).toContain("Solicitar revisión de owner");
    expect(container.textContent).toContain("una fase posterior requerirá aprobación");
    expect(document.activeElement).toBe(button("Cancelar"));
    expect(clientBoundary.preview).not.toHaveBeenCalled();
    expect(container.innerHTML).not.toContain(actionHandle);
    expect(window.location.href).not.toContain(actionHandle);

    await act(async () =>
      document.dispatchEvent(
        new KeyboardEvent("keydown", { key: "Tab", shiftKey: true }),
      ),
    );
    expect(document.activeElement).toBe(button("Confirmar preview"));
    await act(async () =>
      document.dispatchEvent(new KeyboardEvent("keydown", { key: "Tab" })),
    );
    expect(document.activeElement).toBe(button("Cancelar"));

    await act(async () => button("Cancelar")?.click());
    expect(container.querySelector('[role="dialog"]')).toBeNull();
    expect(document.activeElement).toBe(trigger);
    expect(clientBoundary.preview).not.toHaveBeenCalled();
  });

  it("Escape closes only before submit and restores the original CTA", async () => {
    let resolvePreview!: (value: typeof success) => void;
    clientBoundary.preview.mockReturnValue(
      new Promise((resolve) => {
        resolvePreview = resolve;
      }),
    );
    await renderHarness();
    const trigger = button("Generar preview");
    trigger?.focus();
    await act(async () => trigger?.click());

    await act(async () =>
      document.dispatchEvent(new KeyboardEvent("keydown", { key: "Escape" })),
    );
    expect(container.querySelector('[role="dialog"]')).toBeNull();
    expect(clientBoundary.preview).not.toHaveBeenCalled();
    expect(document.activeElement).toBe(trigger);

    await act(async () => trigger?.click());
    await act(async () => button("Confirmar preview")?.click());
    await act(async () =>
      document.dispatchEvent(new KeyboardEvent("keydown", { key: "Escape" })),
    );
    expect(container.querySelector('[role="dialog"]')).not.toBeNull();
    resolvePreview(success);
    await act(async () => undefined);
  });

  it("submits exactly once on a double click and renders only the public success", async () => {
    let resolvePreview!: (value: typeof success) => void;
    clientBoundary.preview.mockReturnValue(
      new Promise((resolve) => {
        resolvePreview = resolve;
      }),
    );
    const invalidate = vi.spyOn(queryClient, "invalidateQueries");
    await renderHarness();
    await act(async () => button("Generar preview")?.click());

    const confirm = button("Confirmar preview");
    await act(async () => {
      confirm?.click();
      confirm?.click();
    });
    expect(clientBoundary.preview).toHaveBeenCalledTimes(1);
    expect(clientBoundary.preview).toHaveBeenCalledWith(actionHandle);
    expect(button("Cancelar")?.hasAttribute("disabled")).toBe(true);

    resolvePreview(success);
    await act(async () => undefined);
    const status = container.querySelector('[role="status"]');
    expect(status?.textContent).toContain(success.message);
    expect(status?.textContent).not.toContain(actionHandle);
    expect(container.textContent).not.toMatch(/Aprobar|Ejecutar|Sí, ejecutar/);
    expect(invalidate).toHaveBeenCalledWith({
      queryKey: controlRoomExperienceKey("workspace-a"),
      exact: true,
      refetchType: "active",
    });
  });

  it("keeps the success flow free of approval or execution copy with a narrative", async () => {
    clientBoundary.preview.mockResolvedValue(success);
    const withNarrative = structuredClone(experience);
    withNarrative.sections[0].facts[0].narrative = {
      status: "ready",
      explanation: "La cobertura bajó tres puntos frente al mes anterior.",
      recommendation: "Revisar con el owner de la región las vacantes abiertas.",
      confidence_label: "media",
      confidence_reason: "El dato cubre solo dos de las tres regiones.",
      basis_note: "Basado en el agregado mensual de posiciones críticas.",
      evidence_note: "Evidencia verificada el 24 de julio.",
      limitations: ["No incluye contratistas."],
    };
    await renderHarness({ currentExperience: withNarrative });
    expect(container.textContent).toContain(
      "Revisar con el owner de la región las vacantes abiertas.",
    );
    expect(container.textContent).toContain(
      "Solo recomendación: nada se aplica automáticamente.",
    );

    await act(async () => button("Generar preview")?.click());
    expect(container.textContent).not.toMatch(/Aprobar|Ejecutar|Sí, ejecutar/);
    await act(async () => button("Confirmar preview")?.click());
    await act(async () => undefined);

    expect(clientBoundary.preview).toHaveBeenCalledTimes(1);
    expect(container.querySelector('[role="status"]')?.textContent).toContain(
      success.message,
    );
    expect(container.textContent).not.toMatch(/Aprobar|Ejecutar|Sí, ejecutar/);
  });

  it("keeps disabled actions inert and shows the exact server reason", async () => {
    const disabled = structuredClone(experience);
    disabled.sections[0].facts[0].actions[0] = {
      ...disabled.sections[0].facts[0].actions[0],
      enabled: false,
      disabled_reason: "Actualiza los datos antes de continuar.",
    };
    await renderHarness({ currentExperience: disabled });
    const trigger = button("Generar preview");

    expect(trigger?.hasAttribute("disabled")).toBe(true);
    expect(container.textContent).toContain(
      "Actualiza los datos antes de continuar.",
    );
    await act(async () => trigger?.click());
    expect(container.querySelector('[role="dialog"]')).toBeNull();
    expect(clientBoundary.preview).not.toHaveBeenCalled();
  });
});

describe("Experience preview safe errors", () => {
  it.each([401, 403, 404, 409, 422])(
    "never exposes backend detail for HTTP %s",
    async (status) => {
      const error = Object.assign(new Error(`private ${actionHandle}`), {
        status,
        data: { detail: `binding ${actionHandle}` },
      }) as ApiError;
      clientBoundary.preview.mockRejectedValue(error);
      await renderHarness();
      await act(async () => button("Generar preview")?.click());
      await act(async () => button("Confirmar preview")?.click());

      const alert = container.querySelector('[role="alert"]');
      expect(alert).not.toBeNull();
      expect(alert?.textContent).not.toContain("private");
      expect(alert?.textContent).not.toContain("binding");
      expect(alert?.textContent).not.toContain(actionHandle);
      expect(container.querySelector('[role="dialog"]')).toBeNull();
      expect(container.querySelector('[role="status"]')).toBeNull();
    },
  );

  it("fails stale without POST when refresh removes the selected action", async () => {
    const invalidate = vi.spyOn(queryClient, "invalidateQueries");
    await renderHarness();
    await act(async () => button("Generar preview")?.click());
    const withoutActions = structuredClone(experience);
    withoutActions.sections[0].facts[0].actions = [];
    await renderHarness({ currentExperience: withoutActions });
    await act(async () => button("Confirmar preview")?.click());

    expect(clientBoundary.preview).not.toHaveBeenCalled();
    expect(container.querySelector('[role="alert"]')?.textContent).toContain(
      "La acción ya no está disponible. Actualiza la información e inténtalo nuevamente.",
    );
    expect(invalidate).toHaveBeenCalledWith({
      queryKey: controlRoomExperienceKey("workspace-a"),
      exact: true,
      refetchType: "active",
    });
  });

  it("maps invalid contracts to fixed safe copy", async () => {
    clientBoundary.preview.mockRejectedValue(
      new Error(`invalid contract ${actionHandle}`),
    );
    await renderHarness();
    await act(async () => button("Generar preview")?.click());
    await act(async () => button("Confirmar preview")?.click());

    const alert = container.querySelector('[role="alert"]');
    expect(alert?.textContent).toContain("No se pudo generar el preview.");
    expect(alert?.textContent).not.toContain("invalid contract");
    expect(alert?.textContent).not.toContain(actionHandle);
    expect(container.querySelector('[role="status"]')).toBeNull();
  });
});
