import { readFileSync } from "node:fs";

import { describe, expect, it } from "vitest";

const PRODUCT_SURFACES = [
  new URL("../app/(shell)/operational-intelligence/page.tsx", import.meta.url),
  new URL("../app/(shell)/supervised-actions/page.tsx", import.meta.url),
  new URL("../components/workspace/CopilotActionsConsole.tsx", import.meta.url),
];

const INTERNAL_TERMS = [
  "Monte Carlo",
  "Bayes",
  "Bayesian",
  "calibración",
  "calibracion",
  "MCP",
  "orquestador",
  "orchestrator",
  "engine",
];

describe("product copy", () => {
  it("keeps internal implementation terms out of visible operational surfaces", () => {
    for (const surface of PRODUCT_SURFACES) {
      const source = readFileSync(surface, "utf8");
      for (const term of INTERNAL_TERMS) {
        expect(source, `${surface.pathname} should not expose ${term}`).not.toContain(term);
      }
    }
  });
});
