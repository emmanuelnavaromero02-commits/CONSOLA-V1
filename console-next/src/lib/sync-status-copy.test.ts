import { describe, expect, it } from "vitest";

import { syncCardTitle, syncReasonCopy, syncStatusCopy, syncStepStatusCopy } from "./sync-status-copy";

describe("sync-status-copy", () => {
  it("translates run statuses to business Spanish", () => {
    expect(syncStatusCopy("running")).toBe("En curso");
    expect(syncStatusCopy("partial")).toBe("Completada con advertencias");
    expect(syncStatusCopy("failed")).toBe("Fallida");
    expect(syncStatusCopy("skipped_explicit")).toBe("Omitida por configuración");
  });

  it("never leaks unknown raw statuses", () => {
    expect(syncStatusCopy("weird_internal_state")).toBe("Sin información");
    expect(syncStatusCopy(undefined)).toBe("Sin información");
    expect(syncStepStatusCopy("__proto__")).toBe("Sin información");
  });

  it("translates step statuses", () => {
    expect(syncStepStatusCopy("success")).toBe("Completado");
    expect(syncStepStatusCopy("failed")).toBe("Falló");
    expect(syncStepStatusCopy("queued")).toBe("En cola");
  });

  it("titles the card from the real run status", () => {
    expect(syncCardTitle("success")).toBe("Sincronización completa");
    expect(syncCardTitle("partial")).toBe("Completada con advertencias");
    expect(syncCardTitle("failed")).toBe("La sincronización falló");
    expect(syncCardTitle("running")).toBe("Sincronización en curso");
    expect(syncCardTitle(undefined)).toBe("Estado de la sincronización");
  });

  it("maps stable sync reason codes and falls back to the preflight map", () => {
    expect(syncReasonCopy("connection_check_failed")).toContain("Bóveda de Accesos");
    expect(syncReasonCopy("sync_stale_timeout")).toContain("agotó el tiempo de espera");
    expect(syncReasonCopy("airflow_trigger_failed")).toContain("orquestador");
    expect(syncReasonCopy("dag_paused_by_operator")).toContain("operador");
    expect(syncReasonCopy("nope")).toBeNull();
    expect(syncReasonCopy(42)).toBeNull();
  });
});
