import { describe, expect, it } from "vitest";

import { formatCount, plural } from "./format";

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
});
