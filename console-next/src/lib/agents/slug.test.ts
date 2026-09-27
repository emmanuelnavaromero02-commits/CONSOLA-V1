import { describe, expect, it } from "vitest";

import { AGENT_SLUG_MAX, agentSlug, agentSlugBase } from "./slug";

describe("agentSlug", () => {
  it("strips accents and symbols into a lowercase identifier", () => {
    expect(agentSlugBase("Auditor de Compensaciones y Equidad Salarial")).toBe("auditor_de_compensaciones_y_equidad_salarial");
    expect(agentSlugBase("  Oficial de Cumplimiento — Normativo ✅ ")).toBe("oficial_de_cumplimiento_normativo");
    expect(agentSlugBase("Análisis de Retención (Ñandú)")).toBe("analisis_de_retencion_nandu");
    expect(agentSlugBase("💼")).toBe("agente");
    expect(agentSlugBase("")).toBe("agente");
  });

  it("caps the length without leaving a trailing separator", () => {
    const slug = agentSlugBase("Analista de Movilidad y Retención de Talento para Toda la Organización Regional");
    expect(slug.length).toBeLessThanOrEqual(AGENT_SLUG_MAX);
    expect(slug.endsWith("_")).toBe(false);
    expect(slug).toMatch(/^[a-z0-9_]+$/);
  });

  it("adds numeric suffixes against existing agents", () => {
    expect(agentSlug("Monitor", [])).toBe("monitor");
    expect(agentSlug("Monitor", ["monitor"])).toBe("monitor_2");
    expect(agentSlug("Monitor", ["monitor", "monitor_2", null, undefined])).toBe("monitor_3");
    const long = "a".repeat(80);
    const taken = [agentSlug(long)];
    const next = agentSlug(long, taken);
    expect(next.length).toBeLessThanOrEqual(AGENT_SLUG_MAX);
    expect(next.endsWith("_2")).toBe(true);
  });
});
