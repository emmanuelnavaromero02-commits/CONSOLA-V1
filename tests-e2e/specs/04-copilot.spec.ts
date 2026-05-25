/**
 * Next.js /copilot.
 *
 * /copilot now ships as the ChatLayout-backed copilot workspace.
 * These tests are active regression coverage for the page shell,
 * message region, input, and conversation history sidebar.
 */
import { test, expect } from "../fixtures/auth";

test.describe("Copilot page (Next.js, /copilot)", () => {
  test("page exists at /copilot (returns 200, not 404)", async ({
    authedPage: page,
  }) => {
    const response = await page.goto("/copilot");
    expect(response?.status(),
      "Expected /copilot to return 200 once the page ships in v1.44.4",
    ).toBe(200);
  });

  test("renders a message list region", async ({ authedPage: page }) => {
    await page.goto("/copilot");
    // The chat surface uses role=log or an explicit aria-label per
    // the v1.44.4 brief — either is acceptable.
    const messageRegion = page.locator(
      '[role="log"], [aria-label*="mensajes" i], [data-testid="chat-messages"]',
    );
    await expect(messageRegion.first()).toBeVisible({ timeout: 10_000 });
  });

  test("renders a message input + send button", async ({
    authedPage: page,
  }) => {
    await page.goto("/copilot");
    await expect(
      page.getByRole("textbox", { name: /mensaje|message|preguntar/i }),
    ).toBeVisible({ timeout: 10_000 });
    await expect(
      page.getByRole("button", { name: /enviar|send/i }),
    ).toBeVisible({ timeout: 10_000 });
  });

  test("sidebar lists previous conversations", async ({ authedPage: page }) => {
    await page.goto("/copilot");
    const sidebar = page.locator(
      '[aria-label*="conversaciones" i], [data-testid="conversations-sidebar"]',
    );
    await expect(sidebar.first()).toBeVisible({ timeout: 10_000 });
  });
});
