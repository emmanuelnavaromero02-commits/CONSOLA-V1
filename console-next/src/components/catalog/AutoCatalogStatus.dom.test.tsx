// @vitest-environment jsdom

import { afterEach, beforeEach, describe, expect, it } from "vitest";

import { AutoCatalogStatus } from "./AutoCatalogStatus";
import { mount, type Mounted } from "./test-utils";

let view: Mounted;

beforeEach(() => {
  view = mount();
});

afterEach(async () => {
  await view.unmount();
});

describe("AutoCatalogStatus", () => {
  it("announces progress only while tables are pending", async () => {
    await view.render(<AutoCatalogStatus status={{ status: "working", processed: 1, pending: 2, stale: 3 }} />);
    const live = view.container.querySelector("[aria-live]");
    expect(live?.getAttribute("aria-live")).toBe("polite");
    expect(live?.textContent).toContain("El Copiloto está documentando 2 tablas");
  });

  it("summarises finished work without a live region", async () => {
    await view.render(<AutoCatalogStatus status={{ status: "ready", processed: 1, pending: 0, stale: 1 }} />);
    expect(view.container.querySelector("[aria-live]")).toBeNull();
    expect(view.container.textContent).toContain("El Copiloto documentó 1 tabla en esta visita.");
  });

  it("renders nothing when there is nothing to say", async () => {
    await view.render(<AutoCatalogStatus status={{ status: "idle", processed: 0, pending: 0, stale: 0 }} />);
    expect(view.container.textContent).toBe("");
    await view.render(<AutoCatalogStatus status={null} />);
    expect(view.container.textContent).toBe("");
  });
});
