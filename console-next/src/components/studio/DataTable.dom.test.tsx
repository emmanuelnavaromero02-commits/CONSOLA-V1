// @vitest-environment jsdom

import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { DataTable } from "./DataTable";

const toastMock = vi.hoisted(() => ({ success: vi.fn(), error: vi.fn(), info: vi.fn(), warning: vi.fn() }));
vi.mock("sonner", () => ({ toast: toastMock }));

const ROWS = Array.from({ length: 30 }, (_, index) => ({ id: index + 1, name: `n${index}` }));

let container: HTMLDivElement;
let root: Root;

async function render(node: React.ReactNode) {
  await act(async () => {
    root.render(node);
  });
}

function byText(selector: string, text: string | RegExp): HTMLElement | undefined {
  return [...container.querySelectorAll<HTMLElement>(selector)].find((element) => {
    const content = element.textContent?.trim() ?? "";
    return typeof text === "string" ? content === text : text.test(content);
  });
}

async function click(element: Element | null | undefined) {
  expect(element, "element to click").toBeTruthy();
  await act(async () => {
    element?.dispatchEvent(new MouseEvent("click", { bubbles: true }));
  });
}

function summary() {
  return container.querySelector('[data-testid="table-summary"]')?.textContent;
}

function firstCells() {
  return [...container.querySelectorAll("tbody tr")].map((row) => row.querySelector("td")?.textContent);
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
  vi.restoreAllMocks();
});

describe("DataTable", () => {
  it("paginates 25 rows at a time with an honest counter", async () => {
    await render(<DataTable columns={["id", "name"]} rows={ROWS} />);
    expect(container.querySelectorAll("tbody tr")).toHaveLength(25);
    expect(summary()).toBe("Mostrando 1–25 de 30 registros");
    expect(byText("button", "Anterior")?.hasAttribute("disabled")).toBe(true);
    expect(container.textContent).toContain("Página 1 de 2");
    await click(byText("button", "Siguiente"));
    expect(container.querySelectorAll("tbody tr")).toHaveLength(5);
    expect(summary()).toBe("Mostrando 26–30 de 30 registros");
    expect(byText("button", "Siguiente")?.hasAttribute("disabled")).toBe(true);
    await click(byText("button", "Anterior"));
    expect(summary()).toBe("Mostrando 1–25 de 30 registros");
  });

  it("hides the pager when everything fits and says so when empty", async () => {
    await render(<DataTable columns={["id"]} rows={ROWS.slice(0, 3)} />);
    expect(byText("button", "Siguiente")).toBeUndefined();
    expect(summary()).toBe("Mostrando 1–3 de 3 registros");
    await render(<DataTable columns={["id"]} rows={[]} />);
    expect(summary()).toBe("Sin registros");
    expect(byText("button", /Copiar CSV/)?.hasAttribute("disabled")).toBe(true);
  });

  it("sorts by a column with aria-sort and resets to the first page", async () => {
    await render(<DataTable columns={["id", "name"]} rows={ROWS} />);
    await click(byText("button", "Siguiente"));
    const [idHeader, nameHeader] = [...container.querySelectorAll("th")];
    expect(idHeader.getAttribute("aria-sort")).toBe("none");
    const sortButton = idHeader.querySelector("button");
    expect(sortButton?.getAttribute("title")).toBe("Ordenar por id");
    await click(sortButton);
    expect(idHeader.getAttribute("aria-sort")).toBe("ascending");
    expect(summary()).toBe("Mostrando 1–25 de 30 registros");
    expect(firstCells()[0]).toBe("1");
    await click(sortButton);
    expect(idHeader.getAttribute("aria-sort")).toBe("descending");
    expect(firstCells()[0]).toBe("30");
    expect(nameHeader.getAttribute("aria-sort")).toBe("none");
  });

  it("uses the API total when it is larger than the loaded rows", async () => {
    await render(<DataTable columns={["id", "name"]} rows={ROWS} total={1240} />);
    expect(summary()).toBe("Mostrando 1–25 de 1,240 registros · 30 cargados en la vista previa");
  });

  it("copies every loaded row as CSV in the sorted order", async () => {
    const writeText = vi.fn().mockResolvedValue(undefined);
    Object.defineProperty(navigator, "clipboard", { value: { writeText }, configurable: true });
    await render(<DataTable columns={["id", "name"]} rows={ROWS} />);
    await click(container.querySelector("th button"));
    await click(container.querySelector("th button"));
    await click(byText("button", /Copiar CSV/));
    const csv = writeText.mock.calls[0][0] as string;
    expect(csv.startsWith("id,name\r\n30,n29\r\n29,n28\r\n")).toBe(true);
    expect(csv.trim().split("\r\n")).toHaveLength(31);
    expect(toastMock.success).toHaveBeenCalledWith("CSV copiado al portapapeles.");
  });

  it("reports a clipboard failure", async () => {
    Object.defineProperty(navigator, "clipboard", { value: undefined, configurable: true });
    await render(<DataTable columns={["id"]} rows={ROWS} />);
    await click(byText("button", /Copiar CSV/));
    expect(toastMock.error).toHaveBeenCalledWith("No se pudo copiar el CSV.");
  });

  it("exports a CSV file with the formula guard", async () => {
    const blobs: Blob[] = [];
    Object.assign(URL, {
      createObjectURL: vi.fn((blob: Blob) => {
        blobs.push(blob);
        return "blob:csv";
      }),
      revokeObjectURL: vi.fn(),
    });
    const anchors: HTMLAnchorElement[] = [];
    vi.spyOn(HTMLAnchorElement.prototype, "click").mockImplementation(function (this: HTMLAnchorElement) {
      anchors.push(this);
    });
    await render(
      <DataTable columns={["id", "name"]} rows={[{ id: 1, name: "=cmd" }, { id: 2, name: "ok" }]} exportName="orders" />,
    );
    await click(byText("button", /Exportar CSV/));
    expect(anchors[0]?.download).toBe("orders.csv");
    const text = new TextDecoder().decode((await blobs[0].arrayBuffer()).slice(3));
    expect(text).toBe("id,name\r\n1,'=cmd\r\n2,ok\r\n");
  });

  it("infers columns from the rows when none are declared", async () => {
    await render(<DataTable columns={[]} rows={[{ a: 1, b: { x: true } }]} />);
    expect([...container.querySelectorAll("th")].map((th) => th.textContent)).toEqual(["a", "b"]);
    expect(container.querySelector("tbody td:nth-child(2)")?.textContent).toBe('{"x":true}');
  });
});
