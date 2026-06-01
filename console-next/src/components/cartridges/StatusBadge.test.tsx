import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";

import { StatusBadge, type ConnectionStatus } from "./StatusBadge";

describe("StatusBadge", () => {
  it.each([
    ["connected", "Conectado", "Estado: Conectado"],
    ["untested", "Sin probar", "Estado: Sin probar"],
    ["unconfigured", "Sin configurar", "Estado: Sin configurar"],
    ["failed", "Falló", "Estado: Falló"],
  ] satisfies Array<[ConnectionStatus, string, string]>)(
    "renders the %s state with label and aria text",
    (status, label, ariaLabel) => {
      const markup = renderToStaticMarkup(<StatusBadge status={status} />);

      expect(markup).toContain(label);
      expect(markup).toContain(`aria-label="${ariaLabel}"`);
    },
  );
});
