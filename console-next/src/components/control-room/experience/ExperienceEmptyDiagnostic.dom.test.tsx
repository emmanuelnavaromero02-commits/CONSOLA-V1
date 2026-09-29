// @vitest-environment jsdom

import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { ExperienceEmptyDiagnostic } from "./ExperienceEmptyDiagnostic";

const boundary = vi.hoisted(() => ({
  getControlRoomDiagnostics: vi.fn(),
}));

vi.mock("@/lib/control-room/diagnostics-client", async (importOriginal) => {
  const actual = await importOriginal<
    typeof import("@/lib/control-room/diagnostics-client")
  >();
  return { ...actual, getControlRoomDiagnostics: boundary.getControlRoomDiagnostics };
});

(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

let container: HTMLDivElement;
let root: Root;

async function render() {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  await act(async () => {
    root.render(
      <QueryClientProvider client={queryClient}>
        <ExperienceEmptyDiagnostic />
      </QueryClientProvider>,
    );
  });
  await act(async () => {
    await new Promise((resolve) => setTimeout(resolve, 0));
  });
}

function links(): Array<{ text: string; href: string | null }> {
  return [...container.querySelectorAll("a")].map((anchor) => ({
    text: anchor.textContent?.trim() ?? "",
    href: anchor.getAttribute("href"),
  }));
}

beforeEach(() => {
  vi.clearAllMocks();
  boundary.getControlRoomDiagnostics.mockResolvedValue({
    sources: [],
    installations: [],
  });
  container = document.createElement("div");
  document.body.append(container);
  root = createRoot(container);
});

afterEach(async () => {
  await act(async () => root.unmount());
  container.remove();
});

describe("ExperienceEmptyDiagnostic", () => {
  it("renders the per-source diagnostic with honest status labels", async () => {
    boundary.getControlRoomDiagnostics.mockResolvedValue({
      sources: [
        {
          domain: "people",
          status: "empty",
          reason: "La fuente no ha materializado filas.",
          count: 0,
          operationallyReady: false,
        },
        {
          domain: "finance",
          status: "ok",
          reason: null,
          count: 12,
          operationallyReady: true,
        },
      ],
      installations: [
        { label: "SAP SuccessFactors", category: "hr", status: "pending_connection" },
      ],
    });
    await render();

    expect(container.textContent).toContain("Diagnóstico preliminar de fuentes");
    expect(container.textContent).toContain("people");
    expect(container.textContent).toContain("Sin datos");
    expect(container.textContent).toContain("finance");
    expect(container.textContent).toContain("Con datos");
    expect(container.textContent).toContain("La fuente no ha materializado filas.");
    expect(container.textContent).toContain("SAP SuccessFactors");
    expect(container.textContent).toContain("Pendiente de conexión");
    expect(container.textContent).not.toContain("No hay observaciones empresariales para mostrar.");
  });

  it("always offers the suggested action plan links", async () => {
    await render();

    expect(container.textContent).toContain("Plan de acción sugerido");
    expect(links()).toEqual(
      expect.arrayContaining([
        { text: "Revisar Fuentes de datos", href: "/marketplace?tab=conectadas" },
        { text: "Ir a extracción", href: "/studio" },
      ]),
    );
  });

  it("degrades to generic copy without diagnostic detail on 403", async () => {
    const forbidden = Object.assign(new Error("forbidden"), { status: 403 });
    boundary.getControlRoomDiagnostics.mockRejectedValue(forbidden);
    await render();

    expect(container.textContent).toContain(
      "Tu perfil no tiene acceso al detalle del diagnóstico de fuentes.",
    );
    expect(container.textContent).toContain("Plan de acción sugerido");
    expect(container.textContent).not.toContain("forbidden");
  });

  it("reports an unavailable diagnostic without leaking backend detail", async () => {
    const failure = Object.assign(new Error("stack trace with internals"), { status: 500 });
    boundary.getControlRoomDiagnostics.mockRejectedValue(failure);
    await render();

    expect(container.textContent).toContain("No se pudo consultar el diagnóstico de fuentes.");
    expect(container.textContent).not.toContain("stack trace with internals");
  });

  it("declares empty diagnostics instead of inventing sources", async () => {
    await render();

    expect(container.textContent).toContain("Ninguna fuente reportó estado.");
    expect(container.textContent).toContain(
      "No hay instalaciones de fuentes registradas en este workspace.",
    );
  });
});
