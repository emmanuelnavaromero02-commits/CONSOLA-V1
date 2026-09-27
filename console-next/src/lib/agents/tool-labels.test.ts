import { describe, expect, it } from "vitest";

import { forbiddenTermsIn } from "@/lib/glossary";

import { hasToolLabel, splitToolId, toolLabel, toolServerLabel, toolTooltip } from "./tool-labels";

describe("tool labels", () => {
  it("splits server and tool on the first separator like the runtime", () => {
    expect(splitToolId("refinement__query_dataset")).toEqual({ server: "refinement", name: "query_dataset" });
    expect(splitToolId("mcp-infra__control_room__talent_kpis_read")).toEqual({
      server: "mcp-infra",
      name: "control_room__talent_kpis_read",
    });
    expect(splitToolId("search_rag")).toEqual({ server: "", name: "search_rag" });
  });

  it("names tools in Spanish and keeps the raw id for the tooltip", () => {
    expect(toolLabel("refinement__get_data_catalog")).toBe("Consultar el catálogo de datos");
    expect(toolLabel("mcp-infra__request_admin_help")).toBe("Escalar al administrador");
    expect(toolLabel("mcp-infra__control_room__talent_kpis_read")).toBe("Indicadores de talento");
    expect(toolTooltip("refinement__query_dataset", "Runs SQL")).toBe("refinement__query_dataset\nRuns SQL");
    expect(toolTooltip("refinement__query_dataset")).toBe("refinement__query_dataset");
    expect(toolServerLabel("refinement")).toBe("Datos y catálogo");
    expect(toolServerLabel("mcp-infra")).toBe("Plataforma y conocimiento");
  });

  it("keeps data-source tool names free of technical vocabulary", () => {
    for (const name of [
      "cartridge_get_manifest",
      "cartridge_list_entities",
      "cartridge_get_schema",
      "cartridge_query_kb",
      "list_cartridges",
      "postgres_execute_query",
    ]) {
      expect(hasToolLabel(name)).toBe(true);
      expect(forbiddenTermsIn(toolLabel(`mcp-infra__${name}`))).toEqual([]);
    }
  });

  it("falls back to a readable name for unknown tools", () => {
    expect(hasToolLabel("x__brand_new_tool")).toBe(false);
    expect(toolLabel("x__brand_new_tool")).toBe("brand new tool");
  });
});
