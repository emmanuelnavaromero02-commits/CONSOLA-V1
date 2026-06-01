import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";

import { KpiCard } from "./KpiCard";

describe("KpiCard", () => {
  it("exposes a stable numeric value for E2E extraction", () => {
    const markup = renderToStaticMarkup(
      <KpiCard label="Alertas" value="1,234" numericValue={1234} hint="12 nuevas" trend="up" />,
    );

    expect(markup).toContain('data-testid="kpi-card"');
    expect(markup).toContain('data-label="Alertas"');
    expect(markup).toContain('data-numeric-value="1234"');
    expect(markup).toContain("12 nuevas");
  });

  it("renders a skeleton value while loading and omits stale numeric data", () => {
    const markup = renderToStaticMarkup(
      <KpiCard label="Riesgo" value="8" numericValue={8} loading />,
    );

    expect(markup).toContain("animate-pulse");
    expect(markup).not.toContain('data-testid="kpi-card-value"');
    expect(markup).not.toContain('data-numeric-value="8"');
  });
});
