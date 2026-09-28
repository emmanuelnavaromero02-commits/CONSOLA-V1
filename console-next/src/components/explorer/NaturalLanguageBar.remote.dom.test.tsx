// @vitest-environment jsdom

import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type { NlColumn } from "@/lib/explorer/nl-parse";
import type { ExplorerSource } from "@/lib/explorer/spec";

const clientMock = vi.hoisted(() => ({
  exploreNl: vi.fn(),
}));

vi.mock("@/lib/explorer/client", () => clientMock);

import { NaturalLanguageBar } from "./NaturalLanguageBar";

const COLUMNS: NlColumn[] = [
  { name: "salario", kind: "number" },
  { name: "nombre", kind: "text" },
];
const SOURCE: ExplorerSource = { kind: "dataset", name: "ventas" };

let container: HTMLDivElement;
let root: Root;

async function render(node: React.ReactNode) {
  await act(async () => {
    root.render(node);
  });
}

async function typeQuestion(text: string) {
  const input = container.querySelector<HTMLInputElement>("input");
  await act(async () => {
    Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, "value")?.set?.call(input, text);
    input?.dispatchEvent(new Event("input", { bubbles: true }));
  });
}

function askButton(): HTMLButtonElement | null {
  return container.querySelector('[data-testid="nl-ask-copilot"]');
}

beforeEach(() => {
  (globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;
  vi.clearAllMocks();
  container = document.createElement("div");
  document.body.append(container);
  root = createRoot(container);
});

afterEach(async () => {
  await act(async () => root.unmount());
  container.remove();
});

describe("NaturalLanguageBar copilot action", () => {
  it("is absent without a source (deterministic parse stays the only path)", async () => {
    await render(
      <NaturalLanguageBar columns={COLUMNS} latestAvailable rowCap={2000} onApply={vi.fn()} />,
    );
    expect(askButton()).toBeNull();
  });

  it("loads the returned spec into the builder as editable filters", async () => {
    clientMock.exploreNl.mockResolvedValue({
      spec: {
        columns: [],
        filters: [
          { column: "salario", op: "gt", value: 20000, values: null },
          { column: "nombre", op: "in", value: null, values: ["García, Juan", "Luis"] },
        ],
        sort: [{ column: "salario", direction: "desc" }],
        limit: 10,
        latest_only: false,
      },
      question: "salario mayor a 20000",
    });
    const onApply = vi.fn();
    await render(
      <NaturalLanguageBar
        columns={COLUMNS}
        latestAvailable
        rowCap={2000}
        onApply={onApply}
        source={SOURCE}
      />,
    );
    await typeQuestion("salario mayor a 20000 de Ana o Luis");
    await act(async () => {
      askButton()?.click();
    });

    expect(clientMock.exploreNl).toHaveBeenCalledWith(SOURCE, "salario mayor a 20000 de Ana o Luis");
    expect(onApply).toHaveBeenCalledTimes(1);
    const applied = onApply.mock.calls[0][0];
    expect(applied.filters).toEqual([
      { column: "salario", op: "gt", value: "20000", valueTo: "" },
      {
        column: "nombre",
        op: "in",
        value: "García, Juan, Luis",
        valueTo: "",
        values: ["García, Juan", "Luis"],
      },
    ]);
    expect(applied.sort).toEqual([{ column: "salario", direction: "desc" }]);
    expect(applied.limit).toBe(10);
    expect(container.textContent).toContain("Interpretación del copiloto");
  });

  it("shows the offline message when the assistant is unavailable", async () => {
    const error = new Error("upstream") as Error & { status: number };
    error.status = 503;
    clientMock.exploreNl.mockRejectedValue(error);
    const onApply = vi.fn();
    await render(
      <NaturalLanguageBar
        columns={COLUMNS}
        latestAvailable
        rowCap={2000}
        onApply={onApply}
        source={SOURCE}
      />,
    );
    await typeQuestion("dame todo");
    await act(async () => {
      askButton()?.click();
    });
    expect(onApply).not.toHaveBeenCalled();
    expect(container.querySelector('[data-testid="nl-remote-error"]')?.textContent)
      .toBe("Sin conexión al asistente");
  });
});
