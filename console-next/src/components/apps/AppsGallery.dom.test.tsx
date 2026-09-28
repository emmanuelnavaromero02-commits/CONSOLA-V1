// @vitest-environment jsdom

import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it } from "vitest";

import type { AnalyticsApp } from "@/lib/admin-surfaces";

import { AppsGalleryView, appGalleryOrigin } from "./AppsGallery";

let container: HTMLDivElement;
let root: Root;

beforeEach(() => {
  container = document.createElement("div");
  document.body.appendChild(container);
  root = createRoot(container);
});

afterEach(() => {
  act(() => root.unmount());
  container.remove();
});

const workspaceApp: AnalyticsApp = {
  name: "ventas_semana",
  title: "Ventas de la semana",
  description: "Resumen de ventas",
  origin: "workspace",
  datasets_used: ["ventas_diarias"],
};

const cartridgeApp: AnalyticsApp = {
  name: "sap_b1_margen",
  title: "Margen SAP B1",
  origin: "cartridge",
  cartridge_id: "sap_b1",
};

function render(apps: AnalyticsApp[], extra: Partial<Parameters<typeof AppsGalleryView>[0]> = {}) {
  act(() => {
    root.render(<AppsGalleryView apps={apps} {...extra} />);
  });
}

describe("AppsGalleryView", () => {
  it("splits apps into workspace and data-source sections by origin", () => {
    render([workspaceApp, cartridgeApp]);
    const sections = Array.from(container.querySelectorAll("section"));
    expect(sections.map((s) => s.getAttribute("aria-label"))).toEqual([
      "Creadas en este workspace",
      "Aplicaciones de fuentes de datos",
    ]);
    expect(sections[0]?.textContent).toContain("Ventas de la semana");
    expect(sections[0]?.textContent).not.toContain("Margen SAP B1");
    expect(sections[1]?.textContent).toContain("Margen SAP B1");
  });

  it("treats a missing origin as a data-source app", () => {
    expect(appGalleryOrigin({ name: "x" })).toBe("cartridge");
    expect(appGalleryOrigin({ name: "x", origin: "workspace" })).toBe("workspace");
  });

  it("links every card to the analytics viewer", () => {
    render([workspaceApp, cartridgeApp]);
    const hrefs = Array.from(container.querySelectorAll("a")).map((a) =>
      a.getAttribute("href"),
    );
    expect(hrefs).toContain("/analytics/viewer?app=ventas_semana");
    expect(hrefs).toContain("/analytics/viewer?app=sap_b1_margen");
  });

  it("shows honest empty states per section", () => {
    render([]);
    expect(container.textContent).toContain(
      "Aún no hay aplicaciones creadas en este workspace",
    );
    expect(container.textContent).toContain(
      "No hay aplicaciones de fuentes de datos disponibles",
    );
  });

  it("falls back to Sin información when a card has no description", () => {
    render([{ ...cartridgeApp, description: null }]);
    expect(container.textContent).toContain("Sin información");
  });

  it("renders honest error states", () => {
    render([], { isError: true, forbidden: true });
    expect(container.textContent).toContain("No tienes acceso");
    render([], { isError: true, forbidden: false });
    expect(container.textContent).toContain("No se pudo cargar la galería");
  });
});
