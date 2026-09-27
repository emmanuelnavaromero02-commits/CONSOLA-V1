import { expect, test } from "../fixtures/auth";
import { FRESHNESS_PATH, liveFreshness } from "./helpers/control-room-experience";

const approveHandle = "a".repeat(64);
const disabledHandle = "b".repeat(64);
const successMessage =
  "Hallazgo archivado como excepción aprobada; puedes reabrirlo desde Excepciones aprobadas.";
const reason = "Proveedor validado por auditoría interna";

const experience = {
  schema_version: "control-room-experience/v2",
  generated_at: "2026-07-25T12:30:00Z",
  sections: [
    {
      title: "Performance",
      facts: [
        {
          kind: "kpi",
          title: "Cobertura crítica",
          entity_label: "Región Norte",
          severity: "low",
          observed_at: "2026-07-24T00:00:00Z",
          stale: false,
          actions: [
            {
              action_handle: approveHandle,
              kind: "exception_approval",
              label: "Aprobar Excepción",
              enabled: true,
              requires_approval: false,
            },
          ],
        },
        {
          kind: "signal",
          title: "Datos incompletos",
          severity: "medium",
          observed_at: "2026-07-24T00:00:00Z",
          stale: false,
          actions: [
            {
              action_handle: disabledHandle,
              kind: "decision_proposal",
              label: "Crear Propuesta de Decisión",
              enabled: false,
              requires_approval: false,
              disabled_reason: "Completa los datos requeridos antes de continuar.",
            },
          ],
        },
      ],
    },
  ],
  exceptions: [],
};

test("Experience approves an exception only after a reasoned confirmation", async ({
  authedPage: page,
}) => {
  const requests: Array<{ method: string; path: string; body: unknown }> = [];
  const observedNetwork: Array<{ method: string; path: string }> = [];
  page.on("request", (request) => {
    if (["fetch", "xhr"].includes(request.resourceType())) {
      observedNetwork.push({
        method: request.method(),
        path: new URL(request.url()).pathname,
      });
    }
  });
  await page.route("**/api/control-room/**", async (route) => {
    const request = route.request();
    const path = new URL(request.url()).pathname;
    const body = request.postDataJSON() ?? null;
    requests.push({ method: request.method(), path, body });
    if (request.method() === "GET" && path === FRESHNESS_PATH) {
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify(liveFreshness),
      });
      return;
    }
    if (request.method() === "GET" && path === "/api/control-room/experience/v2") {
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify(experience),
      });
      return;
    }
    if (request.method() === "POST" && path === "/api/control-room/actions/exception") {
      expect(body).toMatchObject({ action_handle: approveHandle, reason });
      expect(Object.keys(body as object).sort()).toEqual([
        "action_handle",
        "idempotency_key",
        "reason",
      ]);
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({
          action_handle: approveHandle,
          status: "exception_approved",
          reversible: true,
          message: successMessage,
        }),
      });
      return;
    }
    await route.fulfill({ status: 410, body: "forbidden Control Room route" });
  });

  const response = await page.goto("/control-room", { waitUntil: "domcontentloaded" });
  expect(response?.status()).toBe(200);

  const enabledFact = page.locator("article").filter({ hasText: "Cobertura crítica" });
  const disabledFact = page.locator("article").filter({ hasText: "Datos incompletos" });
  const approveCta = enabledFact.getByRole("button", {
    name: "Aprobar Excepción — Cobertura crítica",
  });
  await expect(approveCta).toBeEnabled();
  await expect(
    disabledFact.getByRole("button", {
      name: "Crear Propuesta de Decisión — Datos incompletos",
    }),
  ).toBeDisabled();
  await expect(
    disabledFact.getByText("Completa los datos requeridos antes de continuar."),
  ).toBeVisible();

  await approveCta.click();
  const dialog = page.getByRole("dialog", { name: "Aprobar Excepción" });
  await expect(dialog).toBeVisible();
  await expect(dialog).toContainText(
    "El hallazgo se archivará como excepción aprobada; puedes reabrirlo.",
  );
  await expect(dialog.getByRole("button", { name: "Aprobar Excepción" })).toBeDisabled();
  await page.getByRole("button", { name: "Cancelar" }).click();
  await expect(dialog).toHaveCount(0);
  expect(requests.filter(({ method }) => method === "POST")).toHaveLength(0);

  await approveCta.click();
  await page.getByLabel("Motivo (obligatorio)").fill(reason);
  await dialog.getByRole("button", { name: "Aprobar Excepción" }).click();
  await expect(page.getByRole("status").filter({ hasText: successMessage })).toBeVisible();
  expect(requests.filter(({ method }) => method === "POST")).toHaveLength(1);
  await expect
    .poll(
      () =>
        requests.filter(
          ({ method, path }) =>
            method === "GET" && path === "/api/control-room/experience/v2",
        ).length,
    )
    .toBeGreaterThanOrEqual(2);

  const html = await page.content();
  expect(html).not.toContain(approveHandle);
  expect(html).not.toContain(disabledHandle);
  expect(page.url()).not.toContain(approveHandle);
  const persistedClientState = await page.evaluate(() =>
    JSON.stringify({
      local: Object.entries(localStorage),
      session: Object.entries(sessionStorage),
    }),
  );
  expect(persistedClientState).not.toContain(approveHandle);
  expect(persistedClientState).not.toContain(disabledHandle);
  expect(
    observedNetwork.some(({ path }) =>
      ["/approve", "/execute", "/auto-run", "/talent/actions/preview", "/api/actions"].some(
        (forbidden) => path.includes(forbidden),
      ),
    ),
  ).toBe(false);
});
