import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it, vi } from "vitest";

import {
  buildVaultConnectionPayload,
  ConnectionForm,
  getAuthMethodOptions,
  VaultConnectionsTable,
  type ConnForm,
} from "./VaultConnectionsTable";

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

vi.mock("@/lib/hooks/useCartridges", () => ({
  useCartridgeList: () => ({
    data: { cartridges: ["sap_successfactors", "replicon"] },
    isLoading: false,
    isError: false,
  }),
  useConnectorSchema: () => ({
    data: { authMethodValues: ["oauth2_client_credentials", "saml_bearer_assertion"] },
    isError: false,
    isFetching: false,
  }),
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
    expect(markup).toContain("Revelar secreto");
  });

  it("uses SuccessFactors connector auth methods and renders SAML PEM fields", () => {
    const samlForm: ConnForm = {
      connId: "default",
      baseUrl: "https://api.successfactors.example/odata/v2",
      authMethod: "saml_bearer_assertion",
      token: "",
      clientId: "sf-client",
      clientSecret: "",
      tokenUrl: "https://api.successfactors.example/oauth/token",
      companyId: "FEMSA",
      adminUser: "admin@example.com",
      privateKeyPem: "",
      idpUrl: "",
      extraJson: "",
    };

    const markup = renderToStaticMarkup(
      <ConnectionForm
        cartridge="sap_successfactors"
        authMethodValues={["oauth2_client_credentials", "saml_bearer_assertion"]}
        form={samlForm}
        editingId={null}
        setForm={vi.fn()}
        onSave={vi.fn()}
        onCancel={vi.fn()}
        saving={false}
      />,
    );

    expect(getAuthMethodOptions("sap_successfactors", ["oauth2_client_credentials", "saml_bearer_assertion"])).toEqual([
      "oauth2_client_credentials",
      "saml_bearer_assertion",
    ]);
    expect(getAuthMethodOptions("replicon")).toEqual([
      "bearer_token",
      "api_key",
      "basic",
      "none",
    ]);
    expect(markup).toContain("saml_bearer_assertion");
    expect(markup).toContain("Private key PEM");
    expect(markup).toContain("textarea");
    expect(markup).toContain("Admin user");
    expect(markup).toContain("Vacío = derivado de token_url");
  });

  it("persists SAML bearer fields in the vault connection payload", () => {
    const payload = buildVaultConnectionPayload({
      connId: "default",
      baseUrl: "https://api.successfactors.example/odata/v2",
      authMethod: "saml_bearer_assertion",
      token: "",
      clientId: "sf-client",
      clientSecret: "ignored-for-saml",
      tokenUrl: "https://api.successfactors.example/oauth/token",
      companyId: "FEMSA",
      adminUser: "admin@example.com",
      privateKeyPem: "-----BEGIN PRIVATE KEY-----\nunit-test\n-----END PRIVATE KEY-----",
      idpUrl: "https://api.successfactors.example/oauth/idp",
      extraJson: '{"label":"Femsa SF"}',
    });

    expect(payload).toMatchObject({
      label: "Femsa SF",
      base_url: "https://api.successfactors.example/odata/v2",
      auth_method: "saml_bearer_assertion",
      client_id: "sf-client",
      token_url: "https://api.successfactors.example/oauth/token",
      company_id: "FEMSA",
      admin_user: "admin@example.com",
      private_key_pem: "-----BEGIN PRIVATE KEY-----\nunit-test\n-----END PRIVATE KEY-----",
      idp_url: "https://api.successfactors.example/oauth/idp",
    });
    expect(payload).not.toHaveProperty("client_secret");
  });
});
