import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";

import { MiniBar, ReadinessBadge, readinessLabels, readinessTone } from "./StatusBadge";

describe("Control Room readiness states", () => {
  it.each([
    ["ready", "Operativa"],
    ["partial", "Parcial"],
    ["stub", "Stub"],
    ["empty", "Sin datos"],
    ["missing", "Faltante"],
    ["unavailable", "No disponible"],
    ["blocked", "Bloqueada"],
    ["no_permission", "Sin permiso"],
    ["error", "Error"],
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
    const markup = renderToStaticMarkup(<MiniBar value={30} max={100} label="Data-ready" />);

    expect(markup).toContain("Data-ready");
    expect(markup).toContain("30%");
  });
});
