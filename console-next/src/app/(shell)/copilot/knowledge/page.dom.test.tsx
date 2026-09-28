// @vitest-environment jsdom

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { forbiddenTermsIn } from "@/lib/glossary";

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

async function renderPage() {
  await act(async () => {
    root.render(
      <QueryClientProvider client={new QueryClient()}>
        <KnowledgePage />
      </QueryClientProvider>,
    );
  });
  await flush();
}

function fileInput(): HTMLInputElement {
  const input = container.querySelector<HTMLInputElement>('input[type="file"]');
  expect(input).toBeTruthy();
  return input!;
}

async function selectFile(file: File) {
  const input = fileInput();
  Object.defineProperty(input, "files", { value: [file], configurable: true });
  await act(async () => {
    input.dispatchEvent(new Event("change", { bubbles: true }));
  });
  await flush();
}

class FakeFileReader {
  static nextResult = "";
  onload: (() => void) | null = null;
  onerror: (() => void) | null = null;
  error: Error | null = null;
  result: string | null = null;

  readAsDataURL() {
    this.result = FakeFileReader.nextResult;
    queueMicrotask(() => this.onload?.());
  }

  readAsText() {
    this.result = FakeFileReader.nextResult;
    queueMicrotask(() => this.onload?.());
  }
}

beforeEach(() => {
  (globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;
  vi.clearAllMocks();
  vi.stubGlobal("FileReader", FakeFileReader as unknown as typeof FileReader);
  apiMock.get.mockResolvedValue({
    data: { sources: [{ id: 7, name: "Manual de nómina", kind: "document", chunk_count: 3 }, { name: "sin id" }] },
  });
  apiMock.post.mockResolvedValue({ data: { source_id: 8 } });
  apiMock.delete.mockResolvedValue({ data: { deleted: true } });
  container = document.createElement("div");
  document.body.append(container);
  root = createRoot(container);
});

afterEach(async () => {
  await act(async () => root.unmount());
  container.remove();
  vi.unstubAllGlobals();
});

describe("KnowledgePage business copy", () => {
  it("presents the surface as company documents without technical vocabulary", async () => {
    await renderPage();

    expect(container.querySelector("h1")?.textContent).toBe("Documentos y Políticas de la Empresa");
    expect(container.textContent).toContain("Sube políticas, manuales y documentos; el Copiloto los usa al responder.");
    expect(container.textContent).toContain("Documentos");
    expect(container.textContent).toContain("Fragmentos indexados");
    expect(container.textContent).toContain("Prueba una pregunta");
    expect(container.textContent).toContain("Pegar texto");
    expect(container.textContent).toContain("Reindexar documentos");
    expect(forbiddenTermsIn(container.textContent ?? "")).toEqual([]);
  });
});

describe("KnowledgePage file upload", () => {
  it("uploads a PDF as base64 with application/pdf mime", async () => {
    await renderPage();
    FakeFileReader.nextResult = "data:application/pdf;base64,JVBERi0xLjQ=";

    await selectFile(new File([new Uint8Array([1])], "politicas.pdf", { type: "application/pdf" }));

    expect(apiMock.post).toHaveBeenCalledWith("/api/rag/ingest", {
      name: "politicas.pdf",
      description: "",
      kind: "document",
      content: "JVBERi0xLjQ=",
      mime_type: "application/pdf",
    });
    expect(toastMock.success).toHaveBeenCalledWith("Documento cargado.");
  });

  it("uploads a plain text file with text/plain mime", async () => {
    await renderPage();
    FakeFileReader.nextResult = "contenido de la política";

    await selectFile(new File(["contenido de la política"], "codigo-etica.txt", { type: "text/plain" }));

    expect(apiMock.post).toHaveBeenCalledWith("/api/rag/ingest", {
      name: "codigo-etica.txt",
      description: "",
      kind: "document",
      content: "contenido de la política",
      mime_type: "text/plain",
    });
  });

  it("rejects files over 10 MB before any request", async () => {
    await renderPage();
    const big = new File([new Uint8Array([1])], "grande.pdf", { type: "application/pdf" });
    Object.defineProperty(big, "size", { value: 10 * 1024 * 1024 + 1 });

    await selectFile(big);

    expect(apiMock.post).not.toHaveBeenCalled();
    expect(toastMock.error).toHaveBeenCalledWith("El archivo supera el máximo de 10 MB.");
  });
});

describe("KnowledgePage source deletion", () => {
  it("confirms before calling DELETE /api/rag/sources/{id}", async () => {
    await renderPage();

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
