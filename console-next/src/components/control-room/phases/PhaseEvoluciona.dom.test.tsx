// @vitest-environment jsdom

import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { CANDIDATE_COPY, PhaseEvoluciona } from "./PhaseEvoluciona";

const lessonsBoundary = vi.hoisted(() => ({
  getControlRoomLessons: vi.fn(),
}));

const sapB1Boundary = vi.hoisted(() => ({
  getSapB1View: vi.fn(),
  access: {
    installed: true,
    canWrite: true,
    access: { isSuccess: true, isPending: false },
  },
}));

vi.mock("@/lib/control-room/client", () => ({
  getControlRoomLessons: lessonsBoundary.getControlRoomLessons,
}));

vi.mock("@/lib/sap-b1/client", () => ({
  getSapB1View: sapB1Boundary.getSapB1View,
}));

vi.mock("@/lib/sap-b1/hooks", () => ({
  useSapB1Access: () => sapB1Boundary.access,
}));

(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

let container: HTMLDivElement;
let root: Root;

async function renderPhase() {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  await act(async () => {
    root.render(
      <QueryClientProvider client={queryClient}>
        <PhaseEvoluciona />
      </QueryClientProvider>,
    );
  });
  await act(async () => {
    await new Promise((resolve) => setTimeout(resolve, 0));
  });
}

beforeEach(() => {
  vi.clearAllMocks();
  sapB1Boundary.access = {
    installed: true,
    canWrite: true,
    access: { isSuccess: true, isPending: false },
  };
  lessonsBoundary.getControlRoomLessons.mockResolvedValue({
    lessons: [],
    summary: { total: 0, recent: [] },
  });
  sapB1Boundary.getSapB1View.mockResolvedValue({
    metrics: { aprendizaje: { suggestions: [] } },
  });
  container = document.createElement("div");
  document.body.append(container);
  root = createRoot(container);
});

afterEach(async () => {
  await act(async () => root.unmount());
  container.remove();
});

describe("PhaseEvoluciona", () => {
  it("renders learned rules with the candidate copy and no apply-to-KB write", async () => {
    lessonsBoundary.getControlRoomLessons.mockResolvedValue({
      lessons: [
        {
          anomaly_type: "low_margin",
          rule: "Mantener revision humana",
          confidence: 0.7,
          created_at: "2026-07-01T10:00:00Z",
        },
      ],
      summary: { total: 1, recent: [] },
    });
    await renderPhase();

    expect(container.textContent).toContain("Reglas aprendidas");
    expect(container.textContent).toContain("Mantener revision humana");
    expect(container.textContent).toContain(CANDIDATE_COPY);
    expect(container.textContent).toContain("Confianza registrada: 0.70");
    const labels = [...container.querySelectorAll("button")].map(
      (button) => button.textContent?.trim() ?? "",
    );
    expect(labels).not.toContain("Aplicar");
    expect(labels).not.toContain("Incorporar al paquete");
  });

  it("renders threshold suggestions from the learning view with the candidate copy", async () => {
    sapB1Boundary.getSapB1View.mockResolvedValue({
      metrics: {
        aprendizaje: {
          suggestions: [
            {
              source: "WB-B1-MARGEN",
              thresholds: ["margin_min_pct"],
              reason: "5 de 6 alertas se marcaron como falso positivo.",
            },
          ],
        },
      },
    });
    await renderPhase();

    expect(sapB1Boundary.getSapB1View).toHaveBeenCalledWith("sap_b1_learning_kpis");
    expect(container.textContent).toContain("Sugerencias de umbral");
    expect(container.textContent).toContain("5 de 6 alertas se marcaron como falso positivo.");
    expect(container.textContent).toContain("Umbrales: margin_min_pct");
    expect(container.textContent).toContain(CANDIDATE_COPY);
    const bridge = [...container.querySelectorAll("a")].find(
      (anchor) => anchor.textContent?.trim() === "Ajustar en Parámetros",
    );
    expect(bridge?.getAttribute("href")).toBe("/control-room/sap-b1#parametros");
  });

  it("hides the Parámetros bridge for readers without control_room.write", async () => {
    sapB1Boundary.access = {
      installed: true,
      canWrite: false,
      access: { isSuccess: true, isPending: false },
    };
    sapB1Boundary.getSapB1View.mockResolvedValue({
      metrics: {
        aprendizaje: {
          suggestions: [
            { source: "WB-B1-MARGEN", thresholds: ["margin_min_pct"], reason: "revisar" },
          ],
        },
      },
    });
    await renderPhase();

    expect(container.textContent).toContain(CANDIDATE_COPY);
    expect(container.textContent).not.toContain("Ajustar en Parámetros");
  });

  it("declares the missing SAP Business One installation instead of inventing suggestions", async () => {
    sapB1Boundary.access = {
      installed: false,
      canWrite: false,
      access: { isSuccess: true, isPending: false },
    };
    await renderPhase();

    expect(sapB1Boundary.getSapB1View).not.toHaveBeenCalled();
    expect(container.textContent).toContain(
      "SAP Business One no está instalado en este workspace: sin sugerencias que evaluar.",
    );
  });

  it("shows honest empty states for both sections", async () => {
    await renderPhase();

    expect(container.textContent).toContain("Sin reglas aprendidas todavía.");
    expect(container.textContent).toContain("Sin sugerencias de umbral.");
  });
});
