import { readFileSync } from "node:fs";
import { join } from "node:path";

import type { ReactNode } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it, vi } from "vitest";

vi.mock("next/link", () => ({
  default: ({ href, children, ...props }: { href: string; children: ReactNode }) => (
    <a href={href} {...props}>{children}</a>
  ),
}));

import OperationalIntelligenceRedirectPage from "./page";

describe("/operational-intelligence redirect shell", () => {
  it("points at the Decide phase of the Control Room", () => {
    const markup = renderToStaticMarkup(<OperationalIntelligenceRedirectPage />);

    expect(markup).toContain('href="/control-room?fase=decide"');
    expect(markup).toContain("Redirigiendo");
    expect(markup).toContain("Abrir Control Room");
    expect(markup).toContain("<h1");
  });

  it("replaces the location transparently in the shell source", () => {
    const source = readFileSync(join(__dirname, "page.tsx"), "utf8");

    expect(source).toContain('window.location.replace(TARGET)');
    expect(source).toContain('const TARGET = "/control-room?fase=decide"');
  });
});
