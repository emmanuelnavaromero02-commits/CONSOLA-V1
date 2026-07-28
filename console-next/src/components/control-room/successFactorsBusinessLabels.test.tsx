import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";

import { SuccessFactorsGoldPanel } from "./SuccessFactorsGoldPanel";
import { businessLabel } from "./successFactorsBusinessLabels";

const blankFillers = [
  0x115f, 0x1160, 0x2800, 0x3164, 0xa8f9, 0xffa0, 0x10af6, 0x1144e,
  0x11945, 0x11c44, 0x11c45, 0x11f48, 0x13441, 0x13442, 0x16fe4,
].map((codepoint) => String.fromCodePoint(codepoint));
const forbiddenLabels = [
  "\u200B",
  "\u2060",
  "\uFEFF",
  ...Array.from({ length: 5 }, (_, index) => `Nombre${String.fromCodePoint(0x202a + index)}`),
  ...Array.from({ length: 4 }, (_, index) => `Nombre${String.fromCodePoint(0x2066 + index)}`),
  "Nombre\uFE0F",
  `Nombre${String.fromCodePoint(0xe0100)}`,
  "\u034F",
  "\u2028",
  "\u2029",
  ...blankFillers,
  ...blankFillers.map((filler) => `Nombre${filler}`),
  "\u200B\u2060\uFEFF",
];

describe("SuccessFactors business labels", () => {
  it("rejects Unicode controls and default-ignorables without stripping them", () => {
    for (const value of forbiddenLabels) expect(businessLabel(value)).toBeNull();
    expect(businessLabel("  Dirección de México  ")).toBe("Dirección de México");
    expect(businessLabel("Área ␢ visible")).toBe("Área ␢ visible");
    expect(businessLabel("（ｓｉｎ　ｎｏｍｂｒｅ）")).toBeNull();
  });

  it("does not render a ready headcount when every row has an invalid label", () => {
    const markup = renderToStaticMarkup(
      <SuccessFactorsGoldPanel
        payload={{
          widgets: [{
            id: "sf_headcount_by_company",
            title: "Headcount por compañía",
            value: forbiddenLabels.length,
            status: "ready",
            rows: forbiddenLabels.map((company_name) => ({ company_name, headcount: 1 })),
          }],
        }}
        loading={false}
        error=""
        sources={[]}
      />,
    );

    expect(markup).toContain("Plantilla por compañía");
    expect(markup).toContain("N/D");
    expect(markup).not.toContain(`>${forbiddenLabels.length}<`);
  });
});
