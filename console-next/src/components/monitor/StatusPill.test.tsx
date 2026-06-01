import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";

import { StatusPill } from "./StatusPill";

describe("StatusPill", () => {
  it.each([
    ["success", "text-success"],
    ["running", "text-warning"],
    ["failed", "text-destructive"],
    ["custom", "text-muted-foreground"],
  ])("maps %s to the expected tone", (status, tone) => {
    const markup = renderToStaticMarkup(<StatusPill status={status} />);

    expect(markup).toContain(status);
    expect(markup).toContain(tone);
  });

  it("falls back to unknown when no status is provided", () => {
    const markup = renderToStaticMarkup(<StatusPill status={null} />);

    expect(markup).toContain("unknown");
    expect(markup).toContain("text-warning");
  });
});
