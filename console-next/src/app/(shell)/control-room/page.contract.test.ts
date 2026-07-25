import { readFileSync } from "node:fs";
import { join } from "node:path";

import { describe, expect, it } from "vitest";

const sourceFiles = [
  "src/app/(shell)/control-room/page.tsx",
  "src/components/control-room/experience/ControlRoomExperiencePage.tsx",
  "src/components/control-room/experience/ExperienceSection.tsx",
  "src/components/control-room/experience/ExperienceFact.tsx",
  "src/lib/control-room/experience-client.ts",
  "src/lib/control-room/use-control-room-experience.ts",
];
const source = sourceFiles
  .map((path) => readFileSync(join(process.cwd(), path), "utf8"))
  .join("\n");

describe("Control Room Business Experience boundary", () => {
  it("delegates the root page to the new read-only composition", () => {
    const page = readFileSync(
      join(process.cwd(), "src/app/(shell)/control-room/page.tsx"),
      "utf8",
    );

    expect(page).toContain("ControlRoomExperiencePage");
    expect(page.split("\n").length).toBeLessThanOrEqual(180);
  });

  it("uses only the Experience endpoint", () => {
    const endpointMatches = source.match(/\/api\/control-room\/[a-z-]+/g) ?? [];
    expect([...new Set(endpointMatches)]).toEqual(["/api/control-room/experience"]);
  });

  it("disconnects every legacy root surface and mutation", () => {
    for (const forbidden of [
      "getControlRoomDashboard",
      "SuccessFactorsGoldPanel",
      "AnalyticAppsPanel",
      "AgentsOpsPanel",
      "MarketDecisionEvidencePanel",
      "/diagnostics",
      "/activity",
      "/impact",
      "/lessons",
      "/thresholds",
      "/approve",
      "/execute",
      "/auto-run",
      "includeUnready",
      "Sync Now",
      "setInterval(",
    ]) {
      expect(source).not.toContain(forbidden);
    }
  });

  it("uses bounded remount freshness without retry, polling or ambient refetch", () => {
    expect(source).toContain("retry: false");
    expect(source).toContain("refetchOnMount: true");
    expect(source).toContain("refetchOnReconnect: false");
    expect(source).toContain("refetchOnWindowFocus: false");
    expect(source).toContain("refetchInterval: false");
    expect(source).toContain("staleTime: 15_000");
  });
});
