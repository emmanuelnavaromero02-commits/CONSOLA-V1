import { test, expect } from "../fixtures/auth";
import type { Page, Response } from "@playwright/test";

const TABS = [
  /^Mapa del Flujo$/,
  /^Automatizaciones$/,
  /^Tablas de Origen \(Bronce\)$/,
  /^Modelado y Limpieza \(Plata\)$/,
  /^Indicadores y KPIs \(Oro\)$/,
];

function isPath(pathname: string) {
  return (response: Response) => new URL(response.url()).pathname === pathname;
}

async function openStudio(page: Page): Promise<{ ids: string[] }> {
  const cartridges = page.waitForResponse(isPath("/studio/cartridges"), { timeout: 20_000 });
  await page.goto("/studio", { waitUntil: "domcontentloaded", timeout: 30_000 });
  const response = await cartridges;
  expect(response.status(), "GET /studio/cartridges must succeed for an admin").toBe(200);
  const payload = (await response.json()) as { cartridges?: Array<{ id: string }> };
  const ids = (payload.cartridges ?? []).map((item) => item.id);
  await expect(page.getByRole("heading", { level: 1, name: "Studio" })).toBeVisible({ timeout: 15_000 });
  return { ids };
}

async function openTab(page: Page, name: RegExp) {
  const tab = page.getByRole("tab", { name });
  await tab.click();
  await expect(tab).toHaveAttribute("aria-selected", "true");
}

test.describe("Studio (Next.js, /studio)", () => {
  test("/studio loads the Next.js Studio with tab navigation", async ({ authedPage: page }) => {
    await openStudio(page);
    for (const name of TABS) {
      await expect(page.getByRole("tab", { name })).toBeVisible();
    }
    await expect(page.locator("#step-content")).toHaveCount(0);
  });

  test("cartridge selector lists the cartridges returned by /studio/cartridges", async ({ authedPage: page }) => {
    const { ids } = await openStudio(page);
    expect(ids.length, "the seeded stack exposes at least one cartridge").toBeGreaterThan(0);
    const picker = page.getByTestId("cartridge-picker");
    await expect(picker).toBeEnabled({ timeout: 15_000 });
    const values = await picker.locator("option").evaluateAll((options) =>
      options.map((option) => (option as HTMLOptionElement).value),
    );
    expect(values).toEqual(ids);
    await expect(page.getByTestId("cartridge-status")).toBeVisible({ timeout: 15_000 });
  });

  test("graph renders one node per node returned by the dag-graph API", async ({ authedPage: page }) => {
    const graph = page.waitForResponse(isPath("/api/studio/dag-graph"), { timeout: 20_000 });
    await openStudio(page);
    const response = await graph;
    expect(response.status()).toBe(200);
    const payload = (await response.json()) as { nodes?: Array<{ id: string }> };
    const ids = new Set((payload.nodes ?? []).map((node) => node.id));
    if (!ids.size) {
      await expect(page.getByText("Sin nodos")).toBeVisible();
      return;
    }
    const nodes = page.locator('[data-testid="dag-graph"] [data-node-id]');
    await expect(nodes).toHaveCount(ids.size, { timeout: 15_000 });
    await nodes.first().click();
    await expect(page.getByTestId("dag-graph-detail")).toBeVisible();
  });

  test("DAGs tab lists Airflow DAGs or explains why Airflow is unavailable", async ({ authedPage: page }) => {
    await openStudio(page);
    const dags = page.waitForResponse(isPath("/api/studio/dags"), { timeout: 20_000 });
    await openTab(page, /^Automatizaciones$/);
    const response = await dags;
    if (!response.ok()) {
      await expect(page.getByTestId("dags-error")).toBeVisible();
      return;
    }
    const payload = (await response.json()) as { dags?: Array<{ dag_id: string }> };
    if (!payload.dags?.length) {
      await expect(page.getByTestId("dags-empty")).toBeVisible();
      return;
    }
    const first = page.locator(`[data-dag-id="${payload.dags[0].dag_id}"]`);
    await first.click();
    await expect(page.getByTestId("dag-editor")).toContainText(payload.dags[0].dag_id);
    const config = (await page.evaluate(async () => {
      const r = await fetch("/api/config", { credentials: "same-origin" });
      return r.ok ? r.json() : {};
    })) as { airflow_url?: string };
    const base = String(config.airflow_url || "").replace(/\/+$/, "");
    const link = page.getByTestId("dag-airflow-link");
    if (!/^https?:\/\//.test(base)) {
      await expect(link).toHaveCount(0);
      await expect(page.getByText("Airflow sin URL pública configurada.")).toBeVisible();
      return;
    }
    await expect(link).toHaveAttribute("target", "_blank");
    const href = (await link.getAttribute("href")) || "";
    expect(href.startsWith(base)).toBe(true);
    expect(href).toMatch(/\/dags\/.+\/grid$/);
  });

  test("Deploy a Airflow opens a confirmation that cancels without POST, or is disabled with a reason", async ({
    authedPage: page,
  }) => {
    const deployRequests: string[] = [];
    page.on("request", (request) => {
      if (new URL(request.url()).pathname === "/api/studio/dag-deploy") deployRequests.push(request.method());
    });
    await openStudio(page);
    await openTab(page, /^Automatizaciones$/);
    await page.getByRole("button", { name: /Nuevo DAG/ }).click();
    const cartridge = await page.getByTestId("cartridge-picker").inputValue();
    await page.getByRole("textbox", { name: "dag_id" }).fill(`${cartridge}_e2e_probe`);
    await page.getByRole("textbox", { name: "Código Python" }).fill("# e2e: never deployed\n");
    const deploy = page.getByRole("button", { name: /Deploy a Airflow/ });
    await expect(deploy).toBeVisible();
    if (await deploy.isDisabled()) {
      const reason = (await deploy.getAttribute("data-disabled-reason")) || "";
      expect(reason).toMatch(/deshabilitado|empaquetado|desarrollo|ALLOW_RCE_TOOLS|system\/info|Verificando/i);
      await expect(page.getByTestId("deploy-disabled-reason")).toBeVisible();
      return;
    }
    const entity = page.getByRole("combobox", { name: "Entidad" });
    const hasEntity = Boolean(await entity.inputValue());
    await deploy.click();
    const dialog = page.getByTestId("deploy-dialog");
    if (!hasEntity) {
      await expect(dialog).toHaveCount(0);
      return;
    }
    await expect(dialog).toBeVisible();
    await expect(dialog).toContainText(`${cartridge}_e2e_probe`);
    await dialog.getByRole("button", { name: "Cancelar" }).click();
    await expect(dialog).toHaveCount(0);
    expect(deployRequests, "cancelling the modal must not deploy").toEqual([]);
  });

  test("Entidades tab shows the entity table, spec upload and new-entity dialog", async ({ authedPage: page }) => {
    await openStudio(page);
    const entities = page.waitForResponse(isPath("/api/studio/entities"), { timeout: 20_000 });
    await openTab(page, /^Tablas de Origen \(Bronce\)$/);
    expect((await entities).status()).toBe(200);
    await expect(page.getByTestId("entities-table").or(page.getByTestId("entities-empty"))).toBeVisible();
    await expect(page.locator('input[type="file"][accept*=".yaml"]')).toHaveCount(1);
    await page.getByRole("button", { name: /Nueva entidad/ }).click();
    const dialog = page.getByTestId("new-entity-dialog");
    await expect(dialog).toBeVisible();
    await dialog.getByRole("button", { name: "Cancelar" }).click();
    await expect(dialog).toHaveCount(0);
  });

  test("Capas tab switches Silver/Gold and shows data or an honest state", async ({ authedPage: page }) => {
    await openStudio(page);
    await openTab(page, /^Indicadores y KPIs \(Oro\)$/);
    for (const layer of ["Silver", "Gold"]) {
      await page.getByRole("tab", { name: layer, exact: true }).click();
      const panel = page.locator('[role="tabpanel"][id^="studio-layer-panel-"]');
      await expect(panel).toBeVisible();
      await expect(
        panel.locator("table").or(panel.locator('[role="status"]')).or(panel.locator('[role="alert"]')).first(),
      ).toBeVisible({ timeout: 20_000 });
    }
  });

  test("Refinar previews SQL through /api/bronze/query", async ({ authedPage: page }) => {
    await openStudio(page);
    await openTab(page, /^Modelado y Limpieza \(Plata\)$/);
    await page.getByRole("button", { name: /Nuevo dataset/ }).click();
    await page.getByRole("textbox", { name: "SQL" }).fill("select 1 as uno");
    const request = page.waitForRequest(
      (req) => req.method() === "POST" && new URL(req.url()).pathname === "/api/bronze/query",
      { timeout: 10_000 },
    );
    await page.getByRole("button", { name: /Previsualizar/ }).click();
    const sent = await request;
    expect(JSON.parse(sent.postData() || "{}")).toMatchObject({ sql: "select 1 as uno", limit: 50 });
  });

  test("Studio assistant opens as a side panel and accepts text", async ({ authedPage: page }) => {
    await openStudio(page);
    await page.getByRole("button", { name: /Asistente de Studio/ }).click();
    const panel = page.getByTestId("studio-assistant");
    await expect(panel).toBeVisible();
    const input = panel.getByRole("textbox", { name: "Mensaje para el asistente de Studio" });
    await input.fill("¿Qué DAGs tiene este cartucho?");
    await expect(input).toHaveValue("¿Qué DAGs tiene este cartucho?");
  });
});
