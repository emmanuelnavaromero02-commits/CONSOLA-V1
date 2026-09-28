// @vitest-environment jsdom

import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type { SendMessageResponse } from "@/lib/copilot/types";

const clientMock = vi.hoisted(() => ({
  createConversation: vi.fn(),
  getConversation: vi.fn(),
  streamMessage: vi.fn(),
  approveAction: vi.fn(),
}));

vi.mock("@/lib/copilot/client", () => clientMock);

vi.mock("@/lib/copilot/page-context", () => ({
  readPageContext: () => ({ surface: "decisions", active_tab: "consejo" }),
}));

import { CopilotDockPanel } from "./CopilotDockPanel";

const RESPONSE: SendMessageResponse = {
  message_id: "m1",
  reply: "Hola, esto veo en tu pantalla.",
  tool_calls: [],
  tool_results: [],
  citations: [],
  pending_actions: [],
  requires_approval: false,
};

let container: HTMLDivElement;
let root: Root;

async function render() {
  await act(async () => {
    root.render(<CopilotDockPanel route="/decisions" />);
  });
}

async function send(text: string) {
  const textarea = container.querySelector<HTMLTextAreaElement>("textarea");
  await act(async () => {
    Object.getOwnPropertyDescriptor(HTMLTextAreaElement.prototype, "value")?.set?.call(textarea, text);
    textarea?.dispatchEvent(new Event("input", { bubbles: true }));
  });
  await act(async () => {
    container.querySelector("form")?.dispatchEvent(new Event("submit", { bubbles: true, cancelable: true }));
  });
}

function apiError(status: number): Error {
  const error = new Error(`HTTP ${status}`) as Error & { status: number };
  error.status = status;
  return error;
}

beforeEach(() => {
  (globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;
  window.sessionStorage.clear();
  vi.clearAllMocks();
  clientMock.createConversation.mockResolvedValue({ id: "conv-1", title: null });
  clientMock.streamMessage.mockResolvedValue(RESPONSE);
  container = document.createElement("div");
  document.body.append(container);
  root = createRoot(container);
});

afterEach(async () => {
  await act(async () => root.unmount());
  container.remove();
});

describe("CopilotDockPanel", () => {
  it("sends the message with route, title and published page context", async () => {
    document.title = "Decisiones · OMEGA";
    await render();
    await send("¿qué decisiones hay abiertas?");

    expect(clientMock.createConversation).toHaveBeenCalledTimes(1);
    expect(clientMock.streamMessage).toHaveBeenCalledTimes(1);
    const [cid, text, , pageContext] = clientMock.streamMessage.mock.calls[0];
    expect(cid).toBe("conv-1");
    expect(text).toBe("¿qué decisiones hay abiertas?");
    expect(pageContext).toEqual({
      route: "/decisions",
      title: "Decisiones · OMEGA",
      surface: "decisions",
      active_tab: "consejo",
    });
    expect(window.sessionStorage.getItem("omega-copilot-dock-conversation")).toBe("conv-1");
    expect(container.textContent).toContain("Hola, esto veo en tu pantalla.");
  });

  it("renders streamed tokens while the reply arrives", async () => {
    let resolveStream: (value: SendMessageResponse) => void = () => undefined;
    clientMock.streamMessage.mockImplementation(async (_cid, _text, handlers) => {
      handlers?.onToken?.("Analizando");
      return new Promise<SendMessageResponse>((resolve) => {
        resolveStream = resolve;
      });
    });

    await render();
    await send("hola");
    expect(container.textContent).toContain("Analizando");

    await act(async () => {
      resolveStream(RESPONSE);
    });
    expect(container.textContent).toContain("Hola, esto veo en tu pantalla.");
  });

  it("clears a stale stored conversation on 403 and shows the failure", async () => {
    window.sessionStorage.setItem("omega-copilot-dock-conversation", "conv-old");
    clientMock.getConversation.mockRejectedValue(apiError(403));
    await render();
    await act(async () => Promise.resolve());
    expect(window.sessionStorage.getItem("omega-copilot-dock-conversation")).toBeNull();
  });

  it("shows an honest offline message on 5xx", async () => {
    clientMock.streamMessage.mockRejectedValue(apiError(502));
    await render();
    await send("hola");
    expect(container.textContent).toContain("Sin conexión al asistente");
  });

  it("persists the draft to sessionStorage", async () => {
    await render();
    const textarea = container.querySelector<HTMLTextAreaElement>("textarea");
    await act(async () => {
      Object.getOwnPropertyDescriptor(HTMLTextAreaElement.prototype, "value")?.set?.call(textarea, "borrador");
      textarea?.dispatchEvent(new Event("input", { bubbles: true }));
    });
    expect(window.sessionStorage.getItem("omega-copilot-dock-draft")).toBe("borrador");
  });

  it("opens the approval gate for pending actions", async () => {
    clientMock.streamMessage.mockResolvedValue({
      ...RESPONSE,
      requires_approval: true,
      pending_actions: [{ tool: "postgres_execute", risk_level: "destructive" }],
    });
    await render();
    await send("borra eso");
    expect(container.textContent).toContain("Confirma esta acción");
  });
});
