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
    await expect(page.getByRole("heading", { name: /^sala de control$/i })).toBeVisible();
    await expect(page.getByRole("button", { name: /refrescar/i })).toBeEnabled({
      timeout: 15_000,
    });
    await expect(page.getByText(/dashboard operativo/i)).toBeVisible();
    await expect(page.getByLabel(/fuentes/i)).toBeVisible();

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
    await expect(page.getByRole("heading", { name: /^sala de control$/i })).toBeVisible();

    const targetButton = page.getByRole("button", {
      name: new RegExp(`investigar\\s+${escapeRegExp(targetItem.title)}`, "i"),
    }).first();
    await expect(targetButton, "the control room must render the selected operational item").toBeVisible({
      timeout: 15_000,
    });
    await targetButton.click();

    await expect(page.getByLabel(/ciclo omega/i)).toBeVisible();
    await expect(page.getByText(/senales/i).first()).toBeVisible();
    await expect(page.getByText(/investigacion/i).first()).toBeVisible();
    await expect(page.getByText(/lecciones/i).first()).toBeVisible();

    await expect(page.getByRole("button", { name: /crear decision/i })).toBeVisible();
    await page.getByRole("button", { name: /crear decision/i }).click();
    await expect(page.getByRole("button", { name: /decision #/i })).toBeVisible({
      timeout: 15_000,
    });

    await page.getByRole("button", { name: /aprobar recomendacion/i }).click();
    await expect(page.getByRole("button", { name: /aprobada/i })).toBeVisible({
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

    expect(forbidden3000, "control-room assets and APIs must not call :3000").toEqual([]);
    expect(consoleErrors, "control-room must not emit console.error").toEqual([]);
  });
});
