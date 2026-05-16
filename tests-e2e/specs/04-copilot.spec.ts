/**
 * v1.44.3.2 spec 04 — Next.js /copilot.
 *
 * v1.44.3 explicitly deferred /copilot to v1.44.4. These tests
 * document the EXPECTED state and mark themselves as expected-to-fail
 * until the page lands; the moment it does, the .fail() lines flip
 * and the suite stops being noisy about it.
 *
 * The .fail() pattern (instead of .skip) means the developer running
 * `npx playwright test` sees a green "expected failure" line and KNOWS
 * the page isn't built yet — versus a silent skip that would hide the
 * gap.
 */
import { test, expect } from "../fixtures/auth";

test.describe("Copilot page (Next.js, /copilot — pending v1.44.4)", () => {
  test.fail(true,
    "v1.44.3 shipped backend + memory + drafts + workflows but " +
    "explicitly deferred the /copilot chat page to v1.44.4. " +
    "These tests flip to passing the moment the page lands.",
  );

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
      'role=log, [aria-label*="mensajes" i], [data-testid="chat-messages"]',
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
