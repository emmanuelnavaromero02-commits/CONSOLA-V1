// @vitest-environment jsdom

import { afterEach, beforeEach, describe, expect, it } from "vitest";

import { COPILOT_BADGE_TEXT, CopilotBadge, copilotTooltip } from "./CopilotBadge";
import { mount, type Mounted } from "./test-utils";

let view: Mounted;

beforeEach(() => {
  view = mount();
});

afterEach(async () => {
  await view.unmount();
});

describe("CopilotBadge", () => {
  it("shows the decided label and explains itself in Spanish", async () => {
    await view.render(<CopilotBadge confidence={0.97} basis={["name:rfc", "pattern:rfc"]} />);
    const badge = view.container.querySelector("span[title]");
    expect(view.container.textContent).toContain("✨ Autocatalogado por Copiloto");
    expect(COPILOT_BADGE_TEXT).toBe("✨ Autocatalogado por Copiloto");
    const title = badge?.getAttribute("title") ?? "";
    expect(title).toContain("Confianza 97%");
    expect(title).toContain("El nombre de la columna indica RFC");
    expect(title).toContain("Los valores tienen formato de RFC");
  });

  it("flags partial profiles without inventing a confidence", () => {
    const tooltip = copilotTooltip({ status: "partial" });
    expect(tooltip).toContain("Perfil parcial");
    expect(tooltip).not.toContain("Confianza");
  });
});
