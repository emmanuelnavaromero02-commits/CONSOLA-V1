// @vitest-environment jsdom

import { beforeEach, describe, expect, it, vi } from "vitest";

import {
  PAGE_CONTEXT_STORAGE_KEY,
  clearPageContext,
  publishPageContext,
  readPageContext,
} from "./page-context";

function setPath(path: string) {
  window.history.replaceState(null, "", path);
}

beforeEach(() => {
  window.sessionStorage.clear();
  clearPageContext();
  setPath("/decisions");
  vi.restoreAllMocks();
});

describe("page-context store", () => {
  it("publishes and reads context for the current path", () => {
    publishPageContext({ surface: "decisions", active_tab: "consejo", open: true, count: 3 });
    expect(readPageContext()).toEqual({
      surface: "decisions",
      active_tab: "consejo",
      open: true,
      count: 3,
    });
  });

  it("drops the context when the path changed", () => {
    publishPageContext({ surface: "decisions" });
    setPath("/dashboard");
    expect(readPageContext()).toEqual({});
  });

  it("mirrors to sessionStorage and restores after a reload", () => {
    publishPageContext({ surface: "bronze", source: "raw/acme/Employee" });
    const raw = window.sessionStorage.getItem(PAGE_CONTEXT_STORAGE_KEY);
    expect(raw).toBeTruthy();
    const stored = JSON.parse(raw as string);
    expect(stored.path).toBe("/decisions");

    clearPageContext();
    window.sessionStorage.setItem(PAGE_CONTEXT_STORAGE_KEY, raw as string);
    expect(readPageContext()).toEqual({ surface: "bronze", source: "raw/acme/Employee" });
  });

  it("clears the store and the mirror", () => {
    publishPageContext({ surface: "decisions" });
    clearPageContext();
    expect(readPageContext()).toEqual({});
    expect(window.sessionStorage.getItem(PAGE_CONTEXT_STORAGE_KEY)).toBeNull();
  });

  it("bounds keys and value sizes and drops non-primitives", () => {
    const partial: Record<string, string> = {};
    for (let index = 0; index < 30; index += 1) partial[`k${index}`] = "v";
    partial.long = "x".repeat(2000);
    publishPageContext({
      ...partial,
      bad: { nested: true } as unknown as string,
    });
    const ctx = readPageContext();
    expect(Object.keys(ctx).length).toBeLessThanOrEqual(22);
    expect(ctx.bad).toBeUndefined();
    const long = Object.values(ctx).find((value) => typeof value === "string" && value.startsWith("xx"));
    if (typeof long === "string") expect(long.length).toBeLessThanOrEqual(800);
  });

  it("caps at 22 keys so route and title always fit under the backend's 24", () => {
    const partial: Record<string, string> = {};
    for (let index = 0; index < 23; index += 1) partial[`k${index}`] = "v";
    publishPageContext(partial);
    expect(Object.keys(readPageContext()).length).toBe(22);
  });

  it("survives a sessionStorage that throws", () => {
    vi.spyOn(Storage.prototype, "setItem").mockImplementation(() => {
      throw new Error("quota");
    });
    vi.spyOn(Storage.prototype, "getItem").mockImplementation(() => {
      throw new Error("blocked");
    });
    publishPageContext({ surface: "decisions" });
    expect(readPageContext()).toEqual({ surface: "decisions" });
  });
});
