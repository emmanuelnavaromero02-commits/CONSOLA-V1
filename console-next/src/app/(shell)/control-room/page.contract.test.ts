import { readFileSync } from "node:fs";
import { join } from "node:path";

import { describe, expect, it } from "vitest";

function read(path: string): string {
  return readFileSync(join(process.cwd(), path), "utf8");
}

function concat(paths: string[]): string {
  return paths.map(read).join("\n");
}

const ENTIENDE_FILES = [
  "src/app/(shell)/control-room/page.tsx",
  "src/components/control-room/phases/ControlRoomPhases.tsx",
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

const DIAGNOSTIC_FILES = [
  "src/components/control-room/experience/ExperienceEmptyDiagnostic.tsx",
  "src/lib/control-room/diagnostics-client.ts",
];

const DECIDE_FILES = [
  "src/components/control-room/phases/PhaseDecide.tsx",
  "src/lib/operational-intelligence/client.ts",
  "src/lib/operational-intelligence/types.ts",
];

const EJECUTA_FILES = [
  "src/components/control-room/phases/PhaseEjecuta.tsx",
  "src/components/decisions/ActionCouncil.tsx",
  "src/components/decisions/CouncilDialogs.tsx",
  "src/components/decisions/CouncilProposalCard.tsx",
  "src/lib/decisions/use-action-council.ts",
  "src/lib/decisions/council-client.ts",
];

const SUPERVISA_FILES = [
  "src/components/control-room/phases/PhaseSupervisa.tsx",
  "src/components/control-room/phases/SupervisedActionsQueue.tsx",
  "src/components/control-room/phases/use-action-mutations.ts",
];

const EVOLUCIONA_FILES = [
  "src/components/control-room/phases/PhaseEvoluciona.tsx",
];

const ALL_PHASE_FILES = [
  ...ENTIENDE_FILES,
  ...DIAGNOSTIC_FILES,
  ...DECIDE_FILES,
  ...EJECUTA_FILES,
  ...SUPERVISA_FILES,
  ...EVOLUCIONA_FILES,
];

function controlRoomEndpoints(source: string): string[] {
  return [...new Set(source.match(/"\/api\/control-room\/[^\"]+"/g) ?? [])].sort();
}

function apiPaths(source: string): string[] {
  return [...new Set(source.match(/\/api\/[a-z0-9-]+(?:\/[a-z0-9-{}]+)*/g) ?? [])].sort();
}

describe("Control Room phase shell boundary", () => {
  it("delegates the root page to the phase shell around the read-only experience", () => {
    const page = read("src/app/(shell)/control-room/page.tsx");

    expect(page).toContain("ControlRoomPhases");
    expect(page).toContain("ControlRoomExperiencePage");
    expect(page).toContain("SapB1ControlRoomEntry");
    expect(page.split("\n").length).toBeLessThanOrEqual(60);
  });

  it("keeps the URL-addressable phases and their gates in the shell", () => {
    const shell = read("src/components/control-room/phases/ControlRoomPhases.tsx");

    expect(shell).toContain('role="tablist"');
    expect(shell).toContain('href={`/control-room?fase=${phase.id}`}');
    for (const phase of ["entiende", "decide", "ejecuta", "supervisa", "evoluciona"]) {
      expect(shell).toContain(`"${phase}"`);
    }
    expect(shell).toContain('permission: "datasets.read"');
    expect(shell).toContain('capability: "can_view_decisions"');
  });

  it("Entiende uses only the V2 GETs, the audited refresh and the audited action POSTs", () => {
    expect(controlRoomEndpoints(concat(ENTIENDE_FILES))).toEqual([
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
    const refresh = read("src/lib/control-room/use-control-room-refresh.ts");
    expect(refresh).toContain('includes("control_room.write")');
    expect(refresh).toContain("refetchOnly()");
    expect(refresh).toContain("invalidateControlRoomLive(queryClient, workspaceId)");
    expect(concat(ENTIENDE_FILES).match(/refreshControlRoomState\(/g)).toHaveLength(2);
  });

  it("confirms every mutating action in a dialog and navigates without window.location", () => {
    const dialog = read(
      "src/components/control-room/experience/ExperienceActionDialog.tsx",
    );
    const page = read(
      "src/components/control-room/experience/ControlRoomExperiencePage.tsx",
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

  it("keeps every legacy root surface and mutation disconnected from Entiende", () => {
    const source = concat(ENTIENDE_FILES);
    for (const forbidden of [
      "getControlRoomDashboard",
      "SuccessFactorsGoldPanel",
      "AnalyticAppsPanel",
      "AgentsOpsPanel",
      "MarketDecisionEvidencePanel",
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
    const experienceHook = read("src/lib/control-room/use-control-room-experience.ts");
    const liveHook = read("src/lib/control-room/use-control-room-live.ts");
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
    expect(concat(ENTIENDE_FILES)).not.toContain("setInterval(");
  });

  it("the empty-state diagnostic reads only the diagnostics GET", () => {
    const source = concat(DIAGNOSTIC_FILES);

    expect(controlRoomEndpoints(source)).toEqual(['"/api/control-room/diagnostics"']);
    expect(source).toContain("api.get");
    for (const forbidden of ["api.post", "api.put", "api.patch", "api.delete"]) {
      expect(source).not.toContain(forbidden);
    }
    expect(source).toContain("Diagnóstico preliminar de fuentes");
    expect(source).toContain("Plan de acción sugerido");
    expect(source).toContain('"/marketplace?tab=conectadas"');
    expect(source).toContain('"/studio"');
  });

  it("Decide talks only to the intelligence API through its existing client", () => {
    const source = concat(DECIDE_FILES);

    expect(controlRoomEndpoints(source)).toEqual([]);
    const paths = apiPaths(source);
    expect(paths.length).toBeGreaterThan(0);
    for (const path of paths) {
      expect(path.startsWith("/api/intelligence")).toBe(true);
    }
  });

  it("Ejecuta talks only to the council endpoints", () => {
    const source = concat(EJECUTA_FILES);

    expect(controlRoomEndpoints(source)).toEqual(['"/api/control-room/council"']);
    const paths = apiPaths(source);
    for (const path of paths) {
      expect(path.startsWith("/api/control-room/council")).toBe(true);
    }
  });

  it("Supervisa keeps the #551 preview-only invariant: no approve/execute path", () => {
    const source = concat(SUPERVISA_FILES);

    expect(controlRoomEndpoints(source)).toEqual([]);
    for (const forbidden of [
      "/approve",
      "/execute",
      "approveSupervisedAction",
      "executeSupervisedAction",
      "Aprobar",
      "Ejecutar",
    ]) {
      expect(source).not.toContain(forbidden);
    }
    expect(source).toContain("listDecisions");
    expect(source).toContain("listSupervisedActions");
    expect(source).toContain("Sin información");
    expect(source).toContain("Sin fecha compromiso");
  });

  it("Evoluciona reads lessons and the learning view without console KB writes", () => {
    const source = concat(EVOLUCIONA_FILES);

    expect(source).toContain("getControlRoomLessons");
    expect(source).toContain('getSapB1View("sap_b1_learning_kpis")');
    expect(source).toContain("Candidata a incorporarse al paquete");
    expect(apiPaths(source)).toEqual([]);
    for (const forbidden of ["api.post", "api.put", "upsertThreshold", "applyLesson"]) {
      expect(source).not.toContain(forbidden);
    }
  });

  it("keeps the global bans across every phase surface", () => {
    const source = concat(ALL_PHASE_FILES);
    for (const forbidden of [
      "toast.",
      "setInterval(",
      "window.location",
      "SuccessFactorsGoldPanel",
      "analytics",
    ]) {
      expect(source).not.toContain(forbidden);
    }
  });

  it("scopes window.location.replace to the three redirect shells only", () => {
    for (const shell of [
      "src/app/(shell)/operational-intelligence/page.tsx",
      "src/app/(shell)/supervised-actions/page.tsx",
      "src/app/(shell)/decisions/page.tsx",
    ]) {
      expect(read(shell)).toContain("window.location.replace(");
    }
  });
});
