import { describe, expect, it } from "vitest";

import type { AnalyticsApp } from "@/lib/admin-surfaces";

import {
  appCartridgeId,
  appDataState,
  cartridgesFromApps,
  isPublishedAppName,
} from "./useAnalyticsApps";

function app(overrides: Partial<AnalyticsApp> = {}): AnalyticsApp {
  return { name: "sap_successfactors_workforce_overview", ...overrides };
}

describe("published app name validation", () => {
  it("accepts the names the backend publishes", () => {
    expect(isPublishedAppName("sap_successfactors_workforce_overview")).toBe(true);
    expect(isPublishedAppName("sap_successfactors_talent_health")).toBe(true);
    expect(isPublishedAppName("_private_app")).toBe(true);
  });

  it("refuses anything that could become a different URL", () => {
    for (const value of [
      "",
      " ",
      "javascript:alert(1)",
      "data:text/html,<script>1</script>",
      "https://evil.example/app",
      "../../etc/passwd",
      "app/../../secret",
      "app name",
      "app-name",
      "1_leading_digit",
      "app?query=1",
      "app#hash",
      "a".repeat(129),
      null,
      undefined,
    ]) {
      expect(isPublishedAppName(value as string), `accepted ${String(value)}`).toBe(false);
    }
  });
});

describe("data state", () => {
  it("reports ready only when the server says so", () => {
    expect(appDataState(app({ data_status: "ready" }))).toBe("ready");
  });

  it("treats unready and missing metadata as partial, never as ready", () => {
    expect(appDataState(app({ data_status: "unready" }))).toBe("partial");
    expect(appDataState(app({ data_status: "dataset_metadata_missing" }))).toBe("partial");
  });

  it("does not invent a state when the server omits it", () => {
    expect(appDataState(app())).toBe("unknown");
    expect(appDataState(app({ data_status: null }))).toBe("unknown");
  });
});

describe("cartridge helpers", () => {
  it("prefers cartridge_id and falls back to cartridge", () => {
    expect(appCartridgeId(app({ cartridge_id: "sap_successfactors" }))).toBe(
      "sap_successfactors",
    );
    expect(appCartridgeId(app({ cartridge: "replicon" }))).toBe("replicon");
    expect(appCartridgeId(app())).toBe("");
  });

  it("lists each cartridge once, sorted, skipping blanks", () => {
    expect(
      cartridgesFromApps([
        app({ name: "a", cartridge_id: "replicon" }),
        app({ name: "b", cartridge_id: "sap_successfactors" }),
        app({ name: "c", cartridge_id: "replicon" }),
        app({ name: "d" }),
      ]),
    ).toEqual(["replicon", "sap_successfactors"]);
  });
});
