// @vitest-environment jsdom

import { act, useState } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { forbiddenTermsIn } from "@/lib/glossary";

import { FrequencyPicker, type FrequencyValue } from "./FrequencyPicker";

let container: HTMLDivElement;
let root: Root;
let changes: FrequencyValue[];

function Harness({ initial }: { initial: FrequencyValue }) {
  const [value, setValue] = useState(initial);
  return (
    <FrequencyPicker
      name="freq"
      value={value}
      onChange={(next) => {
        changes.push(next);
        setValue(next);
      }}
    />
  );
}

async function render(initial: FrequencyValue) {
  await act(async () => {
    root.render(<Harness initial={initial} />);
  });
}

function option(id: string): HTMLInputElement | null {
  return container.querySelector<HTMLInputElement>(`input[name="freq"][value="${id}"]`);
}

async function click(element: Element | null | undefined) {
  expect(element, "element to click").toBeTruthy();
  await act(async () => {
    element?.dispatchEvent(new MouseEvent("click", { bubbles: true }));
  });
}

async function typeInto(element: HTMLInputElement | null, value: string) {
  expect(element).toBeTruthy();
  await act(async () => {
    Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, "value")?.set?.call(element, value);
    element?.dispatchEvent(new Event("input", { bubbles: true }));
  });
}

async function selectZone(value: string) {
  const select = container.querySelector<HTMLSelectElement>('select[name="freq_timezone"]');
  expect(select).toBeTruthy();
  await act(async () => {
    Object.getOwnPropertyDescriptor(HTMLSelectElement.prototype, "value")?.set?.call(select, value);
    select?.dispatchEvent(new Event("change", { bubbles: true }));
  });
}

function summary(): string {
  return container.querySelector("[data-frequency-summary]")?.textContent ?? "";
}

beforeEach(() => {
  (globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;
  const original = Intl.DateTimeFormat.prototype.resolvedOptions;
  vi.spyOn(Intl.DateTimeFormat.prototype, "resolvedOptions").mockImplementation(function (this: Intl.DateTimeFormat) {
    return { ...original.call(this), timeZone: "America/Bogota" };
  });
  changes = [];
  container = document.createElement("div");
  document.body.append(container);
  root = createRoot(container);
});

afterEach(async () => {
  await act(async () => root.unmount());
  container.remove();
  vi.restoreAllMocks();
});

describe("FrequencyPicker", () => {
  it("offers business frequencies and hides the expression by default", async () => {
    await render({ cron: "", timeZone: "UTC" });
    expect(option("manual")?.checked).toBe(true);
    expect(container.textContent).toContain("Bajo Demanda (solo manual)");
    expect(container.textContent).toContain("Al finalizar la jornada laboral (19:00)");
    expect(container.querySelector('input[name="freq_custom"]')).toBeNull();
    expect(container.querySelector('select[name="freq_timezone"]')).toBeNull();
    expect(summary()).toBe("Bajo demanda");
    expect(forbiddenTermsIn(container.textContent ?? "")).toEqual([]);
  });

  it("writes the preset expression and the chosen time zone", async () => {
    await render({ cron: "", timeZone: "UTC" });
    await click(option("end_of_day")?.closest("label"));
    expect(changes.at(-1)).toEqual({ cron: "0 19 * * 1-5", timeZone: "UTC" });
    await selectZone("America/Mexico_City");
    expect(changes.at(-1)).toEqual({ cron: "0 19 * * 1-5", timeZone: "America/Mexico_City" });
    expect(summary()).toBe("Al finalizar la jornada laboral · 19:00, lunes a viernes (Ciudad de México)");
    await click(option("manual")?.closest("label"));
    expect(changes.at(-1)).toEqual({ cron: "", timeZone: "America/Mexico_City" });
  });

  it("labels the browser zone and keeps an unlisted current zone", async () => {
    await render({ cron: "0 8 * * *", timeZone: "Asia/Tokyo" });
    const select = container.querySelector<HTMLSelectElement>('select[name="freq_timezone"]');
    expect(select?.value).toBe("Asia/Tokyo");
    const labels = [...(select?.options ?? [])].map((item) => item.textContent);
    expect(labels).toContain("Bogotá (este navegador)");
    expect(labels).toContain("Asia/Tokyo");
    expect(forbiddenTermsIn(container.textContent ?? "")).toEqual([]);
  });

  it("preserves an existing custom expression instead of destroying it", async () => {
    await render({ cron: "*/20 6-18 * * 1-5", timeZone: "UTC" });
    expect(option("custom")?.checked).toBe(true);
    expect(container.querySelector<HTMLInputElement>('input[name="freq_custom"]')?.value).toBe("*/20 6-18 * * 1-5");
    expect(summary()).toBe("Programación personalizada (UTC)");
    await click(option("hourly")?.closest("label"));
    expect(changes.at(-1)?.cron).toBe("0 * * * *");
    expect(container.querySelector('input[name="freq_custom"]')).toBeNull();
    await click(option("custom")?.closest("label"));
    expect(changes.at(-1)?.cron).toBe("*/20 6-18 * * 1-5");
    expect(container.querySelector<HTMLInputElement>('input[name="freq_custom"]')?.value).toBe("*/20 6-18 * * 1-5");
  });

  it("starts a new custom expression from the current preset and flags malformed input", async () => {
    await render({ cron: "0 8 * * *", timeZone: "UTC" });
    await click(option("custom")?.closest("label"));
    const input = container.querySelector<HTMLInputElement>('input[name="freq_custom"]');
    expect(input?.value).toBe("0 8 * * *");
    expect(option("custom")?.checked).toBe(true);
    await typeInto(input, "0 8 * *");
    expect(input?.getAttribute("aria-invalid")).toBe("true");
    expect(container.textContent).toContain("5 campos");
    await typeInto(input, "30 7 * * 1-5");
    expect(input?.getAttribute("aria-invalid")).toBeNull();
    expect(changes.at(-1)).toEqual({ cron: "30 7 * * 1-5", timeZone: "UTC" });
  });
});
