import { describe, expect, it } from "vitest";

import { absoluteTime, formatCount, plural } from "./format";

describe("format helpers", () => {
  it("groups thousands like es-MX", () => {
    expect(formatCount(1200)).toBe("1,200");
    expect(formatCount(0)).toBe("0");
  });

  it("chooses singular or plural", () => {
    expect(plural(1, "línea", "líneas")).toBe("1 línea");
    expect(plural(0, "línea", "líneas")).toBe("0 líneas");
    expect(plural(1240, "registro", "registros")).toBe("1,240 registros");
  });

  it("formats absolute timestamps in UTC and leaves invalid ones untouched", () => {
    const formatted = absoluteTime("2026-09-26T11:48:00Z");
    expect(formatted).toMatch(/^26 sept?\.? 2026, 11:48/);
    expect(formatted.endsWith(" UTC")).toBe(true);
    expect(absoluteTime("no es fecha")).toBe("no es fecha");
  });
});
