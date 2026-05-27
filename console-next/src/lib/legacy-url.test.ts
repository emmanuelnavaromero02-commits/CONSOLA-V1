import { afterEach, describe, expect, it, vi } from "vitest";

// legacy-url resolves NEXT_PUBLIC_LEGACY_CONSOLE_URL at module-load time, so
// each case resets the module registry and re-imports with a stubbed env.
async function loadWithEnv(value: string) {
  vi.resetModules();
  vi.stubEnv("NEXT_PUBLIC_LEGACY_CONSOLE_URL", value);
  return import("./legacy-url");
}

afterEach(() => {
  vi.unstubAllEnvs();
  vi.resetModules();
});

describe("legacyConsoleUrl (Control Room nav link contract)", () => {
  it("targets the backend control-room when a valid http base is configured", async () => {
    const { legacyConsoleUrl } = await loadWithEnv("http://localhost:8000");
    expect(legacyConsoleUrl("/control-room")).toBe("http://localhost:8000/control-room");
  });

  it("strips a trailing slash on the base before concatenating", async () => {
    const { legacyConsoleUrl } = await loadWithEnv("http://localhost:8000/");
    expect(legacyConsoleUrl("/control-room")).toBe("http://localhost:8000/control-room");
  });

  it("falls back to the same-origin /legacy redirector when the env is unset", async () => {
    // The /legacy/[[...path]] route forwards to the backend at runtime, so the
    // Control Room link still reaches :8000 even without a build-time base.
    const { legacyConsoleUrl } = await loadWithEnv("");
    expect(legacyConsoleUrl("/control-room")).toBe("/legacy/control-room");
  });

  it("rejects a non-http(s) base (e.g. javascript:) and falls back", async () => {
    const { legacyConsoleUrl } = await loadWithEnv("javascript:alert(1)");
    expect(legacyConsoleUrl("/control-room")).toBe("/legacy/control-room");
  });

  it("always resolves to a /control-room-suffixed target", async () => {
    for (const base of ["http://localhost:8000", "", "https://omega.example.com"]) {
      const { legacyConsoleUrl } = await loadWithEnv(base);
      expect(legacyConsoleUrl("/control-room").endsWith("/control-room")).toBe(true);
    }
  });

  it("defensively prefixes a path missing its leading slash", async () => {
    const { legacyConsoleUrl } = await loadWithEnv("http://localhost:8000");
    expect(legacyConsoleUrl("control-room")).toBe("http://localhost:8000/control-room");
  });
});
