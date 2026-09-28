// @vitest-environment jsdom

import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it } from "vitest";

import type { Message as MessageType } from "@/lib/copilot/types";

import { Message, appCardFromMessage } from "./Message";

let container: HTMLDivElement;
let root: Root;

beforeEach(() => {
  container = document.createElement("div");
  document.body.appendChild(container);
  root = createRoot(container);
});

afterEach(() => {
  act(() => root.unmount());
  container.remove();
});

function render(message: MessageType) {
  act(() => {
    root.render(<Message message={message} />);
  });
}

const appResult = JSON.stringify({
  published: true,
  name: "ventas_semana",
  title: "Ventas de la semana",
  url: "/analytics/viewer?app=ventas_semana",
  app_url: "/analytics/viewer?app=ventas_semana",
});

describe("Message app-result card", () => {
  it("renders a card with an open button for a generar_app_analitica result", () => {
    render({
      id: "m1",
      role: "tool",
      content: "",
      tool_results: [{ tool_use_id: "t1", content: appResult }],
    });
    const card = container.querySelector('[data-testid="app-result-card"]');
    expect(card).not.toBeNull();
    expect(card?.textContent).toContain("Ventas de la semana");
    const link = card?.querySelector("a");
    expect(link?.getAttribute("href")).toBe("/analytics/viewer?app=ventas_semana");
    expect(link?.getAttribute("target")).toBe("_self");
    expect(link?.textContent).toContain("Abrir aplicación");
  });

  it("accepts a payload that only carries app_url", () => {
    const payload = appCardFromMessage({
      id: "m2",
      role: "tool",
      content: "",
      tool_results: [
        {
          tool_use_id: "t1",
          content: JSON.stringify({ app_url: "/analytics/viewer?app=otra" }),
        },
      ],
    });
    expect(payload?.url).toBe("/analytics/viewer?app=otra");
  });

  it("ignores results that do not point at the analytics viewer", () => {
    render({
      id: "m3",
      role: "tool",
      content: "",
      tool_results: [
        {
          tool_use_id: "t1",
          content: JSON.stringify({ name: "x", url: "https://evil.example/app" }),
        },
        { tool_use_id: "t2", content: "texto plano sin JSON" },
      ],
    });
    expect(container.querySelector('[data-testid="app-result-card"]')).toBeNull();
  });

  it("keeps plain text rendering for ordinary messages", () => {
    render({ id: "m4", role: "assistant", content: "hola" });
    expect(container.textContent).toContain("hola");
    expect(container.querySelector('[data-testid="app-result-card"]')).toBeNull();
  });
});
