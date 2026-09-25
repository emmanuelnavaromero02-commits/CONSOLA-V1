// @vitest-environment jsdom

import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type {
  SfTalentAnomaliesPayload,
  SfTalentAnomaly,
  SfTalentNineBoxPayload,
  SfTalentOverviewPayload,
  SfTalentRosterPayload,
} from "@/lib/control-room/types";

import { TalentControlRoom } from "./TalentControlRoom";

const clientBoundary = vi.hoisted(() => ({
  overview: vi.fn(),
  nineBox: vi.fn(),
  anomalies: vi.fn(),
  boxRoster: vi.fn(),
}));

vi.mock("@/lib/control-room/client", () => ({
  getSuccessFactorsTalentOverview: clientBoundary.overview,
  getSuccessFactorsTalentNineBox: clientBoundary.nineBox,
  getSuccessFactorsTalentAnomalies: clientBoundary.anomalies,
  getSuccessFactorsTalentBoxRoster: clientBoundary.boxRoster,
}));

const overviewPayload: SfTalentOverviewPayload = {
  readiness: { profiled_employees: 120, calculable_employees: 80 },
};

const nineBoxPayload: SfTalentNineBoxPayload = {
  generated_at: "2026-07-29T18:00:00Z",
  status: "partial",
  totals: { employees: 10, ready: 6, blocked: 0, cells: 9 },
  cells: [
    {
      box_id: "estrella",
      box_label: "Estrella",
      potential_band: "high",
      performance_band: "high",
      movement_action: "Sucesion",
      display_order: 1,
      employee_count: 6,
      ready_count: 6,
      blocked_count: 0,
      status: "ready",
    },
  ],
  blockers: [{ id: "b1", title: "Falta Aspiración en SuccessFactors" }],
};

const anomaliesPayload: SfTalentAnomaliesPayload = {
  generated_at: "2026-07-29T18:05:00Z",
  status: "partial",
  summary: { total: 1, high: 1, recommendation_only: 1 },
  items: [
    {
      id: "a1",
      severity: "high",
      title: "Cobertura de sucesión incompleta",
      recommendation: "Revisar la señal antes de decidir.",
    } as SfTalentAnomaly,
  ],
  blockers: [{ id: "b2", status: "no_permission" }],
};

const rosterPayload: SfTalentRosterPayload = {
  generated_at: "2026-07-29T18:10:00Z",
  status: "ready",
  count: 1,
  box: { box_id: "estrella", box_label: "Estrella", display_order: 1 },
  roster: [
    {
      employee_key: "tal_abc123456789",
      display_name: "Colaborador 6789",
      role: "Manager",
      unit: "People",
      region: "Monterrey",
      fit_band: "high",
      movement_age_bucket: "12-24m",
      data_status: "partial_fields",
    },
  ],
  blockers: [],
};

(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

let container: HTMLDivElement;
let root: Root;

async function flush(times = 5) {
  for (let index = 0; index < times; index += 1) {
    await act(async () => {
      await new Promise((resolve) => setTimeout(resolve, 0));
    });
  }
}

async function renderPage() {
  await act(async () => {
    root.render(<TalentControlRoom />);
  });
  await flush();
}

function button(name: string) {
  return [...container.querySelectorAll("button")].find(
    (candidate) => candidate.textContent?.trim() === name,
  );
}

function alerts() {
  return [...container.querySelectorAll('[role="alert"]')];
}

beforeEach(() => {
  clientBoundary.overview.mockReset().mockResolvedValue(overviewPayload);
  clientBoundary.nineBox.mockReset().mockResolvedValue(nineBoxPayload);
  clientBoundary.anomalies.mockReset().mockResolvedValue(anomaliesPayload);
  clientBoundary.boxRoster.mockReset().mockResolvedValue(rosterPayload);
  container = document.createElement("div");
  document.body.append(container);
  root = createRoot(container);
});

afterEach(async () => {
  await act(async () => root.unmount());
  container.remove();
});

describe("TalentControlRoom roster: error != caja vacía", () => {
  it("muestra alerta con copy seguro y Reintentar cuando la consulta del roster falla", async () => {
    clientBoundary.boxRoster.mockRejectedValueOnce(new Error("network down"));
    await renderPage();

    const alert = alerts().find((node) =>
      node.textContent?.includes("No se pudo cargar el roster de esta caja."),
    );
    expect(alert).toBeDefined();
    expect(alert?.textContent).not.toContain("network down");
    expect(container.textContent).not.toContain("esta caja esta vacia");
    expect(button("Reintentar")).toBeDefined();
  });

  it("Reintentar vuelve a consultar y pinta el roster cuando la red se recupera", async () => {
    clientBoundary.boxRoster.mockRejectedValueOnce(new Error("network down"));
    await renderPage();
    expect(clientBoundary.boxRoster).toHaveBeenCalledTimes(1);

    await act(async () => button("Reintentar")?.click());
    await flush();

    expect(clientBoundary.boxRoster).toHaveBeenCalledTimes(2);
    expect(
      alerts().some((node) => node.textContent?.includes("No se pudo cargar el roster")),
    ).toBe(false);
    expect(container.textContent).toContain("Colaborador 6789");
    expect(container.textContent).toContain("Campos parciales");
  });

  it("la caja vacía real conserva su mensaje y no levanta alerta", async () => {
    clientBoundary.boxRoster.mockResolvedValue({
      status: "empty",
      count: 0,
      box: { box_id: "estrella", box_label: "Estrella", display_order: 1 },
      roster: [],
    } satisfies SfTalentRosterPayload);
    await renderPage();

    expect(container.textContent).toContain("esta caja esta vacia para la corrida actual");
    expect(
      alerts().some((node) => node.textContent?.includes("No se pudo cargar el roster")),
    ).toBe(false);
    expect(button("Reintentar")).toBeUndefined();
  });
});

describe("TalentControlRoom estados payload-level (contrato existente)", () => {
  it("muestra status != ready, blockers y generated_at cuando el payload los trae", async () => {
    await renderPage();

    expect(container.textContent).toContain("Datos parciales");
    expect(container.textContent).not.toContain("partial_fields");
    expect(container.textContent).toContain("Bloqueos reportados por la fuente");
    expect(container.textContent).toContain("Falta Aspiración en SuccessFactors");
    expect(container.textContent).toContain("Requiere permisos OData");
    expect(container.textContent).not.toContain("no_permission");
    expect(container.textContent).toContain("Actualizado:");
    expect(container.querySelector('time[datetime="2026-07-29T18:00:00Z"]')).not.toBeNull();
    expect(container.querySelector('time[datetime="2026-07-29T18:05:00Z"]')).not.toBeNull();
  });

  it("no pinta metadatos cuando el payload no los entrega", async () => {
    clientBoundary.nineBox.mockResolvedValue({
      ...nineBoxPayload,
      status: "ready",
      generated_at: null,
      blockers: [],
    } satisfies SfTalentNineBoxPayload);
    clientBoundary.anomalies.mockResolvedValue({
      ...anomaliesPayload,
      status: "ready",
      generated_at: null,
      blockers: [],
    } satisfies SfTalentAnomaliesPayload);
    clientBoundary.boxRoster.mockResolvedValue({
      ...rosterPayload,
      generated_at: null,
      blockers: [],
    } satisfies SfTalentRosterPayload);
    await renderPage();

    expect(container.textContent).not.toContain("Bloqueos reportados por la fuente");
    expect(container.textContent).not.toContain("Actualizado:");
  });
});

describe("TalentControlRoom ausencia de datos (sin ceros fabricados)", () => {
  it("muestra guiones en el resumen cuando overview/9-box/anomalías fallan", async () => {
    const failure = new Error("gold offline");
    clientBoundary.overview.mockRejectedValue(failure);
    clientBoundary.nineBox.mockRejectedValue(failure);
    clientBoundary.anomalies.mockRejectedValue(failure);
    await renderPage();

    const summary = container.querySelector('[aria-label="Resumen Talento"]');
    expect(summary).not.toBeNull();
    expect(summary?.textContent).toContain("—");
    expect(summary?.textContent).not.toContain("0/0");
    expect(summary?.querySelector("strong")?.textContent).toBe("—");
    expect(alerts().length).toBeGreaterThan(0);
  });

  it("muestra N/D afectados cuando la señal llega sin affected_count", async () => {
    await renderPage();

    expect(container.textContent).toContain("N/D afectados");
    expect(container.textContent).not.toContain("0 afectados");
  });

  it("anuncia la carga del roster con role=status", async () => {
    let resolveRoster!: (value: SfTalentRosterPayload) => void;
    clientBoundary.boxRoster.mockReturnValue(
      new Promise<SfTalentRosterPayload>((resolve) => {
        resolveRoster = resolve;
      }),
    );
    await renderPage();

    const status = [...container.querySelectorAll('[role="status"]')].find((node) =>
      node.textContent?.includes("Cargando roster seguro"),
    );
    expect(status).toBeDefined();

    resolveRoster(rosterPayload);
    await flush();
    expect(container.textContent).toContain("Colaborador 6789");
  });
});
