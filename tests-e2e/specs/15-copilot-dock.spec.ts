import { test, expect } from "../fixtures/auth";

test.describe("Copilot dock", () => {
  test("floating launcher is visible on /dashboard", async ({ page }) => {
    await page.goto("/dashboard");
    await expect(page.locator('button[data-fab="copilot"]')).toBeVisible({
      timeout: 10_000,
    });
  });

  test("launcher opens the drawer with a message textbox", async ({ page }) => {
    await page.goto("/dashboard");
    await page.locator('button[data-fab="copilot"]').click();
    const drawer = page.getByRole("dialog", { name: "Copiloto OMEGA" });
    await expect(drawer).toBeVisible({ timeout: 10_000 });
    await expect(
      drawer.getByRole("textbox", { name: "Mensaje para el copiloto" }),
    ).toBeVisible({ timeout: 10_000 });
  });

  test("Escape closes the drawer", async ({ page }) => {
    await page.goto("/dashboard");
    await page.locator('button[data-fab="copilot"]').click();
    const drawer = page.getByRole("dialog", { name: "Copiloto OMEGA" });
    await expect(drawer).toBeVisible({ timeout: 10_000 });
    await page.keyboard.press("Escape");
    await expect(drawer).toHaveCount(0);
  });

  test("the dock is hidden on /copilot itself", async ({ page }) => {
    await page.goto("/copilot");
    await expect(page.locator('button[data-fab="copilot"]')).toHaveCount(0);
  });
});
