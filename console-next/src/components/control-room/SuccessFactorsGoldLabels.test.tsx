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
          rows: [{ company_name: "ACMECO Comercio", headcount: 10 }],
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

    expect(markup).toContain("ACMECO Comercio");
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

  it("keeps a named observed zero and drops placeholder or malformed rows", () => {
    const payload = {
      widgets: [
        {
          id: "sf_headcount_by_company",
          title: "Headcount por compania",
          value: 0,
          status: "ready",
          rows: [
            { company_name: "Cero observado", headcount: 0 },
            { company_name: "(sin nombre)", headcount: 4 },
            { company_name: "   ", headcount: 4 },
            { label: "technical-company-id", headcount: 4 },
            { company_name: "Booleano", headcount: true },
            { company_name: "Negativo", headcount: -1 },
            { company_name: "Decimal", headcount: 1.5 },
            { company_name: "Texto", headcount: "2" },
            { company_name: "Ausente" },
          ],
        },
      ],
    } as unknown as SfGoldKpisPayload;

    const markup = render(payload);

    expect(markup).toContain("Cero observado");
    expect(markup).toContain(">0<");
    expect(markup).toContain("Listo");
    expect(markup).not.toContain("(sin nombre)");
    expect(markup).not.toContain("technical-company-id");
    expect(markup).not.toContain("Booleano");
    expect(markup).not.toContain("Negativo");
    expect(markup).not.toContain("Decimal");
    expect(markup).not.toContain("Texto");
    expect(markup).not.toContain("Ausente");
  });

  it.each(["empty", "missing", "blocked"] as const)(
    "does not render %s as an observed zero",
    (status) => {
      const markup = render({
        widgets: [
          {
            id: "sf_headcount_by_company",
            title: "Headcount por compania",
            value: 0,
            status,
            rows: [{ company_name: "No observada", headcount: 0 }],
          },
        ],
      });

      expect(markup).toContain("N/D");
      expect(markup).not.toContain("No observada");
      expect(markup).not.toContain("Listo");
    },
  );

  it("marks a ready widget without valid observations as unavailable", () => {
    const payload = {
      widgets: [
        {
          id: "sf_headcount_by_location",
          title: "Headcount por ubicacion",
          value: 0,
          status: "ready",
          rows: [{ location_name: "Monterrey", headcount: false }],
        },
      ],
    } as unknown as SfGoldKpisPayload;
    const markup = render(payload);

    expect(markup).toContain("N/D");
    expect(markup).toContain("Información no disponible");
    expect(markup).not.toContain("Monterrey");
  });
});
