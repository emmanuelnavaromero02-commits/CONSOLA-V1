// @vitest-environment jsdom

import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { Inbox } from "lucide-react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { EmptyState } from "./EmptyState";

vi.mock("next/link", () => ({
  default: ({ href, children, ...rest }: { href: string; children: React.ReactNode }) => (
    <a href={href} {...rest}>
      {children}
    </a>
  ),
}));

(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

let container: HTMLDivElement;
let root: Root;

beforeEach(() => {
  container = document.createElement("div");
  document.body.append(container);
  root = createRoot(container);
});

afterEach(async () => {
  await act(async () => root.unmount());
  container.remove();
});

describe("EmptyState", () => {
  it("renders the title, description, reason and a decorative illustration", async () => {
    await act(async () => {
      root.render(
        <EmptyState
          icon={Inbox}
          title="No hay propuestas pendientes"
          description="Aparecerán aquí cuando existan."
          why="El sistema no detectó hallazgos accionables."
        />,
      );
    });
    const state = container.querySelector('[data-testid="empty-state"]');
    expect(state?.textContent).toContain("No hay propuestas pendientes");
    expect(state?.textContent).toContain("Aparecerán aquí cuando existan.");
    expect(state?.textContent).toContain("El sistema no detectó hallazgos accionables.");
    const illustration = state?.querySelector('[aria-hidden="true"]');
    expect(illustration?.querySelector("svg radialGradient")).not.toBeNull();
    const gradient = illustration?.querySelector("radialGradient")?.getAttribute("id") ?? "";
    expect(gradient).toMatch(/^empty-[A-Za-z0-9_-]+$/);
    expect(illustration?.querySelector("circle")?.getAttribute("fill")).toBe(`url(#${gradient})`);
    expect(state?.getAttribute("role")).toBeNull();
    expect(container.querySelector("[style]")).toBeNull();
  });

  it("renders link and button actions without extra controls when none are given", async () => {
    const onClick = vi.fn();
    await act(async () => {
      root.render(
        <EmptyState
          icon={Inbox}
          title="Vacío"
          primaryAction={{ label: "Ir al Control Room", href: "/control-room" }}
          secondaryAction={{ label: "Reintentar", onClick }}
        />,
      );
    });
    const link = container.querySelector("a");
    expect(link?.getAttribute("href")).toBe("/control-room");
    expect(link?.textContent).toBe("Ir al Control Room");
    const button = container.querySelector("button");
    expect(button?.textContent).toBe("Reintentar");
    await act(async () => button?.click());
    expect(onClick).toHaveBeenCalledTimes(1);

    await act(async () => {
      root.render(<EmptyState icon={Inbox} title="Solo título" size="sm" />);
    });
    expect(container.querySelectorAll("a, button")).toHaveLength(0);
    expect(container.textContent).toBe("Solo título");
  });
});
