import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it, vi } from "vitest";

import type { ConnectorSchema } from "@/lib/cartridges";
import { CredentialsForm } from "./CredentialsForm";

vi.mock("@/lib/hooks/useCartridges", () => ({
  useSaveCredentials: () => ({ isPending: false, mutateAsync: vi.fn() }),
  useTestConnection: () => ({ isPending: false, mutateAsync: vi.fn() }),
  useDeleteCredentials: () => ({ isPending: false, mutateAsync: vi.fn() }),
}));

vi.mock("sonner", () => ({
  toast: { success: vi.fn(), error: vi.fn(), info: vi.fn() },
}));

describe("CredentialsForm", () => {
  it("renders required dynamic fields, secret toggles and action buttons", () => {
    const schema: ConnectorSchema = {
      fields: [
        { name: "base_url", type: "url", label: "Base URL", required: true },
        { name: "token", type: "password", label: "API token", required: true },
        { name: "region", type: "select", label: "Region", options: [{ value: "eu", label: "EU" }] },
        { name: "enabled", type: "boolean", label: "Enabled", default: true },
      ],
    };

    const markup = renderToStaticMarkup(<CredentialsForm cartridgeId="hubspot" schema={schema} />);

    expect(markup).toContain("Base URL");
    expect(markup).toContain("API token");
    expect(markup).toContain("Region");
    expect(markup).toContain("Enabled");
    expect(markup).toContain('type="password"');
    expect(markup).toContain('aria-label="Mostrar contraseña"');
    expect(markup).toContain("Guardar credenciales");
    expect(markup).toContain("Probar conexión");
    expect(markup).toContain("Borrar credenciales");
  });

  it("renders an explicit empty state when a cartridge has no connector schema", () => {
    const markup = renderToStaticMarkup(
      <CredentialsForm cartridgeId="internal" schema={{ fields: [] }} />,
    );

    expect(markup).toContain("Este cartucho no expone un schema de configuración.");
    expect(markup).toContain("Guardar credenciales");
  });
});
