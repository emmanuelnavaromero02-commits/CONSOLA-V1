// @vitest-environment jsdom

import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type { AnalysisEnvelope } from "@/lib/control-room/types";

import { GroundedAnalysisPanel } from "./GroundedAnalysisPanel";

const clientBoundary = vi.hoisted(() => ({
  getControlRoomItemAnalysis: vi.fn(),
  requestControlRoomItemAnalysis: vi.fn(),
}));

vi.mock("@/lib/control-room/client", () => clientBoundary);

(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

let container: HTMLDivElement;
let root: Root;

function envelope(id: string, statement: string): AnalysisEnvelope {
  return {
    analysis_run_id: id,
    status: "verified",
    evidence_pack_id: 1,
    as_of: "2026-09-02T17:00:00Z",
    grounding_status: "verified",
    claims: [
      {
        claim_id: `${id}-claim`,
        claim_type: "observed",
        statement,
        value: 20,
        unit: "personas",
        population: 20,
        as_of: "2026-09-02T17:00:00Z",
        completeness: "complete",
        evidence_refs: [{ evidence_item_id: 1, path: "data.population_total" }],
        evidence_item_ids: [1],
        evidence_paths: ["data.population_total"],
        verification_status: "verified",
        verification_reason: "exact",
      },
    ],
    hypotheses: [],
    options: [],
    assumptions: [],
    blockers: [],
    expires_at: "2099-09-03T17:00:00Z",
    model: "claude-sonnet-4-6",
    ruleset_version: "control-room-grounding-v1",
    recommendation_only: true,
    no_writeback: true,
  };
}

beforeEach(() => {
  vi.clearAllMocks();
  container = document.createElement("div");
  document.body.append(container);
  root = createRoot(container);
});

afterEach(async () => {
  await act(async () => root.unmount());
  container.remove();
});

describe("GroundedAnalysisPanel item isolation", () => {
  it("clears an old envelope immediately across A to B to no selection", async () => {
    let resolveB: ((value: AnalysisEnvelope) => void) | undefined;
    const pendingB = new Promise<AnalysisEnvelope>((resolve) => {
      resolveB = resolve;
    });
    clientBoundary.getControlRoomItemAnalysis.mockImplementation((id: string) => {
      if (id === "item-a") return Promise.resolve(envelope("run-a", "A-only claim"));
      return pendingB;
    });

    await act(async () => {
      root.render(
        <GroundedAnalysisPanel
          anomaly={{ id: "a", analysis_item_id: "item-a", title: "Signal A" }}
        />,
      );
      await Promise.resolve();
    });
    expect(container.textContent).toContain("A-only claim");

    await act(async () => {
      root.render(
        <GroundedAnalysisPanel
          anomaly={{ id: "b", analysis_item_id: "item-b", title: "Signal B" }}
        />,
      );
    });
    expect(container.textContent).not.toContain("A-only claim");

    await act(async () => {
      root.render(<GroundedAnalysisPanel anomaly={null} />);
    });
    expect(container.textContent).not.toContain("A-only claim");

    await act(async () => {
      resolveB?.(envelope("run-b", "B-only claim"));
      await pendingB;
    });
    expect(container.textContent).not.toContain("B-only claim");
  });
});
