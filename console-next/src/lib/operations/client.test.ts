import { afterEach, describe, expect, it, vi } from "vitest";

import { api } from "@/lib/api";
import {
  bootstrapTenantAdmin,
  cancelOperationWorkflow,
  createTenant,
  createTenantWorkspace,
  deleteVaultConnection,
  deleteVaultSecret,
  listTenantWorkspaces,
  listTenants,
  listVaultConnections,
  listVaultSecrets,
  triggerOperationWorkflow,
  upsertVaultConnection,
  upsertVaultSecret,
} from "./client";
import type { OperationWorkflow } from "./types";

vi.mock("@/lib/api", () => ({
  api: {
    get: vi.fn(),
    post: vi.fn(),
    put: vi.fn(),
    patch: vi.fn(),
    delete: vi.fn(),
  },
  isApiError: (value: unknown) => (
    typeof value === "object" && value !== null && "status" in value
  ),
}));

const apiMock = vi.mocked(api);

afterEach(() => {
  vi.restoreAllMocks();
  vi.clearAllMocks();
});

describe("operations vault client", () => {
  it("lists vault connections and normalizes missing arrays", async () => {
    apiMock.get.mockResolvedValueOnce({ data: {}, status: 200, headers: new Headers(), requestId: "r" });

    await expect(listVaultConnections("sap hcm")).resolves.toEqual({ connections: [] });
    expect(apiMock.get).toHaveBeenCalledWith("/api/vault/connections/sap%20hcm");
  });

  it("adapts legacy secret key lists into masked secret rows", async () => {
    apiMock.get.mockResolvedValueOnce({ data: { keys: ["api_token", "client_secret"] }, status: 200, headers: new Headers(), requestId: "r" });

    await expect(listVaultSecrets("platform secrets")).resolves.toEqual({
      secrets: [
        { key: "api_token", masked: "••••••••••" },
        { key: "client_secret", masked: "••••••••••" },
      ],
    });
    expect(apiMock.get).toHaveBeenCalledWith("/api/vault/secrets/platform%20secrets");
  });

  it("uses encoded endpoints for vault mutations", async () => {
    apiMock.put.mockResolvedValue({ data: {}, status: 200, headers: new Headers(), requestId: "r" });
    apiMock.delete.mockResolvedValue({ data: undefined, status: 204, headers: new Headers(), requestId: "r" });

    await upsertVaultConnection("sap hcm", "primary conn", {
      base_url: "https://sap.example",
      auth_method: "bearer_token",
      token: "secret",
    });
    await upsertVaultSecret("platform secrets", "anthropic key", { value: "secret" });
    await deleteVaultConnection("sap hcm", "primary conn");
    await deleteVaultSecret("platform secrets", "anthropic key");

    expect(apiMock.put).toHaveBeenNthCalledWith(1, "/api/vault/connections/sap%20hcm/primary%20conn", {
      base_url: "https://sap.example",
      auth_method: "bearer_token",
      token: "secret",
    });
    expect(apiMock.put).toHaveBeenNthCalledWith(2, "/api/vault/secrets/platform%20secrets/anthropic%20key", {
      value: "secret",
    });
    expect(apiMock.delete).toHaveBeenNthCalledWith(1, "/api/vault/connections/sap%20hcm/primary%20conn");
    expect(apiMock.delete).toHaveBeenNthCalledWith(2, "/api/vault/secrets/platform%20secrets/anthropic%20key");
  });
});

describe("operations companies client", () => {
  it("uses the platform tenant onboarding endpoints", async () => {
    apiMock.get
      .mockResolvedValueOnce({ data: { tenants: [] }, status: 200, headers: new Headers(), requestId: "r" })
      .mockResolvedValueOnce({ data: { workspaces: [] }, status: 200, headers: new Headers(), requestId: "r" });
    apiMock.post
      .mockResolvedValueOnce({
        data: { tenant: { id: "tenant 1" }, created: true },
        status: 200,
        headers: new Headers(),
        requestId: "r",
      })
      .mockResolvedValueOnce({
        data: { workspace: { id: "workspace 1" }, created: true },
        status: 200,
        headers: new Headers(),
        requestId: "r",
      })
      .mockResolvedValueOnce({
        data: {
          user: { id: 7, email: "admin@example.com" },
          temporary_password: "secret",
          password_delivery: "one_time_response",
        },
        status: 200,
        headers: new Headers(),
        requestId: "r",
      });

    await listTenants();
    await createTenant({ name: "Cliente Demo", slug: "cliente-demo" });
    await listTenantWorkspaces("tenant 1");
    await createTenantWorkspace("tenant 1", { name: "Principal" });
    await bootstrapTenantAdmin("tenant 1", {
      workspace_id: "workspace 1",
      email: "admin@example.com",
    });

    expect(apiMock.get).toHaveBeenNthCalledWith(1, "/api/admin/tenants");
    expect(apiMock.post).toHaveBeenNthCalledWith(1, "/api/admin/tenants", {
      name: "Cliente Demo",
      slug: "cliente-demo",
    });
    expect(apiMock.get).toHaveBeenNthCalledWith(2, "/api/admin/tenants/tenant%201/workspaces");
    expect(apiMock.post).toHaveBeenNthCalledWith(2, "/api/admin/tenants/tenant%201/workspaces", {
      name: "Principal",
    });
    expect(apiMock.post).toHaveBeenNthCalledWith(3, "/api/admin/tenants/tenant%201/bootstrap-admin", {
      workspace_id: "workspace 1",
      email: "admin@example.com",
    });
  });
});

describe("operations workflow client", () => {
  it("plans then executes a planning workflow and tolerates already-planned 409", async () => {
    const conflict = Object.assign(new Error("already planned"), { status: 409 });
    const workflow: OperationWorkflow = {
      id: "wf 1",
      status: "planning",
    };
    apiMock.post
      .mockRejectedValueOnce(conflict)
      .mockResolvedValueOnce({
        data: { ok: true, workflow_id: "wf 1", action: "execute" },
        status: 200,
        headers: new Headers(),
        requestId: "r",
      });

    await expect(triggerOperationWorkflow(workflow)).resolves.toMatchObject({
      ok: true,
      workflow_id: "wf 1",
    });
    expect(apiMock.post).toHaveBeenNthCalledWith(1, "/api/copilot/workflow/wf%201/plan", {});
    expect(apiMock.post).toHaveBeenNthCalledWith(2, "/api/copilot/workflow/wf%201/execute", {});
  });

  it("cancels workflows through the encoded endpoint", async () => {
    apiMock.post.mockResolvedValueOnce({
      data: { ok: true, workflow_id: "wf 1", action: "cancel" },
      status: 200,
      headers: new Headers(),
      requestId: "r",
    });

    await cancelOperationWorkflow("wf 1");

    expect(apiMock.post).toHaveBeenCalledWith("/api/copilot/workflow/wf%201/cancel", {});
  });
});
