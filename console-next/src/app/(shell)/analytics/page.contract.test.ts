import { readFileSync } from "node:fs";
import { join } from "node:path";

import { describe, expect, it } from "vitest";

/**
 * Boundary for the Analytics surface, mirroring the Control Room contract test.
 *
 * Analytics owns exploration (open a published dashboard); Control Room owns
 * commitment (what needs attention, with evidence and an auditable action).
 * The apps used to live inside Control Room and the cutover left them homeless;
 * this test keeps the two surfaces from merging again.
 */
const analyticsFiles = [
  "src/app/(shell)/analytics/page.tsx",
  "src/app/(shell)/analytics/viewer/page.tsx",
  "src/components/analytics/AppCatalog.tsx",
  "src/components/analytics/AppViewer.tsx",
  "src/lib/hooks/useAnalyticsApps.ts",
];
const read = (path: string) => readFileSync(join(process.cwd(), path), "utf8");
const source = analyticsFiles.map(read).join("\n");

describe("Analytics surface boundary", () => {
  it("reads the catalog from the authorised apps API only", () => {
    const endpoints = source.match(/"\/api\/[^"]+"/g) ?? [];
    // listApps() owns the /api/apps call; the surface adds no second backend.
    expect(endpoints).toEqual([]);
    expect(source).toContain("listApps");
  });

  it("never reaches into Control Room endpoints", () => {
    expect(source).not.toContain("/api/control-room");
    expect(source).not.toContain("experience/v2");
  });

  it("does not resurrect the orphaned Control Room panels", () => {
    for (const forbidden of [
      "AnalyticAppsPanel",
      "SuccessFactorsGoldPanel",
      "AgentsOpsPanel",
      "MarketDecisionEvidencePanel",
    ]) {
      expect(source).not.toContain(forbidden);
    }
  });

  it("keeps the viewer on a statically exportable route", () => {
    // output: "export" cannot pre-render an [app] segment for runtime data, so
    // the app name travels in the query string.
    const viewer = read("src/app/(shell)/analytics/viewer/page.tsx");
    expect(viewer).toContain('params.get("app")');
    expect(source).toContain("/analytics/viewer?app=");
  });

  it("builds the app URL from an encoded, validated name", () => {
    const viewer = read("src/components/analytics/AppViewer.tsx");
    expect(viewer).toContain("isPublishedAppName");
    expect(viewer).toContain("encodeURIComponent(app.name)");
    // The iframe target is always a same-origin path.
    expect(viewer).toContain("src={`/apps/${encodeURIComponent(app.name)}`}");
  });

  it("only offers apps the API returned for this caller", () => {
    const viewer = read("src/components/analytics/AppViewer.tsx");
    expect(viewer).toContain("data?.apps?.find");
  });

  it("renders every honest data state", () => {
    const catalog = read("src/components/analytics/AppCatalog.tsx");
    for (const state of [
      "Datos completos",
      "Datos incompletos",
      "Estado no informado",
      "Frescura no informada",
      "No hay aplicaciones disponibles",
      "No tienes acceso",
    ]) {
      expect(catalog).toContain(state);
    }
    expect(catalog).toContain('role="alert"');
  });
});

describe("navigation no longer points at the removed anchor", () => {
  it("sends the sidebar entry to Analytics", () => {
    const sidebar = read("src/components/AppSidebar.tsx");
    expect(sidebar).toContain('href: "/analytics"');
    expect(sidebar).not.toContain("/control-room#apps");
  });

  it("redirects the legacy gallery to the canonical catalog", () => {
    const gallery = read("src/app/(shell)/apps-gallery/page.tsx");
    expect(gallery).toContain('window.location.replace("/analytics")');
  });

  it("gives the Marketplace app counter a destination", () => {
    const marketplace = read("src/components/marketplace/MarketplaceConsole.tsx");
    expect(marketplace).toContain("/analytics?cartridge=");
  });

  it("leaves Control Room mounting only its own experience", () => {
    const page = read("src/app/(shell)/control-room/page.tsx");
    expect(page).toContain("ControlRoomExperiencePage");
    expect(page).not.toContain("Analytics");
    expect(page).not.toContain("AppCatalog");
  });
});
