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
      ui_capabilities: {
        can_view_control_room: true,
        can_view_monitor: true,
      },
    });

    expect(markup).toContain("Control Room");
    expect(markup).toContain("Catálogo técnico");
    expect(markup).not.toContain("Jobs y extracción");
    expect(markup).not.toContain("Inteligencia Operativa");
    expect(markup).not.toContain("Acciones Supervisadas");
    expect(markup).not.toContain("Usuarios");
    expect(markup).not.toContain("Empresas");
    expect(markup).not.toContain("Vault");
    expect(markup).not.toContain("Ajustes");
  });

  it("shows operational intelligence and supervised actions only with their permissions", () => {
    const permitted = render({
      role: { is_platform_admin: false },
      permissions: ["datasets.read", "control_room.write"],
      ui_capabilities: {},
    }, "/operational-intelligence");

    expect(permitted).toContain("Inteligencia Operativa");
    expect(permitted).toContain('href="/operational-intelligence"');
    expect(permitted).toContain("Acciones Supervisadas");
    expect(permitted).toContain('href="/supervised-actions"');
    expect(permitted).toContain('aria-current="page"');

    const blocked = render({
      role: { is_platform_admin: false },
      permissions: ["workspace.access"],
      ui_capabilities: {},
    }, "/operational-intelligence");

    expect(blocked).not.toContain("Inteligencia Operativa");
    expect(blocked).not.toContain("Acciones Supervisadas");
  });

  it("marks the active nav item and keeps nested data routes active", () => {
    const markup = render({
      role: { is_platform_admin: false },
      permissions: ["datasets.read"],
      ui_capabilities: { can_view_lineage: true },
    }, "/data/lineage");

    expect(markup).toContain('aria-current="page"');
    expect(markup).toContain('href="/data"');
    expect(markup).toContain("Catálogo técnico");
  });

  it("renders one admin entry when role and permission allow admin surfaces", () => {
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
      ui_capabilities: {
        can_manage_companies: true,
        can_manage_workspace_users: true,
        can_view_knowledge: true,
        can_view_vault: true,
        can_view_settings: true,
        can_view_cartridges: true,
      },
    }, "/operations/users");

    expect(markup).toContain("Centro de administración");
    expect(markup).toContain("Cartuchos");
    expect(markup).not.toContain("Empresas");
    expect(markup).not.toContain("Usuarios");
    expect(markup).not.toContain("Vault");
    expect(markup).not.toContain("Seguridad y sesiones");
    expect(markup).not.toContain("Ajustes");
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
        "security.audit.read",
        "vault.connections.read",
        "operations.read",
        "apps.read",
        "marketplace.read",
        "monitor.read",
      ],
      ui_capabilities: {
        can_manage_workspace_users: true,
        can_view_control_room: true,
        can_view_copilot: true,
        can_view_tokens: true,
        can_view_audit: true,
        can_view_vault: true,
        can_view_metrics: true,
      },
    }, "/operations/users");

    expect(markup).toContain("Centro de administración");
    expect(markup).not.toContain("Usuarios");
    expect(markup).not.toContain("Empresas");
    expect(markup).toContain("Control Room");
    expect(markup).toContain("Catálogo técnico");
    expect(markup).toContain("Copiloto");
    expect(markup).toContain("Tokens");
    expect(markup).not.toContain("Auditoría");
    expect(markup).not.toContain("Vault");
    expect(markup).not.toContain("Métricas");
    expect(markup).not.toContain("Conocimiento");
    expect(markup).not.toContain("Consulta Bronce");
    expect(markup).not.toContain("Studio");
    expect(markup).not.toContain("Seguridad");
    expect(markup).not.toContain("Ajustes");
  });

  it("shows Studio as a direct navigation item when backend capability allows it", () => {
    const markup = render({
      role: { global: "super_admin", is_platform_admin: true },
      permissions: ["studio.read"],
      ui_capabilities: {
        can_view_studio: true,
      },
    }, "/studio");

    expect(markup).toContain("Studio");
    expect(markup).toContain('href="/studio"');
    expect(markup).toContain('aria-current="page"');
  });

  it("does not show backend-guarded links when permissions exist but ui capabilities deny them", () => {
    const markup = render({
      role: { is_platform_admin: true },
      permissions: [
        "datasets.write",
        "studio.read",
        "operations.read",
        "vault.connections.read",
        "security.audit.read",
        "iam.users.read",
      ],
      ui_capabilities: {},
    });

    expect(markup).not.toContain("Consulta Bronce");
    expect(markup).not.toContain("Studio");
    expect(markup).not.toContain("Automatizaciones");
    expect(markup).not.toContain("Vault");
    expect(markup).not.toContain("Auditoría");
    expect(markup).not.toContain("Usuarios");
  });

  it("renders a workspace selector when the user has an active workspace", () => {
    const markup = render({
      role: { is_platform_admin: false },
      permissions: ["workspace.access"],
      workspaces: [
        {
          tenant_name: "Cliente A",
          workspace_name: "Finanzas",
          workspace_id: "ws-a",
          active: true,
        },
        {
          tenant_name: "Cliente B",
          workspace_name: "Operaciones",
          workspace_id: "ws-b",
        },
      ],
      ui_capabilities: { can_view_workspace: true },
    });

    expect(markup).toContain("Workspace activo");
    expect(markup).toContain("Cliente A / Finanzas");
    expect(markup).toContain('label="Cliente B"');
    expect(markup).toContain("Operaciones");

    const single = render({
      role: { is_platform_admin: false },
      permissions: ["workspace.access"],
      workspaces: [{ workspace_name: "Solo", workspace_id: "ws-a", active: true }],
      ui_capabilities: { can_view_workspace: true },
    });
    expect(single).toContain("Workspace activo");
    expect(single).toContain("Solo");
  });
});
