import { beforeEach, describe, expect, it, vi } from "vitest";

const apiMock = vi.hoisted(() => ({ get: vi.fn(), post: vi.fn() }));

vi.mock("@/lib/api", () => ({ api: apiMock }));

import {
  allRunsTerminal,
  extractionProgressSchema,
  findProgressRun,
  getExtractionProgress,
  phaseCopy,
  recoverStuckRuns,
  stuckRunRecoverySchema,
  type ExtractionProgressRun,
} from "./extraction-progress";

function run(overrides: Partial<ExtractionProgressRun> = {}): ExtractionProgressRun {
  return {
    run_id: "manual__1",
    entity: "User",
    phase: "connecting",
    phase_index: 1,
    status: "queued",
    terminal: false,
    outcome: null,
    record_count: null,
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

function payload(runs: ExtractionProgressRun[]) {
  return { schema_version: "pipeline-extraction-progress/v1", checked_at: "2026-09-26T12:00:05Z", runs };
}

const recovery = {
  schema_version: "pipeline-recovery/v1",
  mode: "dry_run",
  checked_at: "2026-09-26T12:00:00Z",
  threshold_minutes: 15,
  plan_digest: "a".repeat(64),
  counts: {
    candidates: 2,
    recoverable: 1,
    recovered: 0,
    synced_terminal: 0,
    live: 1,
    unverifiable: 0,
    not_applicable: 0,
    conflicts: 0,
    airflow_neutralized: 0,
    airflow_neutralize_failed: 0,
  },
  runs: [],
  truncated: false,
  message_es: "1 de 2 corridas revisadas se pueden cerrar o sincronizar con Airflow.",
};

beforeEach(() => {
  apiMock.get.mockReset();
  apiMock.post.mockReset();
});

describe("extraction progress client", () => {
  it("asks for up to 20 unique runs of one cartridge and validates the payload", async () => {
    apiMock.get.mockResolvedValue({ data: payload([run()]) });
    const ids = ["manual__1", "manual__1", ...Array.from({ length: 25 }, (_, i) => `r${i}`)];
    const result = await getExtractionProgress("sap_successfactors", ids);
    expect(result.runs[0].run_id).toBe("manual__1");
    const url = new URL(apiMock.get.mock.calls[0][0], "http://console.test");
    expect(url.pathname).toBe("/api/pipelines/extraction-progress");
    expect(url.searchParams.get("cartridge")).toBe("sap_successfactors");
    expect(url.searchParams.getAll("run_id")).toHaveLength(20);
    expect(url.searchParams.getAll("run_id")[0]).toBe("manual__1");
  });

  it("rejects payloads with unexpected fields or phases", async () => {
    expect(() => extractionProgressSchema.parse({ ...payload([]), extra: true })).toThrow();
    expect(() => extractionProgressSchema.parse(payload([{ ...run(), phase: "done" as never }]))).toThrow();
    expect(() => extractionProgressSchema.parse(payload([{ ...run(), record_count: -1 }]))).toThrow();
    apiMock.get.mockResolvedValue({ data: { schema_version: "other", checked_at: "x", runs: [] } });
    await expect(getExtractionProgress("sap_successfactors", ["a"])).rejects.toThrow();
  });

  it("runs the recovery as a dry-run unless a digest is applied", async () => {
    apiMock.post.mockResolvedValue({ data: recovery });
    await recoverStuckRuns({ cartridge: "sap_successfactors" });
    expect(apiMock.post).toHaveBeenLastCalledWith("/api/pipelines/recover-stuck-runs", { cartridge: "sap_successfactors" });
    apiMock.post.mockResolvedValue({ data: { ...recovery, mode: "applied" } });
    await recoverStuckRuns({ apply: true, plan_digest: "a".repeat(64), dag_id: "sap_successfactors_extract" });
    expect(apiMock.post).toHaveBeenLastCalledWith("/api/pipelines/recover-stuck-runs", {
      apply: true,
      plan_digest: "a".repeat(64),
      dag_id: "sap_successfactors_extract",
    });
    expect(() => stuckRunRecoverySchema.parse({ ...recovery, plan_digest: "short" })).toThrow();
  });

  it("accepts Airflow orphans that are stopped without touching the console row", () => {
    const orphan = {
      run_id: "manual__orphan",
      dag_id: "sap_successfactors_extract_all",
      cartridge: "sap_successfactors",
      entity: "__extract_all__",
      status_before: "failed",
      status_after: null,
      started_at: "2026-09-26T07:57:00Z",
      age_minutes: 600,
      created_today: true,
      classification: "airflow_orphan",
      action: "neutralize_airflow",
      neutralize_airflow: true,
      reason_es: "La consola ya cerró esta corrida, pero Airflow la mantiene pendiente.",
    };
    expect(stuckRunRecoverySchema.parse({ ...recovery, runs: [orphan] }).runs[0].action).toBe("neutralize_airflow");
    expect(() => stuckRunRecoverySchema.parse({ ...recovery, runs: [{ ...orphan, action: "delete" }] })).toThrow();
  });

  it("stops polling only when every followed run is terminal", () => {
    const data = extractionProgressSchema.parse(payload([run({ run_id: "a", terminal: true }), run({ run_id: "b" })]));
    expect(allRunsTerminal(data, ["a"])).toBe(true);
    expect(allRunsTerminal(data, ["a", "b"])).toBe(false);
    expect(allRunsTerminal(data, ["a", "missing"])).toBe(false);
    expect(allRunsTerminal(undefined, ["a"])).toBe(false);
    expect(findProgressRun(data, "b")?.run_id).toBe("b");
    expect(findProgressRun(data, null)).toBeUndefined();
  });
});

describe("phaseCopy", () => {
  it("uses the four business phases", () => {
    expect(phaseCopy(run()).title).toBe("Conectando de forma segura con el origen...");
    expect(phaseCopy(run({ phase: "extracting", phase_index: 2, status: "running" })).title).toBe(
      "Extrayendo entidades seleccionadas...",
    );
    expect(phaseCopy(run({ phase: "building", phase_index: 3, status: "running" })).title).toBe(
      "Construyendo vistas de negocio y actualizando catálogo...",
    );
    expect(phaseCopy(run({ phase: "ready", phase_index: 4, status: "success", terminal: true, outcome: "success" }))).toEqual({
      title: "Listo para su análisis.",
      detail: null,
      tone: "success",
    });
  });

  it("shows record and entity counts only when the backend knows them", () => {
    const extracting = { phase: "extracting" as const, phase_index: 2, status: "running" };
    expect(phaseCopy(run(extracting)).title).not.toContain("registros");
    expect(phaseCopy(run({ ...extracting, record_count: 12500 })).title).toContain("(12,500 registros descargados)");
    expect(phaseCopy(run({ ...extracting, record_count: 1 })).title).toContain("(1 registro descargado)");
    expect(phaseCopy(run({ ...extracting, record_count: 0 })).title).toContain("(0 registros descargados)");
    expect(phaseCopy(run({ ...extracting, entities_done: 3 })).detail).toBe("3 entidades completadas");
    expect(phaseCopy(run({ ...extracting, entities_done: 3, entities_total: 8 })).detail).toBe("3 entidades completadas de 8");
    expect(phaseCopy(run({ ...extracting, entities_done: 1 })).detail).toBe("1 entidad completada");
  });

  it("explains warnings, failures and stalls honestly", () => {
    expect(phaseCopy(run({ phase: "ready", phase_index: 4, terminal: true, outcome: "partial", error: "2 campos sin permiso" }))).toEqual({
      title: "Completado con advertencias",
      detail: "2 campos sin permiso",
      tone: "warning",
    });
    expect(phaseCopy(run({ terminal: true, outcome: "failed", status: "failed", error: "Recuperado por el sistema: tiempo de espera agotado" }))).toEqual({
      title: "No se pudo completar",
      detail: "Recuperado por el sistema: tiempo de espera agotado",
      tone: "error",
    });
    expect(phaseCopy(run({ stalled: true })).title).toBe("La ejecución no avanza");
    expect(phaseCopy(undefined).title).toBe("Esperando el registro de la corrida…");
  });
});
