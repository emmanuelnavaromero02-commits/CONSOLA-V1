// @vitest-environment jsdom

import { act, useState } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { CodeEditor } from "./CodeEditor";

let container: HTMLDivElement;
let root: Root;

function Harness({ initial }: { initial: string }) {
  const [value, setValue] = useState(initial);
  return (
    <div>
      <label htmlFor="sql-under-test">SQL</label>
      <CodeEditor id="sql-under-test" name="sql" value={value} onChange={setValue} rows={4} placeholder="select 1" />
    </div>
  );
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

describe("CodeEditor", () => {
  it("keeps the label wiring and draws one gutter row per line", async () => {
    await render(<Harness initial={"select 1\nfrom t\nwhere x"} />);
    const textarea = container.querySelector<HTMLTextAreaElement>("textarea");
    expect(textarea?.id).toBe("sql-under-test");
    expect(textarea?.name).toBe("sql");
    expect(textarea?.getAttribute("wrap")).toBe("off");
    expect(textarea?.getAttribute("spellcheck")).toBe("false");
    expect(textarea?.labels?.[0]?.textContent).toBe("SQL");
    const gutter = container.querySelector('[data-testid="sql-gutter"]');
    expect(gutter?.getAttribute("aria-hidden")).toBe("true");
    expect([...(gutter?.children ?? [])].map((row) => row.textContent)).toEqual(["1", "2", "3"]);
    expect(container.textContent).toContain("Consola SQL · 3 líneas");
  });

  it("updates the gutter as the SQL grows", async () => {
    await render(<Harness initial="" />);
    expect(container.querySelector('[data-testid="sql-gutter"]')?.children).toHaveLength(1);
    const textarea = container.querySelector<HTMLTextAreaElement>("textarea");
    await act(async () => {
      Object.getOwnPropertyDescriptor(HTMLTextAreaElement.prototype, "value")?.set?.call(textarea, "a\nb");
      textarea?.dispatchEvent(new Event("input", { bubbles: true }));
    });
    expect(container.querySelector('[data-testid="sql-gutter"]')?.children).toHaveLength(2);
    expect(container.textContent).toContain("Consola SQL · 2 líneas");
  });

  it("syncs the gutter scroll with the textarea", async () => {
    await render(<Harness initial={"1\n2\n3\n4\n5\n6"} />);
    const textarea = container.querySelector<HTMLTextAreaElement>("textarea") as HTMLTextAreaElement;
    const gutter = container.querySelector<HTMLElement>('[data-testid="sql-gutter"]') as HTMLElement;
    Object.defineProperty(textarea, "scrollTop", { get: () => 40, configurable: true });
    const setter = vi.fn();
    Object.defineProperty(gutter, "scrollTop", { get: () => 0, set: setter, configurable: true });
    await act(async () => {
      textarea.dispatchEvent(new Event("scroll"));
    });
    expect(setter).toHaveBeenCalledWith(40);
  });
});
