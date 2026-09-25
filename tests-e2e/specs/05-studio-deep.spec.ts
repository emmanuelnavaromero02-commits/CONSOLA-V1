import { test, expect } from "../fixtures/auth";
import type { Page, Request, Route } from "@playwright/test";

const CARTRIDGES = [
  { id: "acme", name: "Acme ERP" },
  { id: "beta", name: "Beta CRM" },
];

const MANIFEST = {
  id: "acme",
  name: "Acme ERP",
  dags: [{ dag_id: "acme_packaged" }],
  entities: [{ entity: "Invoice", name: "Invoice", dag_id: "acme_packaged", mode: "incremental" }],
};

type Handler = (route: Route, request: Request) => Promise<void> | void;

function json(route: Route, body: unknown, status = 200) {
  return route.fulfill({ status, contentType: "application/json", body: JSON.stringify(body) });
}

async function installStudioHarness(page: Page, overrides: Record<string, Handler> = {}) {
  const defaults: Record<string, Handler> = {
    "/studio/cartridges": (route) => json(route, { cartridges: CARTRIDGES }),
    "/studio/cartridges/acme": (route) => json(route, MANIFEST),
    "/studio/cartridges/beta": (route) => json(route, { id: "beta", name: "Beta CRM", dags: [], entities: [] }),
    "/studio/cartridges/acme/status": (route) =>
      json(route, { cartridge_id: "acme", status: "registered", detail: "sin servicio propio que sondear" }),
    "/studio/cartridges/beta/status": (route) => json(route, { cartridge_id: "beta", status: "offline" }),
    "/api/studio/dag-graph": (route, request) => {
      const cartridge = new URL(request.url()).searchParams.get("cartridge");
      return json(route, {
        format: "svg",
        svg: '<svg><script>window.__studioInjected = true</script><image href="x" onerror="window.__studioInjected = true"/></svg>',
        nodes: [
          { id: `cartridge:${cartridge}`, kind: "cartridge", label: cartridge },
          { id: "entity:Invoice", kind: "entity", label: "Invoice" },
          { id: "dag:acme_packaged", kind: "dag", label: "acme_packaged" },
          { id: "dataset:silver:orders", kind: "dataset", label: "silver:orders" },
        ],
        edges: [
          { source: `cartridge:${cartridge}`, target: "entity:Invoice" },
          { source: "entity:Invoice", target: "dag:acme_packaged" },
          { source: "entity:Invoice", target: "dataset:silver:orders" },
          { source: "entity:Ghost", target: "dataset:silver:orders" },
        ],
      });
    },
    "/api/studio/dags": (route) =>
      json(route, {
        cartridge: "acme",
        total: 2,
        dags: [
          { dag_id: "acme_custom", is_paused: false, is_active: true, tags: [] },
          { dag_id: "acme_packaged", is_paused: false, is_active: false, registered_only: true, tags: [] },
        ],
      }),
    "/api/studio/dags/acme_custom/source": (route) =>
      json(route, { dag_id: "acme_custom", found: true, source_code: "dag_id = 'acme_custom'\n" }),
    "/api/studio/dags/acme_packaged/source": (route) =>
      json(route, { dag_id: "acme_packaged", found: false, source_code: "", error: "not found" }),
    "/api/studio/templates": (route) => json(route, { templates: [{ id: "full_extract", name: "Extracción completa" }] }),
    "/api/system/info": (route) =>
      json(route, { dev_mode: true, rce_tools_enabled: true, dag_deploy_enabled: true, app_env: "development" }),
    "/api/config": (route) => json(route, { airflow_url: "http://airflow.e2e.test:8082", superset_url: "" }),
    "/api/studio/dag-deploy": (route) => json(route, { status: "deployed", dag_id: "acme_custom", result: {} }),
    "/api/studio/entities": (route) =>
      json(route, {
        cartridge: "acme",
        total: 1,
        entities: [{ name: "Invoice", display_name: "Facturas", mode: "incremental", dag_id: "acme_packaged", source: "entity_config" }],
      }),
    "/datasets": (route) => json(route, { datasets: [{ name: "orders", layer: "silver", cartridge: "acme" }] }),
    "/api/studio/silver/preview": (route) =>
      json(route, {
        layer: "silver",
        columns: [],
        rows: [],
        total: 0,
        available: false,
        reason: "No silver datasets registered for cartridge acme",
      }),
  };
  const handlers = { ...defaults, ...overrides };
  await page.route(
    (url) => Object.prototype.hasOwnProperty.call(handlers, url.pathname),
    async (route, request) => {
      const handler = handlers[new URL(request.url()).pathname];
      await handler(route, request);
    },
  );
}

async function openStudio(page: Page) {
  await page.goto("/studio", { waitUntil: "domcontentloaded", timeout: 30_000 });
  await expect(page.getByTestId("cartridge-picker")).toHaveValue("acme", { timeout: 15_000 });
}

async function openTab(page: Page, name: RegExp) {
  await page.getByRole("tab", { name }).click();
}

test.describe("Studio deep — hermetic API contract", () => {
  test("probe status is shown honestly: registered is not operational", async ({ authedPage: page }) => {
    await installStudioHarness(page);
    await openStudio(page);
    const status = page.getByTestId("cartridge-status");
    await expect(status).toHaveAttribute("data-status", "registered");
    await expect(status).toContainText("sin servicio propio que sondear");
    await expect(status).not.toContainText("Operativo");
  });

  test("graph is drawn from nodes/edges and never injects the server svg", async ({ authedPage: page }) => {
    await installStudioHarness(page);
    await openStudio(page);
    const graph = page.getByTestId("dag-graph");
    await expect(graph.locator("[data-node-id]")).toHaveCount(4);
    await expect(graph.locator("path[marker-end]")).toHaveCount(3);
    await expect(page.locator("script:not([src])").filter({ hasText: "__studioInjected" })).toHaveCount(0);
    expect(await page.evaluate(() => (window as Window & { __studioInjected?: boolean }).__studioInjected)).toBeUndefined();
    await graph.locator('[data-node-id="entity:Invoice"]').click();
    const detail = page.getByTestId("dag-graph-detail");
    await expect(detail).toContainText("Entradas (1)");
    await expect(detail).toContainText("Salidas (2)");
  });

  test("switching cartridge refetches the graph for the new cartridge", async ({ authedPage: page }) => {
    await installStudioHarness(page);
    await openStudio(page);
    const request = page.waitForRequest(
      (req) => {
        const url = new URL(req.url());
        return url.pathname === "/api/studio/dag-graph" && url.searchParams.get("cartridge") === "beta";
      },
      { timeout: 10_000 },
    );
    await page.getByTestId("cartridge-picker").selectOption("beta");
    await request;
    await expect(page.getByTestId("cartridge-status")).toHaveAttribute("data-status", "offline");
    await expect(page.locator('[data-node-id="cartridge:beta"]')).toBeVisible();
  });

  test("deploy: confirm modal → POST with the editor code → success toast", async ({ authedPage: page }) => {
    await installStudioHarness(page);
    await openStudio(page);
    await openTab(page, /^DAGs$/);
    await page.locator('[data-dag-id="acme_custom"]').click();
    await expect(page.getByTestId("dag-airflow-link")).toHaveAttribute(
      "href",
      "http://airflow.e2e.test:8082/dags/acme_custom/grid",
    );
    let posted: Record<string, unknown> | null = null;
    page.on("request", (req) => {
      if (new URL(req.url()).pathname === "/api/studio/dag-deploy") posted = JSON.parse(req.postData() || "{}");
    });
    await page.getByRole("button", { name: /Deploy a Airflow/ }).click();
    const dialog = page.getByTestId("deploy-dialog");
    await expect(dialog).toBeVisible();
    expect(posted).toBeNull();
    const response = page.waitForResponse((res) => new URL(res.url()).pathname === "/api/studio/dag-deploy");
    await dialog.getByRole("button", { name: "Desplegar" }).click();
    await response;
    expect(posted).toMatchObject({
      cartridge: "acme",
      entity: "Invoice",
      dag_id: "acme_custom",
      code: "dag_id = 'acme_custom'\n",
    });
    await expect(page.getByText("DAG acme_custom desplegado en Airflow.")).toBeVisible();
    await expect(dialog).toHaveCount(0);
  });

  test("the ALLOW_RCE_TOOLS 403 from deploy is reported, not hidden", async ({ authedPage: page }) => {
    await installStudioHarness(page, {
      "/api/studio/dag-deploy": (route) =>
        json(route, { detail: "Deploy a Airflow requiere ALLOW_RCE_TOOLS=true en el entorno local." }, 403),
    });
    await openStudio(page);
    await openTab(page, /^DAGs$/);
    await page.locator('[data-dag-id="acme_custom"]').click();
    await page.getByRole("button", { name: /Deploy a Airflow/ }).click();
    await page.getByTestId("deploy-dialog").getByRole("button", { name: "Desplegar" }).click();
    await expect(page.getByText("Deploy a Airflow requiere ALLOW_RCE_TOOLS=true en el entorno local.")).toBeVisible();
  });

  test("packaged DAGs and production disable deploy with a visible reason", async ({ authedPage: page }) => {
    await installStudioHarness(page, {
      "/api/system/info": (route) => json(route, { dev_mode: false, rce_tools_enabled: false, dag_deploy_enabled: false }),
    });
    await openStudio(page);
    await openTab(page, /^DAGs$/);
    const packaged = page.locator('[data-dag-id="acme_packaged"]');
    await expect(packaged).toContainText("Inactivo");
    await expect(packaged).toContainText("Solo manifiesto");
    await page.locator('[data-dag-id="acme_custom"]').click();
    const deploy = page.getByRole("button", { name: /Deploy a Airflow/ });
    await expect(deploy).toBeDisabled();
    await expect(page.getByTestId("deploy-disabled-reason")).toContainText("desarrollo");
    await expect(page.getByRole("button", { name: /Eliminar/ })).toBeDisabled();
  });

  test("an Airflow timeout on the DAG list is shown and does not block entities", async ({ authedPage: page }) => {
    await installStudioHarness(page, {
      "/api/studio/dags": (route) => json(route, { detail: "Airflow DAG list timed out" }, 504),
    });
    await openStudio(page);
    await openTab(page, /^DAGs$/);
    await expect(page.getByTestId("dags-error")).toContainText("HTTP 504", { timeout: 15_000 });
    await openTab(page, /^Entidades$/);
    await expect(page.getByTestId("entities-table")).toContainText("Invoice");
  });

  test("delete asks for confirmation and cancelling sends nothing", async ({ authedPage: page }) => {
    await installStudioHarness(page);
    const deletes: string[] = [];
    page.on("request", (req) => {
      if (req.method() === "DELETE") deletes.push(req.url());
    });
    await openStudio(page);
    await openTab(page, /^DAGs$/);
    await page.locator('[data-dag-id="acme_custom"]').click();
    await page.getByRole("button", { name: /Eliminar/ }).click();
    const dialog = page.getByTestId("delete-dag-dialog");
    await expect(dialog).toBeVisible();
    await page.keyboard.press("Escape");
    await expect(dialog).toHaveCount(0);
    expect(deletes).toEqual([]);
  });

  test("rename opens an input and requires confirmation", async ({ authedPage: page }) => {
    await installStudioHarness(page);
    await openStudio(page);
    await openTab(page, /^DAGs$/);
    await page.locator('[data-dag-id="acme_custom"]').click();
    await page.getByRole("button", { name: /Renombrar/ }).click();
    const input = page.getByTestId("rename-input");
    await expect(input).toHaveValue("acme_custom");
    await input.fill("acme_custom_v2");
    await page.getByRole("button", { name: "Continuar" }).click();
    const dialog = page.getByTestId("deploy-dialog");
    await expect(dialog).toContainText("acme_custom_v2");
    await dialog.getByRole("button", { name: "Cancelar" }).click();
    await expect(dialog).toHaveCount(0);
  });

  test("silver preview shows the backend's unavailable reason verbatim", async ({ authedPage: page }) => {
    await installStudioHarness(page);
    await openStudio(page);
    await openTab(page, /^Capas$/);
    await expect(page.getByText("No silver datasets registered for cartridge acme")).toBeVisible({ timeout: 15_000 });
    await page.getByRole("tab", { name: "Gold", exact: true }).click();
    await expect(page.getByText(/No hay datasets Gold registrados para acme/)).toBeVisible();
    await expect(page.getByRole("button", { name: /Publicar en Superset/ })).toHaveCount(0);
  });

  test("oversized spec uploads are rejected before any request", async ({ authedPage: page }) => {
    await installStudioHarness(page);
    const uploads: string[] = [];
    page.on("request", (req) => {
      if (new URL(req.url()).pathname === "/api/studio/entities/upload") uploads.push(req.method());
    });
    await openStudio(page);
    await openTab(page, /^Entidades$/);
    await page.locator('input[type="file"][accept*=".yaml"]').setInputFiles({
      name: "big.yaml",
      mimeType: "application/x-yaml",
      buffer: Buffer.alloc(2 * 1024 * 1024 + 1, "a"),
    });
    await expect(page.getByText("La spec supera el máximo de 2 MB.")).toBeVisible();
    expect(uploads).toEqual([]);
  });
});

test.describe("Knowledge base — delete source", () => {
  test("Borrar fuente asks for confirmation before DELETE /api/rag/sources/{id}", async ({ authedPage: page }) => {
    await page.route(
      (url) => url.pathname === "/api/rag/sources",
      (route) => json(route, { sources: [{ id: 9001, name: "Fuente e2e", kind: "document", chunk_count: 1 }] }),
    );
    let deleted: string | null = null;
    await page.route(
      (url) => url.pathname === "/api/rag/sources/9001",
      (route, request) => {
        deleted = request.method();
        return json(route, { deleted: true });
      },
    );
    await page.goto("/copilot/knowledge", { waitUntil: "domcontentloaded" });
    await page.getByRole("button", { name: "Borrar fuente Fuente e2e" }).click();
    const dialog = page.getByTestId("delete-rag-source-dialog");
    await expect(dialog).toContainText("Fuente e2e");
    expect(deleted).toBeNull();
    await dialog.getByRole("button", { name: "Borrar fuente" }).click();
    await expect(page.getByText("Fuente Fuente e2e borrada.")).toBeVisible();
    expect(deleted).toBe("DELETE");
  });
});
