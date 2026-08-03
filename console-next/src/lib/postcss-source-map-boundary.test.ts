import { mkdtempSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";

import postcss from "postcss";
import { afterEach, describe, expect, it } from "vitest";

const temporaryDirectories: string[] = [];

afterEach(() => {
  for (const directory of temporaryDirectories.splice(0)) {
    rmSync(directory, { force: true, recursive: true });
  }
});

describe("PostCSS source map boundary", () => {
  it("does not load an absolute synthetic map when from is unset", async () => {
    const directory = mkdtempSync(join(tmpdir(), "omega-postcss-map-"));
    temporaryDirectories.push(directory);
    const marker = "OMEGA_SYNTHETIC_SOURCE_MAP_MARKER";
    const mapPath = join(directory, "outside.map");
    writeFileSync(
      mapPath,
      JSON.stringify({
        mappings: "",
        names: [],
        sources: ["synthetic-private-source.css"],
        sourcesContent: [marker],
        version: 3,
      }),
      { encoding: "utf8", mode: 0o600 },
    );

    const css = `a{color:red}\n/*# sourceMappingURL=${mapPath} */`;
    const result = await postcss([]).process(css, {
      from: undefined,
      map: { annotation: false, inline: false },
    });
    const publicMap = JSON.stringify(result.map?.toJSON() ?? null);

    expect(publicMap).not.toContain(marker);
    expect(publicMap).not.toContain("synthetic-private-source.css");
  });
});
