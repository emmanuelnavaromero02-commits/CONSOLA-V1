// @vitest-environment jsdom

import { act, useState } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it } from "vitest";

import { EMPTY_SPEC, type ExplorerColumn, type ExplorerSpec } from "@/lib/explorer/spec";

import { FilterBuilder } from "./FilterBuilder";

const COLUMNS: ExplorerColumn[] = [
  { name: "nombre", type: "VARCHAR", kind: "text" },
  { name: "salario", type: "DOUBLE", kind: "number" },
  { name: "fecha_ingreso", type: "DATE", kind: "temporal" },
  { name: "activo", type: "BOOLEAN", kind: "boolean" },
  { name: "meta", type: "STRUCT(a INTEGER)", kind: "other" },
];

let container: HTMLDivElement;
let root: Root;
let latest: ExplorerSpec = EMPTY_SPEC;

function Harness({ initial = EMPTY_SPEC, latestAvailable = false }: { initial?: ExplorerSpec; latestAvailable?: boolean }) {
  const [spec, setSpec] = useState(initial);
  const onChange = (next: ExplorerSpec) => {
    latest = next;
    setSpec(next);
  };
  return <FilterBuilder columns={COLUMNS} spec={spec} onChange={onChange} rowCap={2000} latestAvailable={latestAvailable} />;
}

async function render(node: React.ReactNode) {
  await act(async () => {
    root.render(node);
  });
}

function button(text: string | RegExp): HTMLButtonElement | undefined {
  return [...container.querySelectorAll<HTMLButtonElement>("button")].find((item) =>
    typeof text === "string" ? item.textContent?.includes(text) || item.getAttribute("aria-label") === text : text.test(item.textContent ?? ""),
  );
}

function byLabel<T extends HTMLElement>(label: string): T | null {
  return container.querySelector<T>(`[aria-label="${label}"]`);
}

async function click(element: Element | null | undefined) {
  expect(element).toBeTruthy();
  await act(async () => {
    element?.dispatchEvent(new MouseEvent("click", { bubbles: true }));
  });
}

async function choose(element: HTMLSelectElement | null, value: string) {
  expect(element).toBeTruthy();
  await act(async () => {
    Object.getOwnPropertyDescriptor(HTMLSelectElement.prototype, "value")?.set?.call(element, value);
    element?.dispatchEvent(new Event("change", { bubbles: true }));
  });
}

async function type(element: HTMLInputElement | null, value: string) {
  expect(element).toBeTruthy();
  await act(async () => {
    Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, "value")?.set?.call(element, value);
    element?.dispatchEvent(new Event("input", { bubbles: true }));
  });
}

beforeEach(() => {
  (globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;
  container = document.createElement("div");
  document.body.append(container);
  root = createRoot(container);
});

afterEach(async () => {
  await act(async () => root.unmount());
  container.remove();
});

describe("FilterBuilder", () => {
  it("adds filters whose operators depend on the column type", async () => {
    await render(<Harness />);
    expect(container.textContent).toContain("Sin filtros");
    await click(button("Agregar filtro"));
    expect(latest.filters[0]).toMatchObject({ column: "nombre", op: "contains" });
    const ops = () => [...(byLabel<HTMLSelectElement>("Condición del filtro 1")?.options ?? [])].map((option) => option.textContent);
    expect(ops()).toContain("contiene");
    expect(ops()).not.toContain("es mayor que");

    await type(byLabel("Valor del filtro 1"), "Ana");
    expect(latest.filters[0].value).toBe("Ana");
    await choose(byLabel("Columna del filtro 1"), "salario");
    expect(latest.filters[0]).toMatchObject({ column: "salario", op: "eq", value: "" });
    expect(ops()).toContain("está entre");
    expect(ops()).not.toContain("contiene");

    await choose(byLabel("Condición del filtro 1"), "between");
    await type(byLabel("Desde (filtro 1)"), "10");
    await type(byLabel("Hasta (filtro 1)"), "20");
    expect(latest.filters[0]).toMatchObject({ op: "between", value: "10", valueTo: "20" });

    await choose(byLabel("Columna del filtro 1"), "fecha_ingreso");
    expect(byLabel<HTMLInputElement>("Desde (filtro 1)")?.type).toBe("date");

    await choose(byLabel("Columna del filtro 1"), "activo");
    expect(byLabel<HTMLSelectElement>("Valor del filtro 1")?.tagName).toBe("SELECT");
    await choose(byLabel("Valor del filtro 1"), "false");
    expect(latest.filters[0]).toMatchObject({ column: "activo", op: "eq", value: "false" });

    await choose(byLabel("Columna del filtro 1"), "meta");
    expect(ops()).toEqual(["está vacío", "no está vacío"]);
    expect(byLabel("Valor del filtro 1")).toBeNull();

    await click(button("Quitar filtro 1"));
    expect(latest.filters).toEqual([]);
  });

  it("builds sorting only on sortable columns", async () => {
    await render(<Harness />);
    await click(button("Agregar orden"));
    expect(latest.sort).toEqual([{ column: "nombre", direction: "asc" }]);
    const options = [...(byLabel<HTMLSelectElement>("Columna de orden 1")?.options ?? [])].map((option) => option.value);
    expect(options).not.toContain("meta");
    await choose(byLabel("Dirección de orden 1"), "desc");
    await choose(byLabel("Columna de orden 1"), "salario");
    expect(latest.sort).toEqual([{ column: "salario", direction: "desc" }]);
    await click(button("Agregar orden"));
    expect(latest.sort[1].column).toBe("nombre");
    await click(button("Quitar orden 1"));
    expect(latest.sort).toEqual([{ column: "nombre", direction: "asc" }]);
  });

  it("selects columns, clamps the row limit and exposes the latest-load switch", async () => {
    await render(<Harness latestAvailable />);
    const boxes = [...container.querySelectorAll<HTMLInputElement>('details input[type="checkbox"]')];
    await click(boxes[1]);
    await click(boxes[0]);
    expect(latest.columns).toEqual(["nombre", "salario"]);
    await click(button("Mostrar todas las columnas"));
    expect(latest.columns).toEqual([]);

    const rows = [...container.querySelectorAll<HTMLInputElement>('input[type="number"]')][0];
    await type(rows, "5000");
    expect(latest.limit).toBe(2000);
    expect(container.textContent).toContain("Filas a mostrar");

    const latestBox = [...container.querySelectorAll<HTMLLabelElement>("label")].find((label) =>
      label.textContent?.includes("Solo la carga más reciente"),
    )?.querySelector("input");
    await click(latestBox);
    expect(latest.latestOnly).toBe(true);
  });

  it("hides the latest-load switch when the source cannot honor it", async () => {
    await render(<Harness />);
    expect(container.textContent).not.toContain("Solo la carga más reciente");
  });
});
