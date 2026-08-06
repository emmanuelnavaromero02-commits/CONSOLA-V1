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
  });

  it("loads the trusted wrapper, never the published app directly", () => {
    // Regression for the P0: the viewer framed `/apps/{name}` — untrusted,
    // externally-authored HTML — as a same-origin document, so a malicious
    // published app could read parent.document, storage, cookies and the CSRF
    // token, and call authenticated endpoints. `/embed` is first-party markup
    // we generate; it hosts the app in an opaque-origin child of its own.
    const viewer = read("src/components/analytics/AppViewer.tsx");
    expect(viewer).toContain("src={`/apps/${encodeURIComponent(app.name)}/embed`}");
    const iframeSrc = viewer.match(/src=\{`\/apps\/[^`]*`\}/g) ?? [];
    expect(iframeSrc).toEqual(["src={`/apps/${encodeURIComponent(app.name)}/embed`}"]);
    expect(viewer).not.toContain("src={`/apps/${encodeURIComponent(app.name)}`}");
  });

  it("keeps the outer sandbox minimal and downloads disabled", () => {
    // The outer sandbox is not the boundary — the wrapper needs
    // allow-same-origin to read the CSRF cookie and call the API for the user.
    // The boundary is the wrapper's inner frame. Still, nothing beyond those
    // two tokens is justified, and allow-downloads never was.
    const viewer = read("src/components/analytics/AppViewer.tsx");
    // Anchor to the JSX element: the surrounding comment names the inner
    // frame's own sandbox, which is a different (stricter) value.
    const iframe = viewer.slice(viewer.indexOf("<iframe"));
    const sandbox = iframe.match(/^\s*sandbox="([^"]*)"/m)?.[1] ?? "";
    expect(sandbox.split(/\s+/).filter(Boolean).sort()).toEqual([
      "allow-same-origin",
      "allow-scripts",
    ]);
    expect(sandbox).not.toContain("allow-downloads");
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
