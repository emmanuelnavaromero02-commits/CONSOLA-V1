import { afterEach, describe, expect, it, vi } from "vitest";

import { api } from "@/lib/api";
import {
  activateCartridge,
  deleteCredentials,
  getConnectorSchema,
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
