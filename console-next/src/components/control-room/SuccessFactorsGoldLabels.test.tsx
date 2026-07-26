import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";

import type { SfGoldKpisPayload } from "@/lib/control-room/types";

import { SuccessFactorsGoldPanel } from "./SuccessFactorsGoldPanel";


function render(payload: SfGoldKpisPayload): string {
  return renderToStaticMarkup(
    <SuccessFactorsGoldPanel
      payload={payload}
      loading={false}
      error=""
      sources={[]}
    />,
  );
}


describe("SuccessFactors Gold business row labels", () => {
  it("uses real company, location and department names", () => {
    const markup = render({
      widgets: [
        {
          id: "sf_headcount_by_company",
          title: "Headcount por compania",
          value: 10,
          rows: [{ company_name: "FEMSA Comercio", headcount: 10 }],
        },
        {
          id: "sf_headcount_by_location",
          title: "Headcount por ubicacion",
          value: 8,
          rows: [{ location_name: "Monterrey", headcount: 8 }],
        },
        {
          id: "sf_headcount_by_department",
          title: "Headcount por departamento",
          value: 6,
          rows: [{ department_name: "Finanzas", headcount: 6 }],
        },
      ],
    });

    expect(markup).toContain("FEMSA Comercio");
    expect(markup).toContain("Monterrey");
    expect(markup).toContain("Finanzas");
    expect(markup).not.toContain("Registro");
  });

  it("does not invent a label when the business name is absent", () => {
    const markup = render({
      widgets: [
        {
          id: "sf_headcount_by_company",
          title: "Headcount por compania",
          value: 0,
          rows: [{ headcount: 0 }],
        },
      ],
    });

    expect(markup).not.toContain("Registro");
    expect(markup).not.toContain("Sin clasificar");
  });
});
