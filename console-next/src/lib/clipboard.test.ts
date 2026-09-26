import { afterEach, describe, expect, it, vi } from "vitest";

import { copyText } from "./clipboard";

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("copyText", () => {
  it("writes to the clipboard", async () => {
    const writeText = vi.fn().mockResolvedValue(undefined);
    vi.stubGlobal("navigator", { clipboard: { writeText } });
    await copyText("select 1");
    expect(writeText).toHaveBeenCalledWith("select 1");
  });

  it("fails loudly when the clipboard is missing", async () => {
    vi.stubGlobal("navigator", {});
    await expect(copyText("x")).rejects.toThrow("portapapeles");
  });

  it("propagates clipboard errors", async () => {
    vi.stubGlobal("navigator", { clipboard: { writeText: vi.fn().mockRejectedValue(new Error("denegado")) } });
    await expect(copyText("x")).rejects.toThrow("denegado");
  });
});
