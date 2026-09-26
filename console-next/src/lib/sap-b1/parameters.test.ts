import { describe, expect, it } from "vitest";

import {
  buildParametersText,
  emptyForm,
  formFromParameters,
  parseParametersText,
  validateForm,
  validateParameter,
} from "./parameters";
import type { SapB1CatalogEntry } from "./types";

const catalog: SapB1CatalogEntry[] = [
  { key: "margin_min_pct", kind: "threshold", unit: "%", default: null, case: "finanzas", label: "Margen bruto mínimo" },
  { key: "reconciliation_tolerance_pct", kind: "setting", unit: "%", default: "1", case: "finanzas", label: "Tolerancia" },
  { key: "expiry_red_days", kind: "setting", unit: "días", default: "30", case: "ventas", label: "Rojo" },
];

const text = [
  "# comentario",
  "threshold:*:*:margin_min_pct=18.5",
  "setting:*:*:expiry_red_days=21",
  "account:*:*:revenue=4100*, 4200",
  "account:mx:*:cogs=5100*",
  "branch:mx:*:ALM01=Filial Norte",
  "threshold:us:2026-08:margin_min_pct=15",
  "setting:*:*:custom_flag=abc",
].join("\n");

describe("business parameters grammar", () => {
  it("parses kind:company:period:key=value entries, comments and separators", () => {
    const parsed = parseParametersText(`${text};setting:*:*:safety_days=7`, catalog);
    expect(parsed.errors).toEqual([]);
    expect(parsed.parameters).toHaveLength(8);
    expect(parsed.parameters[2]).toEqual({ kind: "account", company: "*", period: "*", key: "revenue", value: "4100*,4200" });
    expect(parsed.parameters[4]).toEqual({ kind: "branch", company: "mx", period: "*", key: "ALM01", value: "Filial Norte" });
  });

  it("round-trips text → form → text without losing entries the form does not edit", () => {
    const parsed = parseParametersText(text, catalog);
    const form = formFromParameters(parsed.parameters, catalog);
    expect(form.values.margin_min_pct).toBe("18.5");
    expect(form.values.reconciliation_tolerance_pct).toBe("");
    expect(form.branches).toEqual([{ company: "mx", warehouse: "ALM01", name: "Filial Norte" }]);
    expect(form.accounts).toEqual([
      { company: "*", key: "revenue", codes: "4100*,4200" },
      { company: "mx", key: "cogs", codes: "5100*" },
    ]);
    expect(form.extra.map((item) => item.key)).toEqual(["margin_min_pct", "custom_flag"]);

    const built = buildParametersText(form, catalog);
    expect(built).toBe(
      [
        "threshold:*:*:margin_min_pct=18.5",
        "setting:*:*:expiry_red_days=21",
        "account:*:*:revenue=4100*,4200",
        "account:mx:*:cogs=5100*",
        "branch:mx:*:ALM01=Filial Norte",
        "threshold:us:2026-08:margin_min_pct=15",
        "setting:*:*:custom_flag=abc",
        "",
      ].join("\n"),
    );
    const reparsed = parseParametersText(built, catalog);
    expect(reparsed.errors).toEqual([]);
    expect(new Set(reparsed.parameters.map((item) => JSON.stringify(item)))).toEqual(
      new Set(parsed.parameters.map((item) => JSON.stringify(item))),
    );
    expect(buildParametersText(formFromParameters(reparsed.parameters, catalog), catalog)).toBe(built);
  });

  it("omits empty form values so the catalog default applies", () => {
    const form = emptyForm(catalog);
    expect(buildParametersText(form, catalog)).toBe("");
    form.values.expiry_red_days = " 25 ";
    expect(buildParametersText(form, catalog)).toBe("setting:*:*:expiry_red_days=25\n");
  });

  it("rejects what the cartridge would reject", () => {
    const { errors } = parseParametersText(
      [
        "threshold:*:*:margin_min_pct=alto",
        "branch:*:*:ALM01=Sin empresa",
        "branch:mx:*:ALMACEN-LARGO=Norte",
        "account:*:2026-01:revenue=4100",
        "account:*:*:gastos=4100",
        "setting:MX:*:safety_days=7",
        "setting:*:2026-13:safety_days=7",
        "unknown:*:*:x=1",
        "sin_separador",
        "setting:*:*:expiry_red_days=21",
        "setting:*:*:expiry_red_days=22",
        "threshold:*:*:reconciliation_tolerance_pct=1",
      ].join("\n"),
      catalog,
    );
    expect(errors).toHaveLength(11);
    expect(errors.at(-2)).toContain("repetida");
    expect(errors.at(-1)).toContain("setting");
  });

  it("validates the form before building the text", () => {
    const form = emptyForm(catalog);
    form.branches.push({ company: "mx", warehouse: "A1", name: "Norte; Sur" });
    form.accounts.push({ company: "*", key: "revenue", codes: "41 00" });
    form.values.margin_min_pct = "x";
    const errors = validateForm(form, catalog);
    expect(errors).toHaveLength(3);
    expect(validateParameter({ kind: "branch", company: "mx", period: "*", key: "A1", value: "Norte" })).toBeNull();
  });
});
