import { spawnSync } from "node:child_process";

import { describe, expect, it } from "vitest";

const MINIMATCH_SETUP = `
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

function runIsolated(source: string): Record<string, unknown> {
  const result = spawnSync(
    process.execPath,
    [
      "--max-old-space-size=64",
      "--input-type=module",
      "--eval",
      source,
    ],
    {
      cwd: process.cwd(),
      encoding: "utf8",
      timeout: 5_000,
    },
  );

  expect(result.error).toBeUndefined();
  expect(result.signal).toBeNull();
  expect(result.status, result.stderr).toBe(0);
  return JSON.parse(result.stdout.trim());
}

describe("brace-expansion shallow group limit", () => {
  it("rejects the exact shallow group flood in every consumer", () => {
    const result = runIsolated(`
      import { performance } from "node:perf_hooks";
      import { expand } from "brace-expansion";
      ${MINIMATCH_SETUP}
      const pattern = "{" + "{}".repeat(8000) + ",x}";
      const started = performance.now();
      console.log(JSON.stringify({
        length: pattern.length,
        direct: expand(pattern),
        legacy: legacy("not-a-match", pattern),
        modern: modern("not-a-match", pattern),
        version,
        elapsedMs: performance.now() - started
      }));
    `);

    expect(result).toMatchObject({
      length: 16_004,
      direct: [],
      legacy: false,
      modern: false,
      version: "10.2.5",
    });
    expect(result.elapsedMs).toBeTypeOf("number");
    expect(result.elapsedMs as number).toBeLessThan(1_000);
  });

  it("accepts 256 groups and rejects the first extra group", () => {
    const result = runIsolated(`
      import { expand } from "brace-expansion";
      const allowed = "{" + "{}".repeat(255) + ",x}";
      const rejected = "{" + "{}".repeat(256) + ",x}";
      console.log(JSON.stringify({
        allowed: expand(allowed),
        rejected: expand(rejected)
      }));
    `);

    expect(result.allowed).toEqual(["{}".repeat(255), "x"]);
    expect(result.rejected).toEqual([]);
  });

  it("preserves normal, escaped, and small unbalanced patterns", () => {
    const result = runIsolated(`
      import { expand } from "brace-expansion";
      const escapedFlood = "\\\\{x\\\\}".repeat(300);
      console.log(JSON.stringify({
        normal: expand("file.{js,ts}"),
        escaped: expand("\\\\{literal\\\\}"),
        escapedFlood: expand(escapedFlood),
        unbalanced: expand("{a,b")
      }));
    `);

    expect(result.normal).toEqual(["file.js", "file.ts"]);
    expect(result.escaped).toEqual(["{literal}"]);
    expect(result.escapedFlood).toEqual(["{x}".repeat(300)]);
    expect(result.unbalanced).toEqual(["{a,b"]);
  });
});
