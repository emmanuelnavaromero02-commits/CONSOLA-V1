"use client";

import { X } from "lucide-react";
import { useEffect, useRef, useState } from "react";

import { ChatMessages } from "@/components/workspace/ChatMessages";
import { MessageInput } from "@/components/workspace/MessageInput";
import type { Message } from "@/lib/copilot/types";
import { streamStudioChat, studioErrorMessage } from "@/lib/studio/client";

import { buttonClass } from "./ui";

interface TrailStep {
  tool: string;
  summary: string | null;
}

let messageSeq = 0;
function nextId(prefix: string): string {
  messageSeq += 1;
  return `${prefix}-${messageSeq}`;
}

export function StudioAssistant({
  cartridge,
  step,
  onClose,
}: {
  cartridge: string | null;
  step: number;
  onClose: () => void;
}) {
  const [messages, setMessages] = useState<Message[]>([]);
  const [history, setHistory] = useState<unknown[]>([]);
  const [streaming, setStreaming] = useState<string | null>(null);
  const [trail, setTrail] = useState<TrailStep[]>([]);
  const [links, setLinks] = useState<Array<{ url: string; label: string }>>([]);
  const [busy, setBusy] = useState(false);
  const abortRef = useRef<AbortController | null>(null);

  useEffect(() => () => abortRef.current?.abort(), []);

  async function send(text: string) {
    if (busy) return;
    setMessages((current) => [...current, { id: nextId("user"), role: "user", content: text }]);
    setBusy(true);
    setStreaming("");
    setTrail([]);
    setLinks([]);
    const controller = new AbortController();
    abortRef.current = controller;
    const tools: string[] = [];
    try {
      const result = await streamStudioChat(
        { message: text, history, step, cartridge_id: cartridge },
        {
          onTextDelta: (delta) => setStreaming((current) => `${current ?? ""}${delta}`),
          onToolUse: (tool) => {
            tools.push(tool);
            setTrail((current) => [...current, { tool, summary: null }]);
          },
          onToolResult: (tool, summary) =>
            setTrail((current) => {
              const index = current.findIndex((item) => item.tool === tool && item.summary === null);
              if (index === -1) return current;
              const next = [...current];
              next[index] = { tool, summary: summary || "ok" };
              return next;
            }),
        },
        { signal: controller.signal },
      );
      setHistory(result.history);
      setLinks(result.viewerUrls);
      setMessages((current) => [
        ...current,
        {
          id: nextId("assistant"),
          role: "assistant",
          content: result.reply || "(sin respuesta)",
          tool_calls: tools.map((name) => ({ name })),
        },
      ]);
    } catch (error) {
      if (controller.signal.aborted) return;
      setMessages((current) => [
        ...current,
        {
          id: nextId("assistant"),
          role: "assistant",
          content: `⚠ ${studioErrorMessage(error, "El asistente de Studio no pudo responder.")}`,
        },
      ]);
    } finally {
      if (abortRef.current === controller) abortRef.current = null;
      setBusy(false);
      setStreaming(null);
    }
  }

  return (
    <aside
      aria-label="Asistente de Studio"
      data-testid="studio-assistant"
      className="flex h-[70vh] min-h-[420px] flex-col overflow-hidden rounded-lg border bg-card shadow-sm"
    >
      <header className="flex items-center justify-between gap-2 border-b px-4 py-2">
        <div>
          <h2 className="text-sm font-semibold">Asistente de Studio</h2>
          <p className="text-xs text-muted-foreground">{cartridge ? `Cartucho ${cartridge}` : "Sin cartucho activo"}</p>
        </div>
        <button type="button" className={buttonClass} onClick={onClose} aria-label="Cerrar asistente">
          <X aria-hidden className="h-4 w-4" />
        </button>
      </header>
      {!messages.length && streaming === null ? (
        <p className="px-4 py-3 text-xs text-muted-foreground">
          Pide ayuda para revisar DAGs, entidades o datasets del cartucho activo. Las acciones que cambian datos
          requieren tu aprobación explícita.
        </p>
      ) : null}
      <ChatMessages messages={messages} streamingContent={streaming} />
      {trail.length ? (
        <ol aria-label="Razonamiento del asistente" className="max-h-32 space-y-0.5 overflow-y-auto border-t px-4 py-2 text-xs">
          {trail.map((item, index) => (
            <li key={`${item.tool}-${index}`} className="font-mono text-muted-foreground">
              {item.summary === null ? "⟳" : "✓"} {item.tool}
              {item.summary ? ` → ${item.summary}` : ""}
            </li>
          ))}
        </ol>
      ) : null}
      {links.length ? (
        <ul className="flex flex-wrap gap-2 border-t px-4 py-2 text-xs">
          {links.map((link) => (
            <li key={link.url}>
              <a href={link.url} className="underline underline-offset-2">{link.label}</a>
            </li>
          ))}
        </ul>
      ) : null}
      <MessageInput
        onSend={send}
        disabled={busy}
        placeholder="Pregunta al asistente de Studio…"
        ariaLabel="Mensaje para el asistente de Studio"
      />
    </aside>
  );
}
