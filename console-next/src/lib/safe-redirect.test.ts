import { describe, expect, it } from "vitest";

import { safeInternalPath } from "./safe-redirect";

const ORIGIN = "https://console.example.test";

describe("safeInternalPath", () => {
  it.each([
    ["/dashboard", "/dashboard"],
    ["/studio?tab=datasets&x=1", "/studio?tab=datasets&x=1"],
    ["/viewer/pipeline#run-7", "/viewer/pipeline#run-7"],
    ["/a/../control-room", "/control-room"],
    ["/%5Cevil", "/%5Cevil"],
    ["/%2F%2Fevil.example", "/%2F%2Fevil.example"],
  ])("keeps same-origin path %s", (raw, expected) => {
    expect(safeInternalPath(raw, ORIGIN)).toBe(expected);
  });

  it.each([
    "/\\evil.example",
    "/\\/evil.example",
    "\\\\evil.example",
    "/\t/evil.example",
    "/\n/evil.example",
    "/\r/evil.example",
    "/\u0000/evil.example",
    "/\u007f/evil.example",
    "//evil.example",
    "//evil.example/dashboard",
    "/.//evil.example",
    "/./%2e//evil.example",
    "https://evil.example/",
    "https:evil.example",
    "https:",
    "javascript:alert(1)",
    "JAVASCRIPT:alert(1)",
    "data:text/html,x",
    "evil.example",
    "",
    `/${"a".repeat(2048)}`,
  ])("falls back for %j", (raw) => {
    expect(safeInternalPath(raw, ORIGIN)).toBe("/dashboard");
  });

  it.each([null, undefined, 42, {}, ["/dashboard"]])("falls back for non-string %j", (raw) => {
    expect(safeInternalPath(raw, ORIGIN)).toBe("/dashboard");
  });

  it("uses the supplied fallback and rejects an unusable origin", () => {
    expect(safeInternalPath("//evil.example", ORIGIN, "/")).toBe("/");
    expect(safeInternalPath("/dashboard", "null")).toBe("/dashboard");
    expect(safeInternalPath("/studio", "null", "")).toBe("");
  });

  it("never returns a value that a browser would resolve off-origin", () => {
    const candidates = ["/ok", "/.//x", "/%09/x", "/;//x", "/?next=//x", "/#//x"];
    for (const raw of candidates) {
      const result = safeInternalPath(raw, ORIGIN, "/dashboard");
      expect(new URL(result, ORIGIN).origin).toBe(ORIGIN);
      expect(result.startsWith("//")).toBe(false);
    }
  });
});
