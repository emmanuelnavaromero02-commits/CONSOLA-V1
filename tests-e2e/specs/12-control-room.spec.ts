import { test, expect } from "../fixtures/auth";
import type { Page } from "@playwright/test";

const LEGACY = process.env.LEGACY_URL || "http://localhost:8000";
// Control Room lives on the FastAPI backend. Honour CONTROL_ROOM_URL so the
// documented env var is actually wired; default keeps it on :8000. The
// "no :3000 calls" assertions below enforce the invariant regardless of how
// this is configured.
const CONTROL_ROOM = process.env.CONTROL_ROOM_URL || `${LEGACY}/control-room`;

function extractCsrfFromSetCookie(setCookie: string | undefined): string | null {
  const match = (setCookie || "").match(/csrf_token=([^;,\s]+)/);
  return match?.[1] || null;
}

async function csrfToken(page: Page): Promise<string> {
  let cookies = await page.context().cookies(LEGACY);
  let token = cookies.find((cookie) => cookie.name === "csrf_token")?.value;
  if (!token) {
    const response = await page.request.get(`${LEGACY}/login`, { timeout: 30_000 });
    cookies = await page.context().cookies(LEGACY);
    token = cookies.find((cookie) => cookie.name === "csrf_token")?.value;
    const seeded = token || extractCsrfFromSetCookie(response.headers()["set-cookie"]);
    if (seeded && !token) {
      await page.context().addCookies([{ name: "csrf_token", value: seeded, url: LEGACY }]);
      token = seeded;
    }
  }
  expect(token, "csrf_token cookie must be present for control-room mutations").toBeTruthy();
  return token || "";
}

async function controlRoomDashboard(page: Page) {
  const response = await page.request.get(`${LEGACY}/api/control-room/dashboard`, {
    timeout: 30_000,
  });
  expect(response.status(), "control-room dashboard API must respond").toBe(200);
  return response.json();
}

test.describe("Control Room OMEGA on FastAPI :8000", () => {
  test("loads hydrated operational cockpit from :8000 without :3000 calls", async ({
    authedPage: page,
  }) => {
    const consoleErrors: string[] = [];
    const forbidden3000: string[] = [];

    page.on("console", (message) => {
      if (message.type() === "error") {
        consoleErrors.push(message.text());
      }
    });
    page.on("request", (request) => {
      const url = new URL(request.url());
      if (url.port === "3000") {
        forbidden3000.push(request.url());
      }
    });

    const dashboard = await controlRoomDashboard(page);
    expect(dashboard.cartridges.length, "active cartridges must come from backend catalog").toBeGreaterThan(0);
    expect(dashboard.summary.active_connectors, "dashboard must distinguish commercial connectors").toBeGreaterThan(0);
    expect(dashboard.summary.active_modules, "dashboard must expose operational modules").toBeGreaterThan(0);
    expect(dashboard.summary.data_ready_modules, "dashboard must expose data-ready module count").toBeGreaterThanOrEqual(0);
    expect(dashboard.summary.data_readiness, "dashboard must expose source data-readiness rollup").toBeTruthy();
    expect(dashboard.summary.alerts.total, "dashboard must expose operational alert queue").toBeGreaterThan(0);
    expect(dashboard.summary.alerts.push_ready, "external push must stay disabled until delivery connectors exist").toBe(0);
    const alertsResponse = await page.request.get(`${LEGACY}/api/control-room/alerts`, { timeout: 30_000 });
    expect(alertsResponse.status(), "control-room alerts API must respond").toBe(200);
    const alertsPayload = await alertsResponse.json();
    expect(alertsPayload.summary.total, "alerts API must expose alert summary").toBeGreaterThan(0);
    expect(dashboard.meta?.live_mode, "dashboard must expose live refresh mode").toBe("polling");
    expect(dashboard.meta?.refresh_interval_seconds, "dashboard must publish polling interval").toBe(30);
    expect(dashboard.sources.every((source: { checked_at?: string }) => Boolean(source.checked_at))).toBe(true);
    expect(dashboard.sources.every((source: { data_readiness?: string; operationally_ready?: boolean }) => (
      Boolean(source.data_readiness) && typeof source.operationally_ready === "boolean"
    ))).toBe(true);
    expect(dashboard.sources.every((source: { data_readiness?: string; operationally_ready?: boolean }) => (
      !["partial", "stub"].includes(source.data_readiness || "") || source.operationally_ready === false
    ))).toBe(true);
    const csrf = await csrfToken(page);
    const seededThreshold = {
      cartridge_id: "replicon",
      anomaly_type: "low_margin",
      metric: "margen_bruto_pct",
      warning_value: 21,
      critical_value: 1,
      currency: "USD",
      enabled: true,
    };
    const seedThresholdResponse = await page.request.post(`${LEGACY}/api/control-room/thresholds`, {
      headers: { "X-CSRF-Token": csrf },
      data: seededThreshold,
    });
    expect(seedThresholdResponse.status(), "control-room threshold seed must be accepted").toBe(200);

    const response = await page.goto(CONTROL_ROOM, {
      waitUntil: "domcontentloaded",
    });
    expect(response?.status(), "/control-room must be served by FastAPI").toBe(200);
    await expect(page.getByRole("heading", { name: /^dashboard operativo$/i, level: 1 })).toBeVisible();
    await expect(page.getByRole("button", { name: /^recursos humanos\s+\d+/i }).first()).toBeVisible({
      timeout: 30_000,
    });
    await expect(page.getByText(/conectores .* frentes con señales/i).first()).toBeVisible();
    // Contextual breadcrumb: portfolio level shows "Sala de Control / Todos".
    const breadcrumb = page.getByRole("navigation", { name: /ruta de navegaci[oó]n/i });
    await expect(breadcrumb).toBeVisible();
    await expect(breadcrumb).toContainText(/sala de control/i);
    await expect(breadcrumb).toContainText(/todos/i);
    await expect(page.getByRole("region", { name: /contexto activo/i })).toContainText(/vista ejecutiva/i);
    await expect(page.getByRole("button", { name: /refrescar/i })).toBeEnabled({
      timeout: 15_000,
    });
    await expect(page.getByText(/vivo 30s/i).first()).toBeVisible();
    await expect(page.getByText(/siguiente/i).first()).toBeVisible();
    // Runtime confidence: Control Room V1 is explicit about supervised execution
    // and only advertises ERP write-back when the external flag is enabled.
    await expect(page.getByText(/supervisada/i).first()).toBeVisible();
    await expect(page.getByLabel(/navegaci[oó]n operativa/i)).toBeVisible();
    await expect(page.getByLabel(/n[uú]meros ejecutivos/i)).toBeVisible();
    await expect(page.getByLabel(/centro de mando visual/i)).toBeVisible();
    await expect(page.getByLabel(/indicadores ejecutivos de personal/i)).toBeVisible();
    await expect(page.getByLabel(/cola de alertas operativas/i)).toContainText(/prioridad/i);
    await expect(page.getByLabel(/cola de alertas operativas/i)).toContainText(/cola interna/i);
    const alertQueue = page.getByLabel(/cola de alertas operativas/i);
    await alertQueue.getByRole("button", { name: /reconocer/i }).first().click();
    await expect(alertQueue.getByText(/alerta reconocida/i)).toBeVisible({
      timeout: 15_000,
    });
    await alertQueue.getByRole("button", { name: /posponer 24h/i }).first().click();
    await expect(alertQueue.getByText(/alerta pospuesta 24h/i)).toBeVisible({
      timeout: 15_000,
    });
    await alertQueue.getByRole("button", { name: /asignarme/i }).first().click();
    await expect(alertQueue.getByText(/alerta asignada/i)).toBeVisible({
      timeout: 15_000,
    });
    await expect(page.getByLabel(/estado por dominio/i)).toBeVisible();
    await expect(page.getByLabel(/anomal[ií]as detectadas/i)).toBeVisible();
    await expect(page.getByLabel(/reglas de decisi[oó]n del contexto/i)).toBeVisible();
    await expect(page.getByLabel(/reglas de decisi[oó]n visibles/i)).toContainText(/margen bajo/i);
    await expect(page.getByText(/conectores .* frentes/i).first()).toBeVisible();

    await page.getByLabel(/regla de decisi[oó]n/i).selectOption("replicon:low_margin:margen_bruto_pct");
    await page.getByLabel(/valor de advertencia/i).fill("22");
    await page.getByLabel(/valor cr[ií]tico/i).fill("2");
    await page.getByRole("button", { name: /guardar regla/i }).click();
    await expect(page.getByText(/regla guardada/i)).toBeVisible({
      timeout: 15_000,
    });
    const thresholdStateResponse = await page.request.get(`${LEGACY}/api/control-room/thresholds`, {
      timeout: 30_000,
    });
    expect(thresholdStateResponse.status(), "thresholds API must expose UI-updated rule").toBe(200);
    const thresholdState = await thresholdStateResponse.json();
    expect(
      thresholdState.thresholds.some((threshold: {
        cartridge_id?: string;
        anomaly_type?: string;
        metric?: string;
        warning_value?: number;
        critical_value?: number;
        enabled?: boolean;
      }) => (
        threshold.cartridge_id === "replicon"
        && threshold.anomaly_type === "low_margin"
        && threshold.metric === "margen_bruto_pct"
        && threshold.warning_value === 22
        && threshold.critical_value === 2
        && threshold.enabled === true
      )),
      "threshold override must be persisted workspace-scoped",
    ).toBe(true);

    const refreshButton = page.getByRole("button", { name: /refrescar/i });
    await expect(refreshButton).toBeEnabled({ timeout: 20_000 });
    const refreshResponse = page.waitForResponse((apiResponse) => (
      apiResponse.url().includes("/api/control-room/dashboard") && apiResponse.status() === 200
    ));
    await refreshButton.click();
    await refreshResponse;
    await expect(page.getByText(/actualizado/i).first()).toBeVisible();

    expect(forbidden3000, "control-room assets and APIs must not call :3000").toEqual([]);
    expect(consoleErrors, "control-room must not emit console.error").toEqual([]);

    await page.request.patch(`${LEGACY}/api/control-room/thresholds`, {
      headers: { "X-CSRF-Token": csrf },
      data: { ...seededThreshold, enabled: false },
    });
  });

  test("navigates domain and module contexts in the same :8000 tab", async ({
    authedPage: page,
  }) => {
    const consoleErrors: string[] = [];
    const forbidden3000: string[] = [];
    page.on("console", (message) => {
      if (message.type() === "error") consoleErrors.push(message.text());
    });
    page.on("request", (request) => {
      const url = new URL(request.url());
      if (url.port === "3000") forbidden3000.push(request.url());
    });

    const response = await page.goto(CONTROL_ROOM, {
      waitUntil: "domcontentloaded",
    });
    expect(response?.status(), "/control-room must be served by FastAPI").toBe(200);
    await expect(page.getByRole("heading", { name: /^dashboard operativo$/i, level: 1 })).toBeVisible();
    await expect(page.getByRole("button", { name: /^recursos humanos\s+\d+/i }).first()).toBeVisible({
      timeout: 30_000,
    });

    await page.getByRole("button", { name: /^recursos humanos\s+\d+/i }).first().click();
    await expect(page).toHaveURL(/\/control-room\?domain=Recursos(?:\+|%20)Humanos/);
    await expect(page.getByRole("heading", { name: /^recursos humanos$/i, level: 1 })).toBeVisible();
    await expect(page.getByRole("navigation", { name: /ruta de navegaci[oó]n/i })).toContainText(/recursos humanos/i);
    await expect(page.getByRole("region", { name: /contexto activo/i })).toContainText(/vista de [aá]rea/i);
    await expect(page.getByRole("region", { name: /panel operativo contextual/i })).toContainText(/vista exclusiva/i);
    await expect(page.getByLabel(/lecciones aprendidas del contexto/i)).toContainText(/reglas visibles/i);
    await expect(page.getByLabel(/cola de alertas operativas/i)).toContainText(/alertas activas/i);
    await expect(page.getByText(/actualizado/i).first()).toBeVisible();
    await expect(page.getByLabel(/anomal[ií]as detectadas/i)).toContainText(/recursos humanos/i);
    await expect(page.getByLabel(/indicadores ejecutivos de personal/i)).toContainText(/successfactors/i);

    await page.getByLabel(/^frente personal\s+\d+$/i).first().click();
    await expect.poll(
      () => new URL(page.url()).searchParams.get("module"),
      { message: "the Personal front must navigate to the scoped SuccessFactors module", timeout: 15_000 },
    ).toBe("sap_successfactors");
    await expect(page.getByRole("heading", { name: /^personal$/i, level: 1 })).toBeVisible();
    await expect(page.getByRole("navigation", { name: /ruta de navegaci[oó]n/i })).toContainText(/personal/i);
    await expect(page.getByRole("region", { name: /contexto activo/i })).toContainText(/vista de frente/i);
    await expect(page.getByRole("region", { name: /panel operativo contextual/i })).toContainText(/vista exclusiva/i);
    await expect(page.getByText(/estado del frente/i)).toBeVisible();
    await expect(page.getByLabel(/estado por dominio/i)).toContainText(/personal/i);
    await expect(page.getByText(/diagn[oó]stico interno/i).first()).toBeVisible();

    await page.goto(`${CONTROL_ROOM}?module=sap_successfactors`, {
      waitUntil: "domcontentloaded",
    });
    await expect(page.getByRole("heading", { name: /^personal$/i, level: 1 })).toBeVisible();
    await expect(page.getByRole("region", { name: /contexto activo/i })).toContainText(/vista de frente/i);
    await expect(page.getByText(/diagn[oó]stico interno/i).first()).toBeVisible();

    expect(forbidden3000, "control-room navigation must not call :3000").toEqual([]);
    expect(consoleErrors, "control-room navigation must not emit console.error").toEqual([]);
  });

  test("runs item -> decision -> approval -> audit without relying on :3000", async ({
    authedPage: page,
  }) => {
    test.slow();
    const dashboard = await controlRoomDashboard(page);
    expect(dashboard.items.length, "dashboard must expose at least one real operational item").toBeGreaterThan(0);
    const terminalStatuses = new Set(["approved", "dismissed", "resolved"]);
    const targetItem = [...dashboard.items]
      .filter((item: { status?: string }) => !terminalStatuses.has(String(item.status || "")))
      .sort((left: { priority_score?: number; severity_weight?: number }, right: { priority_score?: number; severity_weight?: number }) => (
        (right.priority_score || 0) - (left.priority_score || 0)
        || (right.severity_weight || 0) - (left.severity_weight || 0)
      ))[0] || dashboard.items[0];
    const csrf = await csrfToken(page);
    const autoItem = dashboard.items.find((item: { id?: string; status?: string }) => (
      item.id !== targetItem.id && !["approved", "dismissed", "resolved"].includes(String(item.status || ""))
    ));
    if (autoItem?.id) {
      const autoResetResponse = await page.request.post(
        `${LEGACY}/api/control-room/items/${encodeURIComponent(autoItem.id)}/reopen`,
        {
          headers: { "X-CSRF-Token": csrf },
          data: { reason: "E2E reset before automatic mode" },
        },
      );
      expect(autoResetResponse.status(), "auto-run item must be reopenable").toBe(200);
      const autoRunResponse = await page.request.post(
        `${LEGACY}/api/control-room/items/${encodeURIComponent(autoItem.id)}/auto-run`,
        {
          headers: { "X-CSRF-Token": csrf },
          data: {},
        },
      );
      expect(autoRunResponse.status(), "server-side automatic mode must complete safely").toBe(200);
      const autoRun = await autoRunResponse.json();
      expect(autoRun.auto_run.completed).toBe(true);
      expect(autoRun.auto_run.stopped_before_writeback).toBe(true);
      expect(autoRun.item.execution_status).toBe("dry_run_validated");
      const autoActivityResponse = await page.request.get(
        `${LEGACY}/api/control-room/items/${encodeURIComponent(autoItem.id)}/activity`,
        { timeout: 30_000 },
      );
      expect(autoActivityResponse.status(), "auto-run activity must be visible").toBe(200);
      const autoActivity = await autoActivityResponse.json();
      const autoActivityTypes = autoActivity.activity.map((entry: { type?: string }) => entry.type);
      expect(autoActivityTypes).toContain("auto_run_completed");
      expect(autoActivityTypes).toContain("action_dry_run");
    }
    const reopenResponse = await page.request.post(
      `${LEGACY}/api/control-room/items/${encodeURIComponent(targetItem.id)}/reopen`,
      {
        headers: { "X-CSRF-Token": csrf },
        data: { reason: "E2E reset before decision flow" },
      },
    );
    expect(reopenResponse.status(), "control-room item must be reopenable before E2E mutation").toBe(200);

    const consoleErrors: string[] = [];
    const forbidden3000: string[] = [];

    page.on("console", (message) => {
      if (message.type() === "error") {
        consoleErrors.push(message.text());
      }
    });
    page.on("request", (request) => {
      const url = new URL(request.url());
      if (url.port === "3000") {
        forbidden3000.push(request.url());
      }
    });

    const response = await page.goto(CONTROL_ROOM, {
      waitUntil: "domcontentloaded",
    });
    expect(response?.status(), "/control-room must be served by FastAPI").toBe(200);
    await expect(page.getByRole("heading", { name: /^dashboard operativo$/i, level: 1 })).toBeVisible();
    await expect(page.getByRole("button", { name: /^recursos humanos\s+\d+/i }).first()).toBeVisible({
      timeout: 30_000,
    });

    let openedItemId = String(targetItem.id || "");
    const targetButton = page.getByRole("button", { name: /abrir zona de decisi[oó]n/i }).first();
    await expect(targetButton, "the control room must render the selected operational item").toBeVisible({
      timeout: 15_000,
    });
    await targetButton.click();

    await expect(page.getByRole("button", { name: /volver/i })).toBeVisible();
    await expect(page.getByRole("button", { name: /modo autom[aá]tico/i })).toBeVisible();
    await expect(page.getByRole("button", { name: /modo manual/i })).toBeVisible();
    await page.getByRole("button", { name: /modo manual/i }).click();
    await expect(page.getByRole("tab", { name: /investigaci[oó]n/i })).toBeVisible();
    await expect(page.getByRole("tab", { name: /opciones/i })).toBeVisible();
    await expect(page.getByRole("tab", { name: /ejecuci[oó]n/i })).toBeVisible();
    await expect(page.getByRole("tab", { name: /control/i })).toBeVisible();
    await expect(page.getByRole("tab", { name: /reglas/i })).toBeVisible();
    // Each manual step renders as a titled section ("Paso N de 6").
    await expect(page.getByText(/paso 1 de 6/i)).toBeVisible();
    await expect(page.getByRole("region", { name: /bit[aá]cora operativa/i })).toBeVisible();
    await page.getByRole("button", { name: /registrar investigaci[oó]n revisada/i }).click();
    await expect(page.getByText(/investigaci[oó]n revisada/i).first()).toBeVisible({
      timeout: 15_000,
    });
    await page.getByRole("tab", { name: /opciones/i }).click();
    await expect(page.getByText(/prioridad/i).first()).toBeVisible();
    const exceptionOption = page.getByRole("button", { name: /aprobar excepci[oó]n temporal/i });
    await expect(exceptionOption).toBeVisible();
    const optionPersistResponse = page.waitForResponse((response) => {
      const url = new URL(response.url());
      return response.request().method() === "POST"
        && url.pathname.includes("/api/control-room/items/")
        && url.pathname.endsWith("/option");
    });
    await exceptionOption.click();
    const optionResponse = await optionPersistResponse;
    expect(optionResponse.status(), "selected option POST must succeed").toBe(200);
    const optionPayload = await optionResponse.json();
    openedItemId = String(optionPayload.item?.id || optionPayload.anomaly?.id || openedItemId);
    expect(optionPayload.option_id || optionPayload.item?.selected_option_id).toBe("exception");
    await expect(exceptionOption).toHaveAttribute("aria-pressed", "true", {
      timeout: 15_000,
    });
    const optionStateResponse = await page.request.get(
      `${LEGACY}/api/control-room/items/${encodeURIComponent(openedItemId)}`,
      { timeout: 30_000 },
    );
    expect(optionStateResponse.status(), "selected option must be persisted in backend").toBe(200);
    const optionState = await optionStateResponse.json();
    expect(optionState.selected_option_id).toBe("exception");

    await page.getByRole("tab", { name: /^decisi[oó]n$/i }).click();
    await expect(page.getByRole("button", { name: /crear decisi[oó]n/i })).toBeVisible();
    await page.getByRole("button", { name: /crear decisi[oó]n/i }).click();
    await expect(page.getByRole("button", { name: /decisi[oó]n #/i })).toBeVisible({
      timeout: 15_000,
    });

    await page.getByRole("tab", { name: /ejecuci[oó]n/i }).click();
    // Execution clarity: V1 does not promise universal ERP/SAP write-back.
    await expect(page.getByText(/registra ejecuci[oó]n supervisada|seguimiento auditado disponible|sin ejecuci[oó]n disponible/i).first()).toBeVisible();
    await expect(page.getByRole("button", { name: /revisar antes de ejecutar/i }).first()).toBeVisible();
    await page.getByRole("button", { name: /revisar antes de ejecutar/i }).first().click();
    await expect(page.getByText(/ejecuci[oó]n actualizada/i)).toBeVisible({
      timeout: 15_000,
    });
    await page.getByRole("button", { name: /validar antes de ejecutar/i }).first().click();
    await expect(page.getByText(/ejecuci[oó]n actualizada/i)).toBeVisible({
      timeout: 15_000,
    });

    await page.getByRole("button", { name: /aprobar recomendaci[oó]n/i }).click();
    await expect(page.getByRole("button", { name: /recomendaci[oó]n aprobada/i })).toBeVisible({
      timeout: 15_000,
    });
    await expect(page.getByRole("status").filter({ hasText: /aprobaci[oó]n registrada/i })).toBeVisible({
      timeout: 15_000,
    });
    await page.getByRole("tab", { name: /control/i }).click();
    await page.getByRole("button", { name: /confirmar control/i }).first().click();
    await expect(page.getByText(/control confirmado/i).first()).toBeVisible({
      timeout: 15_000,
    });
    const controlStateResponse = await page.request.get(
      `${LEGACY}/api/control-room/items/${encodeURIComponent(openedItemId)}`,
      { timeout: 30_000 },
    );
    expect(controlStateResponse.status(), "control item state must be persisted").toBe(200);
    const controlState = await controlStateResponse.json();
    expect(
      controlState.omega.control.items.some((control: { id?: string; status?: string; owner?: string; due_at?: string }) => (
        control.id === "refresh"
        && control.status === "closed"
        && Boolean(control.owner)
        && Boolean(control.due_at)
      )),
      "control follow-up must persist status, owner and due date",
    ).toBe(true);
    await page.getByRole("tab", { name: /reglas/i }).click();
    const manualLesson = `Leccion E2E ${Date.now()}: validar owner antes de aprobar`;
    await page.getByLabel(/nueva lecci[oó]n persistida/i).fill(manualLesson);
    await page.getByRole("button", { name: /guardar lecci[oó]n/i }).click();
    await expect(page.getByText(manualLesson).first()).toBeVisible({
      timeout: 15_000,
    });
    const applyLessonButton = page.getByRole("button", { name: /aplicar lecci[oó]n/i }).first();
    await expect(applyLessonButton).toBeVisible({ timeout: 15_000 });
    await applyLessonButton.click();
    await expect(page.getByText(/lecci[oó]n aplicada/i).first()).toBeVisible({
      timeout: 15_000,
    });

    const lessonStateResponse = await page.request.get(
      `${LEGACY}/api/control-room/items/${encodeURIComponent(openedItemId)}`,
      { timeout: 30_000 },
    );
    expect(lessonStateResponse.status(), "lesson application state must be persisted").toBe(200);
    const lessonState = await lessonStateResponse.json();
    expect(
      Array.isArray(lessonState.lesson_applications)
      && lessonState.lesson_applications.some((entry: { rule?: string }) => entry.rule === manualLesson),
      "applied lesson must be stored on the control room item",
    ).toBe(true);

    const activityResponse = await page.request.get(
      `${LEGACY}/api/control-room/items/${encodeURIComponent(openedItemId)}/activity`,
      { timeout: 30_000 },
    );
    expect(activityResponse.status(), "control-room item must expose visible activity trail").toBe(200);
    const activity = await activityResponse.json();
    expect(activity.counts.total, "activity trail must have persisted operational events").toBeGreaterThanOrEqual(5);
    const activityTypes = activity.activity.map((entry: { type?: string }) => entry.type);
    expect(activityTypes).toContain("investigation_reviewed");
    expect(activityTypes).toContain("option_selected");
    expect(activityTypes).toContain("action_preview");
    expect(activityTypes).toContain("action_dry_run");
    expect(activityTypes).toContain("approved");
    expect(activityTypes).toContain("control_checked");
    expect(activityTypes).toContain("lesson_recorded");
    expect(activityTypes).toContain("lesson_applied");

    const auditResponse = await page.request.get(`${LEGACY}/security/audit`, {
      timeout: 30_000,
    });
    expect(auditResponse.status(), "/security/audit must expose the audit trail").toBe(200);
    const auditEvents = await auditResponse.json();
    expect(Array.isArray(auditEvents)).toBe(true);
    expect(
      auditEvents.some((event: { action?: string }) => event.action === "control_room.approve"),
      "approval must be written to audit_events",
    ).toBe(true);
    expect(
      auditEvents.some((event: { action?: string }) => event.action === "control_room.lesson.apply"),
      "lesson application must be written to audit_events",
    ).toBe(true);

    const lessonsResponse = await page.request.get(
      `${LEGACY}/api/control-room/lessons?cartridge_id=${encodeURIComponent(targetItem.cartridge)}&anomaly_type=${encodeURIComponent(targetItem.anomaly_type)}`,
      { timeout: 30_000 },
    );
    expect(lessonsResponse.status(), "approval must expose persisted lessons").toBe(200);
    const lessons = await lessonsResponse.json();
    expect(lessons.summary.total, "approval should create at least one learned rule").toBeGreaterThan(0);
    expect(
      lessons.lessons.some((lesson: { item_id?: string; source_decision_id?: number | null }) => (
        lesson.item_id === targetItem.id || Boolean(lesson.source_decision_id)
      )),
      "lessons endpoint must return item or decision-linked memory",
    ).toBe(true);

    const cleanupResponse = await page.request.post(
      `${LEGACY}/api/control-room/items/${encodeURIComponent(targetItem.id)}/reopen`,
      {
        headers: { "X-CSRF-Token": await csrfToken(page) },
        data: { reason: "E2E cleanup after approval assertion" },
      },
    );
    expect(cleanupResponse.status(), "E2E cleanup must reopen the mutated control-room item").toBe(200);

    expect(forbidden3000, "control-room assets and APIs must not call :3000").toEqual([]);
    expect(consoleErrors, "control-room must not emit console.error").toEqual([]);
  });
});
