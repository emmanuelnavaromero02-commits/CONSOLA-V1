// @vitest-environment jsdom

import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { toApiError } from "@/lib/api";
import type { StuckRunRecovery } from "@/lib/monitor/extraction-progress";

import { StuckRunsDialog } from "./StuckRunsDialog";

const recoverMock = vi.hoisted(() => vi.fn());

vi.mock("@/lib/monitor/extraction-progress", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/monitor/extraction-progress")>();
  return { ...actual, recoverStuckRuns: recoverMock };
});

let container: HTMLDivElement;
let root: Root;

function plan(overrides: Partial<StuckRunRecovery> = {}): StuckRunRecovery {
  return {
    schema_version: "pipeline-recovery/v1",
    mode: "dry_run",
    checked_at: "2026-09-26T12:00:00Z",
    threshold_minutes: 15,
    plan_digest: "a".repeat(64),
    counts: {
      candidates: 3,
      recoverable: 2,
      recovered: 0,
      synced_terminal: 0,
      live: 1,
      unverifiable: 0,
      not_applicable: 0,
      conflicts: 0,
      airflow_neutralized: 0,
      airflow_neutralize_failed: 0,
    },
    runs: [
      {
        run_id: "manual__old",
        dag_id: "sap_successfactors_extract",
        cartridge: "sap_successfactors",
        entity: "User",
        status_before: "queued",
        status_after: null,
        started_at: "2026-09-26T08:00:00Z",
        age_minutes: 240,
        created_today: true,
        classification: "stalled_queued_paused_dag",
        action: "mark_failed",
        neutralize_airflow: true,
        reason_es: "La corrida quedó en cola con el proceso pausado en Airflow.",
      },
      {
        run_id: "manual__live",
        dag_id: "sap_successfactors_extract",
        cartridge: "sap_successfactors",
        entity: "PerPhone",
        status_before: "running",
        status_after: null,
        started_at: "2026-09-26T11:58:00Z",
        age_minutes: 2,
        created_today: true,
        classification: "too_recent",
        action: "none",
        neutralize_airflow: false,
        reason_es: "Corrida reciente: sigue dentro del tiempo de espera normal.",
      },
    ],
    truncated: false,
    message_es: "2 de 3 corridas revisadas se pueden cerrar o sincronizar con Airflow.",
    ...overrides,
  };
}

async function render(node: React.ReactNode) {
  await act(async () => {
    root.render(node);
  });
  await flush();
}

async function flush() {
  for (let tick = 0; tick < 4; tick += 1) {
    await act(async () => {
      await new Promise((resolve) => setTimeout(resolve, 0));
    });
  }
}

function button(label: RegExp) {
  return [...document.querySelectorAll<HTMLButtonElement>('[data-testid="stuck-runs-dialog"] button')].find((item) =>
    label.test(item.textContent ?? ""),
  );
}

async function click(element: Element | undefined) {
  expect(element).toBeTruthy();
  await act(async () => {
    element?.dispatchEvent(new MouseEvent("click", { bubbles: true }));
  });
  await flush();
}

beforeEach(() => {
  (globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;
  recoverMock.mockReset();
  container = document.createElement("div");
  document.body.append(container);
  root = createRoot(container);
});

afterEach(async () => {
  await act(async () => root.unmount());
  container.remove();
});

describe("StuckRunsDialog", () => {
  it("runs a dry-run on open and lists only the runs it would change", async () => {
    recoverMock.mockResolvedValue(plan());
    await render(<StuckRunsDialog open cartridge="sap_successfactors" dagId="sap_successfactors_extract" onClose={vi.fn()} />);
    expect(recoverMock).toHaveBeenCalledWith({ cartridge: "sap_successfactors", dag_id: "sap_successfactors_extract" });
    const dialog = document.querySelector('[data-testid="stuck-runs-dialog"]');
    expect(dialog?.textContent).toContain("2 de 3 corridas revisadas");
    expect(dialog?.querySelector('[data-testid="stuck-runs-counts"]')?.textContent).toContain("Por cerrar o sincronizar2");
    expect(dialog?.textContent).toContain("La corrida quedó en cola con el proceso pausado en Airflow.");
    expect(dialog?.textContent).not.toContain("PerPhone");
    expect(button(/Cerrar 2 corridas/)?.disabled).toBe(false);
  });

  it("applies the reviewed plan with its digest", async () => {
    const onRecovered = vi.fn();
    recoverMock.mockResolvedValueOnce(plan());
    recoverMock.mockResolvedValueOnce(plan({ mode: "applied", message_es: "Se cerraron 2 corridas atascadas y se sincronizaron 0 con su estado final en Airflow." }));
    await render(<StuckRunsDialog open cartridge="sap_successfactors" onClose={vi.fn()} onRecovered={onRecovered} />);
    await click(button(/Cerrar 2 corridas/));
    expect(recoverMock).toHaveBeenLastCalledWith({ cartridge: "sap_successfactors", dag_id: undefined, apply: true, plan_digest: "a".repeat(64) });
    expect(onRecovered).toHaveBeenCalledTimes(1);
    expect(document.body.textContent).toContain("Se cerraron 2 corridas atascadas");
  });

  it("reviews again when the plan changed before confirming", async () => {
    recoverMock.mockResolvedValueOnce(plan());
    recoverMock.mockRejectedValueOnce(
      toApiError("x", 409, { detail: { reason: "plan_changed", plan_digest: "b".repeat(64) } }),
    );
    recoverMock.mockResolvedValueOnce(plan({ plan_digest: "b".repeat(64), counts: { ...plan().counts, recoverable: 1 } }));
    await render(<StuckRunsDialog open cartridge="sap_successfactors" onClose={vi.fn()} />);
    await click(button(/Cerrar 2 corridas/));
    expect(recoverMock).toHaveBeenCalledTimes(3);
    expect(recoverMock.mock.calls[2][0]).toEqual({ cartridge: "sap_successfactors", dag_id: undefined });
    expect(document.body.textContent).toContain("Las corridas cambiaron desde la revisión");
    expect(button(/Cerrar 1 corrida$/)).toBeTruthy();
  });

  it("disables confirmation when nothing is recoverable and shows errors", async () => {
    recoverMock.mockResolvedValueOnce(plan({ counts: { ...plan().counts, recoverable: 0 }, runs: [] }));
    await render(<StuckRunsDialog open cartridge="sap_successfactors" onClose={vi.fn()} />);
    expect(button(/Cerrar 0 corridas/)?.disabled).toBe(true);

    await act(async () => root.unmount());
    root = createRoot(container);
    recoverMock.mockRejectedValueOnce(new Error("No tienes permisos para esta acción."));
    await render(<StuckRunsDialog open cartridge="sap_successfactors" onClose={vi.fn()} />);
    expect(document.body.textContent).toContain("No tienes permisos para esta acción.");
  });

  it("does nothing while closed", async () => {
    await render(<StuckRunsDialog open={false} cartridge="sap_successfactors" onClose={vi.fn()} />);
    expect(recoverMock).not.toHaveBeenCalled();
    expect(document.querySelector('[data-testid="stuck-runs-dialog"]')).toBeNull();
  });
});
