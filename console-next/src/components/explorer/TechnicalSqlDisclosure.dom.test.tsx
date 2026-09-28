// @vitest-environment jsdom

import { act, useState } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { TechnicalSqlDisclosure } from "./TechnicalSqlDisclosure";

let container: HTMLDivElement;
let root: Root;

function Harness({ generated, onUse }: { generated?: string | null; onUse?: (sql: string) => void }) {
  const [open, setOpen] = useState(false);
  return (
    <TechnicalSqlDisclosure open={open} onToggle={setOpen} generatedSql={generated} onUseGenerated={onUse}>
      <label htmlFor="sql-field">SQL</label>
      <textarea id="sql-field" name="sql" defaultValue="select 1" />
    </TechnicalSqlDisclosure>
  );
}

async function render(node: React.ReactNode) {
  await act(async () => {
    root.render(node);
  });
}

async function click(element: Element | null | undefined) {
  expect(element).toBeTruthy();
  await act(async () => {
    element?.dispatchEvent(new MouseEvent("click", { bubbles: true, cancelable: true }));
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

describe("TechnicalSqlDisclosure", () => {
  it("keeps the SQL editor mounted but collapsed until the user opens it", async () => {
    await render(<Harness />);
    const details = container.querySelector("details");
    expect(details?.open).toBe(false);
    expect(container.querySelector("summary")?.textContent).toContain("Ver consulta SQL técnica");
    expect(container.querySelector('textarea[name="sql"]')).toBeTruthy();
    await click(container.querySelector("summary"));
    expect(container.querySelector("details")?.open).toBe(true);
    await click(container.querySelector("summary"));
    expect(container.querySelector("details")?.open).toBe(false);
  });

  it("shows the server-generated SQL read-only and can copy it to the editor", async () => {
    const onUse = vi.fn();
    await render(<Harness generated={"SELECT * FROM t WHERE \"a\" = 'x'"} onUse={onUse} />);
    expect(container.querySelector('[data-testid="generated-sql"]')?.textContent).toBe("SELECT * FROM t WHERE \"a\" = 'x'");
    expect(container.querySelector('[data-testid="generated-sql"]')?.tagName).toBe("PRE");
    const copy = [...container.querySelectorAll("button")].find((button) => button.textContent?.includes("Copiar al editor"));
    await click(copy);
    expect(onUse).toHaveBeenCalledWith("SELECT * FROM t WHERE \"a\" = 'x'");
  });

  it("omits the generated block when there is nothing to show", async () => {
    await render(<Harness generated={null} />);
    expect(container.querySelector('[data-testid="generated-sql"]')).toBeNull();
  });
});
