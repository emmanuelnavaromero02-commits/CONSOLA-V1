// @vitest-environment jsdom

import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

let mockParams = new URLSearchParams();

vi.mock("next/navigation", () => ({
  useSearchParams: () => mockParams,
}));

vi.mock("next/link", () => ({
  default: ({ href, children, ...rest }: { href: string; children: React.ReactNode }) => (
    <a href={href} {...rest}>
      {children}
    </a>
  ),
}));

import WorkspacePage from "./page";

const replaceSpy = vi.fn();

let container: HTMLDivElement;
let root: Root;

beforeEach(() => {
  (globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;
  mockParams = new URLSearchParams();
  replaceSpy.mockClear();
  Object.defineProperty(window, "location", {
    value: { ...window.location, replace: replaceSpy },
    writable: true,
  });
  container = document.createElement("div");
  document.body.append(container);
  root = createRoot(container);
});

afterEach(async () => {
  await act(async () => root.unmount());
  container.remove();
});

describe("workspace redirect page", () => {
  it("redirects to /copilot", async () => {
    await act(async () => {
      root.render(<WorkspacePage />);
    });
    expect(replaceSpy).toHaveBeenCalledWith("/copilot");
    expect(container.querySelector('a[href="/copilot"]')).not.toBeNull();
    expect(container.textContent).toContain("copiloto");
  });

  it("preserves the prompt deep link", async () => {
    mockParams = new URLSearchParams({ prompt: "dame el briefing" });
    await act(async () => {
      root.render(<WorkspacePage />);
    });
    expect(replaceSpy).toHaveBeenCalledWith("/copilot?prompt=dame+el+briefing");
    expect(container.querySelector('a[href="/copilot?prompt=dame+el+briefing"]')).not.toBeNull();
  });
});
