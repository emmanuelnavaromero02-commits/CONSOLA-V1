import { afterEach, describe, expect, it, vi } from "vitest";

import {
  customScheduleError,
  defaultTimeZone,
  describeSchedule,
  FREQUENCY_PRESETS,
  isValidTimeZone,
  normalizeCron,
  presetFromCron,
  timeZoneName,
  timeZoneOptions,
} from "./frequency";

afterEach(() => {
  vi.restoreAllMocks();
});

describe("frequency presets", () => {
  it("maps each business frequency to its schedule", () => {
    expect(FREQUENCY_PRESETS.map((preset) => [preset.id, preset.label, preset.cron])).toEqual([
      ["manual", "Bajo Demanda (solo manual)", null],
      ["hourly", "Cada hora", "0 * * * *"],
      ["daily_morning", "Diario a primera hora (08:00)", "0 8 * * *"],
      ["end_of_day", "Al finalizar la jornada laboral (19:00)", "0 19 * * 1-5"],
      ["weekly_monday", "Semanal (Lunes por la mañana)", "0 8 * * 1"],
    ]);
  });

  it("recognises presets and keeps anything else as custom", () => {
    expect(presetFromCron(null)).toBe("manual");
    expect(presetFromCron("   ")).toBe("manual");
    expect(presetFromCron("0 8 * * *")).toBe("daily_morning");
    expect(presetFromCron("  0   19 * *   1-5 ")).toBe("end_of_day");
    expect(presetFromCron("0 8 * * 1")).toBe("weekly_monday");
    expect(presetFromCron("0 * * * *")).toBe("hourly");
    expect(presetFromCron("0 8 * * 1-5")).toBe("custom");
    expect(presetFromCron("*/15 * * * *")).toBe("custom");
    expect(normalizeCron("  0   8 * * * ")).toBe("0 8 * * *");
  });
});

describe("describeSchedule", () => {
  it("describes presets in business words with the time zone", () => {
    expect(describeSchedule("0 8 * * *", "UTC")).toBe("Diario a primera hora · 08:00 (UTC)");
    expect(describeSchedule("0 8 * * *", "America/Mexico_City")).toBe("Diario a primera hora · 08:00 (Ciudad de México)");
    expect(describeSchedule("0 * * * *", null)).toBe("Cada hora (UTC)");
    expect(describeSchedule("0 8 * * 1", "America/Lima")).toBe("Semanal, lunes · 08:00 (Lima)");
    expect(describeSchedule("", "UTC")).toBe("Bajo demanda");
  });

  it("never shows the raw expression for custom schedules", () => {
    expect(describeSchedule("45 6 * * *", "UTC")).toBe("Diario · 06:45 (UTC)");
    expect(describeSchedule("*/15 * * * *", "Asia/Tokyo")).toBe("Programación personalizada (Asia/Tokyo)");
    expect(describeSchedule("*/15 * * * *", "Asia/Tokyo")).not.toContain("*/15");
  });
});

describe("time zones", () => {
  it("names common zones and keeps unknown ones verbatim", () => {
    expect(timeZoneName("America/Bogota")).toBe("Bogotá");
    expect(timeZoneName("Asia/Tokyo")).toBe("Asia/Tokyo");
    expect(timeZoneName("")).toBe("UTC");
  });

  it("adds the browser and current zones to the common list once", () => {
    const zones = timeZoneOptions("Asia/Tokyo", "UTC", "Asia/Tokyo", null);
    expect(zones[0]).toBe("UTC");
    expect(zones).toContain("America/Mexico_City");
    expect(zones.filter((zone) => zone === "Asia/Tokyo")).toHaveLength(1);
    expect(zones.filter((zone) => zone === "UTC")).toHaveLength(1);
  });

  it("reads the browser zone and falls back to UTC", () => {
    const original = Intl.DateTimeFormat.prototype.resolvedOptions;
    const spy = vi.spyOn(Intl.DateTimeFormat.prototype, "resolvedOptions");
    spy.mockImplementation(function (this: Intl.DateTimeFormat) {
      return { ...original.call(this), timeZone: "America/Santiago" };
    });
    expect(defaultTimeZone()).toBe("America/Santiago");
    spy.mockImplementation(function (this: Intl.DateTimeFormat) {
      return { ...original.call(this), timeZone: "" };
    });
    expect(defaultTimeZone()).toBe("UTC");
  });

  it("validates zones through Intl", () => {
    expect(isValidTimeZone("America/Mexico_City")).toBe(true);
    expect(isValidTimeZone("Mars/Base")).toBe(false);
    expect(isValidTimeZone("")).toBe(false);
  });
});

describe("customScheduleError", () => {
  it("accepts five-field expressions and aliases", () => {
    expect(customScheduleError("30 7 * * 1-5")).toBeNull();
    expect(customScheduleError("@daily")).toBeNull();
  });

  it("explains empty or malformed expressions in Spanish", () => {
    expect(customScheduleError("")).toBe("Escribe la programación o elige una de las frecuencias.");
    expect(customScheduleError("0 8 * *")).toContain("5 campos");
    expect(customScheduleError("0 8 * * * *")).toContain("5 campos");
    expect(customScheduleError("0 8 * * <script>")).toContain("5 campos");
  });
});
