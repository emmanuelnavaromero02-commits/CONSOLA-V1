import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";

import { StatusBadge, type ConnectionStatus } from "./StatusBadge";

describe("StatusBadge", () => {
  it.each([
    ["connected", "Conectado", "Estado: Conectado"],
    ["untested", "Sin probar", "Estado: Sin probar"],
    ["unconfigured", "Sin configurar", "Estado: Sin configurar"],
    ["failed", "Falló", "Estado: Falló"],
    ["stale", "Datos antiguos", "Estado: Datos antiguos"],
    ["very_stale", "Datos muy antiguos", "Estado: Datos muy antiguos"],
  ] satisfies Array<[ConnectionStatus, string, string]>)(
    "renders the %s state with label and aria text",
    (status, label, ariaLabel) => {
      const markup = renderToStaticMarkup(<StatusBadge status={status} />);

      expect(markup).toContain(label);
      expect(markup).toContain(`aria-label="${ariaLabel}"`);
    },
  );

  it("does not present stale data as connected", () => {
    const markup = renderToStaticMarkup(<StatusBadge status="stale" />);

    expect(markup).toContain("Datos antiguos");
    expect(markup).not.toContain("Conectado");
    expect(markup).not.toContain("Falló");
  });

  it("does not present very stale data as a connection failure", () => {
    const markup = renderToStaticMarkup(<StatusBadge status="very_stale" />);

    expect(markup).toContain("Datos muy antiguos");
    expect(markup).not.toContain("Falló");
    expect(markup).not.toContain("Conectado");
  });

  it("appends the data age when ageHours is provided", () => {
    const markup = renderToStaticMarkup(<StatusBadge status="stale" ageHours={30} />);

    expect(markup).toContain("Datos antiguos (hace ~30 h)");
  });

  it("formats multi-day ages in days", () => {
    const markup = renderToStaticMarkup(<StatusBadge status="very_stale" ageHours={72} />);

    expect(markup).toContain("Datos muy antiguos (hace ~3 d)");
  });

  it("omits the age when ageHours is null", () => {
    const markup = renderToStaticMarkup(<StatusBadge status="stale" ageHours={null} />);

    expect(markup).toContain("Datos antiguos");
    expect(markup).not.toContain("hace ~");
  });
});
