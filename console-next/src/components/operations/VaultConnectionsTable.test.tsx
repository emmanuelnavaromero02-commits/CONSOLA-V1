import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it, vi } from "vitest";

import { VaultConnectionsTable } from "./VaultConnectionsTable";

const refetch = vi.fn();
const mutate = vi.fn();
const mutateAsync = vi.fn();

vi.mock("@/lib/operations/hooks", () => ({
  useVaultConnections: () => ({
    data: {
      connections: [
        {
          conn_id: "primary",
          base_url: "https://sap.example",
          auth_method: "bearer_token",
        },
      ],
    },
    isError: false,
    isFetching: false,
    error: null,
    refetch,
  }),
  useVaultSecrets: () => ({
    data: { secrets: [{ key: "anthropic_api_key", masked: "••••••••••" }] },
    isError: false,
    isFetching: false,
    error: null,
    refetch,
  }),
  useRevealVaultConnection: () => ({ mutateAsync }),
  useDeleteVaultConnection: () => ({ mutate, isPending: false }),
  useUpsertVaultConnection: () => ({ mutate, isPending: false }),
  useRevealVaultSecret: () => ({ mutateAsync }),
  useDeleteVaultSecret: () => ({ mutate, isPending: false }),
  useUpsertVaultSecret: () => ({ mutate, isPending: false }),
}));

vi.mock("sonner", () => ({
  toast: { success: vi.fn(), error: vi.fn(), info: vi.fn() },
}));

describe("VaultConnectionsTable", () => {
  it("renders masked connection rows and the connection editor", () => {
    const markup = renderToStaticMarkup(<VaultConnectionsTable />);

    expect(markup).toContain("Conexiones");
    expect(markup).toContain("Secrets");
    expect(markup).toContain("primary");
    expect(markup).toContain("https://sap.example");
    expect(markup).toContain("bearer_token");
    expect(markup).toContain("••••••••••");
    expect(markup).toContain("Nueva conexión");
    expect(markup).toContain("Campos extra JSON");
    expect(markup).toContain("Revelar token");
  });
});
