import { describe, expect, it } from "vitest";

import { publicErrorMessage } from "./api";
import { pipelineErrorDetail, pipelineReasonCopy } from "./pipeline-error-copy";

describe("pipeline error copy", () => {
  it("maps every backend reason to Spanish business copy", () => {
    for (const reason of [
      "extract_all_already_running",
      "too_many_active_entity_extracts",
      "backpressure_unavailable",
      "dag_unavailable",
      "dag_paused_by_operator",
      "stale_runs_require_recovery",
      "foreign_backlog_requires_platform_recovery",
      "auto_unpause_disabled",
      "plan_changed",
    ]) {
      const copy = pipelineReasonCopy(reason);
      expect(copy, reason).toBeTruthy();
      expect(copy).not.toMatch(/[A-Z][a-z]+ [a-z]+ is /);
    }
  });

  it("ignores unknown or non-string reasons", () => {
    expect(pipelineReasonCopy("drop_everything")).toBeNull();
    expect(pipelineReasonCopy("toString")).toBeNull();
    expect(pipelineReasonCopy(42)).toBeNull();
    expect(pipelineErrorDetail({ detail: "texto" })).toBeNull();
    expect(pipelineErrorDetail({ detail: ["a"] })).toBeNull();
    expect(pipelineErrorDetail(null)).toBeNull();
  });

  it("extracts the running job and new plan digest only when they are well formed", () => {
    const running = pipelineErrorDetail({
      detail: { reason: "extract_all_already_running", message: "SAP ... already running", job_id: "manual__2026-09-26T10:00:00+00:00" },
    });
    expect(running?.jobId).toBe("manual__2026-09-26T10:00:00+00:00");
    expect(running?.copy).toContain("extracción completa en curso");
    const unsafe = pipelineErrorDetail({ detail: { reason: "extract_all_already_running", job_id: "<script>" } });
    expect(unsafe?.jobId).toBeNull();
    const changed = pipelineErrorDetail({ detail: { reason: "plan_changed", plan_digest: "b".repeat(64) } });
    expect(changed?.planDigest).toBe("b".repeat(64));
    expect(pipelineErrorDetail({ detail: { reason: "plan_changed", plan_digest: "XYZ" } })?.planDigest).toBeNull();
  });

  it("feeds publicErrorMessage for client errors instead of a bare HTTP code", () => {
    const payload = {
      detail: {
        reason: "too_many_active_entity_extracts",
        message: "SAP SuccessFactors extraction backpressure: 2 active entity runs; use Extract All/sync or wait.",
        active: 2,
        limit: 2,
      },
    };
    expect(publicErrorMessage(429, payload, "req-1")).toBe(pipelineReasonCopy("too_many_active_entity_extracts"));
    expect(publicErrorMessage(409, { detail: { reason: "dag_paused_by_operator" } })).toContain("pausó");
    expect(publicErrorMessage(429, { detail: { reason: "unknown_reason" } }, "req-2")).toBe(
      "No se pudo completar la solicitud (HTTP 429). Ref: req-2",
    );
    expect(publicErrorMessage(503, { detail: { reason: "backpressure_unavailable" } })).toBe(
      "El backend no pudo completar la solicitud.",
    );
    expect(publicErrorMessage(400, { detail: "Entidad deshabilitada" })).toBe("Entidad deshabilitada");
  });
});
