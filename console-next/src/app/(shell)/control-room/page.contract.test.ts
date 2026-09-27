import { readFileSync } from "node:fs";
import { join } from "node:path";

import { describe, expect, it } from "vitest";

const sourceFiles = [
  "src/app/(shell)/control-room/page.tsx",
  "src/components/control-room/experience/ControlRoomExperiencePage.tsx",
  "src/components/control-room/experience/ExperienceSection.tsx",
  "src/components/control-room/experience/ExperienceFact.tsx",
  "src/components/control-room/experience/ExperienceActionDialog.tsx",
  "src/components/control-room/experience/ExperienceExceptions.tsx",
  "src/components/control-room/experience/ExperienceLiveBadge.tsx",
  "src/components/control-room/experience/FactFallbackReading.tsx",
  "src/lib/control-room/experience-client.ts",
  "src/lib/control-room/experience-headlines.ts",
  "src/lib/control-room/use-control-room-experience.ts",
  "src/lib/control-room/use-control-room-experience-action.ts",
  "src/lib/control-room/use-control-room-live.ts",
  "src/lib/control-room/use-control-room-refresh.ts",
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

  it("uses only the V2 GETs, the audited refresh and the audited action POSTs", () => {
    const endpointMatches = source.match(/"\/api\/control-room\/[^\"]+"/g) ?? [];
    expect([...new Set(endpointMatches)].sort()).toEqual([
      '"/api/control-room/actions/decision-proposal"',
      '"/api/control-room/actions/exception"',
      '"/api/control-room/actions/exception-reopen"',
      '"/api/control-room/actions/preview"',
      '"/api/control-room/actions/studio-target"',
      '"/api/control-room/experience/v2"',
      '"/api/control-room/experience/v2/freshness"',
      '"/api/control-room/refresh"',
    ]);
  });

  it("persists only from the explicit Actualizar control for writers", () => {
    const refresh = readFileSync(
      join(process.cwd(), "src/lib/control-room/use-control-room-refresh.ts"),
      "utf8",
    );
    expect(refresh).toContain('includes("control_room.write")');
    expect(refresh).toContain("refetchOnly()");
    expect(refresh).toContain("invalidateControlRoomLive(queryClient, workspaceId)");
    expect(source.match(/refreshControlRoomState\(/g)).toHaveLength(2);
  });

  it("confirms every mutating action in a dialog and navigates without window.location", () => {
    const dialog = readFileSync(
      join(
        process.cwd(),
        "src/components/control-room/experience/ExperienceActionDialog.tsx",
      ),
      "utf8",
    );
    const page = readFileSync(
      join(
        process.cwd(),
        "src/components/control-room/experience/ControlRoomExperiencePage.tsx",
      ),
      "utf8",
    );

    expect(dialog).toContain('role="dialog"');
    expect(dialog).toContain('aria-modal="true"');
    expect(dialog).toContain('role="status"');
    expect(dialog).toContain(
      "El hallazgo se archivará como excepción aprobada; puedes reabrirlo.",
    );
    expect(dialog).toContain(
      "Se crea una decisión con compromiso a 7 días y pasa al Consejo para aprobación.",
    );
    expect(page).toContain("router.push");
    expect(page).toContain("paused: action.dialogOpen");
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
