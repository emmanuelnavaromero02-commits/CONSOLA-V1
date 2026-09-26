"use client";

import { Sparkles, X } from "lucide-react";
import { useEffect, useRef, useState } from "react";

import { MessageInput } from "@/components/workspace/MessageInput";
import type { Message } from "@/lib/copilot/types";
import { approvalMessage, pendingApprovals, rejectionMessage, type PendingApproval } from "@/lib/studio/assistant";
import { streamStudioChat, studioErrorMessage } from "@/lib/studio/client";
import { quickPromptsForStep, sectionForStep } from "@/lib/studio/sections";
import type { StudioManifest } from "@/lib/studio/types";

import { ApprovalCard, quickPromptButtonClass, StudioChatLog } from "./AssistantMessages";
import { buttonClass, ConfirmDialog } from "./ui";

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
  manifest,
}: {
  cartridge: string | null;
  step: number;
  onClose: () => void;
  manifest?: StudioManifest | null;
}) {
  const [messages, setMessages] = useState<Message[]>([]);
  const [history, setHistory] = useState<unknown[]>([]);
  const [streaming, setStreaming] = useState<string | null>(null);
  const [trail, setTrail] = useState<TrailStep[]>([]);
  const [links, setLinks] = useState<Array<{ url: string; label: string }>>([]);
  const [approvals, setApprovals] = useState<PendingApproval[]>([]);
  const [confirming, setConfirming] = useState<PendingApproval | null>(null);
  const [busy, setBusy] = useState(false);
  const abortRef = useRef<AbortController | null>(null);
  const section = sectionForStep(step);
  const prompts = quickPromptsForStep(step, manifest);

  useEffect(() => () => abortRef.current?.abort(), []);

  async function send(text: string) {
    if (busy) return;
    setMessages((current) => [...current, { id: nextId("user"), role: "user", content: text }]);
    setBusy(true);
    setStreaming("");
    setTrail([]);
    setLinks([]);
    setApprovals([]);
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
      setApprovals(pendingApprovals(result.history.slice(history.length)));
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

  function approve() {
    const approval = confirming;
    setConfirming(null);
    if (approval) void send(approvalMessage(approval));
  }

  return (
    <aside
      aria-label="Asistente de Studio"
      data-testid="studio-assistant"
      className="flex h-[70vh] min-h-[420px] flex-col overflow-hidden rounded-xl border bg-card shadow-sm"
    >
      <header className="flex items-start justify-between gap-2 border-b px-4 py-2">
        <div className="min-w-0 space-y-1">
          <h2 className="flex items-center gap-1.5 text-sm font-semibold">
            <Sparkles aria-hidden className="h-4 w-4 text-primary" /> Asistente de Studio
          </h2>
          <p className="flex flex-wrap items-center gap-1.5 text-xs text-muted-foreground">
            <span>{cartridge ? `Cartucho ${cartridge}` : "Sin cartucho activo"}</span>
            <span className="rounded-full border border-primary/30 bg-primary/5 px-2 py-0.5 text-[11px] font-medium text-primary">
              Sección: {section.label}
            </span>
          </p>
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
      <StudioChatLog messages={messages} streamingContent={streaming} />
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
      {approvals.length ? (
        <div className="max-h-64 space-y-2 overflow-y-auto border-t px-4 py-2">
          {approvals.map((approval) => (
            <ApprovalCard
              key={approval.approvalKey}
              approval={approval}
              disabled={busy}
              onApprove={() => setConfirming(approval)}
              onReject={() => void send(rejectionMessage(approval))}
            />
          ))}
        </div>
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
      {prompts.length ? (
        <ul aria-label="Preguntas sugeridas" className="flex flex-wrap gap-1.5 border-t px-3 py-2">
          {prompts.map((prompt) => (
            <li key={prompt}>
              <button type="button" className={quickPromptButtonClass} disabled={busy} onClick={() => void send(prompt)}>
                {prompt}
              </button>
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
      <ConfirmDialog
        open={confirming !== null}
        title="Aplicar cambio propuesto por el asistente"
        confirmLabel="Aprobar y enviar"
        onConfirm={approve}
        onCancel={() => setConfirming(null)}
        testId="assistant-approval-dialog"
        description={
          confirming ? (
            <div className="space-y-2">
              <p>Se enviará este mensaje al asistente:</p>
              <code className="block break-all rounded-md border bg-muted px-2 py-1.5 font-mono text-xs text-foreground">
                {approvalMessage(confirming)}
              </code>
              <p>
                El servidor solo ejecuta el paso si la clave coincide con el paso que está esperando aprobación; si no
                coincide, lo rechaza y no cambia nada.
              </p>
            </div>
          ) : null
        }
      />
    </aside>
  );
}
