import { test, expect } from "../fixtures/auth";
import type { Page } from "@playwright/test";

const LEGACY = process.env.LEGACY_URL || "http://localhost:8000";

function escapeRegExp(value: string): string {
  return value.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
}

async function csrfToken(page: Page): Promise<string> {
  const cookies = await page.context().cookies(LEGACY);
  const token = cookies.find((cookie) => cookie.name === "csrf_token")?.value;
  expect(token, "csrf_token cookie must be present for control-room mutations").toBeTruthy();
  return token || "";
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

    const dashboardResponse = await page.request.get(`${LEGACY}/api/control-room/dashboard`);
    expect(dashboardResponse.status(), "control-room dashboard API must respond").toBe(200);
    const dashboard = await dashboardResponse.json();
    expect(dashboard.cartridges.length, "active cartridges must come from backend catalog").toBeGreaterThan(0);

    const response = await page.goto(`${LEGACY}/control-room`, {
      waitUntil: "domcontentloaded",
    });
    expect(response?.status(), "/control-room must be served by FastAPI").toBe(200);
    await expect(page.getByRole("heading", { name: /^dashboard operativo$/i })).toBeVisible();
    await expect(page.getByRole("button", { name: /refrescar/i })).toBeEnabled({
      timeout: 15_000,
    });
    await expect(page.getByLabel(/navegacion operativa/i)).toBeVisible();
    await expect(page.getByLabel(/estado por dominio/i)).toBeVisible();
    await expect(page.getByLabel(/anomalias detectadas/i)).toBeVisible();
    await expect(page.getByText(/duckdb \+ parquet/i)).toBeVisible();

    expect(forbidden3000, "control-room assets and APIs must not call :3000").toEqual([]);
    expect(consoleErrors, "control-room must not emit console.error").toEqual([]);
  });

  test("runs item -> decision -> approval -> audit without relying on :3000", async ({
    authedPage: page,
  }) => {
    const dashboardResponse = await page.request.get(`${LEGACY}/api/control-room/dashboard`);
    expect(dashboardResponse.status(), "control-room dashboard API must respond").toBe(200);
    const dashboard = await dashboardResponse.json();
    expect(dashboard.items.length, "dashboard must expose at least one real operational item").toBeGreaterThan(0);
    const targetItem = dashboard.items[0];
    const csrf = await csrfToken(page);
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

    const response = await page.goto(`${LEGACY}/control-room`, {
      waitUntil: "domcontentloaded",
    });
    expect(response?.status(), "/control-room must be served by FastAPI").toBe(200);
    await expect(page.getByRole("heading", { name: /^dashboard operativo$/i })).toBeVisible();

    const targetButton = page.getByRole("button", {
      name: new RegExp(
        `investigar\\s+${escapeRegExp(targetItem.title)}.*${escapeRegExp(targetItem.entity_label)}`,
        "i",
      ),
    }).first();
    await expect(targetButton, "the control room must render the selected operational item").toBeVisible({
      timeout: 15_000,
    });
    await targetButton.click();

    await expect(page.getByRole("button", { name: /volver/i })).toBeVisible();
    await expect(page.getByRole("button", { name: /modo automatico/i })).toBeVisible();
    await expect(page.getByRole("button", { name: /modo manual/i })).toBeVisible();
    await page.getByRole("button", { name: /modo manual/i }).click();
    await expect(page.getByRole("tab", { name: /investigacion/i })).toBeVisible();
    await expect(page.getByRole("tab", { name: /opciones/i })).toBeVisible();
    await expect(page.getByRole("tab", { name: /ejecucion/i })).toBeVisible();
    await expect(page.getByRole("tab", { name: /control/i })).toBeVisible();
    await expect(page.getByRole("tab", { name: /reglas/i })).toBeVisible();
    await page.getByRole("tab", { name: /opciones/i }).click();
    await expect(page.getByText(/score/i).first()).toBeVisible();
    const exceptionOption = page.getByRole("button", { name: /aprobar excepcion temporal/i });
    await expect(exceptionOption).toBeVisible();
    await exceptionOption.click();
    await expect(exceptionOption).toHaveAttribute("aria-pressed", "true", {
      timeout: 15_000,
    });
    const optionStateResponse = await page.request.get(
      `${LEGACY}/api/control-room/items/${encodeURIComponent(targetItem.id)}`,
    );
    expect(optionStateResponse.status(), "selected option must be persisted in backend").toBe(200);
    const optionState = await optionStateResponse.json();
    expect(optionState.selected_option_id).toBe("exception");
    await page.getByRole("tab", { name: /ejecucion/i }).click();
    await expect(page.getByRole("button", { name: /^preview$/i }).first()).toBeVisible();
    await page.getByRole("button", { name: /^preview$/i }).first().click();
    await expect(page.getByText(/preview_generated/i)).toBeVisible({
      timeout: 15_000,
    });
    await page.getByRole("button", { name: /dry-run/i }).first().click();
    await expect(page.getByText(/dry_run_validated/i)).toBeVisible({
      timeout: 15_000,
    });

    await expect(page.getByRole("button", { name: /crear decision/i })).toBeVisible();
    await page.getByRole("button", { name: /crear decision/i }).click();
    await expect(page.getByRole("button", { name: /decision #/i })).toBeVisible({
      timeout: 15_000,
    });

    await page.getByRole("button", { name: /aprobar recomendacion/i }).click();
    await expect(page.getByRole("button", { name: /recomendacion aprobada/i })).toBeVisible({
      timeout: 15_000,
    });

    const auditResponse = await page.request.get(`${LEGACY}/security/audit`);
    expect(auditResponse.status(), "/security/audit must expose the audit trail").toBe(200);
    const auditEvents = await auditResponse.json();
    expect(Array.isArray(auditEvents)).toBe(true);
    expect(
      auditEvents.some((event: { action?: string }) => event.action === "control_room.approve"),
      "approval must be written to audit_events",
    ).toBe(true);

    const cleanupResponse = await page.request.post(
      `${LEGACY}/api/control-room/items/${encodeURIComponent(targetItem.id)}/reopen`,
      {
        headers: { "X-CSRF-Token": csrf },
        data: { reason: "E2E cleanup after approval assertion" },
      },
    );
    expect(cleanupResponse.status(), "E2E cleanup must reopen the mutated control-room item").toBe(200);

    expect(forbidden3000, "control-room assets and APIs must not call :3000").toEqual([]);
    expect(consoleErrors, "control-room must not emit console.error").toEqual([]);
  });
});
