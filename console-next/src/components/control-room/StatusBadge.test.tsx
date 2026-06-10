import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";

import { MiniBar, ReadinessBadge, readinessLabels, readinessTone } from "./StatusBadge";

describe("Control Room readiness states", () => {
  it.each([
    ["ready", "Listo"],
    ["partial", "Datos incompletos"],
    ["stub", "Sin información suficiente"],
    ["empty", "Sin información suficiente"],
    ["missing", "Información no disponible"],
    ["unavailable", "Información no disponible"],
    ["blocked", "Bloqueado"],
    ["no_permission", "Bloqueado por permisos"],
    ["error", "Error operativo"],
  ] as const)("renders %s explicitly", (status, label) => {
    const markup = renderToStaticMarkup(<ReadinessBadge status={status} />);

    expect(readinessLabels[status]).toBe(label);
    expect(markup).toContain(label);
    expect(markup).not.toContain("undefined");
  });

  it("keeps blocked and no_permission visually distinct from ready", () => {
    expect(readinessTone("ready")).toContain("emerald");
    expect(readinessTone("blocked")).toContain("orange");
    expect(readinessTone("no_permission")).toContain("orange");
    expect(readinessTone("error")).toContain("destructive");
  });

  it("renders a bounded lightweight chart without external libraries", () => {
    const markup = renderToStaticMarkup(<MiniBar value={30} max={100} label="Listos para decidir" />);

    expect(markup).toContain("Listos para decidir");
    expect(markup).toContain("30%");
  });
});
