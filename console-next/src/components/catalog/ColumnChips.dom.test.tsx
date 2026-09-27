// @vitest-environment jsdom

import { afterEach, beforeEach, describe, expect, it } from "vitest";

import { ColumnChips } from "./ColumnChips";
import { mount, type Mounted } from "./test-utils";

let view: Mounted;

beforeEach(() => {
  view = mount();
});

afterEach(async () => {
  await view.unmount();
});

describe("ColumnChips", () => {
  it("shows the human type and keeps the raw type in the tooltip only", async () => {
    await view.render(<ColumnChips column={{ name: "employee_id", type: "BIGINT", is_key: true }} />);
    expect(view.container.textContent).toContain("Identificador");
    expect(view.container.textContent).toContain("Llave");
    expect(view.container.textContent).not.toContain("BIGINT");
    expect(view.container.querySelector('[title="Tipo técnico: BIGINT"]')).toBeTruthy();
  });

  it("renders business classification chips with their evidence", async () => {
    await view.render(
      <ColumnChips
        column={{
          name: "rfc",
          type: "VARCHAR",
          semantic_type: "text",
          classifications: ["confidential", "pii"],
          classification_origin: "copilot",
          copilot_confidence: 0.97,
          copilot_basis: ["name:rfc", "pattern:rfc"],
          stats_redacted: true,
        }}
      />,
    );
    const text = view.container.textContent ?? "";
    expect(text).toContain("Texto");
    expect(text.indexOf("Dato Personal Identificable")).toBeLessThan(text.indexOf("Confidencial"));
    expect(text).toContain("Valores ocultos");
    expect(text).not.toContain("VARCHAR");
    const chip = [...view.container.querySelectorAll("span[title]")].find((node) =>
      node.textContent === "Dato Personal Identificable",
    );
    expect(chip?.getAttribute("title")).toContain("Inferido por el Copiloto");
    expect(chip?.getAttribute("title")).toContain("Confianza 97%");
  });

  it("credits declared protection to the data source", async () => {
    await view.render(
      <ColumnChips column={{ name: "payCompValue", classifications: ["financial"], classification_origin: "packaged" }} />,
    );
    expect(view.container.querySelector('[title^="Declarado por la fuente de datos"]')).toBeTruthy();
    expect(view.container.textContent).toContain("Información Financiera");
  });
});
