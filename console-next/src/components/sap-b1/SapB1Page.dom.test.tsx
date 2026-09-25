// @vitest-environment jsdom

import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type { SapB1BusinessParameters, SapB1Overview } from "@/lib/sap-b1/types";

import { SapB1ControlRoomEntry } from "./SapB1ControlRoomEntry";
import { SapB1Page } from "./SapB1Page";

const state = vi.hoisted(() => ({
  access: { installed: true, canWrite: true, canReadAgents: true as boolean | null, loaded: true },
  queries: {} as Record<string, unknown>,
  views: {} as Record<string, unknown>,
  save: vi.fn(),
  upload: vi.fn(),
  add: vi.fn(),
  remove: vi.fn(),
  monitors: {} as unknown,
}));

vi.mock("@/lib/sap-b1/hooks", () => ({
  useSapB1Access: () => ({
    access: { data: state.access.loaded ? {} : undefined },
    installed: state.access.installed,
    canWrite: state.access.canWrite,
    canReadAgents: state.access.canReadAgents,
  }),
  useSapB1Overview: () => state.queries.overview,
  useSapB1Mapping: () => state.queries.mapping,
  useSapB1LoadReconciliation: () => state.queries.loads,
  useSapB1Indicators: () => state.queries.indicators,
  useSapB1View: (view: string) => state.views[view],
  useSapB1BusinessParameters: () => state.queries.parameters,
  useSapB1Recipients: () => state.queries.recipients,
  useSaveSapB1BusinessParameters: () => ({ mutate: state.save, isPending: false }),
  useUploadSapB1FinanceRun: () => ({ mutate: state.upload, isPending: false }),
  useAddSapB1Recipient: () => ({ mutate: state.add, isPending: false }),
  useRemoveSapB1Recipient: () => ({ mutate: state.remove, isPending: false, variables: undefined }),
}));

vi.mock("@/lib/control-room/use-wisdom-bit-monitors", () => ({
  useWisdomBitMonitors: () => state.monitors,
}));

function query<T>(data: T, overrides: Record<string, unknown> = {}) {
  return { data, isPending: false, isError: false, isFetching: false, error: null, refetch: vi.fn(), ...overrides };
}

const overview: SapB1Overview = {
  installed: "active",
  connection: { present: true },
  parameters: { loaded: true, valid: true, keys_total: 3, keys_set: 2, missing: ["margin_min_pct"], using_default: [], branches: 1, accounts: 0 },
  connector: {
    present: true,
    readable: true,
    age_seconds: 120,
    at: "2026-09-25T13:00:00Z",
    source_ok: true,
    source_ms: 42,
    dialect: "hana",
    companies: ["mx", "us"],
    last_cycle: { status: "success", finished_at: "2026-09-25T12:55:00Z", entities_ok: 48, entities_failed: 0 },
    initial_load: { state: "done", months_done: 24, months_total: 24 },
    next_cycle_at: "2026-09-25T13:15:00Z",
  },
  dags: [{ dag_id: "sap_b1_refresh", present: true, paused: true }],
  digest: { recipients: 1, transport: "sin_configurar", last: null },
};

const parameters: SapB1BusinessParameters = {
  text: "setting:*:*:expiry_red_days=21\nbranch:mx:*:ALM01=Filial Norte\n",
  parameters: [
    { kind: "setting", company: "*", period: "*", key: "expiry_red_days", value: "21" },
    { kind: "branch", company: "mx", period: "*", key: "ALM01", value: "Filial Norte" },
  ],
  catalog: [
    { key: "margin_min_pct", kind: "threshold", unit: "%", default: null, case: "finanzas", label: "Margen bruto mínimo aceptable" },
    { key: "expiry_red_days", kind: "setting", unit: "días", default: "30", case: "ventas", label: "Lotes en rojo" },
  ],
  keys_total: 2,
  keys_set: 1,
  missing: ["margin_min_pct"],
  using_default: [],
  branches: 1,
  accounts: 0,
};

(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

let container: HTMLDivElement;
let root: Root;

async function render(node: React.ReactNode) {
  await act(async () => root.render(node));
}

function tab(name: string) {
  return [...container.querySelectorAll('[role="tab"]')].find((node) => node.textContent === name) as HTMLButtonElement | undefined;
}

function button(name: string) {
  return [...container.querySelectorAll("button")].find((node) => node.textContent?.trim() === name) as HTMLButtonElement | undefined;
}

async function click(node: Element | undefined) {
  expect(node).toBeDefined();
  await act(async () => (node as HTMLElement).click());
}

async function typeInto(input: HTMLInputElement | HTMLTextAreaElement, value: string) {
  const prototype = input instanceof HTMLTextAreaElement ? HTMLTextAreaElement.prototype : HTMLInputElement.prototype;
  await act(async () => {
    Object.getOwnPropertyDescriptor(prototype, "value")?.set?.call(input, value);
    input.dispatchEvent(new Event("input", { bubbles: true }));
  });
}

beforeEach(() => {
  window.history.replaceState(null, "", "/control-room/sap-b1/");
  state.access = { installed: true, canWrite: true, canReadAgents: true, loaded: true };
  state.queries = {
    overview: query(overview),
    mapping: query({
      entities: [
        { entity: "OINV", business_name: "Facturas de clientes", mode: "incremental", date_field: "DocDate", fields: ["DocEntry", "DocDate"], datasets: ["sap_b1_ar_invoice_lines"] },
      ],
    }),
    loads: query([
      { company: "mx", entity: "OINV", dated: true, window_start: "2024-09-01", window_end: "2026-09-01", source_rows: 10, platform_rows: 8, loaded_pct: 80, status: "faltan" },
    ]),
    indicators: query({
      indicators: [
        { id: "margen_bruto", case: "finanzas", name: "Margen bruto", formula: "Venta neta − costo", unit: "moneda local", dimensions: ["empresa"], granularity: "mes", dataset: "sap_b1_margin_kpis_month", filter: "", thresholds: ["margin_min_pct"] },
        { id: "dias_cobertura", case: "compras", name: "Días de cobertura", formula: "Disponible ÷ requerimiento", unit: "días", dimensions: ["empresa"], granularity: "corte diario", dataset: "sap_b1_item_coverage", filter: "", thresholds: [] },
      ],
    }),
    parameters: query(parameters),
    recipients: query({ recipients: ["direccion@cliente.mx"], max: 20, transport: "smtp" }),
  };
  state.views = {
    sap_b1_margin_kpis: query({
      status: "ready",
      metrics: {
        margen_bruto: { status: "ready", group: { value: 1000, pct: 25 }, companies: [], breaches: ["mx: cliente C1 bajo el mínimo"] },
        modelo_entidades: {
          status: "ready",
          entities: [
            { entity: "cliente", company: "grupo", records: 120, identities: 100, shared_identities: 10, complete_records: 90, completeness_pct: 90, orphans: 2, relation_rule: "tiene grupo y vendedor" },
            { entity: "cliente", company: "mx", records: 70, identities: 70, shared_identities: 0, complete_records: 60, completeness_pct: 85.7, orphans: 0, relation_rule: "tiene grupo y vendedor" },
          ],
          breaches: ["cliente: 2 referencias huérfanas en el grupo."],
        },
      },
    }),
    sap_b1_sales_kpis: query({ status: "ready", metrics: {} }),
    sap_b1_expiry_kpis: query({ status: "ready", metrics: {} }),
    sap_b1_supply_kpis: query(undefined, { isPending: true }),
    sap_b1_learning_kpis: query({ status: "degraded", metrics: { aprendizaje: { status: "degraded", notes: ["todavía no hay alertas de SAP Business One en la ventana"], sources: [] } } }),
    sap_b1_semaforo_kpis: query({
      generated_at: "2026-09-25T13:00:00Z",
      metrics: {
        calidad_datos: { status: "ready" },
        caducidad_lotes: { status: "ready", breaches: ["Hay 2 lotes ya vencidos con existencia por 1,000.00."] },
        dias_cobertura: { status: "unavailable", error: "unavailable: sin datos" },
      },
    }),
  };
  state.monitors = {
    agents: query([]),
    ops: query({ summary: {}, agents: [] }),
    monitors: [
      { id: "a1", name: "Semáforo diario SAP Business One", slug: "s", cartridgeId: "sap_b1", wisdomBitId: "WB-B1-SEMAFORO", description: "Semáforo de las 8", active: true, cron: "0 8 * * *", timeZone: "America/Mexico_City" },
    ],
    runsByAgent: { a1: { runs: [], loading: false, failed: false } },
  };
  state.save.mockReset();
  state.upload.mockReset();
  state.add.mockReset();
  state.remove.mockReset();
  container = document.createElement("div");
  document.body.append(container);
  root = createRoot(container);
});

afterEach(async () => {
  await act(async () => root.unmount());
  container.remove();
  vi.restoreAllMocks();
});

describe("SapB1Page", () => {
  it("opens on the rehearsal checklist built from the overview", async () => {
    await render(<SapB1Page />);
    expect(tab("Puesta en marcha")?.getAttribute("aria-selected")).toBe("true");
    const text = container.textContent ?? "";
    expect(text).toContain("Cartucho instalado");
    expect(text).toContain("Faltan 1: margin_min_pct");
    expect(text).toContain("Último heartbeat hace 2 min");
    expect(text).toContain("Completa: 24 meses");
    expect(text).toContain("Extracción SAP B1: pausado");
    expect(text).toContain("Transporte de correo sin configurar");
    expect(text).toContain("Sin envíos todavía");
  });

  it("shows an honest error with a retry instead of an empty checklist", async () => {
    const refetch = vi.fn();
    state.queries.overview = query(undefined, { isError: true, error: Object.assign(new Error("x"), { status: 503 }), refetch });
    await render(<SapB1Page />);
    expect(container.querySelector('[role="alert"]')?.textContent).toContain("El servicio no está disponible por ahora.");
    await click(button("Reintentar"));
    expect(refetch).toHaveBeenCalled();
  });

  it("merges the mapping with the load counts and downloads it as CSV", async () => {
    const createObjectURL = vi.fn(() => "blob:mapeo");
    Object.assign(URL, { createObjectURL, revokeObjectURL: vi.fn() });
    const clicks = vi.spyOn(HTMLAnchorElement.prototype, "click").mockImplementation(() => undefined);
    await render(<SapB1Page />);
    await click(tab("InfoBit"));
    expect(window.location.hash).toBe("#infobit");
    const text = container.textContent ?? "";
    expect(text).toContain("en línea");
    expect(text).toContain("mx, us");
    expect(text).toContain("Facturas de clientes");
    expect(text).toContain("8 / 10 · 80 %");
    expect(text).toContain("faltan filas");
    expect(text).toContain("2024-09-01 a 2026-09-01");
    await click(button("Descargar CSV"));
    expect(createObjectURL).toHaveBeenCalledTimes(1);
    expect(clicks).toHaveBeenCalledTimes(1);
  });

  it("says there is no data yet when the load reconciliation is not published", async () => {
    state.queries.loads = query(null);
    window.history.replaceState(null, "", "#infobit");
    await render(<SapB1Page />);
    expect(container.textContent).toContain("La reconciliación de cargas aún no se publica para este workspace.");
    expect(container.textContent).toContain("sin conteo");
  });

  it("renders the entity model for the group and per company", async () => {
    window.history.replaceState(null, "", "#wisdombit");
    await render(<SapB1Page />);
    expect(container.textContent).toContain("tiene grupo y vendedor");
    expect(container.textContent).toContain("120");
    expect(container.textContent).toContain("2 referencias huérfanas");
    await click(button("mx"));
    expect(container.textContent).toContain("85.7 %");
    expect(container.textContent).not.toContain("120");
  });

  it("groups indicators by case with their current value and app link", async () => {
    window.history.replaceState(null, "", "#knowledgebit");
    await render(<SapB1Page />);
    const text = container.textContent ?? "";
    expect(text).toContain("Finanzas · 1 indicador");
    expect(text).toContain("25 % grupo");
    expect(text).toContain("mx: cliente C1 bajo el mínimo");
    expect(text).toContain("Calculando…");
    expect(text).toContain("Reconciliación con FinanzasSin datos todavía");
    const links = [...container.querySelectorAll("a")].map((node) => node.getAttribute("href"));
    expect(links).toContain("/analytics/viewer?app=sap_b1_margen");
    expect(links).toContain("/analytics/viewer?app=sap_b1_abasto");
  });

  it("shows the agents of the plan and the decision record", async () => {
    window.history.replaceState(null, "", "#agentes");
    await render(<SapB1Page />);
    const text = container.textContent ?? "";
    expect(text).toContain("WB-B1-SEMAFORO");
    expect(text).toContain("Semáforo de las 8:00");
    expect(text).toContain("Horario 08:00 America/Mexico_City");
    expect(text).toContain("Monitores sin registrar en este workspace");
    expect(text).toContain("todavía no hay alertas de SAP Business One en la ventana");
    expect(container.querySelector('a[href="/control-room"]')).not.toBeNull();
  });

  it("paints the semáforo like the 8 AM email", async () => {
    window.history.replaceState(null, "", "#semaforo");
    await render(<SapB1Page />);
    const text = container.textContent ?? "";
    expect(text).toContain("Semáforo SAP Business One: 1 área en rojo");
    const areas = [...container.querySelectorAll('[aria-label="Áreas del semáforo"] > li')].map((node) => node.textContent ?? "");
    expect(areas[0]).toContain("Caducidad de lotes");
    expect(areas[0]).toContain("Rojo");
    expect(areas[1]).toContain("Sin datos");
    expect(areas[1]).toContain("unavailable: sin datos");
    expect(areas[2]).toContain("Verde");
    expect(text).toContain("Transporte de correo sin configurar");
  });

  it("hides Parámetros from users without control_room.write", async () => {
    state.access.canWrite = false;
    window.history.replaceState(null, "", "#parametros");
    await render(<SapB1Page />);
    expect(tab("Parámetros")).toBeUndefined();
    expect(tab("Puesta en marcha")?.getAttribute("aria-selected")).toBe("true");
  });

  it("saves the parameters built from the form and shows 422 messages inline", async () => {
    window.history.replaceState(null, "", "#parametros");
    await render(<SapB1Page />);
    const margin = container.querySelector<HTMLInputElement>("#param-margin_min_pct");
    expect(margin?.getAttribute("placeholder")).toBe("obligatorio");
    await typeInto(margin as HTMLInputElement, "18");
    await click(button("Guardar parámetros"));
    expect(state.save).toHaveBeenCalledTimes(1);
    expect(state.save.mock.calls[0][0]).toBe(
      "threshold:*:*:margin_min_pct=18\nsetting:*:*:expiry_red_days=21\nbranch:mx:*:ALM01=Filial Norte\n",
    );
    const options = state.save.mock.calls[0][1] as { onError: (error: unknown) => void };
    await act(async () => options.onError(Object.assign(new Error("unknown parameter kind 'x'"), { status: 422 })));
    expect(container.querySelector('[role="alert"]')?.textContent).toContain("El texto no pasó la validación: unknown parameter kind 'x'");

    await typeInto(margin as HTMLInputElement, "alto");
    expect(container.textContent).toContain("Corrige antes de guardar");
    expect(button("Guardar parámetros")?.disabled).toBe(true);
  });

  it("uploads the finance run and manages recipients with confirmation", async () => {
    Object.assign(URL, { createObjectURL: vi.fn(() => "blob:plantilla"), revokeObjectURL: vi.fn() });
    vi.spyOn(HTMLAnchorElement.prototype, "click").mockImplementation(() => undefined);
    window.history.replaceState(null, "", "#parametros");
    await render(<SapB1Page />);

    await click(button("Descargar plantilla"));
    const csv = container.querySelector<HTMLTextAreaElement>('textarea[aria-label="Corrida de Finanzas en CSV"]');
    await typeInto(csv as HTMLTextAreaElement, "indicador,empresa,mes,dimension,clave,valor\nmargen_bruto,grupo,2026-08,total,,100\n");
    await click(button("Cargar corrida"));
    expect(state.upload).toHaveBeenCalledWith(
      "indicador,empresa,mes,dimension,clave,valor\nmargen_bruto,grupo,2026-08,total,,100\n",
      expect.any(Object),
    );
    const uploadOptions = state.upload.mock.calls[0][1] as { onError: (error: unknown) => void };
    await act(async () => uploadOptions.onError(Object.assign(new Error("línea 2: mes '2026-13' debe ser AAAA-MM"), { status: 422 })));
    expect(container.textContent).toContain("La corrida no pasó la validación: línea 2: mes '2026-13' debe ser AAAA-MM");

    const confirm = vi.spyOn(window, "confirm").mockReturnValueOnce(false).mockReturnValueOnce(true);
    await click(container.querySelector('button[aria-label="Quitar direccion@cliente.mx"]') ?? undefined);
    expect(state.remove).not.toHaveBeenCalled();
    await click(container.querySelector('button[aria-label="Quitar direccion@cliente.mx"]') ?? undefined);
    expect(confirm).toHaveBeenCalledTimes(2);
    expect(state.remove).toHaveBeenCalledWith("direccion@cliente.mx", expect.any(Object));

    const email = container.querySelector<HTMLInputElement>("#sap-b1-recipient");
    await typeInto(email as HTMLInputElement, "finanzas@cliente.mx");
    await click(button("Agregar"));
    expect(state.add).toHaveBeenCalledWith("finanzas@cliente.mx", expect.any(Object));
  });
});

describe("SapB1ControlRoomEntry", () => {
  it("links to the page only when the workspace has the cartridge", async () => {
    await render(<SapB1ControlRoomEntry />);
    expect(container.querySelector('a[href="/control-room/sap-b1"]')).not.toBeNull();
    state.access.installed = false;
    await render(<SapB1ControlRoomEntry />);
    expect(container.querySelector('a[href="/control-room/sap-b1"]')).toBeNull();
  });
});
