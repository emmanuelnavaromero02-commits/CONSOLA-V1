import { afterEach, describe, expect, it, vi } from "vitest";

import { api } from "@/lib/api";
import {
  activateCartridge,
  deleteCredentials,
  getConnectorSchema,
  KNOWN_CARTRIDGES,
  listCartridges,
  saveCredentials,
  testConnection,
} from "./cartridges";

vi.mock("@/lib/api", () => ({
  api: {
    get: vi.fn(),
    post: vi.fn(),
    delete: vi.fn(),
  },
}));

const apiMock = vi.mocked(api);

afterEach(() => {
  vi.restoreAllMocks();
});

describe("cartridge client", () => {
  it("lists cartridge ids from the backend contract", async () => {
    apiMock.get.mockResolvedValueOnce({ data: { cartridges: ["hubspot"] }, status: 200, headers: new Headers(), requestId: "r" });

    await expect(listCartridges()).resolves.toEqual({ cartridges: ["hubspot"] });
    expect(apiMock.get).toHaveBeenCalledWith("/api/cartridges");
  });

  it("normalizes canonical connector_schema fields", async () => {
    apiMock.get.mockResolvedValueOnce({
      data: {
        name: "HubSpot",
        description: "CRM",
        fields: [
          { name: "token", type: "password", label: "Token", required: true, min_length: 8 },
          { name: "region", type: "select", options: [{ value: "eu" }, { value: "us", label: "US" }] },
        ],
      },
      status: 200,
      headers: new Headers(),
      requestId: "r",
    });

    const schema = await getConnectorSchema("hub spot");

    expect(apiMock.get).toHaveBeenCalledWith("/api/cartridges/hub%20spot/connector_schema");
    expect(schema.name).toBe("HubSpot");
    expect(schema.fields).toEqual([
      expect.objectContaining({ name: "token", type: "password", required: true, min_length: 8 }),
      expect.objectContaining({ name: "region", type: "select", options: [{ value: "eu", label: "eu" }, { value: "us", label: "US" }] }),
    ]);
  });

  it("adapts legacy connector metadata into editable fields", async () => {
    apiMock.get.mockResolvedValueOnce({
      data: {
        connector: {
          name: "Replicon",
          api: { base_url_env: "REPLICON_BASE_URL" },
          auth: {
            type: "bearer_token",
            env_var: "REPLICON_TOKEN",
            auth_method_values: ["bearer_token", "oauth2_client_credentials"],
          },
        },
      },
      status: 200,
      headers: new Headers(),
      requestId: "r",
    });

    await expect(getConnectorSchema("replicon")).resolves.toMatchObject({
      name: "Replicon",
      fields: [
        { name: "base_url", type: "url", label: "Base URL", description: "REPLICON_BASE_URL", required: true },
        { name: "token", type: "password", label: "Bearer token", description: "REPLICON_TOKEN", required: true },
      ],
      authMethodValues: ["bearer_token", "oauth2_client_credentials"],
    });
  });

  it("adapts Banxico Bmx-Token metadata into a Vault token field", async () => {
    apiMock.get.mockResolvedValueOnce({
      data: {
        connector: {
          name: "Banco de Mexico SIE",
          auth: {
            type: "bmx_token",
            env_var: "BANXICO_API_TOKEN",
            header: "Bmx-Token",
          },
        },
      },
      status: 200,
      headers: new Headers(),
      requestId: "r",
    });

    await expect(getConnectorSchema("banxico")).resolves.toMatchObject({
      name: "Banco de Mexico SIE",
      fields: [
        { name: "token", type: "password", label: "Bmx-Token", description: "BANXICO_API_TOKEN", required: true },
      ],
    });
  });

  it("includes Banxico as a known cartridge id", () => {
    expect(KNOWN_CARTRIDGES).toContain("banxico");
  });

  it("adapts INEGI token metadata into a Vault token field", async () => {
    apiMock.get.mockResolvedValueOnce({
      data: {
        connector: {
          name: "INEGI Banco de Indicadores",
          auth: {
            type: "bearer_token",
            env_var: "INEGI_API_TOKEN",
            header: "token-path-segment",
          },
        },
      },
      status: 200,
      headers: new Headers(),
      requestId: "r",
    });

    await expect(getConnectorSchema("inegi")).resolves.toMatchObject({
      name: "INEGI Banco de Indicadores",
      fields: [
        { name: "token", type: "password", label: "Bearer token", description: "INEGI_API_TOKEN", required: true },
      ],
    });
  });

  it("includes INEGI as a known cartridge id", () => {
    expect(KNOWN_CARTRIDGES).toContain("inegi");
  });


  it("adapts SuccessFactors OAuth/SAML metadata into Vault fields", async () => {
    apiMock.get.mockResolvedValueOnce({
      data: {
        connector: {
          name: "SAP SuccessFactors HXM",
          api: { base_url_env: "SF_BASE_URL" },
          auth: {
            type: "oauth2_client_credentials",
            auth_method_env: "SF_AUTH_METHOD",
            auth_method_values: ["oauth2_client_credentials", "saml_bearer_assertion"],
            token_url_env: "SF_TOKEN_URL",
            idp_url_env: "SF_IDP_URL",
            client_id_env: "SF_CLIENT_ID",
            client_secret_env: "SF_CLIENT_SECRET",
            company_id_env: "SF_COMPANY_ID",
            private_key_path_env: "SF_PRIVATE_KEY_PATH",
            admin_user_env: "SF_ADMIN_USER",
          },
        },
      },
      status: 200,
      headers: new Headers(),
      requestId: "r",
    });

    const schema = await getConnectorSchema("sap_successfactors");
    const fieldsByName = Object.fromEntries(schema.fields.map((field) => [field.name, field]));

    expect(schema.authMethodValues).toEqual(["oauth2_client_credentials", "saml_bearer_assertion"]);
    expect(Object.keys(fieldsByName)).toEqual([
      "base_url",
      "auth_method",
      "client_id",
      "token_url",
      "company_id",
      "client_secret",
      "admin_user",
      "idp_url",
      "private_key_pem",
    ]);
    expect(fieldsByName.auth_method).toMatchObject({
      type: "select",
      label: "Método de autenticación",
      description: "SF_AUTH_METHOD",
      required: true,
      options: [
        { value: "oauth2_client_credentials", label: "oauth2_client_credentials" },
        { value: "saml_bearer_assertion", label: "saml_bearer_assertion" },
      ],
    });
    expect(fieldsByName.client_secret).toMatchObject({ type: "password", required: false });
    expect(fieldsByName.private_key_pem).toMatchObject({
      type: "password",
      description: "SF_PRIVATE_KEY_PEM / SF_PRIVATE_KEY_PATH",
      required: true,
    });
  });

  it("uses encoded mutation endpoints for credentials and activation", async () => {
    apiMock.post.mockResolvedValue({ data: { ok: true }, status: 200, headers: new Headers(), requestId: "r" });
    apiMock.delete.mockResolvedValueOnce({ data: { ok: true }, status: 200, headers: new Headers(), requestId: "r" });

    await saveCredentials("sap hcm", { token: "secret" });
    await testConnection("sap hcm");
    await testConnection("sap hcm", "femsa_sf");
    await deleteCredentials("sap hcm");
    await activateCartridge("sap hcm");

    expect(apiMock.post).toHaveBeenNthCalledWith(1, "/api/cartridges/sap%20hcm/credentials", { token: "secret" });
    expect(apiMock.post).toHaveBeenNthCalledWith(2, "/api/cartridges/sap%20hcm/test_connection");
    expect(apiMock.post).toHaveBeenNthCalledWith(3, "/api/cartridges/sap%20hcm/test_connection?conn_id=femsa_sf");
    expect(apiMock.delete).toHaveBeenCalledWith("/api/cartridges/sap%20hcm/credentials");
    expect(apiMock.post).toHaveBeenNthCalledWith(4, "/api/marketplace/products/sap%20hcm/activate", {});
  });
});
