// @vitest-environment jsdom

import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { renderToStaticMarkup } from "react-dom/server";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type { PipelineEntity } from "@/lib/monitor/types";
import { PipelineTable } from "./PipelineTable";

const apiMock = vi.hoisted(() => ({ post: vi.fn(), get: vi.fn() }));
const toastMock = vi.hoisted(() => ({ success: vi.fn(), error: vi.fn(), info: vi.fn() }));
const progressMock = vi.hoisted(() => ({ calls: [] as Array<{ cartridge: string; runIds: string[] }> }));

vi.mock("@/lib/monitor/hooks", () => ({
  useVaultConnections: () => ({ data: [] }),
}));

vi.mock("@/lib/api", () => ({
  api: apiMock,
  isApiError: (value: unknown) => typeof value === "object" && value !== null && "message" in value,
}));

vi.mock("@/lib/monitor/extraction-progress", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/monitor/extraction-progress")>();
  return {
    ...actual,
    useExtractionProgress: (cartridge: string, runIds: string[]) => {
      progressMock.calls.push({ cartridge, runIds });
      return {
        data: {
          schema_version: "pipeline-extraction-progress/v1",
          checked_at: "2026-09-26T12:00:00Z",
          runs: runIds.map((runId) => ({
            run_id: runId,
            entity: "employees",
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
          })),
        },
        isLoading: false,
        isError: false,
        error: null,
        dataUpdatedAt: Date.now(),
        refetch: vi.fn(),
      };
    },
  };
});

vi.mock("sonner", () => ({
  toast: toastMock,
}));

function makeRow(bronze: Partial<PipelineEntity["bronze"]>): PipelineEntity {
  return {
    entity: "employees",
    cartridge: "replicon",
    modes: ["incremental"],
    watermark: null,
    last_run: null,
    last_job: null,
    bronze: {
      source: "bronze.replicon_employees",
      latest_date: "2026-07-29",
      status: "fresh",
      ...bronze,
    },
    silver: [],
    gold: [],
  };
}

describe("PipelineTable bronze summary", () => {
  it("shows N/D when record_count is null and the table is not flagged empty", () => {
    const markup = renderToStaticMarkup(
      <PipelineTable rows={[makeRow({ record_count: null, empty: false })]} />,
    );

    expect(markup).toContain("N/D filas");
    expect(markup).not.toContain("0 filas");
  });

  it("shows N/D when record_count is missing entirely", () => {
    const markup = renderToStaticMarkup(
      <PipelineTable rows={[makeRow({})]} />,
    );

    expect(markup).toContain("N/D filas");
    expect(markup).not.toContain("0 filas");
  });

  it("keeps the explicit empty state when empty is true", () => {
    const markup = renderToStaticMarkup(
      <PipelineTable rows={[makeRow({ record_count: null, empty: true })]} />,
    );

    expect(markup).toContain("Sin filas extraídas");
    expect(markup).not.toContain("N/D filas");
  });

  it("shows the real count when record_count is present, including 0", () => {
    const withCount = renderToStaticMarkup(
      <PipelineTable rows={[makeRow({ record_count: 42, empty: false })]} />,
    );
    const withZero = renderToStaticMarkup(
      <PipelineTable rows={[makeRow({ record_count: 0, empty: false })]} />,
    );

    expect(withCount).toContain("42 filas");
    expect(withZero).toContain("0 filas");
    expect(withCount).not.toContain("N/D filas");
  });
});

function apiError(status: number, data: unknown) {
  return Object.assign(new Error("x"), { status, data });
}

let container: HTMLDivElement;
let root: Root;

async function render(node: React.ReactNode) {
  await act(async () => {
    root.render(node);
  });
}

async function click(label: RegExp) {
  const button = [...container.querySelectorAll("button")].find((item) => label.test(item.textContent ?? ""));
  expect(button, String(label)).toBeTruthy();
  await act(async () => {
    button?.dispatchEvent(new MouseEvent("click", { bubbles: true }));
  });
  for (let tick = 0; tick < 3; tick += 1) {
    await act(async () => {
      await new Promise((resolve) => setTimeout(resolve, 0));
    });
  }
}

describe("PipelineTable extraction cards", () => {
  beforeEach(() => {
    (globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;
    apiMock.post.mockReset();
    toastMock.success.mockReset();
    toastMock.error.mockReset();
    toastMock.info.mockReset();
    progressMock.calls = [];
    container = document.createElement("div");
    document.body.append(container);
    root = createRoot(container);
  });

  afterEach(async () => {
    await act(async () => root.unmount());
    container.remove();
  });

  it("follows an entity extraction in a progress card instead of a toast", async () => {
    apiMock.post.mockResolvedValue({
      data: {
        dag_id: "replicon_extract",
        dag_run_id: "manual__emp",
        automation: { was_paused: true, unpaused: true, message_es: "El proceso estaba en pausa; se reactivó." },
      },
    });
    const started = vi.fn();
    await render(<PipelineTable rows={[makeRow({ record_count: 3 })]} cartridge="replicon" onExtractionStarted={started} />);
    expect(container.textContent).toContain("Extraer datos");
    await click(/^Extraer$/);
    const card = container.querySelector('[data-testid="extraction-progress-card"]');
    expect(card?.textContent).toContain("Extracción de employees");
    expect(card?.textContent).toContain("manual__emp");
    expect(card?.textContent).toContain("Conectando de forma segura con el origen...");
    expect(card?.textContent).toContain("se reactivó");
    expect(toastMock.success).not.toHaveBeenCalled();
    expect(started).toHaveBeenCalledTimes(1);
    expect(progressMock.calls.at(-1)).toEqual({ cartridge: "replicon", runIds: ["manual__emp"] });
  });

  it("attaches to the running full extraction on a 429 with its job id", async () => {
    apiMock.post.mockRejectedValue(
      apiError(429, { detail: { reason: "extract_all_already_running", message: "already running", job_id: "manual__all" } }),
    );
    await render(<PipelineTable rows={[makeRow({})]} cartridge="sap_successfactors" />);
    await click(/^Extraer$/);
    const card = container.querySelector('[data-testid="extraction-progress-card"]');
    expect(card?.textContent).toContain("Extracción completa");
    expect(card?.textContent).toContain("manual__all");
    expect(toastMock.error).not.toHaveBeenCalled();
    expect(toastMock.info.mock.calls[0][0]).toContain("extracción completa en curso");
  });

  it("reports extract_all conflicts in Spanish and follows reused runs", async () => {
    apiMock.post.mockResolvedValueOnce({
      data: {
        count: 0,
        error_count: 1,
        triggered: [],
        errors: [{ entity: "__extract_all__", status_code: 409, error: "backlog", reason: "foreign_backlog_requires_platform_recovery" }],
      },
    });
    await render(<PipelineTable rows={[makeRow({})]} cartridge="sap_successfactors" />);
    await click(/Extraer Todo/);
    expect(toastMock.error.mock.calls[0][0]).toContain("no pertenecen a este espacio de trabajo");
    expect(container.querySelector('[data-testid="extraction-progress-card"]')).toBeNull();

    apiMock.post.mockResolvedValueOnce({
      data: {
        count: 1,
        error_count: 0,
        reused: true,
        reason: "active_extract_all_run",
        triggered: [{ entity: "__extract_all__", dag_run_id: "manual__live", job_id: "manual__live", dag_id: "sap_successfactors_extract_all" }],
        errors: [],
      },
    });
    await click(/Extraer Todo/);
    const card = container.querySelector('[data-testid="extraction-progress-card"]');
    expect(card?.textContent).toContain("Extracción completa");
    expect(card?.textContent).toContain("manual__live");
    expect(card?.textContent).toContain("Ya había una extracción en curso");
  });

  it("maps unknown failures to the API message", async () => {
    apiMock.post.mockRejectedValue(apiError(502, null));
    await render(<PipelineTable rows={[makeRow({})]} cartridge="replicon" />);
    await click(/^Extraer$/);
    expect(toastMock.error).toHaveBeenCalledWith("x");
    expect(container.querySelector('[data-testid="extraction-progress-card"]')).toBeNull();
  });
});
