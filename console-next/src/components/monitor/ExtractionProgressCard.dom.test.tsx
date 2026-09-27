// @vitest-environment jsdom

import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type { ExtractionProgressRun } from "@/lib/monitor/extraction-progress";

import { ExtractionProgressCard } from "./ExtractionProgressCard";

let container: HTMLDivElement;
let root: Root;

function run(overrides: Partial<ExtractionProgressRun> = {}): ExtractionProgressRun {
  return {
    run_id: "manual__1",
    entity: "User",
    phase: "extracting",
    phase_index: 2,
    status: "running",
    terminal: false,
    outcome: null,
    record_count: 340,
    entities_done: null,
    entities_total: null,
    error: null,
    recovered: false,
    stalled: false,
    started_at: "2026-09-26T12:00:00Z",
    finished_at: null,
    ...overrides,
  };
}

async function render(node: React.ReactNode) {
  await act(async () => {
    root.render(node);
  });
}

function steps() {
  return [...container.querySelectorAll("ol li")].map((item) => ({
    text: item.textContent ?? "",
    current: item.getAttribute("aria-current"),
  }));
}

beforeEach(() => {
  (globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;
  container = document.createElement("div");
  document.body.append(container);
  root = createRoot(container);
});

afterEach(async () => {
  await act(async () => root.unmount());
  container.remove();
});

describe("ExtractionProgressCard", () => {
  it("renders a four-step stepper with a live region and the run id", async () => {
    await render(<ExtractionProgressCard title="Extracción de User" runId="manual__1" run={run()} />);
    const items = steps();
    expect(items.map((item) => item.text.replace(/ \(.+\)$/, ""))).toEqual([
      "Conexión",
      "Extracción",
      "Vistas de negocio",
      "Listo",
    ]);
    expect(items[0].text).toContain("completado");
    expect(items[1].current).toBe("step");
    expect(items[2].text).toContain("pendiente");
    const live = container.querySelector('[aria-live="polite"]');
    expect(live?.textContent).toContain("Extrayendo entidades seleccionadas... (340 registros descargados)");
    expect(container.textContent).toContain("manual__1");
    expect(container.textContent).toContain("En ejecución");
  });

  it("waits honestly before the run is recorded", async () => {
    await render(<ExtractionProgressCard title="Extracción de User" runId="manual__1" loading />);
    expect(container.textContent).toContain("Esperando el registro de la corrida…");
    expect(container.querySelector('[aria-current="step"]')).toBeNull();
  });

  it("marks the failing phase and shows the error", async () => {
    await render(
      <ExtractionProgressCard
        title="Extracción de User"
        runId="manual__1"
        run={run({ phase: "connecting", phase_index: 1, status: "failed", terminal: true, outcome: "failed", error: "Credenciales inválidas", record_count: null })}
      />,
    );
    expect(steps()[0].text).toContain("con error");
    expect(container.textContent).toContain("No se pudo completar");
    expect(container.textContent).toContain("Credenciales inválidas");
  });

  it("completes every step when the data is ready and flags warnings", async () => {
    await render(
      <ExtractionProgressCard
        title="Extracción completa"
        runId="manual__all"
        run={run({ phase: "ready", phase_index: 4, status: "partial", terminal: true, outcome: "partial", error: "1 entidad sin permisos" })}
      />,
    );
    expect(steps().every((item) => item.text.includes("completado"))).toBe(true);
    expect(container.textContent).toContain("Completado con advertencias");
    expect(container.textContent).toContain("1 entidad sin permisos");
  });

  it("offers the stuck-runs review only for a stalled run", async () => {
    const open = vi.fn();
    await render(
      <ExtractionProgressCard
        title="Extracción de User"
        runId="manual__1"
        run={run({ phase: "connecting", phase_index: 1, status: "queued", record_count: null, stalled: true })}
        onOpenStuckRuns={open}
      />,
    );
    expect(container.textContent).toContain("La ejecución no avanza");
    const button = [...container.querySelectorAll("button")].find((item) => item.textContent?.includes("Revisar corridas atascadas"));
    await act(async () => {
      button?.dispatchEvent(new MouseEvent("click", { bubbles: true }));
    });
    expect(open).toHaveBeenCalledTimes(1);

    await render(<ExtractionProgressCard title="Extracción de User" runId="manual__1" run={run()} onOpenStuckRuns={open} />);
    expect(container.textContent).not.toContain("Revisar corridas atascadas");
  });

  it("keeps logs under technical details and reports automation and recovery", async () => {
    await render(
      <ExtractionProgressCard
        title="Extracción de User"
        runId="manual__1"
        run={run({ status: "failed", terminal: true, outcome: "failed", recovered: true, error: "Recuperado por el sistema: tiempo de espera agotado" })}
        automationMessage="El proceso de extracción estaba en pausa en Airflow; se reactivó para ejecutar tu solicitud."
        details={<p>linea de log</p>}
      />,
    );
    const details = container.querySelector("details");
    expect(details?.querySelector("summary")?.textContent).toBe("Detalles técnicos");
    expect(details?.textContent).toContain("linea de log");
    expect(container.textContent).toContain("se reactivó");
    expect(container.textContent).toContain("La plataforma cerró esta corrida");
  });
});
