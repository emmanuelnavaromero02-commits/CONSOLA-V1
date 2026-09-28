import type { ComponentType, ReactNode } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it, vi } from "vitest";

vi.mock("next/link", () => ({
  default: ({ href, children, ...props }: { href: string; children: ReactNode }) => (
    <a href={href} {...props}>{children}</a>
  ),
}));

import CartridgesRedirectPage from "./page";
import CustomerCartridgesRedirectPage from "../customer/cartridges/page";
import AdminInstallationsRedirectPage from "../admin/installations/page";
import AdminLicensesRedirectPage from "../admin/licenses/page";

const CASES: Array<{ name: string; Page: ComponentType; target: string }> = [
  { name: "/cartridges", Page: CartridgesRedirectPage, target: "/marketplace?tab=conectadas" },
  { name: "/customer/cartridges", Page: CustomerCartridgesRedirectPage, target: "/marketplace?tab=conectadas" },
  { name: "/admin/installations", Page: AdminInstallationsRedirectPage, target: "/marketplace?tab=licencias" },
  { name: "/admin/licenses", Page: AdminLicensesRedirectPage, target: "/marketplace?tab=licencias" },
];

describe("legacy data-source routes render redirect shells", () => {
  for (const { name, Page, target } of CASES) {
    it(`${name} points at ${target}`, () => {
      const markup = renderToStaticMarkup(<Page />);
      expect(markup).toContain(`href="${target}"`);
      expect(markup).toContain("Redirigiendo");
      expect(markup).toContain("<h1");
    });
  }

  it("keeps the fallback copy in Spanish with an explicit action", () => {
    const markup = renderToStaticMarkup(<CartridgesRedirectPage />);
    expect(markup).toContain("Fuentes de datos");
    expect(markup).toContain("Abrir Fuentes de datos");
  });
});
