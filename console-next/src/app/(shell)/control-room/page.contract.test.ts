import { readFileSync } from "node:fs";
import { join } from "node:path";

import { describe, expect, it } from "vitest";

const sourceFiles = [
  "src/app/(shell)/control-room/page.tsx",
  "src/components/control-room/experience/ControlRoomExperiencePage.tsx",
  "src/components/control-room/experience/ExperienceSection.tsx",
  "src/components/control-room/experience/ExperienceFact.tsx",
  "src/components/control-room/experience/ExperiencePreviewFlow.tsx",
  "src/components/control-room/experience/ExperienceLiveBadge.tsx",
  "src/components/control-room/experience/FactFallbackReading.tsx",
  "src/lib/control-room/experience-client.ts",
  "src/lib/control-room/experience-headlines.ts",
  "src/lib/control-room/use-control-room-experience.ts",
  "src/lib/control-room/use-control-room-experience-preview.ts",
  "src/lib/control-room/use-control-room-live.ts",
  "src/lib/time/use-now.ts",
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

  it("uses only the V2 Experience GET, its freshness GET and the action preview POST", () => {
    const endpointMatches = source.match(/"\/api\/control-room\/[^\"]+"/g) ?? [];
    expect([...new Set(endpointMatches)].sort()).toEqual([
      '"/api/control-room/actions/preview"',
      '"/api/control-room/experience/v2"',
      '"/api/control-room/experience/v2/freshness"',
    ]);
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
      "execute_live",
      "supervised-actions",
      "/api/actions",
      "/talent/actions/preview",
      "/items/{item_id}",
      "localStorage",
      "sessionStorage",
      "analytics",
      "toast.",
      "console.",
      "window.location",
      "includeUnready",
      "Sync Now",
      "setInterval(",
    ]) {
      expect(source).not.toContain(forbidden);
    }
  });

  it("polls only the pure freshness fingerprint and never the V2 payload", () => {
    const experienceHook = readFileSync(
      join(process.cwd(), "src/lib/control-room/use-control-room-experience.ts"),
      "utf8",
    );
    const liveHook = readFileSync(
      join(process.cwd(), "src/lib/control-room/use-control-room-live.ts"),
      "utf8",
    );
    for (const option of [
      "retry: false",
      "refetchOnMount: true",
      "refetchOnReconnect: false",
      "refetchOnWindowFocus: true",
      "refetchInterval: false",
      "staleTime: 15_000",
    ]) {
      expect(experienceHook).toContain(option);
    }
    for (const option of [
      "retry: false",
      "refetchOnWindowFocus: true",
      "refetchInterval: 30_000",
      "refetchIntervalInBackground: false",
      "staleTime: 0",
    ]) {
      expect(liveHook).toContain(option);
    }
    expect(experienceHook).not.toContain("refetchInterval: 30_000");
    expect(source).not.toContain("setInterval(");
  });
});
