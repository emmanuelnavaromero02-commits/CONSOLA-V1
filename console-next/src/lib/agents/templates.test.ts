import { describe, expect, it } from "vitest";

import type { AgentToolCatalogItem } from "@/lib/admin-surfaces";
import { forbiddenTermsIn } from "@/lib/glossary";

import { AGENT_TEMPLATES, catalogToolIds, templateTools } from "./templates";
import { hasToolLabel } from "./tool-labels";

const CATALOG: Record<string, AgentToolCatalogItem[]> = {
  refinement: [
    { name: "get_data_catalog" },
    { name: "list_datasets" },
    { name: "get_schema" },
    { name: "describe_silver" },
    { name: "query_dataset" },
  ],
  "mcp-infra": [{ name: "search_rag" }, { name: "request_admin_help" }],
};

describe("agent templates", () => {
  it("ships the three executive profiles", () => {
    expect(AGENT_TEMPLATES.map((template) => [template.icon, template.name, template.style])).toEqual([
      ["💼", "Auditor de Compensaciones y Equidad Salarial", "precision"],
      ["📈", "Analista de Movilidad y Retención de Talento", "creative"],
      ["🛡️", "Oficial de Cumplimiento Normativo", "precision"],
    ]);
  });

  it("uses only real backend knowledge kinds and labelled tools", () => {
    for (const template of AGENT_TEMPLATES) {
      expect(template.ragKinds.every((kind) => kind === "document" || kind === "schema")).toBe(true);
      expect(template.tools.every((tool) => /^(refinement|mcp-infra)__/.test(tool))).toBe(true);
      expect(template.tools.every(hasToolLabel)).toBe(true);
      expect(template.tools).toContain("mcp-infra__request_admin_help");
    }
  });

  it("instructs aggregates only, small-group suppression and honest escalation", () => {
    for (const template of AGENT_TEMPLATES) {
      expect(template.instructions).toContain("cifras agregadas");
      expect(template.instructions).toContain("menos de 5 personas");
      expect(template.instructions).toContain("get_data_catalog");
      expect(template.instructions).toContain("payCompValue");
      expect(template.instructions).toContain("request_admin_help");
      expect(template.instructions).toContain("no estimes ni inventes");
      expect(forbiddenTermsIn(`${template.name} ${template.description} ${template.instructions} ${template.personality}`)).toEqual([]);
    }
  });

  it("intersects template tools with the live catalog", () => {
    expect(catalogToolIds(CATALOG).has("refinement__query_dataset")).toBe(true);
    const mobility = AGENT_TEMPLATES.find((template) => template.id === "talent_mobility")!;
    expect(templateTools(mobility, CATALOG)).toEqual({
      available: [
        "refinement__get_data_catalog",
        "refinement__list_datasets",
        "refinement__get_schema",
        "refinement__describe_silver",
        "refinement__query_dataset",
        "mcp-infra__search_rag",
        "mcp-infra__request_admin_help",
      ],
      missing: ["mcp-infra__control_room__talent_kpis_read"],
    });
    expect(templateTools(mobility, {}).available).toEqual([]);
    expect(templateTools(mobility, null).missing).toHaveLength(mobility.tools.length);
  });
});
