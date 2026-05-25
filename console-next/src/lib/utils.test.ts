import { describe, expect, it } from "vitest";

import { cn } from "./utils";

describe("cn", () => {
  it("keeps truthy classes and drops falsy values", () => {
    expect(cn("px-2", false && "hidden", null, "text-sm")).toBe("px-2 text-sm");
  });

  it("lets later Tailwind utilities win", () => {
    expect(cn("px-2 py-1", "px-4")).toBe("py-1 px-4");
  });
});
