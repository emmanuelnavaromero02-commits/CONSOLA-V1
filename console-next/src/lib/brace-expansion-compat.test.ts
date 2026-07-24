import { createRequire } from "node:module";

import { describe, expect, it } from "vitest";

type Expand = {
  (pattern: string, options?: { max?: number; maxLength?: number }): string[];
  expand: Expand;
  EXPANSION_MAX: number;
  EXPANSION_MAX_LENGTH: number;
};

const require = createRequire(import.meta.url);
const expand = require("brace-expansion") as Expand;
const minimatch = require("minimatch") as (
  value: string,
  pattern: string,
) => boolean;

describe("brace-expansion compatibility", () => {
  it("supports legacy callable and modern named APIs", () => {
    expect(expand("file.{ts,tsx}")).toEqual(["file.ts", "file.tsx"]);
    expect(expand.expand("file.{js,jsx}")).toEqual(["file.js", "file.jsx"]);
  });

  it("keeps legacy minimatch brace patterns working", () => {
    expect(minimatch("component.tsx", "*.{ts,tsx}")).toBe(true);
  });

  it("delegates bounded expansion to the patched implementation", () => {
    const values = expand.expand("{a,b}".repeat(40), {
      max: 100,
      maxLength: 400,
    });

    expect(values.join("").length).toBeLessThanOrEqual(400);
  });

  it("bounds the legacy callable API with secure defaults", () => {
    const values = expand("{a,b}".repeat(41));
    const outputLength = values.reduce((total, value) => total + value.length, 0);

    expect(values.length).toBeLessThan(expand.EXPANSION_MAX);
    expect(outputLength).toBeLessThanOrEqual(expand.EXPANSION_MAX_LENGTH);
  });
});
