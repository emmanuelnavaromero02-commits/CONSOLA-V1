import { expect, test } from "../fixtures/auth";
import {
  assertReadOnlyRequests,
  emptyExperience,
  installExperienceMock,
  invalidExperience,
  longCopyExperience,
  performanceExperience,
  staleExperience,
  zeroExperience,
  type MockReply,
} from "./helpers/control-room-experience";

async function openControlRoom(
  page: Parameters<typeof installExperienceMock>[0],
  replies: MockReply[],
) {
  const observation = await installExperienceMock(page, replies);
  const response = await page.goto(process.env.CONTROL_ROOM_URL || "/control-room", {
    waitUntil: "domcontentloaded",
  });
  expect(response?.status()).toBe(200);
  return observation;
}

test.describe("Control Room Business Experience", () => {
  test("loads only Performance through one read-only Experience GET", async ({
    authedPage: page,
  }) => {
    const observation = await openControlRoom(page, [
      { status: 200, body: performanceExperience },
    ]);

    await expect(page.getByRole("heading", { name: "Experiencia empresarial" })).toBeVisible();
    await expect(page.getByRole("region", { name: "Performance" })).toBeVisible();
    await expect(page.getByText("Rotación voluntaria")).toBeVisible();
    await expect(page.getByText("Competencias", { exact: true })).toHaveCount(0);
    await expect(page.getByText("Potencial", { exact: true })).toHaveCount(0);
    await expect(page.getByText("Aspiración", { exact: true })).toHaveCount(0);

    assertReadOnlyRequests(observation, 1);
    expect(observation.consoleErrors).toEqual([]);
    expect(observation.pageErrors).toEqual([]);
  });

  test("renders the neutral empty state", async ({ authedPage: page }) => {
    const observation = await openControlRoom(page, [
      { status: 200, body: emptyExperience },
    ]);

    await expect(
      page.getByText("No hay observaciones empresariales para mostrar."),
    ).toBeVisible();
    assertReadOnlyRequests(observation, 1);
  });

  test("keeps a real zero visible", async ({ authedPage: page }) => {
    const observation = await openControlRoom(page, [
      { status: 200, body: zeroExperience },
    ]);

    await expect(page.getByText("0%")).toBeVisible();
    assertReadOnlyRequests(observation, 1);
  });

  test("keeps stale information visible with its observation date", async ({
    authedPage: page,
  }) => {
    const observation = await openControlRoom(page, [
      { status: 200, body: staleExperience },
    ]);

    await expect(page.getByText("Información anterior")).toBeVisible();
    await expect(page.locator('time[datetime="2026-04-01T00:00:00Z"]')).toBeVisible();
    await expect(page.getByText(/error|missing|stale/i)).toHaveCount(0);
    assertReadOnlyRequests(observation, 1);
  });

  for (const scenario of [
    { name: "403", status: 403, copy: "No tienes acceso a esta vista." },
    { name: "404", status: 404, copy: "Esta vista no está disponible." },
    {
      name: "500",
      status: 500,
      copy: "No se pudo cargar la información empresarial.",
    },
  ]) {
    test(`maps ${scenario.name} to fixed safe copy`, async ({ authedPage: page }) => {
      const observation = await openControlRoom(page, [
        {
          status: scenario.status,
          body: { detail: "internal stack and request-id must stay hidden" },
        },
      ]);

      await expect(page.getByText(scenario.copy)).toBeVisible();
      await expect(page.getByText(/internal stack|request-id/i)).toHaveCount(0);
      assertReadOnlyRequests(observation, 1, 1);
    });
  }

  test("fails closed on an invalid contract", async ({ authedPage: page }) => {
    const observation = await openControlRoom(page, [
      { status: 200, body: invalidExperience },
    ]);

    await expect(
      page.getByText("No se pudo cargar la información empresarial."),
    ).toBeVisible();
    await expect(page.getByText("technical-secret")).toHaveCount(0);
    assertReadOnlyRequests(observation, 1);
  });

  test("preserves valid data when an explicit update fails", async ({
    authedPage: page,
  }) => {
    const observation = await openControlRoom(page, [
      { status: 200, body: performanceExperience },
      { status: 500, body: { detail: "refresh internals" } },
    ]);
    await expect(page.getByText("Rotación voluntaria")).toBeVisible();

    await page.getByRole("button", { name: "Actualizar información empresarial" }).click();
    await expect(
      page.getByText("No se pudo actualizar. Se mantiene la última información disponible."),
    ).toBeVisible();
    await expect(page.getByText("Rotación voluntaria")).toBeVisible();
    await expect(page.getByText("refresh internals")).toHaveCount(0);

    assertReadOnlyRequests(observation, 2, 1);
  });

  for (const viewport of [
    { name: "desktop", width: 1440, height: 900 },
    { name: "mobile", width: 390, height: 844 },
  ]) {
    test(`renders without overflow at ${viewport.name}`, async ({ authedPage: page }) => {
      await page.setViewportSize({ width: viewport.width, height: viewport.height });
      const observation = await openControlRoom(page, [
        {
          status: 200,
          body: viewport.name === "mobile" ? longCopyExperience : performanceExperience,
        },
      ]);

      await expect(page.getByRole("heading", { name: "Experiencia empresarial" })).toBeVisible();
      const overflow = await page.evaluate(
        () => document.documentElement.scrollWidth - document.documentElement.clientWidth,
      );
      expect(overflow).toBeLessThanOrEqual(0);
      await test.info().attach(`control-room-${viewport.name}`, {
        body: await page.screenshot({ fullPage: true }),
        contentType: "image/png",
      });

      assertReadOnlyRequests(observation, 1);
      expect(observation.consoleErrors).toEqual([]);
      expect(observation.pageErrors).toEqual([]);
    });
  }
});
