import { expect, type Page } from "@playwright/test";

export interface MockReply {
  status: number;
  body: unknown;
}

interface ObservedRequest {
  method: string;
  pathname: string;
}

export interface ExperienceObservation {
  requests: ObservedRequest[];
  consoleErrors: string[];
  pageErrors: string[];
}

const shellReads = new Set(["/api/me/access", "/auth/me"]);
export const EXPERIENCE_PATH = "/api/control-room/experience/v2";
export const FRESHNESS_PATH = "/api/control-room/experience/v2/freshness";
export const liveFreshness = {
  schema_version: "control-room-freshness/v1",
  fingerprint: "e".repeat(64),
  checked_at: "2026-07-25T12:30:00Z",
  data_refreshed_at: null,
};

const zeroFact = {
  kind: "kpi",
  title: "Rotación voluntaria",
  severity: "medium",
  observed_at: "2026-07-24T00:00:00Z",
  stale: false,
  metric: { name: "Rotación", kind: "percentage", value: 0, unit: "%" },
  decision: { status: "decision_created" },
  actions: [],
};

function experience(facts: unknown[]) {
  return {
    schema_version: "control-room-experience/v2",
    generated_at: "2026-07-25T12:30:00Z",
    sections: facts.length
      ? [
          {
            title: "Performance",
            facts,
          },
        ]
      : [],
  };
}

export const performanceExperience = experience([
  zeroFact,
  {
    kind: "signal",
    title: "Cobertura de posiciones críticas",
    severity: "low",
    observed_at: "2026-07-23T00:00:00Z",
    stale: false,
    metric: { name: "Cobertura", kind: "count", value: 18 },
    actions: [],
  },
]);

export const zeroExperience = experience([zeroFact]);
export const longCopyExperience = experience([
  {
    ...zeroFact,
    title: "ContinuidadOperativaInternacionalSinInterrupcionesParaEquiposCriticos",
  },
]);
export const staleExperience = experience([
  {
    kind: "signal",
    title: "Cobertura de posiciones críticas",
    severity: "high",
    observed_at: "2026-04-01T00:00:00Z",
    stale: true,
    metric: { name: "Cobertura", kind: "percentage", value: 78.5 },
    actions: [],
  },
]);
export const emptyExperience = experience([]);
export const invalidExperience = {
  ...performanceExperience,
  technical_secret: "technical-secret",
};

export async function installExperienceMock(
  page: Page,
  replies: MockReply[],
): Promise<ExperienceObservation> {
  const observation: ExperienceObservation = {
    requests: [],
    consoleErrors: [],
    pageErrors: [],
  };
  let replyIndex = 0;

  page.on("console", (message) => {
    if (message.type() === "error") observation.consoleErrors.push(message.text());
  });
  page.on("pageerror", (error) => observation.pageErrors.push(error.message));
  page.on("request", (request) => {
    const url = new URL(request.url());
    if (["fetch", "xhr"].includes(request.resourceType())) {
      observation.requests.push({ method: request.method(), pathname: url.pathname });
    }
  });

  await page.route("**/api/control-room/**", async (route) => {
    const requestUrl = new URL(route.request().url());
    if (route.request().method() === "GET" && requestUrl.pathname === FRESHNESS_PATH) {
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify(liveFreshness),
      });
      return;
    }
    if (requestUrl.pathname !== EXPERIENCE_PATH) {
      await route.fulfill({ status: 410, body: "legacy endpoint disabled" });
      return;
    }
    const reply = replies[Math.min(replyIndex, replies.length - 1)];
    replyIndex += 1;
    await route.fulfill({
      status: reply.status,
      contentType: "application/json",
      body: JSON.stringify(reply.body),
    });
  });

  return observation;
}

export function assertReadOnlyRequests(
  observation: ExperienceObservation,
  expectedReads: number,
  expectedHttpFailures = 0,
) {
  const pageRequests = observation.requests.filter(
    ({ pathname }) => !shellReads.has(pathname) && pathname !== FRESHNESS_PATH,
  );
  expect(pageRequests).toEqual(
    Array.from({ length: expectedReads }, () => ({
      method: "GET",
      pathname: EXPERIENCE_PATH,
    })),
  );
  expect(
    observation.requests.filter(
      ({ method, pathname }) => pathname === FRESHNESS_PATH && method !== "GET",
    ),
  ).toEqual([]);
  expect(
    observation.requests.filter(({ method }) =>
      ["POST", "PUT", "PATCH", "DELETE"].includes(method),
    ),
  ).toEqual([]);
  const resourceFailures = observation.consoleErrors.filter((message) =>
    /^Failed to load resource: the server responded with a status of (403|404|500) \(/.test(
      message,
    ),
  );
  expect(resourceFailures).toHaveLength(expectedHttpFailures);
  expect(observation.consoleErrors.filter((message) => !resourceFailures.includes(message))).toEqual(
    [],
  );
  expect(observation.pageErrors).toEqual([]);
}
