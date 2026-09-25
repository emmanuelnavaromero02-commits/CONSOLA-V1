import { beforeEach, describe, expect, it, vi } from "vitest";

import { api, toApiError } from "@/lib/api";

import {
  addSapB1Recipient,
  getLoadReconciliation,
  getSapB1Indicators,
  getSapB1Mapping,
  getSapB1Overview,
  getSapB1View,
  loadReconciliationPath,
  recipientPath,
  removeSapB1Recipient,
  saveSapB1BusinessParameters,
  sapB1ViewPath,
  SAP_B1_PATHS,
  uploadSapB1FinanceRun,
} from "./client";

vi.mock("@/lib/api", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/api")>();
  return {
    ...actual,
    api: { get: vi.fn(), post: vi.fn(), put: vi.fn(), delete: vi.fn() },
  };
});

function ok<T>(data: T) {
  return { data, status: 200, headers: new Headers(), requestId: "req" };
}

describe("sap-b1 client", () => {
  beforeEach(() => {
    vi.mocked(api.get).mockReset();
    vi.mocked(api.post).mockReset();
    vi.mocked(api.put).mockReset();
    vi.mocked(api.delete).mockReset();
  });

  it("reads the overview, mapping and indicators from the console SAP B1 API", async () => {
    vi.mocked(api.get)
      .mockResolvedValueOnce(ok({ installed: "active" }))
      .mockResolvedValueOnce(ok({}))
      .mockResolvedValueOnce(ok({ indicators: [{ id: "margen_bruto" }] }));

    await expect(getSapB1Overview()).resolves.toEqual({ installed: "active" });
    await expect(getSapB1Mapping()).resolves.toEqual({ entities: [] });
    await expect(getSapB1Indicators()).resolves.toEqual({ indicators: [{ id: "margen_bruto" }] });
    expect(vi.mocked(api.get).mock.calls.map(([path]) => path)).toEqual([
      "/api/sap-b1/overview",
      "/api/sap-b1/mapping",
      "/api/sap-b1/indicators",
    ]);
  });

  it("builds the Control Room view path with a bounded top_n", async () => {
    expect(sapB1ViewPath("sap_b1_margin_kpis")).toBe("/api/control-room/sap-b1/views/sap_b1_margin_kpis");
    expect(sapB1ViewPath("sap_b1_sales_kpis", 3)).toBe("/api/control-room/sap-b1/views/sap_b1_sales_kpis?top_n=3");
    expect(sapB1ViewPath("sap_b1_sales_kpis", 40)).toBe("/api/control-room/sap-b1/views/sap_b1_sales_kpis?top_n=10");
    vi.mocked(api.get).mockResolvedValueOnce(ok({ status: "ready", metrics: {} }));
    await expect(getSapB1View("sap_b1_semaforo_kpis")).resolves.toEqual({ status: "ready", metrics: {} });
    expect(api.get).toHaveBeenCalledWith("/api/control-room/sap-b1/views/sap_b1_semaforo_kpis");
  });

  it("reads the load reconciliation gold dataset and treats an unpublished dataset as no data", async () => {
    expect(loadReconciliationPath()).toBe("/api/data/sap_b1_load_reconciliation?limit=5000");
    vi.mocked(api.get).mockResolvedValueOnce(ok([{ company: "mx", entity: "OINV" }]));
    await expect(getLoadReconciliation()).resolves.toEqual([{ company: "mx", entity: "OINV" }]);

    vi.mocked(api.get).mockResolvedValueOnce(ok({ data: [{ company: "us", entity: "OCRD" }] }));
    await expect(getLoadReconciliation()).resolves.toEqual([{ company: "us", entity: "OCRD" }]);

    vi.mocked(api.get).mockRejectedValueOnce(toApiError("Recurso no encontrado.", 404));
    await expect(getLoadReconciliation()).resolves.toBeNull();

    vi.mocked(api.get).mockRejectedValueOnce(toApiError("El backend no pudo completar la solicitud.", 503));
    await expect(getLoadReconciliation()).rejects.toMatchObject({ status: 503 });
  });

  it("sends parameters, finance runs and recipients with the documented bodies", async () => {
    vi.mocked(api.put).mockResolvedValueOnce(ok({ saved: true, count: 2, refreshed: true, refresh_error: null }));
    vi.mocked(api.post)
      .mockResolvedValueOnce(ok({ rows: 1, indicators: ["margen_bruto"], companies: { mx: ["2026-08"] } }))
      .mockResolvedValueOnce(ok({ added: true, email: "a@b.mx" }));
    vi.mocked(api.delete).mockResolvedValueOnce(ok({ removed: true, email: "a+b@c.mx" }));

    await saveSapB1BusinessParameters("setting:*:*:safety_days=7\n");
    await uploadSapB1FinanceRun("indicador,empresa,mes,dimension,clave,valor\n");
    await addSapB1Recipient("a@b.mx");
    await removeSapB1Recipient("a+b@c.mx");

    expect(api.put).toHaveBeenCalledWith(SAP_B1_PATHS.businessParameters, { text: "setting:*:*:safety_days=7\n" });
    expect(api.post).toHaveBeenNthCalledWith(1, "/api/sap-b1/finance-runs", { csv: "indicador,empresa,mes,dimension,clave,valor\n" });
    expect(api.post).toHaveBeenNthCalledWith(2, "/api/sap-b1/recipients", { email: "a@b.mx" });
    expect(api.delete).toHaveBeenCalledWith("/api/sap-b1/recipients/a%2Bb%40c.mx");
    expect(recipientPath("x/y@z.mx")).toBe("/api/sap-b1/recipients/x%2Fy%40z.mx");
  });

  it("propagates validation errors so the UI can show them inline", async () => {
    vi.mocked(api.post).mockRejectedValueOnce(toApiError("línea 3: mes '2026-13' debe ser AAAA-MM", 422));
    await expect(uploadSapB1FinanceRun("x")).rejects.toMatchObject({ status: 422, message: "línea 3: mes '2026-13' debe ser AAAA-MM" });
  });
});
