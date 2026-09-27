// @vitest-environment jsdom

import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type { NlColumn } from "@/lib/explorer/nl-parse";

import { NaturalLanguageBar } from "./NaturalLanguageBar";

const COLUMNS: NlColumn[] = [
  { name: "salario", kind: "number" },
  { name: "fecha_ingreso", kind: "temporal" },
  { name: "nombre", kind: "text" },
];

let container: HTMLDivElement;
let root: Root;

async function render(node: React.ReactNode) {
  await act(async () => {
    root.render(node);
  });
}

async function submit(text: string) {
  const input = container.querySelector<HTMLInputElement>("input");
  await act(async () => {
    Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, "value")?.set?.call(input, text);
    input?.dispatchEvent(new Event("input", { bubbles: true }));
  });
  await act(async () => {
    container.querySelector("form")?.dispatchEvent(new Event("submit", { bubbles: true, cancelable: true }));
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

describe("NaturalLanguageBar", () => {
  it("labels the input with the business question", async () => {
    await render(<NaturalLanguageBar columns={COLUMNS} latestAvailable rowCap={2000} onApply={vi.fn()} />);
    const input = container.querySelector("input");
    expect(input?.labels?.[0]?.textContent).toBe("¿Qué deseas analizar o filtrar?");
    expect(container.textContent).toContain("sin IA");
  });

  it("applies what it understood and shows what it did not", async () => {
    const onApply = vi.fn();
    await render(<NaturalLanguageBar columns={COLUMNS} latestAvailable rowCap={2000} onApply={onApply} />);
    await submit("Mostrar empleados con salario mayor al promedio y nombre contiene Ana");
    expect(onApply).toHaveBeenCalledTimes(1);
    expect(onApply.mock.calls[0][0].filters).toEqual([{ column: "nombre", op: "contains", value: "Ana", valueTo: "" }]);
    const feedback = container.querySelector('[data-testid="nl-feedback"]')?.textContent ?? "";
    expect(feedback).toContain("Se aplicó: 1 filtro.");
    expect(feedback).toContain("Los filtros interpretados reemplazan a los anteriores.");
    expect(feedback).toContain("No entendí: «salario mayor al promedio».");
    expect(feedback).toContain("requiere calcularlo");
    expect(feedback).toContain("Se omitió «empleados»");
  });

  it("does not touch the builder when nothing was understood", async () => {
    const onApply = vi.fn();
    await render(<NaturalLanguageBar columns={COLUMNS} latestAvailable rowCap={2000} onApply={onApply} />);
    await submit("color azul");
    expect(onApply).not.toHaveBeenCalled();
    expect(container.textContent).toContain("No se aplicó ningún cambio.");
    expect(container.textContent).toContain("No entendí: «color azul».");
  });

  it("caps requested rows at the source maximum and says so", async () => {
    const onApply = vi.fn();
    await render(<NaturalLanguageBar columns={COLUMNS} latestAvailable rowCap={2000} onApply={onApply} />);
    await submit("primeros 5000");
    expect(onApply.mock.calls[0][0].limit).toBe(2000);
    expect(container.textContent).toContain("el máximo para esta fuente es 2,000");
  });

  it("uses the provided date for relative periods", async () => {
    const onApply = vi.fn();
    await render(
      <NaturalLanguageBar columns={COLUMNS} latestAvailable={false} rowCap={2000} onApply={onApply} today={new Date(2026, 0, 15)} />,
    );
    await submit("fecha de ingreso en este mes");
    expect(onApply.mock.calls[0][0].filters).toEqual([
      { column: "fecha_ingreso", op: "gte", value: "2026-01-01", valueTo: "" },
      { column: "fecha_ingreso", op: "lt", value: "2026-02-01", valueTo: "" },
    ]);
  });
});
