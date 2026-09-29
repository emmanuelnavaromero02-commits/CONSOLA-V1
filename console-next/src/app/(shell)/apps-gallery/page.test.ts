import { readFileSync } from "node:fs";
import { join } from "node:path";

import { describe, expect, it } from "vitest";

const source = readFileSync(
  join(process.cwd(), "src/app/(shell)/apps-gallery/page.tsx"),
  "utf8",
);

describe("apps-gallery page", () => {
  it("mounts the gallery and never redirects away", () => {
    expect(source).toContain("AppsGallery");
    expect(source).not.toContain("window.location.replace");
    expect(source).not.toContain("redirect(");
  });

  it("keeps the Spanish gallery heading", () => {
    expect(source).toContain("Galería de aplicaciones");
  });
});
