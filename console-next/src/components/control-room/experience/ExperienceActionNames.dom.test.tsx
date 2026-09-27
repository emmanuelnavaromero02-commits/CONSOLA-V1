// @vitest-environment jsdom

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it } from "vitest";

import type { ControlRoomExperienceV2 } from "@/lib/control-room/experience-contract";
import { useControlRoomExperienceAction } from "@/lib/control-room/use-control-room-experience-action";

import { ExperienceActionDialog } from "./ExperienceActionDialog";
import { ExperienceFact } from "./ExperienceFact";

const handles = ["a".repeat(64), "b".repeat(64), "c".repeat(64)];
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
          severity: "low",
          observed_at: "2026-07-24T00:00:00Z",
          stale: false,
          actions: [
            {
              action_handle: handles[0],
              kind: "followup_task",
              label: "Solicitar revisión de owner",
              enabled: true,
              requires_approval: true,
            },
            {
              action_handle: handles[1],
              kind: "decision_proposal",
              label: "Crear Propuesta de Decisión",
              enabled: true,
              requires_approval: false,
            },
            {
              action_handle: handles[2],
              kind: "exception_approval",
              label: "Aprobar Excepción",
              enabled: false,
              requires_approval: false,
              disabled_reason: "Actualiza los datos antes de continuar.",
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

function Harness() {
  const action = useControlRoomExperienceAction(experience, "workspace-a");
  return (
    <>
      <ExperienceFact fact={experience.sections[0].facts[0]} onAction={action.openAction} />
      <ExperienceActionDialog {...action} />
    </>
  );
}

function accessibleName(button: HTMLButtonElement) {
  return button.getAttribute("aria-label") ?? button.textContent?.trim();
}

function actionButton(name: string) {
  return [...container.querySelectorAll("button")].find(
    (button) => accessibleName(button) === name,
  );
}

function visibleButton(name: string) {
  return [...container.querySelectorAll("button")].find(
    (button) => button.textContent?.trim() === name,
  );
}

beforeEach(() => {
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

describe("Experience action names", () => {
  beforeEach(async () => {
    await act(async () => {
      root.render(
        <QueryClientProvider client={queryClient}>
          <Harness />
        </QueryClientProvider>,
      );
    });
  });

  it("identifies enabled and disabled actions without exposing their handles", () => {
    const buttons = [...container.querySelectorAll("button")];
    const disabled = actionButton("Aprobar Excepción — Cobertura crítica");

    expect(actionButton("Solicitar revisión de owner — Cobertura crítica")).toBeDefined();
    expect(actionButton("Crear Propuesta de Decisión — Cobertura crítica")).toBeDefined();
    expect(buttons.map((button) => button.textContent?.trim())).toEqual([
      "Solicitar revisión de owner",
      "Crear Propuesta de Decisión",
      "Aprobar Excepción",
    ]);
    expect(buttons.some((button) => button.textContent?.includes("Generar preview"))).toBe(
      false,
    );
    expect(disabled?.disabled).toBe(true);
    const reasonId = disabled?.getAttribute("aria-describedby");
    expect(reasonId).toBeTruthy();
    expect(document.getElementById(reasonId ?? "")?.textContent).toBe(
      "Actualiza los datos antes de continuar.",
    );
    handles.forEach((handle) => expect(container.innerHTML).not.toContain(handle));
  });

  it("opens a dialog for the exact selected server action", async () => {
    const owner = actionButton("Solicitar revisión de owner — Cobertura crítica");
    await act(async () => owner?.click());
    let dialog = container.querySelector('[role="dialog"]');
    expect(dialog?.textContent).toContain("Solicitar revisión de owner");
    expect(dialog?.textContent).toContain("Confirmar preview");
    expect(dialog?.textContent).not.toContain("Crear Propuesta de Decisión");

    await act(async () => visibleButton("Cancelar")?.click());
    const proposal = actionButton("Crear Propuesta de Decisión — Cobertura crítica");
    await act(async () => proposal?.click());
    dialog = container.querySelector('[role="dialog"]');
    expect(dialog?.textContent).toContain("Crear Propuesta de Decisión");
    expect(dialog?.textContent).toContain(
      "Se crea una decisión con compromiso a 7 días y pasa al Consejo para aprobación.",
    );
    expect(dialog?.textContent).not.toContain("Solicitar revisión de owner");
    expect(dialog?.textContent).not.toContain("Confirmar preview");
  });
});
