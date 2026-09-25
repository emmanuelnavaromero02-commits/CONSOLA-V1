import { renderToStaticMarkup } from "react-dom/server";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { describe, expect, it, vi } from "vitest";

import type { ConnectorSchema } from "@/lib/cartridges";
import { CredentialsForm } from "./CredentialsForm";

type MockVaultConnection = { conn_id: string; auth_method?: string };

const vaultConnectionsMock = vi.hoisted(() =>
  vi.fn((): { data: { connections: MockVaultConnection[] } } => ({ data: { connections: [] } })),
);

vi.mock("@/lib/hooks/useCartridges", () => ({
  useSaveCredentials: () => ({ isPending: false, mutateAsync: vi.fn() }),
  useTestConnection: () => ({ isPending: false, mutateAsync: vi.fn() }),
  useDeleteCredentials: () => ({ isPending: false, mutateAsync: vi.fn() }),
}));

vi.mock("@/lib/operations/hooks", () => ({
  useVaultConnections: () => vaultConnectionsMock(),
}));

vi.mock("sonner", () => ({
  toast: { success: vi.fn(), error: vi.fn(), info: vi.fn() },
}));

function renderCredentials(cartridgeId: string, schema: ConnectorSchema): string {
  const queryClient = new QueryClient();
  return renderToStaticMarkup(
    <QueryClientProvider client={queryClient}>
      <CredentialsForm cartridgeId={cartridgeId} schema={schema} />
    </QueryClientProvider>,
  );
}

describe("CredentialsForm", () => {
  it("routes credential writes to the scoped Vault operations page", () => {
    const schema: ConnectorSchema = {
      fields: [
        { name: "base_url", type: "url", label: "Base URL", required: true },
        { name: "token", type: "password", label: "API token", required: true },
        { name: "region", type: "select", label: "Region", options: [{ value: "eu", label: "EU" }] },
        { name: "enabled", type: "boolean", label: "Enabled", default: true },
      ],
    };

    const markup = renderCredentials("hubspot", schema);

    expect(markup).toContain("Configurar en Vault");
    expect(markup).toContain("/operations/vault");
    expect(markup).toContain("Las credenciales se administran en Vault");
    expect(markup).not.toContain("Guardar credenciales");
    expect(markup).not.toContain('type="password"');
    expect(markup).not.toContain("Base URL");
    expect(markup).not.toContain("API token");
    expect(markup).not.toContain("Campos esperados");
    expect(markup).toContain("Probar conexión");
    expect(markup).toContain("Sincronizar ahora");
  });

  it("renders an explicit empty state when a cartridge has no connector schema", () => {
    const markup = renderCredentials("internal", { fields: [] });

    expect(markup).toContain("Este cartucho se valida con las conexiones disponibles en Vault.");
    expect(markup).toContain("Configurar en Vault");
    expect(markup).not.toContain("Guardar credenciales");
  });

  it("renders saved Vault connections as test-connection choices", () => {
    vaultConnectionsMock.mockReturnValueOnce({
      data: {
        connections: [
          { conn_id: "tenant_sf", auth_method: "saml_bearer_assertion" },
          { conn_id: "default", auth_method: "oauth2_client_credentials" },
        ],
      },
    });

    const markup = renderCredentials("sap_successfactors", { fields: [] });

    expect(markup).toContain("Conexión a probar");
    expect(markup).toContain('value="tenant_sf"');
    expect(markup).toContain('value="default"');
  });
});
