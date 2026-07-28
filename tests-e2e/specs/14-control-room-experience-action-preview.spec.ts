import { expect, test } from "../fixtures/auth";

const enabledHandle = "a".repeat(64);
const disabledHandle = "b".repeat(64);
const successMessage = "Preview generado; no se ejecuto ningun cambio externo.";

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
              action_handle: enabledHandle,
              label: "Solicitar revisión de owner",
              enabled: true,
              requires_approval: true,
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
              label: "Preparar seguimiento",
              enabled: false,
              requires_approval: false,
              disabled_reason: "Completa los datos requeridos antes de continuar.",
            },
          ],
        },
      ],
    },
  ],
};

test("Experience generates one preview without exposing or executing action internals", async ({
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
    if (request.method() === "GET" && path === "/api/control-room/experience/v2") {
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify(experience),
      });
      return;
    }
    if (request.method() === "POST" && path === "/api/control-room/actions/preview") {
      expect(body).toEqual({ action_handle: enabledHandle });
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({
          action_handle: enabledHandle,
          operation: "preview",
          status: "generated",
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
  const enabledCta = enabledFact.getByRole("button", {
    name: "Generar preview: Solicitar revisión de owner — Cobertura crítica",
  });
  await expect(enabledCta).toBeEnabled();
  await expect(
    disabledFact.getByRole("button", {
      name: "Generar preview: Preparar seguimiento — Datos incompletos",
    }),
  ).toBeDisabled();
  await expect(
    disabledFact.getByText("Completa los datos requeridos antes de continuar."),
  ).toBeVisible();

  await enabledCta.click();
  await expect(page.getByRole("dialog", { name: "Confirmar preview" })).toBeVisible();
  expect(requests.filter(({ method }) => method === "POST")).toHaveLength(0);
  await page.getByRole("button", { name: "Cancelar" }).click();
  await expect(page.getByRole("dialog", { name: "Confirmar preview" })).toHaveCount(0);
  expect(requests.filter(({ method }) => method === "POST")).toHaveLength(0);

  await enabledCta.click();
  await page.getByRole("button", { name: "Confirmar preview" }).click();
  await expect(page.getByRole("status")).toContainText(successMessage);
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
  expect(html).not.toContain(enabledHandle);
  expect(html).not.toContain(disabledHandle);
  expect(page.url()).not.toContain(enabledHandle);
  const persistedClientState = await page.evaluate(() =>
    JSON.stringify({
      local: Object.entries(localStorage),
      session: Object.entries(sessionStorage),
    }),
  );
  expect(persistedClientState).not.toContain(enabledHandle);
  expect(persistedClientState).not.toContain(disabledHandle);
  expect(
    observedNetwork.some(({ path }) =>
      [
        "/approve",
        "/execute",
        "/auto-run",
        "/talent/actions/preview",
        "/api/actions",
      ].some(
        (forbidden) => path.includes(forbidden),
      ),
    ),
  ).toBe(false);
});
