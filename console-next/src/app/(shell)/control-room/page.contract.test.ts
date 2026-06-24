import { readFileSync } from "node:fs";
import { join } from "node:path";

import { describe, expect, it } from "vitest";

const pageSource = readFileSync(join(process.cwd(), "src/app/(shell)/control-room/page.tsx"), "utf8");
const analyticAppsPanelSource = readFileSync(join(process.cwd(), "src/components/control-room/AnalyticAppsPanel.tsx"), "utf8");

describe("Control Room page functional contract", () => {
  it("keeps existing read surfaces wired after the visual redesign", () => {
    expect(pageSource).toContain("getControlRoomDashboard");
    expect(pageSource).toContain("getControlRoomActivity");
    expect(pageSource).toContain("getControlRoomImpact");
    expect(pageSource).toContain("getControlRoomLessons");
    expect(pageSource).toContain("getControlRoomThresholds");
    expect(pageSource).toContain("getSuccessFactorsGoldKpis");
    expect(pageSource).toContain("getSuccessFactorsTalentKpis");
    expect(pageSource).toContain("getSuccessFactorsDecisionModel");
    expect(pageSource).toContain("SuccessFactorsGoldPanel");
    expect(pageSource).toContain("SourceInventoryPanel");
  });

  it("keeps OMEGA detail capabilities accessible", () => {
    expect(pageSource).toContain("ImpactSnapshot");
    expect(pageSource).toContain("ActivityTrail");
    expect(pageSource).toContain("ThresholdRulesBoard");
    expect(pageSource).toContain("LessonsBoard");
    expect(pageSource).toContain("onApplyLesson");
    expect(pageSource).toContain("ExecutionStep");
    expect(pageSource).toContain("ControlStep");
  });

  it("keeps supervised execution and approval flows wired to existing endpoints", () => {
    expect(pageSource).toContain("/action-preview");
    expect(pageSource).toContain("/action-dry-run");
    expect(pageSource).toContain("/execute");
    expect(pageSource).toContain("/auto-run");
    expect(pageSource).toContain("/approve");
    expect(pageSource).toContain("/dismiss");
    expect(pageSource).toContain("/alerts/");
  });

  it("surfaces advisory agent monitor alerts distinctly", () => {
    expect(pageSource).toContain('type AlertSourceFilter = "all" | "agent" | "system" | "intelligence"');
    expect(pageSource).toContain("Agente monitor");
    expect(pageSource).toContain("Advisory");
    expect(pageSource).toContain("occurrence_count");
    expect(pageSource).toContain("expected_outcome");
  });

  it("keeps Sync Now scope explicit in the Control Room header", () => {
    expect(pageSource).toContain("Alcance de sincronización");
    expect(pageSource).toContain('const [controlSyncTarget, setControlSyncTarget] = useState<SyncTarget>("all")');
    expect(pageSource).toContain("syncTargetSupportsTalent");
    expect(pageSource).toContain('<option value="talent">Talento</option>');
    expect(pageSource).toContain("startCartridgeSyncNow(activeCartridge, { mode: \"incremental\", target })");
    expect(pageSource).toContain("SYNC_NOW_MAX_POLL_ATTEMPTS = 600");
    expect(pageSource).toContain("SYNC_NOW_POLL_INTERVAL_MS = 3000");
    expect(pageSource).not.toContain("startCartridgeSyncNow(activeCartridge, { mode: \"incremental\", target: \"all\" })");
    expect(pageSource).not.toContain("attempt < 40");
  });

  it("embeds analytic apps through the safe Control Room wrapper", () => {
    expect(pageSource).toContain("AnalyticAppsPanel");
    expect(pageSource).toContain("listApps");
    expect(pageSource).toContain("loadAnalyticsApps");
    expect(analyticAppsPanelSource).toContain("/embed");
    expect(analyticAppsPanelSource).toContain("iframe");
    expect(analyticAppsPanelSource).toContain("Apps analíticas");
    expect(analyticAppsPanelSource).toContain("Gráficas publicadas con datos Gold listos");
    expect(analyticAppsPanelSource).not.toContain('src={`/apps/${encodeURIComponent(activeApp.name)}`}');
  });

  it("keeps heavy Control Room sections in a single-open accordion", () => {
    expect(pageSource).toContain("type ControlRoomSectionId");
    expect(pageSource).toContain('useState<ControlRoomSectionId>("operations")');
    expect(pageSource).toContain("ControlRoomAccordionSection");
    expect(pageSource).toContain('id="operations"');
    expect(pageSource).toContain('id="apps"');
    expect(pageSource).toContain('id="signals"');
    expect(pageSource).toContain('id="rules"');
    expect(pageSource).toContain('id="lessons"');
    expect(pageSource).toContain("aria-expanded={open}");
  });

  it("surfaces deterministic math provenance and control origins", () => {
    expect(pageSource).toContain("OriginBadge");
    expect(pageSource).toContain("type ControlOrigin");
    expect(pageSource).toContain("math_provenance");
    expect(pageSource).toContain("monte_carlo");
    expect(pageSource).toContain("bayesianCalibrationStatus");
    expect(pageSource).toContain("bayesian_calibration");
    expect(pageSource).toContain("generic_gold_signal");
    expect(pageSource).toContain("bayesian_calibration");
    expect(pageSource).toContain("monte_carlo");
  });

  it("keeps the executive room self-contained and does not depend on the reference HTML", () => {
    expect(pageSource).not.toContain("sourceCatalogHref");
    expect(pageSource).not.toContain("sourceDataHref");
    expect(pageSource).not.toContain("sourceSchemaHref");
    expect(pageSource).not.toContain("/api/data/");
    expect(pageSource).not.toContain("/viewer?type=schema");
    expect(pageSource).not.toContain("/data/catalog");
    expect(pageSource).not.toContain("sap.html");
    expect(pageSource).not.toContain("mock");
  });
});
