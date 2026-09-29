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

import SupervisedActionsRedirectPage from "./page";

describe("/supervised-actions redirect shell", () => {
  it("points at the Supervisa phase of the Control Room", () => {
    const markup = renderToStaticMarkup(<SupervisedActionsRedirectPage />);

    expect(markup).toContain('href="/control-room?fase=supervisa"');
    expect(markup).toContain("Redirigiendo");
    expect(markup).toContain("Abrir Control Room");
    expect(markup).toContain("<h1");
  });

  it("keeps the shell free of queue mutations and approve/execute paths", () => {
    const source = readFileSync(join(__dirname, "page.tsx"), "utf8");

    expect(source).toContain('window.location.replace(TARGET)');
    expect(source).toContain('const TARGET = "/control-room?fase=supervisa"');
    expect(source).not.toMatch(/\/approve/);
    expect(source).not.toMatch(/\/execute/);
    expect(source).not.toContain("/api/actions");
  });
});
