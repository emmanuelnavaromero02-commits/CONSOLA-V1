import { expect, test } from "../fixtures/auth";


const overview = {
  profile: {
    industry: "retail",
    company_profile: "enterprise",
    wisdom_bit: "none",
    decision_mode: "supervised",
    compensation_enabled: false,
    write_back_enabled: false,
  },
  readiness: {
    ready_min: 0,
    near_min: 0,
    profiled_employees: 0,
    calculable_employees: 0,
    insufficient_data_employees: 0,
    nine_box_available: 0,
    status: "insufficient_data",
  },
  widgets: [],
  signals: [],
  blockers: [],
  nine_box: { status: "insufficient_data", cells: [], blockers: [] },
  anomalies: {
    status: "insufficient_data",
    summary: { total: 0, high: 0, recommendation_only: 0 },
    items: [],
  },
  metadata_readiness: {
    status: "insufficient_data",
    summary: {
      cpa_ready_employees: 0,
      cpa_insufficient_employees: 0,
      entities: 0,
      blocked_entities: 0,
    },
    entities: [],
  },
};

const nineBox = {
  dataset: "sap_successfactors_talent_9box",
  status: "insufficient_data",
  totals: { employees: 0, ready: 0, blocked: 0, cells: 0 },
  cells: [],
  blockers: [],
};

const anomalies = {
  dataset: "sap_successfactors_talent_action_candidates",
  status: "insufficient_data",
  summary: { total: 0, high: 0, recommendation_only: 0 },
  items: [],
  blockers: [],
};

const metadata = {
  status: "insufficient_data",
  summary: {
    cpa_ready_employees: 0,
    cpa_insufficient_employees: 0,
    entities: 0,
    blocked_entities: 0,
  },
  entities: [],
  blockers: [],
};

test("Talent omits legacy actions when no V2 action is authorized", async ({
  authedPage: page,
}) => {
  const requests: Array<{ method: string; path: string }> = [];
  await page.route("**/api/control-room/sap-successfactors/talent/**", async (route) => {
    const request = route.request();
    const path = new URL(request.url()).pathname;
    requests.push({ method: request.method(), path });
    const body = path.endsWith("/overview")
      ? overview
      : path.endsWith("/9box")
        ? nineBox
        : path.endsWith("/anomalies")
          ? anomalies
          : path.endsWith("/metadata-readiness")
            ? metadata
            : { detail: "unexpected Talent endpoint" };
    await route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(body) });
  });

  const response = await page.goto("/control-room/talent", {
    waitUntil: "domcontentloaded",
  });

  expect(response?.status()).toBe(200);
  await expect(page.getByRole("heading", { name: "Control Room Talento" })).toBeVisible();
  await expect(page.getByRole("button", { name: "Generar preview" })).toHaveCount(0);
  await expect(page.getByText("Ciclo OMEGA", { exact: true })).toHaveCount(0);
  await expect(page.getByText("Simulacion", { exact: true })).toHaveCount(0);
  expect(requests.some(({ path }) => path.includes("/talent/actions/preview"))).toBe(false);
  expect(requests.some(({ method }) => method !== "GET")).toBe(false);
});
