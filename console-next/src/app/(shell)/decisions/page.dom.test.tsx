// @vitest-environment jsdom

import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import DecisionsRedirectPage from "./page";
import { decisionsRedirectTarget } from "./redirect-target";

const navigation = vi.hoisted(() => ({
  params: new URLSearchParams(),
}));

vi.mock("next/navigation", () => ({
  useSearchParams: () => navigation.params,
}));

vi.mock("next/link", () => ({
  default: ({ href, children, ...props }: { href: string; children: React.ReactNode }) => (
    <a href={href} {...props}>{children}</a>
  ),
}));

(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

let container: HTMLDivElement;
let root: Root;
const replaceSpy = vi.fn();

async function render(query: string) {
  navigation.params = new URLSearchParams(query);
  await act(async () => {
    root.render(<DecisionsRedirectPage />);
  });
}

beforeEach(() => {
  replaceSpy.mockClear();
  Object.defineProperty(window, "location", {
    configurable: true,
    value: { ...window.location, replace: replaceSpy },
  });
  container = document.createElement("div");
  document.body.append(container);
  root = createRoot(container);
});

afterEach(async () => {
  await act(async () => root.unmount());
  container.remove();
});

describe("/decisions redirect shell", () => {
  it("maps the council tab (and the default) to the Ejecuta phase", () => {
    expect(decisionsRedirectTarget(new URLSearchParams(""))).toBe("/control-room?fase=ejecuta");
    expect(decisionsRedirectTarget(new URLSearchParams("tab=consejo"))).toBe(
      "/control-room?fase=ejecuta",
    );
    expect(decisionsRedirectTarget(new URLSearchParams("tab=otro"))).toBe(
      "/control-room?fase=ejecuta",
    );
  });

  it("maps the register tab to the Supervisa phase", () => {
    expect(decisionsRedirectTarget(new URLSearchParams("tab=registro"))).toBe(
      "/control-room?fase=supervisa",
    );
  });

  it("preserves only safe proposal deep links", () => {
    expect(decisionsRedirectTarget(new URLSearchParams("tab=consejo&propuesta=41"))).toBe(
      "/control-room?fase=ejecuta&propuesta=41",
    );
    expect(decisionsRedirectTarget(new URLSearchParams("propuesta=0"))).toBe(
      "/control-room?fase=ejecuta",
    );
    expect(decisionsRedirectTarget(new URLSearchParams("propuesta=4e2"))).toBe(
      "/control-room?fase=ejecuta",
    );
    expect(decisionsRedirectTarget(new URLSearchParams("tab=registro&propuesta=41"))).toBe(
      "/control-room?fase=supervisa",
    );
  });

  it("replaces the location and renders the fallback link for the resolved target", async () => {
    await render("tab=consejo&propuesta=41");

    expect(replaceSpy).toHaveBeenCalledWith("/control-room?fase=ejecuta&propuesta=41");
    const anchor = container.querySelector("a");
    expect(anchor?.getAttribute("href")).toBe("/control-room?fase=ejecuta&propuesta=41");
    expect(container.textContent).toContain("Redirigiendo");
    expect(container.textContent).toContain("Abrir Control Room");
  });
});
