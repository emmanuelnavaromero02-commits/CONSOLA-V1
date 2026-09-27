// @vitest-environment jsdom

import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { formatObservedAt } from "@/lib/control-room/experience-presenter";

import { ExperienceLiveBadge, formatCheckedAgo } from "./ExperienceLiveBadge";

(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

const NOW = Date.parse("2026-09-26T12:00:00Z");
let container: HTMLDivElement;
let root: Root;

async function renderBadge(props: Parameters<typeof ExperienceLiveBadge>[0]) {
  await act(async () => {
    root.render(<ExperienceLiveBadge {...props} />);
  });
}

beforeEach(() => {
  vi.useFakeTimers();
  vi.setSystemTime(NOW);
  container = document.createElement("div");
  document.body.append(container);
  root = createRoot(container);
});

afterEach(async () => {
  await act(async () => root.unmount());
  container.remove();
  vi.useRealTimers();
});

describe("ExperienceLiveBadge", () => {
  it("shows when the fingerprint was last checked and ticks without polling", async () => {
    await renderBadge({
      checkedAt: NOW - 5_000,
      offline: false,
      sourceObservedAt: "2026-09-20T00:00:00Z",
    });

    expect(container.textContent).toContain("En vivo · consultado hace 5 s");
    await act(async () => {
      vi.advanceTimersByTime(2_000);
    });
    expect(container.textContent).toContain("consultado hace 7 s");
    expect(container.querySelector('[role="status"]')).toBeNull();
  });

  it("keeps source age separate from query time and warns after 30 days", async () => {
    await renderBadge({
      checkedAt: NOW,
      offline: false,
      sourceObservedAt: "2026-07-01T00:00:00Z",
    });

    const source = [...container.querySelectorAll("p")].find((node) =>
      node.textContent?.startsWith("Datos del origen al"),
    );
    expect(source?.textContent).toBe(
      `Datos del origen al ${formatObservedAt("2026-07-01T00:00:00Z")}`,
    );
    expect(source?.className).toContain("text-warning");
    expect(container.textContent).toContain("hace un momento");
  });

  it("does not warn for recent source data", async () => {
    await renderBadge({
      checkedAt: NOW,
      offline: false,
      sourceObservedAt: "2026-09-10T00:00:00Z",
    });
    const source = [...container.querySelectorAll("p")].find((node) =>
      node.textContent?.startsWith("Datos del origen al"),
    );
    expect(source?.className).not.toContain("text-warning");
  });

  it("announces the offline state and keeps showing the last information", async () => {
    await renderBadge({
      checkedAt: NOW - 90_000,
      offline: true,
      sourceObservedAt: "2026-09-20T00:00:00Z",
    });

    expect(container.querySelector('[role="status"]')?.textContent).toBe(
      "Sin conexión en vivo · mostrando última información",
    );
    expect(container.textContent).not.toContain("En vivo · consultado");
    expect(container.textContent).toContain("Datos del origen al");
  });

  it("renders nothing invented before the first check", async () => {
    await renderBadge({ checkedAt: null, offline: false, sourceObservedAt: null });
    expect(container.textContent).toBe("");
  });
});

describe("formatCheckedAgo", () => {
  it.each([
    [0, "hace un momento"],
    [1_000, "hace 1 s"],
    [59_000, "hace 59 s"],
    [60_000, "hace 1 min"],
    [3_600_000, "hace 1 h"],
    [-5_000, "hace un momento"],
  ])("formats %i ms", (elapsed, expected) => {
    expect(formatCheckedAgo(NOW - elapsed, NOW)).toBe(expected);
  });
});
