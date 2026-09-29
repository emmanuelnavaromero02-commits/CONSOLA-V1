import { describe, expect, it } from "vitest";

import { installationStatusCopy, runStatusCopy, statusCopy } from "./status-copy";

describe("status copy", () => {
  it("translates common machine statuses", () => {
    expect(statusCopy("success")).toBe("Completado");
    expect(statusCopy("FAILED")).toBe("Falló");
    expect(statusCopy("running")).toBe("En curso");
    expect(statusCopy("waiting_approval")).toBe("Requiere aprobación");
  });

  it("never returns a raw enum", () => {
    expect(statusCopy("weird_internal_state")).toBe("Sin información");
    expect(statusCopy("dry_run_passed")).toBe("Sin información");
    expect(statusCopy("")).toBe("Sin información");
    expect(statusCopy(null)).toBe("Sin información");
    expect(statusCopy(undefined)).toBe("Sin información");
  });

  it("prefers per-surface overrides over the base map", () => {
    expect(statusCopy("failed", { failed: "Requiere revisión" })).toBe("Requiere revisión");
    expect(statusCopy("dry_run_passed", { dry_run_passed: "Simulado con éxito" })).toBe("Simulado con éxito");
  });

  it("speaks about runs in feminine business Spanish", () => {
    expect(runStatusCopy("success")).toBe("Completada");
    expect(runStatusCopy("failed")).toBe("Falló");
    expect(runStatusCopy("queued")).toBe("En cola");
    expect(runStatusCopy("cancelled")).toBe("Cancelada");
    expect(runStatusCopy("something_else")).toBe("Sin información");
  });

  it("covers marketplace installation states", () => {
    expect(installationStatusCopy("waiting_credentials")).toBe("Requiere credenciales");
    expect(installationStatusCopy("ready")).toBe("Activa");
    expect(installationStatusCopy("made_up")).toBe("Sin información");
  });
});
