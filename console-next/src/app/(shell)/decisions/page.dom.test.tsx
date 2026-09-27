// @vitest-environment jsdom

import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { resolveDecisionsTab, resolveFocusedProposal } from "@/components/decisions/DecisionsTabs";

import DecisionsPage from "./page";

const navigation = vi.hoisted(() => ({
  params: new URLSearchParams(),
  replace: vi.fn(),
}));

vi.mock("next/navigation", () => ({
  useSearchParams: () => navigation.params,
  useRouter: () => ({ replace: navigation.replace, push: vi.fn() }),
}));
vi.mock("@/components/decisions/ActionCouncil", () => ({
  ActionCouncil: ({ focusDecisionId }: { focusDecisionId: number | null }) => (
    <div data-testid="council">{`consejo:${focusDecisionId ?? "none"}`}</div>
  ),
}));
vi.mock("@/components/decisions/DecisionsBoard", () => ({
  DecisionsBoard: () => <div data-testid="registro">registro</div>,
}));

(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

let container: HTMLDivElement;
let root: Root;

async function render(query: string) {
  navigation.params = new URLSearchParams(query);
  await act(async () => {
    root.render(<DecisionsPage />);
  });
}

beforeEach(() => {
  vi.clearAllMocks();
  container = document.createElement("div");
  document.body.append(container);
  root = createRoot(container);
});

afterEach(async () => {
  await act(async () => root.unmount());
  container.remove();
});

describe("DecisionsPage", () => {
  it("opens the council by default and honours the proposal link", async () => {
    await render("");
    const tabs = [...container.querySelectorAll('[role="tab"]')];
    expect(tabs.map((tab) => tab.textContent)).toEqual([
      "Consejo de Acciones Sugeridas",
      "Registro",
    ]);
    expect(tabs[0].getAttribute("aria-selected")).toBe("true");
    expect(container.querySelector('[data-testid="council"]')?.textContent).toBe("consejo:none");

    await render("tab=consejo&propuesta=41");
    expect(container.querySelector('[data-testid="council"]')?.textContent).toBe("consejo:41");
  });

  it("switches to the register tab through the URL, also with the keyboard", async () => {
    await render("tab=registro");
    expect(container.querySelector('[data-testid="registro"]')).not.toBeNull();
    expect(container.querySelector('[role="tabpanel"]')?.getAttribute("aria-labelledby")).toBe(
      "decisions-tab-registro",
    );
    const council = container.querySelector("#decisions-tab-consejo") as HTMLButtonElement;
    await act(async () => council.click());
    expect(navigation.replace).toHaveBeenLastCalledWith("/decisions?tab=consejo", { scroll: false });
    const registro = container.querySelector("#decisions-tab-registro") as HTMLButtonElement;
    await act(async () => {
      registro.dispatchEvent(new KeyboardEvent("keydown", { key: "ArrowRight", bubbles: true }));
    });
    expect(navigation.replace).toHaveBeenLastCalledWith("/decisions?tab=consejo", { scroll: false });
  });

  it("parses only safe tab and proposal values", () => {
    expect(resolveDecisionsTab(null)).toBe("consejo");
    expect(resolveDecisionsTab("otro")).toBe("consejo");
    expect(resolveDecisionsTab("registro")).toBe("registro");
    expect(resolveFocusedProposal("41")).toBe(41);
    expect(resolveFocusedProposal("0")).toBeNull();
    expect(resolveFocusedProposal("-3")).toBeNull();
    expect(resolveFocusedProposal("4e2")).toBeNull();
    expect(resolveFocusedProposal("99999999999999999999")).toBeNull();
  });
});
