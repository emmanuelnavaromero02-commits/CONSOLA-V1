// @vitest-environment jsdom

import { act, useState } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it } from "vitest";

import { EMPTY_SPEC, type ExplorerColumn, type ExplorerSpec } from "@/lib/explorer/spec";

import { AssistedExplorer } from "./AssistedExplorer";

const COLUMNS: ExplorerColumn[] = [
  { name: "salario", type: "DOUBLE", kind: "number" },
  { name: "nombre", type: "VARCHAR", kind: "text" },
  { name: "load_date", type: "DATE", kind: "temporal" },
];

let container: HTMLDivElement;
let root: Root;
let latest: ExplorerSpec = EMPTY_SPEC;

function Harness({ initial = EMPTY_SPEC }: { initial?: ExplorerSpec }) {
  const [spec, setSpec] = useState(initial);
  const onSpecChange = (next: ExplorerSpec) => {
    latest = next;
    setSpec(next);
  };
  return <AssistedExplorer columns={COLUMNS} spec={spec} onSpecChange={onSpecChange} rowCap={2000} latestAvailable />;
}

async function render(node: React.ReactNode) {
  await act(async () => {
    root.render(node);
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

describe("AssistedExplorer", () => {
  it("renders loading, error and empty schema states honestly", async () => {
    await render(<AssistedExplorer columns={[]} spec={EMPTY_SPEC} onSpecChange={() => {}} rowCap={2000} latestAvailable={false} loading />);
    expect(container.textContent).toContain("Cargando columnas de la fuente de datos");
    await render(
      <AssistedExplorer columns={[]} spec={EMPTY_SPEC} onSpecChange={() => {}} rowCap={2000} latestAvailable={false} error="Sin archivos" />,
    );
    expect(container.querySelector('[role="alert"]')?.textContent).toContain("Sin archivos");
    await render(<AssistedExplorer columns={[]} spec={EMPTY_SPEC} onSpecChange={() => {}} rowCap={2000} latestAvailable={false} />);
    expect(container.textContent).toContain("no tiene columnas legibles");
  });

  it("feeds the natural-language result into the visual builder", async () => {
    await render(<Harness />);
    const input = container.querySelector<HTMLInputElement>('[data-testid="nl-bar"] input');
    await act(async () => {
      Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, "value")?.set?.call(input, "salario mayor que 100 y solo la carga más reciente");
      input?.dispatchEvent(new Event("input", { bubbles: true }));
    });
    await act(async () => {
      container.querySelector("form")?.dispatchEvent(new Event("submit", { bubbles: true, cancelable: true }));
    });
    expect(latest.filters).toHaveLength(1);
    expect(latest.filters[0]).toMatchObject({ column: "salario", op: "gt", value: "100" });
    expect(latest.latestOnly).toBe(true);
    expect(container.querySelector<HTMLSelectElement>('[aria-label="Columna del filtro 1"]')?.value).toBe("salario");
    expect(container.querySelector<HTMLInputElement>('[aria-label="Valor del filtro 1"]')?.value).toBe("100");
  });

  it("lists incomplete filters before the user can run them", async () => {
    await render(
      <Harness initial={{ ...EMPTY_SPEC, filters: [{ id: "x", column: "salario", op: "gt", value: "", valueTo: "" }] }} />,
    );
    expect(container.querySelector('[data-testid="explorer-problems"]')?.textContent).toContain("Filtro 1: escribe un valor.");
  });
});
