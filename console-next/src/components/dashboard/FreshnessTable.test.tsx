import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";

import { FreshnessTable } from "./FreshnessTable";

describe("FreshnessTable", () => {
  it("renders a stable four-row loading skeleton", () => {
    const markup = renderToStaticMarkup(<FreshnessTable rows={[]} loading />);

    expect((markup.match(/animate-pulse/g) ?? []).length).toBe(12);
    expect(markup).toContain("Frescura de datos");
  });

  it("renders an explicit empty state instead of an empty tbody", () => {
    const markup = renderToStaticMarkup(<FreshnessTable rows={[]} loading={false} />);

    expect(markup).toContain("Sin datos de frescura todavía.");
    expect(markup).toContain("colSpan");
  });

  it("formats rows and links to the cartridge viewer", () => {
    const markup = renderToStaticMarkup(
      <FreshnessTable
        loading={false}
        rows={[
          { cartridge: "sap hcm", ageHours: 0.4, status: "fresh" },
          { cartridge: "replicon", ageHours: 72, status: "very_stale" },
        ]}
      />,
    );

    expect(markup).toContain("sap hcm");
    expect(markup).toContain("replicon");
    expect(markup).toContain("&lt; 1 h");
    expect(markup).toContain("3 d");
    expect(markup).toContain("/cartridges/viewer?id=sap%20hcm");
    expect(markup).toContain("Muy antigua");
  });
});
