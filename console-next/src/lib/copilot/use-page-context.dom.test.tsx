// @vitest-environment jsdom

import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it } from "vitest";

import { clearPageContext, readPageContext } from "./page-context";
import { usePageContextPublisher } from "./use-page-context";

function Publisher({ tab }: { tab: string }) {
  usePageContextPublisher({ surface: "decisions", active_tab: tab });
  return <span>ok</span>;
}

function Silent() {
  usePageContextPublisher(null);
  return <span>ok</span>;
}

let container: HTMLDivElement;
let root: Root;

beforeEach(() => {
  (globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;
  window.sessionStorage.clear();
  clearPageContext();
  window.history.replaceState(null, "", "/decisions");
  container = document.createElement("div");
  document.body.append(container);
  root = createRoot(container);
});

afterEach(() => {
  container.remove();
});

describe("usePageContextPublisher", () => {
  it("publishes on mount and updates when the context changes", async () => {
    await act(async () => {
      root.render(<Publisher tab="consejo" />);
    });
    expect(readPageContext()).toEqual({ surface: "decisions", active_tab: "consejo" });

    await act(async () => {
      root.render(<Publisher tab="tablero" />);
    });
    expect(readPageContext()).toEqual({ surface: "decisions", active_tab: "tablero" });
  });

  it("clears on unmount", async () => {
    await act(async () => {
      root.render(<Publisher tab="consejo" />);
    });
    await act(async () => root.unmount());
    expect(readPageContext()).toEqual({});
  });

  it("publishes nothing for a null context", async () => {
    await act(async () => {
      root.render(<Silent />);
    });
    expect(readPageContext()).toEqual({});
    await act(async () => root.unmount());
  });
});
