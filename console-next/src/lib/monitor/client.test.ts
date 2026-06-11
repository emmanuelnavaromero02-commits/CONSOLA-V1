import { beforeEach, describe, expect, it, vi } from "vitest";

import { api } from "@/lib/api";

import { getDatasetDetail } from "./client";

vi.mock("@/lib/api", () => ({
  api: {
    get: vi.fn(),
  },
}));

describe("monitor client", () => {
  beforeEach(() => {
    vi.mocked(api.get).mockReset();
  });

  it("uses the dataset detail contract instead of the schema-only endpoint", async () => {
    vi.mocked(api.get).mockResolvedValue({
      data: {
        name: "sap_successfactors_employee_360",
        layer: "gold",
        columns: [{ name: "user_id" }],
        row_count: 1288,
        status: "ok",
      },
      status: 200,
      headers: new Headers(),
      requestId: "req-detail",
    });

    const detail = await getDatasetDetail("sap_successfactors_employee_360");

    expect(detail.name).toBe("sap_successfactors_employee_360");
    expect(api.get).toHaveBeenCalledWith("/api/datasets/sap_successfactors_employee_360/detail");
    expect(api.get).not.toHaveBeenCalledWith("/datasets/sap_successfactors_employee_360/schema");
  });
});
