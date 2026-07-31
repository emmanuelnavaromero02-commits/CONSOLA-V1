// @vitest-environment jsdom

import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { DashboardErrorBoundary } from "./DashboardErrorBoundary";

(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

function Bomb(): never {
  throw new Error("payload inválido: /var/secret.py");
}

let container: HTMLDivElement;
let root: Root;

beforeEach(() => {
  container = document.createElement("div");
  document.body.append(container);
  root = createRoot(container);
});

afterEach(async () => {
  await act(async () => root.unmount());
  container.remove();
});

describe("DashboardErrorBoundary", () => {
  it("degrada a un aviso seguro con reintento, sin detalles técnicos", async () => {
    const consoleError = vi.spyOn(console, "error").mockImplementation(() => undefined);

    await act(async () => {
      root.render(
        <DashboardErrorBoundary>
          <Bomb />
        </DashboardErrorBoundary>,
      );
    });

    const alert = container.querySelector('[role="alert"]');
    expect(alert).not.toBeNull();
    expect(alert?.textContent).toContain("No se pudo mostrar el panel.");
    expect(container.textContent).not.toContain("secret.py");
    expect(container.textContent).not.toContain("payload inválido");
    expect(
      [...container.querySelectorAll("button")].some(
        (button) => button.textContent?.trim() === "Reintentar",
      ),
    ).toBe(true);

    consoleError.mockRestore();
  });

  it("renderiza a sus hijos cuando no hay error", async () => {
    await act(async () => {
      root.render(
        <DashboardErrorBoundary>
          <p>Contenido sano</p>
        </DashboardErrorBoundary>,
      );
    });

    expect(container.textContent).toContain("Contenido sano");
    expect(container.querySelector('[role="alert"]')).toBeNull();
  });
});
