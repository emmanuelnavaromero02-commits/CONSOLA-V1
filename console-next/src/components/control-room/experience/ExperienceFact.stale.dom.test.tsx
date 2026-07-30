// @vitest-environment jsdom

import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type { ExperienceFactV2 } from "@/lib/control-room/experience-contract";

import { ExperienceFact } from "./ExperienceFact";

(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

let container: HTMLDivElement;
let root: Root;

function makeFact(stale: boolean | null): ExperienceFactV2 {
  return {
    kind: "kpi",
    title: "Cobertura crítica",
    severity: "low",
    observed_at: "2026-07-24T00:00:00Z",
    stale,
    actions: [],
  } as ExperienceFactV2;
}

async function renderFact(stale: boolean | null) {
  await act(async () => {
    root.render(<ExperienceFact fact={makeFact(stale)} onPreviewAction={vi.fn()} />);
  });
}

beforeEach(() => {
  container = document.createElement("div");
  document.body.append(container);
  root = createRoot(container);
});

afterEach(async () => {
  await act(async () => root.unmount());
  container.remove();
});

describe("ExperienceFact: frescura nunca es vigente por omisión", () => {
  it("stale desconocido (null) se muestra explícitamente, no como vigente", async () => {
    await renderFact(null);

    expect(container.textContent).toContain("Frescura no informada");
    expect(container.textContent).not.toContain("Información anterior");
  });

  it("stale true muestra el aviso de información anterior", async () => {
    await renderFact(true);

    expect(container.textContent).toContain("Información anterior");
    expect(container.textContent).not.toContain("Frescura no informada");
  });

  it("stale false (vigencia confirmada por el backend) no muestra avisos", async () => {
    await renderFact(false);

    expect(container.textContent).not.toContain("Información anterior");
    expect(container.textContent).not.toContain("Frescura no informada");
  });
});
