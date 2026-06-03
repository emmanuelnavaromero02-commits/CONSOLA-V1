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
        "mcp.registry.read",
        "settings.read",
        "vault.connections.read",
        "cartridges.read",
      ],
      ui_capabilities: { can_manage_workspace_users: true },
    }, "/operations/users");

    expect(markup).toContain("Usuarios");
    expect(markup).toContain("Conocimiento");
    expect(markup).toContain("Vault");
    expect(markup).toContain("Ajustes");
    expect(markup).toContain("Cartuchos");
    expect(markup).toContain('aria-current="page"');
  });

  it("shows tenant user administration without internal data-builder surfaces", () => {
    const markup = render({
      role: { global: "user", is_platform_admin: false },
      workspace: { workspace_role: "tenant_admin" },
      permissions: [
        "iam.users.read",
        "iam.users.write",
        "datasets.read",
        "workspace.access",
        "copilot.use",
        "apps.read",
        "marketplace.read",
        "monitor.read",
      ],
      ui_capabilities: { can_manage_workspace_users: true },
    }, "/operations/users");

    expect(markup).toContain("Usuarios");
    expect(markup).toContain("Control Room");
    expect(markup).toContain("Copiloto");
    expect(markup).not.toContain("Conocimiento");
    expect(markup).not.toContain("Consulta Bronce");
    expect(markup).not.toContain("Studio");
    expect(markup).not.toContain("Seguridad");
    expect(markup).not.toContain("Vault");
  });
});
