import { afterEach, describe, expect, it, vi } from "vitest";

import { api } from "@/lib/api";

import {
  approveSupervisedAction,
  cancelSupervisedAction,
  executeSupervisedAction,
  listSupervisedActions,
  rejectSupervisedAction,
  validateSupervisedAction,
} from "./client";

vi.mock("@/lib/api", () => ({
  api: {
    get: vi.fn(),
    post: vi.fn(),
  },
}));

const apiMock = vi.mocked(api);

afterEach(() => {
  vi.clearAllMocks();
});

const RESPONSE = { data: { id: "act 1", status: "prepared" }, status: 200, headers: new Headers(), requestId: "r" };

describe("supervised actions client", () => {
  it("normalizes list payloads that wrap actions in different keys", async () => {
    apiMock.get.mockResolvedValueOnce({ ...RESPONSE, data: { items: [{ id: "act 1" }] } });

    await expect(listSupervisedActions(10)).resolves.toMatchObject({
      actions: [{ id: "act 1" }],
    });
    expect(apiMock.get).toHaveBeenCalledWith("/api/actions?limit=10");
  });

  it("forwards idempotency_key in every mutation body", async () => {
    apiMock.post.mockResolvedValue(RESPONSE);

    await validateSupervisedAction("act 1", { idempotency_key: "key-validate" });
    await approveSupervisedAction("act 1", { idempotency_key: "key-approve" });
    await rejectSupervisedAction("act 1", { idempotency_key: "key-reject" });
    await cancelSupervisedAction("act 1", { idempotency_key: "key-cancel" });

    expect(apiMock.post).toHaveBeenNthCalledWith(1, "/api/actions/act%201/dry-run", { idempotency_key: "key-validate" });
    expect(apiMock.post).toHaveBeenNthCalledWith(2, "/api/actions/act%201/approve", { idempotency_key: "key-approve" });
    expect(apiMock.post).toHaveBeenNthCalledWith(3, "/api/actions/act%201/reject", { idempotency_key: "key-reject" });
    expect(apiMock.post).toHaveBeenNthCalledWith(4, "/api/actions/act%201/cancel", { idempotency_key: "key-cancel" });
  });

  it("keeps executeSupervisedAction available at the contract level (unused by the preview-only UI)", async () => {
    apiMock.post.mockResolvedValueOnce(RESPONSE);

    await executeSupervisedAction("act 1", { idempotency_key: "key-execute" });

    expect(apiMock.post).toHaveBeenCalledWith("/api/actions/act%201/execute", { idempotency_key: "key-execute" });
  });
});
