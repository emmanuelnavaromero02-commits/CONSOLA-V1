import type { ReactNode } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type { BriefingHighlight } from "@/lib/copilot/types";

import { BriefingCard } from "./BriefingCard";

vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: vi.fn() }),
}));

vi.mock("next/link", () => ({
  default: ({ href, children, ...rest }: { href: string; children: ReactNode }) => (
    <a href={href} data-next-link="true" {...rest}>
      {children}
    </a>
  ),
}));

function render(actionHref: string | null): string {
  const highlight: BriefingHighlight = {
    id: "briefing-1",
    severity: "warning",
    title: "Aviso",
    body: "Detalle",
    category: "ops",
    cartridge: null,
    action_label: "Revisar",
    action_href: actionHref,
  };
  return renderToStaticMarkup(<BriefingCard highlight={highlight} onDismiss={() => undefined} />);
}

describe("BriefingCard action links", () => {
  beforeEach(() => {
    vi.spyOn(console, "warn").mockImplementation(() => undefined);
  });

  afterEach(() => {
    vi.restoreAllMocks();
  });

  it("keeps same-origin internal paths as in-app links", () => {
    const markup = render("/studio?tab=datasets#top");
    expect(markup).toContain('href="/studio?tab=datasets#top"');
    expect(markup).toContain('data-next-link="true"');
    expect(markup).not.toContain("data-action-dropped");
  });

  it("turns workspace prompts into an in-app action", () => {
    const markup = render("/workspace?prompt=hola");
    expect(markup).not.toContain("data-next-link");
    expect(markup).toContain("Revisar");
    expect(markup).not.toContain("data-action-dropped");
  });

  it("turns copilot prompts into an in-app action", () => {
    const markup = render("/copilot?prompt=hola");
    expect(markup).not.toContain("data-next-link");
    expect(markup).toContain("Revisar");
    expect(markup).not.toContain("data-action-dropped");
  });

  it("rewrites legacy /workspace links to /copilot", () => {
    const markup = render("/workspace");
    expect(markup).toContain('href="/copilot"');
    expect(markup).toContain('data-next-link="true"');
  });

  it("opens absolute https links as external links", () => {
    const markup = render("https://docs.example.test/guia");
    expect(markup).toContain('href="https://docs.example.test/guia"');
    expect(markup).toContain('rel="noopener noreferrer"');
  });

  it.each([
    "//evil.example",
    "/.//evil.example",
    "/\\evil.example",
    "/\t/evil.example",
    "javascript:alert(1)",
    "data:text/html,x",
  ])("drops unsafe action href %j", (href) => {
    const markup = render(href);
    expect(markup).toContain('data-action-dropped="true"');
    expect(markup).not.toContain("evil.example");
    expect(markup).not.toContain("Revisar");
  });
});
