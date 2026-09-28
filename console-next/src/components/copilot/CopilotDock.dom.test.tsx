// @vitest-environment jsdom

import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

let mockPath = "/dashboard";

vi.mock("next/navigation", () => ({
  usePathname: () => mockPath,
}));

vi.mock("next/dynamic", () => ({
  default: () => function DockPanelStub() {
    return <div data-testid="dock-panel-stub" />;
  },
}));

import { CopilotDock } from "./CopilotDock";

let container: HTMLDivElement;
let root: Root;

async function render() {
  await act(async () => {
    root.render(<CopilotDock />);
  });
}

async function pressCmdK() {
  await act(async () => {
    document.dispatchEvent(new KeyboardEvent("keydown", {
      key: "k",
      metaKey: true,
      bubbles: true,
      cancelable: true,
    }));
  });
}

async function flushTimers() {
  await act(async () => {
    await new Promise((resolve) => setTimeout(resolve, 5));
  });
}

beforeEach(() => {
  (globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;
  mockPath = "/dashboard";
  window.sessionStorage.clear();
  container = document.createElement("div");
  document.body.append(container);
  root = createRoot(container);
});

afterEach(async () => {
  await act(async () => root.unmount());
  container.remove();
  vi.restoreAllMocks();
});

function launcher(): HTMLButtonElement | null {
  return container.querySelector('button[aria-label="Abrir copiloto"]');
}

function drawer(): HTMLElement | null {
  return container.querySelector('[role="dialog"][aria-label="Copiloto OMEGA"]');
}

describe("CopilotDock", () => {
  it("renders the floating launcher on authenticated routes", async () => {
    await render();
    expect(launcher()).not.toBeNull();
    expect(drawer()).toBeNull();
  });

  it("toggles the drawer with Cmd/Ctrl+K and persists the state", async () => {
    await render();
    await pressCmdK();
    expect(drawer()).not.toBeNull();
    expect(container.querySelector('[data-testid="dock-panel-stub"]')).not.toBeNull();
    expect(window.sessionStorage.getItem("omega-copilot-dock-open")).toBe("1");

    await pressCmdK();
    expect(drawer()).toBeNull();
    expect(window.sessionStorage.getItem("omega-copilot-dock-open")).toBe("0");
  });

  it("renders closed on first paint and reopens from sessionStorage afterwards", async () => {
    window.sessionStorage.setItem("omega-copilot-dock-open", "1");
    await render();
    expect(drawer()).toBeNull();
    await flushTimers();
    expect(drawer()).not.toBeNull();
  });

  it("moves focus into the drawer when it opens", async () => {
    await render();
    await act(async () => {
      launcher()?.click();
    });
    await flushTimers();
    expect(document.activeElement?.getAttribute("aria-label")).toBe("Cerrar copiloto");
  });

  it("ignores Escape while an inner dialog is open", async () => {
    await render();
    await act(async () => {
      launcher()?.click();
    });
    const aside = container.querySelector("aside");
    const inner = document.createElement("div");
    inner.setAttribute("role", "dialog");
    aside?.append(inner);
    await act(async () => {
      document.dispatchEvent(new KeyboardEvent("keydown", { key: "Escape", bubbles: true }));
    });
    expect(drawer()).not.toBeNull();
    inner.remove();
    await act(async () => {
      document.dispatchEvent(new KeyboardEvent("keydown", { key: "Escape", bubbles: true }));
    });
    expect(drawer()).toBeNull();
  });

  it("restores the previous body overflow after closing", async () => {
    document.body.style.overflow = "auto";
    await render();
    await act(async () => {
      launcher()?.click();
    });
    expect(document.body.style.overflow).toBe("hidden");
    await act(async () => {
      document.dispatchEvent(new KeyboardEvent("keydown", { key: "Escape", bubbles: true }));
    });
    expect(document.body.style.overflow).toBe("auto");
    document.body.style.overflow = "";
  });

  it("closes with Escape and returns focus to the launcher", async () => {
    await render();
    await act(async () => {
      launcher()?.click();
    });
    expect(drawer()).not.toBeNull();
    await act(async () => {
      document.dispatchEvent(new KeyboardEvent("keydown", { key: "Escape", bubbles: true }));
    });
    expect(drawer()).toBeNull();
    expect(document.activeElement).toBe(launcher());
  });

  it("is hidden entirely on /copilot and /workspace", async () => {
    mockPath = "/copilot";
    await render();
    expect(launcher()).toBeNull();
    mockPath = "/workspace";
    await render();
    expect(launcher()).toBeNull();
  });

  it("focuses the main chat input with Cmd+K when one is present", async () => {
    mockPath = "/copilot";
    const input = document.createElement("textarea");
    input.setAttribute("data-copilot-input", "true");
    document.body.append(input);
    await render();
    await pressCmdK();
    expect(document.activeElement).toBe(input);
    input.remove();
  });

  it("survives a sessionStorage that throws", async () => {
    vi.spyOn(Storage.prototype, "setItem").mockImplementation(() => {
      throw new Error("quota");
    });
    vi.spyOn(Storage.prototype, "getItem").mockImplementation(() => {
      throw new Error("blocked");
    });
    await render();
    await pressCmdK();
    expect(drawer()).not.toBeNull();
  });
});
