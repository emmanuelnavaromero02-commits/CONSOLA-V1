// @vitest-environment jsdom

import { afterEach, describe, expect, it, vi } from "vitest";

import { csvCell, downloadText, saveBlob, toCsv } from "./csv";

afterEach(() => {
  vi.restoreAllMocks();
  vi.useRealTimers();
});

describe("csv", () => {
  it("guards formulas and quotes separators", () => {
    expect(csvCell("=cmd")).toBe("'=cmd");
    expect(csvCell("+1")).toBe("'+1");
    expect(csvCell("a,b")).toBe('"a,b"');
    expect(csvCell(Number.POSITIVE_INFINITY)).toBe("");
    expect(toCsv([["id"], [1]])).toBe("id\r\n1\r\n");
  });

  it("saves a blob through a temporary link and revokes the URL", async () => {
    vi.useFakeTimers();
    const createObjectURL = vi.fn(() => "blob:csv");
    const revokeObjectURL = vi.fn();
    Object.assign(URL, { createObjectURL, revokeObjectURL });
    const click = vi.spyOn(HTMLAnchorElement.prototype, "click").mockImplementation(() => undefined);
    saveBlob(new Blob(["x"]), "datos.zip");
    expect(click).toHaveBeenCalledTimes(1);
    const anchor = click.mock.instances[0] as unknown as HTMLAnchorElement;
    expect(anchor.download).toBe("datos.zip");
    expect(anchor.getAttribute("href")).toBe("blob:csv");
    expect(document.querySelector("a[download]")).toBeNull();
    vi.runAllTimers();
    expect(revokeObjectURL).toHaveBeenCalledWith("blob:csv");
  });

  it("downloads text as UTF-8 CSV with a BOM", async () => {
    const blobs: Blob[] = [];
    Object.assign(URL, {
      createObjectURL: vi.fn((blob: Blob) => {
        blobs.push(blob);
        return "blob:text";
      }),
      revokeObjectURL: vi.fn(),
    });
    vi.spyOn(HTMLAnchorElement.prototype, "click").mockImplementation(() => undefined);
    downloadText("orders.csv", "id\r\n1\r\n");
    expect(blobs[0].type).toBe("text/csv;charset=utf-8");
    const bytes = new Uint8Array(await blobs[0].arrayBuffer());
    expect([...bytes.slice(0, 3)]).toEqual([0xef, 0xbb, 0xbf]);
    expect(new TextDecoder().decode(bytes.slice(3))).toBe("id\r\n1\r\n");
  });
});
