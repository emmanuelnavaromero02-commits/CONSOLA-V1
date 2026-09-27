import { beforeEach, describe, expect, it, vi } from "vitest";

const apiMock = vi.hoisted(() => ({ post: vi.fn() }));
vi.mock("@/lib/api", () => ({ api: apiMock }));

import { describeSource, exploreData, exploreRowsAsRecords, normalizeExploreResponse } from "./client";

const BRONZE = { kind: "bronze" as const, cartridge: "acme", entity: "Employee" };

beforeEach(() => {
  apiMock.post.mockReset();
});

describe("explorer client", () => {
  it("posts the structured request to /api/data/explore", async () => {
    apiMock.post.mockResolvedValue({
      data: {
        source: "raw/acme/Employee",
        source_kind: "bronze",
        available_columns: [{ name: "nombre", type: "VARCHAR", kind: "text" }],
        executed: true,
        columns: ["nombre"],
        rows: [["Ana"]],
        row_count: 1,
        limit: 50,
        truncated: false,
        sql_display: "SELECT * FROM read_parquet('raw/acme/Employee') LIMIT 50",
        sql_definition: null,
        sources: ["raw/acme/Employee"],
      },
    });
    const request = { source: BRONZE, columns: [], filters: [], sort: [], limit: 50, latest_only: false, execute: true };
    const response = await exploreData(request);
    expect(apiMock.post).toHaveBeenCalledWith("/api/data/explore", request);
    expect(response.rows).toEqual([["Ana"]]);
    expect(response.available_columns[0].kind).toBe("text");
  });

  it("asks for the schema without executing rows", async () => {
    apiMock.post.mockResolvedValue({ data: { available_columns: [] } });
    await describeSource(BRONZE);
    expect(apiMock.post.mock.calls[0][1]).toMatchObject({ source: BRONZE, execute: false, filters: [] });
  });

  it("normalizes partial or malformed payloads defensively", () => {
    const normalized = normalizeExploreResponse({
      available_columns: [{ name: "a", kind: "weird" }, { kind: "text" }, "x"],
      rows: [["1"], "bad"],
      columns: ["a"],
    });
    expect(normalized.available_columns).toEqual([{ name: "a", type: "", kind: "other" }]);
    expect(normalized.rows).toEqual([["1"]]);
    expect(normalized.row_count).toBe(1);
    expect(normalized.sql_definition).toBeNull();
    expect(normalizeExploreResponse(null).columns).toEqual([]);
  });

  it("turns row arrays into records for the data table", () => {
    expect(exploreRowsAsRecords({ columns: ["a", "b"], rows: [[1, null], [2]] })).toEqual([
      { a: 1, b: null },
      { a: 2, b: null },
    ]);
    expect(exploreRowsAsRecords(null)).toEqual([]);
  });
});
