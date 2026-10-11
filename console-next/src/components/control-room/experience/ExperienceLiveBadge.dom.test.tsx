// @vitest-environment jsdom

import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { formatObservedAt } from "@/lib/control-room/experience-presenter";

import { ExperienceLiveBadge, formatCheckedAgo, formatSourceAge } from "./ExperienceLiveBadge";

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

  it("keeps source age separate from query time and warns when the source is old", async () => {
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

  it("does not warn for source data from the last day and a half", async () => {
    await renderBadge({
      checkedAt: NOW,
      offline: false,
      sourceObservedAt: "2026-09-25T06:00:00Z",
    });
    const source = [...container.querySelectorAll("p")].find((node) =>
      node.textContent?.startsWith("Datos del origen al"),
    );
    expect(source?.className).not.toContain("text-warning");
    expect(container.querySelector('[data-testid="control-room-source-stale"]')).toBeNull();
    expect(container.querySelector(".bg-success")).not.toBeNull();
  });

  it("makes week-old source data obvious next to the live check", async () => {
    await renderBadge({
      checkedAt: NOW - 5_000,
      offline: false,
      sourceObservedAt: "2026-09-19T09:00:00Z",
    });

    const stale = container.querySelector('[data-testid="control-room-source-stale"]');
    expect(stale?.textContent).toBe("Datos de hace 7 días");
    expect(stale?.className).toContain("text-warning");
    expect(container.textContent).toContain("En vivo · consultado hace 5 s");
    expect(container.querySelector(".bg-warning")).not.toBeNull();
    expect(container.querySelector(".bg-success")).toBeNull();
  });

  it("flags data older than 36 hours in hours before it reaches two days", async () => {
    await renderBadge({
      checkedAt: NOW,
      offline: false,
      sourceObservedAt: "2026-09-24T20:00:00Z",
    });
    expect(
      container.querySelector('[data-testid="control-room-source-stale"]')?.textContent,
    ).toBe("Datos de hace 40 h");
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

describe("formatSourceAge", () => {
  it.each([
    [37 * 3_600_000, "Datos de hace 37 h"],
    [47 * 3_600_000, "Datos de hace 47 h"],
    [48 * 3_600_000, "Datos de hace 2 días"],
    [7 * 24 * 3_600_000 + 1, "Datos de hace 7 días"],
  ])("formats %i ms", (elapsed, expected) => {
    expect(formatSourceAge(NOW - elapsed, NOW)).toBe(expected);
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
