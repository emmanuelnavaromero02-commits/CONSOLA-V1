import { readFileSync } from "node:fs";
import { createRequire } from "node:module";
import { spawnSync } from "node:child_process";
import { resolve } from "node:path";

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
const MINIMATCH_ESM_SETUP = `
  import { readFileSync } from "node:fs";
  import { createRequire } from "node:module";
  import { dirname, resolve } from "node:path";
  import { pathToFileURL } from "node:url";
  const require = createRequire(import.meta.url);
  const legacy = require("minimatch");
  const estree = require.resolve(
    "@typescript-eslint/typescript-estree/package.json"
  );
  const pkg = require.resolve("minimatch/package.json", {
    paths: [dirname(estree)]
  });
  const version = JSON.parse(readFileSync(pkg, "utf8")).version;
  const entry = resolve(dirname(pkg), "dist/esm/index.js");
  const { minimatch: modern } = await import(pathToFileURL(entry).href);
`;

function runNodeEsm(source: string, nodeArgs: string[] = []): string {
  const result = spawnSync(
    process.execPath,
    [...nodeArgs, "--input-type=module", "--eval", source],
    {
      cwd: process.cwd(),
      encoding: "utf8",
      timeout: 5_000,
    },
  );

  expect(result.error).toBeUndefined();
  expect(result.signal).toBeNull();
  expect(result.status, result.stderr).toBe(0);
  return result.stdout.trim();
}

describe("brace-expansion compatibility", () => {
  it("keeps the CommonJS default export callable", () => {
    expect(expand("file.{ts,tsx}")).toEqual(["file.ts", "file.tsx"]);
    expect(expand.expand("file.{js,jsx}")).toEqual(["file.js", "file.jsx"]);
  });

  it("publishes static named exports to a real Node ESM process", () => {
    const output = runNodeEsm(`
      import {
        expand,
        EXPANSION_MAX,
        EXPANSION_MAX_LENGTH
      } from "brace-expansion";
      console.log(JSON.stringify({
        values: expand("file.{ts,tsx}"),
        max: EXPANSION_MAX,
        maxLength: EXPANSION_MAX_LENGTH
      }));
    `);

    expect(JSON.parse(output)).toEqual({
      values: ["file.ts", "file.tsx"],
      max: 100_000,
      maxLength: 4_000_000,
    });
  });

  it("keeps legacy minimatch brace patterns working", () => {
    expect(minimatch("component.tsx", "*.{ts,tsx}")).toBe(true);
  });

  it("loads the installed minimatch 10 ESM implementation", () => {
    const output = runNodeEsm(`
      ${MINIMATCH_ESM_SETUP}
      console.log(JSON.stringify({
        version,
        matches: modern("component.tsx", "*.{ts,tsx}")
      }));
    `);

    expect(JSON.parse(output)).toEqual({
      version: "10.2.5",
      matches: true,
    });
  });

  it("enforces max and maxLength in an isolated process", () => {
    const output = runNodeEsm(`
      import { expand } from "brace-expansion";
      const pattern = "{1..1000}";
      const byCount = expand(pattern, { max: 32, maxLength: 1000000 });
      const byLength = expand(pattern, { max: 100000, maxLength: 40 });
      console.log(JSON.stringify({
        count: byCount.length,
        length: byLength.reduce((total, value) => total + value.length, 0)
      }));
    `);
    const result = JSON.parse(output);

    expect(result.count).toBe(32);
    expect(result.length).toBeGreaterThan(0);
    expect(result.length).toBeLessThanOrEqual(40);
  });

  it("bounds aggregate nested branches before expansion", () => {
    const output = runNodeEsm(
      `
        import {
          expand,
          EXPANSION_MAX,
          EXPANSION_MAX_LENGTH
        } from "brace-expansion";
        const branch = "{a,b}".repeat(17);
        const pattern = "{" + Array(15).fill(branch).join(",") + "}";
        const values = expand(pattern, {
          max: Number.MAX_SAFE_INTEGER,
          maxLength: Number.MAX_SAFE_INTEGER
        });
        console.log(JSON.stringify({
          count: values.length,
          length: values.reduce((total, value) => total + value.length, 0),
          max: EXPANSION_MAX,
          maxLength: EXPANSION_MAX_LENGTH
        }));
      `,
      ["--max-old-space-size=64"],
    );
    const result = JSON.parse(output);

    expect(result.count).toBeGreaterThan(0);
    expect(result.count).toBeLessThanOrEqual(result.max);
    expect(result.length).toBeLessThanOrEqual(result.maxLength);
  });

  it("bounds the nested attack through minimatch 3 and 10", () => {
    const output = runNodeEsm(
      `
        ${MINIMATCH_ESM_SETUP}
        const branch = "{a,b}".repeat(17);
        const pattern = "{" + Array(15).fill(branch).join(",") + "}";
        console.log(JSON.stringify({
          legacy: legacy("not-a-match", pattern),
          modern: modern("not-a-match", pattern),
          version
        }));
      `,
      ["--max-old-space-size=64"],
    );

    expect(JSON.parse(output)).toEqual({
      legacy: false,
      modern: false,
      version: "10.2.5",
    });
  });

  it("rejects excessive nesting before parser recursion", () => {
    const output = runNodeEsm(
      `
        import { expand } from "brace-expansion";
        ${MINIMATCH_ESM_SETUP}
        const pattern =
          "{".repeat(5000) + "a,b" + "}".repeat(5000);
        console.log(JSON.stringify({
          direct: expand(pattern).length,
          legacy: legacy("not-a-match", pattern),
          modern: modern("not-a-match", pattern),
          version
        }));
      `,
      ["--max-old-space-size=64"],
    );

    expect(JSON.parse(output)).toEqual({
      direct: 0,
      legacy: false,
      modern: false,
      version: "10.2.5",
    });
  });

  it("bounds the legacy unbalanced rewrite in every consumer", () => {
    const output = runNodeEsm(
      `
        import {
          expand,
          EXPANSION_MAX,
          EXPANSION_MAX_LENGTH
        } from "brace-expansion";
        ${MINIMATCH_ESM_SETUP}
        const pattern =
          "{x}," + Array(20).fill("{1..100000}").join(",") + "}";
        const values = expand(pattern);
        const start = "0".repeat(8000) + "1";
        const end = "0".repeat(7995) + "100000";
        const padded = "{" + start + ".." + end + "}";
        const paddedValues = expand(padded);
        console.log(JSON.stringify({
          count: values.length,
          length: values.reduce((total, value) => total + value.length, 0),
          legacy: legacy("not-a-match", pattern),
          modern: modern("not-a-match", pattern),
          paddedCount: paddedValues.length,
          paddedLegacy: legacy("not-a-match", padded),
          paddedModern: modern("not-a-match", padded),
          max: EXPANSION_MAX,
          maxLength: EXPANSION_MAX_LENGTH
        }));
      `,
      ["--max-old-space-size=64"],
    );
    const result = JSON.parse(output);

    expect(result.count).toBeGreaterThan(0);
    expect(result.count).toBeLessThanOrEqual(result.max);
    expect(result.length).toBeLessThanOrEqual(result.maxLength);
    expect(result.legacy).toBe(false);
    expect(result.modern).toBe(false);
    expect(result.paddedCount).toBeGreaterThan(0);
    expect(result.paddedLegacy).toBe(false);
    expect(result.paddedModern).toBe(false);
  });

  it("rejects oversized flat inputs before allocation", () => {
    const output = runNodeEsm(
      `
        import {
          expand,
          EXPANSION_MAX,
          EXPANSION_MAX_LENGTH
        } from "brace-expansion";
        ${MINIMATCH_ESM_SETUP}
        const boundary =
          "{" + Array(128).fill("{1..100000}").join(",") + "}";
        const boundaryValues = expand(boundary);
        const alternatives =
          "{" + Array(2048).fill("value").join(",") + "}";
        const sequences = "{1..100000}".repeat(1489);
        console.log(JSON.stringify({
          boundaryCount: boundaryValues.length,
          boundaryLength: boundaryValues.reduce(
            (total, value) => total + value.length,
            0
          ),
          alternatives: expand(alternatives).length,
          oversized: expand("x".repeat(20000)).length,
          sequences: expand(sequences).length,
          sequenceLegacy: legacy("not-a-match", sequences),
          sequenceModern: modern("not-a-match", sequences),
          max: EXPANSION_MAX,
          maxLength: EXPANSION_MAX_LENGTH
        }));
      `,
      ["--max-old-space-size=64"],
    );

    const result = JSON.parse(output);

    expect(result.boundaryCount).toBeGreaterThan(0);
    expect(result.boundaryCount).toBeLessThanOrEqual(result.max);
    expect(result.boundaryLength).toBeLessThanOrEqual(result.maxLength);
    expect(result.alternatives).toBe(0);
    expect(result.oversized).toBe(0);
    expect(result.sequences).toBe(0);
    expect(result.sequenceLegacy).toBe(false);
    expect(result.sequenceModern).toBe(false);
  });

  it("locks the official patched npm payload", () => {
    const lock = JSON.parse(
      readFileSync(resolve(process.cwd(), "package-lock.json"), "utf8"),
    );
    const payload = lock.packages["vendor/brace-expansion-compat/node_modules/brace-expansion-safe"];

    expect(payload).toMatchObject({
      name: "brace-expansion",
      version: "5.0.9",
      resolved:
        "https://registry.npmjs.org/brace-expansion/-/brace-expansion-5.0.9.tgz",
      integrity:
        "sha512-ScQ4IuvIEF1TMlP7Zt+vjJ//9zlPb2SDcxWxM3bk8s6t6GGdJ7KO1dCcTidOPJKePW30LE/2cT7wCyPho9/Wxg==",
    });
  });
});
