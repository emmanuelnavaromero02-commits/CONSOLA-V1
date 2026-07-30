import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";

import { StatusPill } from "./StatusPill";

describe("StatusPill", () => {
  it.each([
    ["success", "Exitoso", "text-success"],
    ["done", "Completado", "text-success"],
    ["fresh", "Fresca", "text-success"],
    ["queued", "En cola", "text-warning"],
    ["running", "En ejecución", "text-warning"],
    ["partial", "Parcial", "text-warning"],
    ["empty", "Sin filas", "text-warning"],
    ["stale", "Antigua", "text-warning"],
    ["failed", "Fallido", "text-destructive"],
    ["error", "Error", "text-destructive"],
    ["very_stale", "Muy antigua", "text-destructive"],
  ])("renders %s with Spanish label %s and expected tone", (status, label, tone) => {
    const markup = renderToStaticMarkup(<StatusPill status={status} />);

    expect(markup).toContain(label);
    expect(markup).toContain(tone);
  });

  it("does not surface raw English keys in the visible label", () => {
    for (const status of ["very_stale", "queued", "running", "failed"]) {
      const markup = renderToStaticMarkup(<StatusPill status={status} />);
      // The raw key stays only in the title attribute, not as visible text.
      expect(markup).toContain(`title="${status}"`);
      expect(markup).toMatch(/>[^<]*</);
      const visible = markup.replace(/<[^>]+>/g, "");
      expect(visible).not.toContain(status);
    }
  });

  it("falls back to a neutral no-data label when no status is provided", () => {
    const markup = renderToStaticMarkup(<StatusPill status={null} />);

    expect(markup).toContain("Sin dato de frescura");
    expect(markup).toContain("text-warning");
  });

  it("keeps unknown backend keys visible as raw text", () => {
    const markup = renderToStaticMarkup(<StatusPill status="custom" />);

    expect(markup).toContain("custom");
    expect(markup).toContain("text-muted-foreground");
  });
});
