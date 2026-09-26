import { describe, expect, it } from "vitest";

import type { AnalyticsApp } from "@/lib/admin-surfaces";
import type { PipelineEntity } from "@/lib/monitor/types";

import {
  aggregateStatus,
  appsUsing,
  bronzeLoad,
  datasetStatus,
  entityStatus,
  NODE_STATUS_LABEL,
  registeredRun,
  runStatus,
  runTime,
  scheduleIsActive,
  scheduleText,
} from "./node-facts";

function pipeline(status: string | null, extra: Partial<PipelineEntity> = {}): PipelineEntity {
  return {
    entity: "Invoice",
    cartridge: "acme",
    modes: ["incremental"],
    last_run: status === null ? null : { status, error: status === "failed" ? "timeout" : null },
    bronze: { source: "raw/acme/Invoice", status: "ok" },
    silver: [],
    gold: [],
    ...extra,
  };
}

describe("node facts", () => {
  it("labels the three states in Spanish", () => {
    expect(Object.values(NODE_STATUS_LABEL)).toEqual(["Sincronizado", "En proceso", "Requiere revisión"]);
  });

  it("maps run statuses and leaves unknown ones empty", () => {
    for (const status of ["running", "queued", "scheduled", "up_for_retry", "restarting", "deferred"]) {
      expect(runStatus(status)).toBe("en_proceso");
    }
    expect(runStatus("SUCCESS")).toBe("sincronizado");
    expect(runStatus("done")).toBe("sincronizado");
    for (const status of ["failed", "error", "upstream_failed", "partial"]) {
      expect(runStatus(status)).toBe("requiere_revision");
    }
    expect(runStatus("skipped")).toBeNull();
    expect(runStatus(undefined)).toBeNull();
  });

  it("derives a dataset status only from real fields", () => {
    expect(datasetStatus({ name: "x" }, { name: "x", status: "unavailable", error: "sin parquet" })).toEqual({
      status: "requiere_revision",
      reason: "sin parquet",
    });
    expect(datasetStatus({ name: "x", is_stale: true, staleness_reason: "fuente cambió" }, undefined)).toEqual({
      status: "requiere_revision",
      reason: "fuente cambió",
    });
    expect(datasetStatus({ name: "x", is_stale: false, last_refresh: "2026-09-26T10:00:00Z" }, undefined)).toEqual({
      status: "sincronizado",
      reason: null,
    });
    expect(datasetStatus({ name: "x", is_stale: false }, undefined)).toBeNull();
    expect(datasetStatus({ name: "x", last_refresh: "2026-09-26T10:00:00Z" }, undefined)).toBeNull();
    expect(datasetStatus(undefined, undefined)).toBeNull();
  });

  it("reads the entity status from the last run or job", () => {
    expect(entityStatus(pipeline("success"))).toEqual({ status: "sincronizado", reason: null });
    expect(entityStatus(pipeline("failed"))).toEqual({ status: "requiere_revision", reason: "timeout" });
    expect(entityStatus(pipeline(null, { last_job: { status: "running" } }))).toEqual({ status: "en_proceso", reason: null });
    expect(entityStatus(pipeline(null))).toBeNull();
    expect(entityStatus(undefined)).toBeNull();
  });

  it("never treats a partition snapshot as a registered run", () => {
    const physical = pipeline(null, {
      last_run: { source: "bronze", status: "success", finished_at: "2026-09-25T00:00:00+00:00" },
      bronze: { source: "raw/acme/Invoice", status: "ok", record_count: 40, latest_date: "2026-09-25" },
    });
    expect(registeredRun(physical)).toBeNull();
    expect(entityStatus(physical)).toBeNull();
    expect(bronzeLoad(physical)).toEqual({ count: 40, day: "2026-09-25" });
  });

  it("shows the latest load only when the last run confirmed it", () => {
    const ok = pipeline("success", { bronze: { source: "raw/acme/Invoice", status: "ok", record_count: 12, latest_date: "2026-09-26" } });
    expect(bronzeLoad(ok)).toEqual({ count: 12, day: "2026-09-26" });
    const failed = pipeline("failed", { bronze: { source: "raw/acme/Invoice", status: "error", record_count: 3, latest_date: "2026-09-26" } });
    expect(bronzeLoad(failed)).toBeNull();
    expect(bronzeLoad(pipeline("running"))).toBeNull();
    expect(bronzeLoad(pipeline("success"))).toBeNull();
    expect(bronzeLoad(undefined)).toBeNull();
  });

  it("reads the run time from the most precise field available", () => {
    expect(runTime({ finished_at: "2026-09-26T11:00:00Z", started_at: "2026-09-26T10:00:00Z" })).toBe("2026-09-26T11:00:00Z");
    expect(runTime({ started_at: "2026-09-26T10:00:00Z" })).toBe("2026-09-26T10:00:00Z");
    expect(runTime({ triggered_at: "2026-09-26T09:00:00Z" })).toBe("2026-09-26T09:00:00Z");
    expect(runTime(null)).toBeNull();
  });

  it("aggregates DAG status without claiming unknown entities are synced", () => {
    const synced = { status: "sincronizado" as const, reason: null };
    const running = { status: "en_proceso" as const, reason: null };
    const failed = { status: "requiere_revision" as const, reason: "x" };
    expect(aggregateStatus([synced, synced])).toEqual(synced);
    expect(aggregateStatus([synced, null])).toBeNull();
    expect(aggregateStatus([synced, running])).toEqual(running);
    expect(aggregateStatus([running, failed])).toEqual(failed);
    expect(aggregateStatus([])).toBeNull();
  });

  it("describes a schedule only when the entity scheduler would fire it", () => {
    const scheduled = { trigger_type: "scheduled", cron_expression: "0 8 * * *", dag_id: "acme_invoice", enabled: true };
    expect(scheduleIsActive(scheduled)).toBe(true);
    expect(scheduleText(scheduled)).toBe("Diaria a las 08:00 UTC (cron 0 8 * * *)");
    expect(scheduleText({ ...scheduled, trigger_type: "Scheduled", cron_expression: "*/15 * * * *" })).toBe(
      "Programada en UTC (cron */15 * * * *)",
    );
    expect(scheduleText({ ...scheduled, trigger_type: "manual" })).toBe(
      "Sin programación activa (cron 0 8 * * * registrado sin activar)",
    );
    expect(scheduleText({ ...scheduled, enabled: false })).toBe(
      "Sin programación activa (cron 0 8 * * * registrado sin activar)",
    );
    expect(scheduleText({ ...scheduled, dag_id: "" })).toBe(
      "Sin programación activa (cron 0 8 * * * registrado sin activar)",
    );
    expect(scheduleText({ trigger_type: "manual", cron_expression: "" })).toBe("Sin programación activa");
    expect(scheduleText({ trigger_type: "scheduled", cron_expression: null, dag_id: "x" })).toBe("Sin programación activa");
    expect(scheduleIsActive(undefined)).toBe(false);
    expect(scheduleText(undefined)).toBeNull();
  });

  it("finds the analytic apps that use any of the datasets", () => {
    const apps: AnalyticsApp[] = [
      { name: "tablero_ventas", title: "Tablero de ventas", datasets_used: ["sales"] },
      { name: "margen", title: "Análisis de margen", datasets_used: ["margin", "sales"] },
      { name: "otra", datasets_used: ["orders_x"] },
      { name: "vacia" },
    ];
    expect(appsUsing(["sales"], apps).map((app) => app.name)).toEqual(["margen", "tablero_ventas"]);
    expect(appsUsing(["orders"], apps)).toEqual([]);
    expect(appsUsing([], apps)).toEqual([]);
    expect(appsUsing(["sales"], undefined)).toEqual([]);
  });
});
