// @vitest-environment jsdom

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import KnowledgePage from "./page";

const apiMock = vi.hoisted(() => ({ get: vi.fn(), post: vi.fn(), delete: vi.fn() }));
const toastMock = vi.hoisted(() => ({ success: vi.fn(), error: vi.fn() }));

vi.mock("@/lib/api", () => ({ api: apiMock }));
vi.mock("sonner", () => ({ toast: toastMock }));

let container: HTMLDivElement;
let root: Root;

async function flush() {
  for (let tick = 0; tick < 5; tick += 1) {
    await act(async () => {
      await new Promise((resolve) => setTimeout(resolve, 0));
    });
  }
}

function byText(selector: string, text: string): HTMLElement | undefined {
  return [...container.querySelectorAll<HTMLElement>(selector)].find(
    (element) => element.textContent?.trim() === text,
  );
}

async function click(element: Element | null | undefined) {
  expect(element).toBeTruthy();
  await act(async () => {
    element?.dispatchEvent(new MouseEvent("click", { bubbles: true }));
  });
}

beforeEach(() => {
  (globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;
  vi.clearAllMocks();
  apiMock.get.mockResolvedValue({
    data: { sources: [{ id: 7, name: "Manual de nómina", kind: "document", chunk_count: 3 }, { name: "sin id" }] },
  });
  apiMock.delete.mockResolvedValue({ data: { deleted: true } });
  container = document.createElement("div");
  document.body.append(container);
  root = createRoot(container);
});

afterEach(async () => {
  await act(async () => root.unmount());
  container.remove();
});

describe("KnowledgePage source deletion", () => {
  it("confirms before calling DELETE /api/rag/sources/{id}", async () => {
    await act(async () => {
      root.render(
        <QueryClientProvider client={new QueryClient()}>
          <KnowledgePage />
        </QueryClientProvider>,
      );
    });
    await flush();

    const buttons = [...container.querySelectorAll<HTMLButtonElement>("tbody button")];
    expect(buttons).toHaveLength(2);
    expect(buttons[1].disabled).toBe(true);

    await click(buttons[0]);
    expect(apiMock.delete).not.toHaveBeenCalled();
    const dialog = container.querySelector('[data-testid="delete-rag-source-dialog"]');
    expect(dialog?.textContent).toContain("Manual de nómina");

    await click(byText('[data-testid="delete-rag-source-dialog"] button', "Borrar fuente"));
    await flush();
    expect(apiMock.delete).toHaveBeenCalledWith("/api/rag/sources/7");
    expect(toastMock.success).toHaveBeenCalledWith("Fuente Manual de nómina borrada.");
  });
});
