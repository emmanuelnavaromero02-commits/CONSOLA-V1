import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";

import { AppSidebar } from "./AppSidebar";
import type { MeAccessResponse } from "@/lib/admin-surfaces";

function render(access?: MeAccessResponse, pathname = "/control-room") {
  return renderToStaticMarkup(
    <AppSidebar pathname={pathname} access={access} />,
  );
}

describe("AppSidebar", () => {
  it("shows only permitted non-admin operational entries for a workspace user", () => {
    const markup = render({
      role: { is_platform_admin: false },
      permissions: ["workspace.access", "monitor.read"],
      ui_capabilities: {},
    });

    expect(markup).toContain("Control Room");
    expect(markup).toContain("Monitor");
    expect(markup).not.toContain("Usuarios");
    expect(markup).not.toContain("Vault");
    expect(markup).not.toContain("Ajustes");
  });

  it("marks the active nav item and keeps nested data routes active", () => {
    const markup = render({
      role: { is_platform_admin: false },
      permissions: ["datasets.read"],
    }, "/data/lineage");

    expect(markup).toContain('aria-current="page"');
    expect(markup).toContain('href="/data/lineage"');
    expect(markup).toContain("Linaje");
  });

  it("renders admin surfaces only when role and permission both allow them", () => {
    const markup = render({
      role: { is_platform_admin: true },
      permissions: [
        "security.audit.read",
        "iam.users.read",
        "settings.read",
        "vault.connections.read",
        "cartridges.read",
      ],
    }, "/operations/users");

    expect(markup).toContain("Usuarios");
    expect(markup).toContain("Vault");
    expect(markup).toContain("Ajustes");
    expect(markup).toContain("Cartuchos");
    expect(markup).toContain('aria-current="page"');
  });
});
