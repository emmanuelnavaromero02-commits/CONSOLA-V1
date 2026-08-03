import { createRequire } from "node:module";
import { spawnSync } from "node:child_process";

import { describe, expect, it } from "vitest";

const require = createRequire(import.meta.url);

function runIsolated(source: string, timeout = 2_000) {
  return spawnSync(
    process.execPath,
    ["--input-type=module", "--eval", source],
    {
      cwd: process.cwd(),
      encoding: "utf8",
      timeout,
    },
  );
}

describe("brace-expansion intermediate allocation limits", () => {
  it("bounds comma alternatives and rejects the oversized advisory shape", () => {
    const wrapperRequire = createRequire(
      require.resolve("brace-expansion/package.json"),
    );
    const modern = wrapperRequire("brace-expansion-safe") as {
      expand(pattern: string, options?: { maxLength?: number }): string[];
    };
    const wrapper = require("brace-expansion") as (pattern: string) => string[];

    const bounded = `{${Array(1_000).fill("{1..5}").join(",")}}`;
    const started = performance.now();
    const values = modern.expand(bounded, { maxLength: 50 });
    const elapsedMs = performance.now() - started;

    expect(values.length).toBeGreaterThan(0);
    expect(
      values.reduce((total, value) => total + value.length, 0),
    ).toBeLessThanOrEqual(50);
    expect(elapsedMs).toBeLessThan(500);

    const paddedPart = `{${"0".repeat(50)}1..100000}`;
    const oversizedCanary = `{${Array(400).fill(paddedPart).join(",")}}`;
    expect(wrapper(oversizedCanary)).toEqual([]);
  });

  it("stops padded sequence generation before an event-loop stall", () => {
    const result = runIsolated(
      `
        import { createRequire } from "node:module";
        const require = createRequire(import.meta.url);
        const wrapperRequire = createRequire(
          require.resolve("brace-expansion/package.json")
        );
        const { expand, EXPANSION_MAX_LENGTH } = wrapperRequire(
          "brace-expansion-safe"
        );
        const pattern = "{" + "0".repeat(8_000) + "1..100000}";
        const started = performance.now();
        const values = expand(pattern);
        console.log(JSON.stringify({
          count: values.length,
          length: values.reduce((total, value) => total + value.length, 0),
          limit: EXPANSION_MAX_LENGTH,
          elapsedMs: performance.now() - started
        }));
      `,
      5_000,
    );

    expect(result.error).toBeUndefined();
    expect(result.signal).toBeNull();
    expect(result.status, result.stderr).toBe(0);
    const output = JSON.parse(result.stdout.trim()) as {
      count: number;
      elapsedMs: number;
      length: number;
      limit: number;
    };
    expect(output.count).toBeGreaterThan(0);
    expect(output.length).toBeLessThanOrEqual(output.limit);
    expect(output.elapsedMs).toBeLessThan(2_000);
  });
});
