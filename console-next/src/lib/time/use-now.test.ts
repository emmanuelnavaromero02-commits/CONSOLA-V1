import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { scheduleTicks } from "./use-now";

describe("scheduleTicks", () => {
  beforeEach(() => {
    vi.useFakeTimers();
  });

  afterEach(() => {
    vi.useRealTimers();
  });

  it("chains timeouts at the requested cadence and stops after cleanup", () => {
    const onTick = vi.fn();
    let clock = 1_000;
    const stop = scheduleTicks(onTick, 1_000, () => clock);

    expect(onTick).not.toHaveBeenCalled();
    clock = 2_000;
    vi.advanceTimersByTime(1_000);
    expect(onTick).toHaveBeenLastCalledWith(2_000);
    clock = 3_000;
    vi.advanceTimersByTime(1_000);
    expect(onTick).toHaveBeenCalledTimes(2);

    stop();
    vi.advanceTimersByTime(5_000);
    expect(onTick).toHaveBeenCalledTimes(2);
    expect(vi.getTimerCount()).toBe(0);
  });

  it("never keeps more than one pending timer", () => {
    const stop = scheduleTicks(() => undefined, 500);
    for (let index = 0; index < 5; index += 1) {
      vi.advanceTimersByTime(500);
      expect(vi.getTimerCount()).toBe(1);
    }
    stop();
  });
});
