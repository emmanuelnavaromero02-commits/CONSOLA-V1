"use client";

import { Check, ChevronDown, ChevronRight, Code2, Copy, ShieldAlert, X } from "lucide-react";
import { useEffect, useId, useRef, useState } from "react";
import { toast } from "sonner";

import { copyText } from "@/lib/clipboard";
import type { Message } from "@/lib/copilot/types";
import { splitFencedBlocks, type PendingApproval } from "@/lib/studio/assistant";
import { plural } from "@/lib/studio/format";
import { mediaMatches, REDUCED_MOTION_QUERY } from "@/lib/studio/media";
import { cn } from "@/lib/utils";

import { buttonClass, dangerButtonClass, primaryButtonClass } from "./ui";

export function CodeCard({ lang, code }: { lang: string | null; code: string }) {
  const [open, setOpen] = useState(false);
  const bodyId = useId();
  const lines = code ? code.split("\n").length : 0;

  async function copy() {
    try {
      await copyText(code);
      toast.success("Código copiado.");
    } catch {
      toast.error("No se pudo copiar el código.");
    }
  }

  return (
    <div data-testid="assistant-code-card" className="my-2 overflow-hidden rounded-md border border-zinc-800 bg-zinc-950 text-zinc-100">
      <div className="flex flex-wrap items-center justify-between gap-2 border-b border-zinc-800 bg-zinc-900/80 px-2 py-1.5">
        <span className="inline-flex items-center gap-1.5 text-[11px] font-medium text-zinc-300">
          <Code2 aria-hidden className="h-3.5 w-3.5 text-emerald-400" />
          <span className="rounded bg-zinc-800 px-1.5 py-0.5 font-mono uppercase">{lang ?? "código"}</span>
          {plural(lines, "línea", "líneas")}
        </span>
        <span className="flex gap-1">
          <button
            type="button"
            onClick={copy}
            className="inline-flex min-h-[32px] items-center gap-1 rounded px-2 text-[11px] font-medium text-zinc-200 hover:bg-zinc-800 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
          >
            <Copy aria-hidden className="h-3.5 w-3.5" /> Copiar
          </button>
          <button
            type="button"
            aria-expanded={open}
            aria-controls={bodyId}
            onClick={() => setOpen((value) => !value)}
            className="inline-flex min-h-[32px] items-center gap-1 rounded px-2 text-[11px] font-medium text-zinc-200 hover:bg-zinc-800 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
          >
            {open ? <ChevronDown aria-hidden className="h-3.5 w-3.5" /> : <ChevronRight aria-hidden className="h-3.5 w-3.5" />}
            {open ? "Ocultar código" : "Ver código"}
          </button>
        </span>
      </div>
      <div id={bodyId} hidden={!open}>
        {open ? (
          <pre className="max-h-72 overflow-auto p-3 font-mono text-[11px] leading-5">
            <code>{code}</code>
          </pre>
        ) : null}
      </div>
    </div>
  );
}

export function StudioMessage({ message, pending }: { message: Message; pending?: boolean }) {
  const isUser = message.role === "user";
  const tools = message.tool_calls ?? [];
  const segments = isUser || pending ? [] : splitFencedBlocks(message.content ?? "");
  return (
    <article
      data-role={message.role}
      aria-label={isUser ? "Tu mensaje" : "Mensaje del asistente"}
      className={cn("flex w-full", isUser ? "justify-end" : "justify-start")}
    >
      <div
        className={cn(
          "max-w-[92%] rounded-lg px-3 py-2 text-sm",
          isUser ? "bg-primary text-primary-foreground" : "border bg-card text-card-foreground shadow-sm",
        )}
      >
        {pending ? (
          <p className="flex items-center gap-2 text-muted-foreground">
            <span className="h-2 w-2 rounded-full bg-current motion-safe:animate-pulse" aria-hidden />
            Pensando…
          </p>
        ) : isUser ? (
          <p className="whitespace-pre-wrap break-words">{message.content}</p>
        ) : (
          segments.map((segment, index) =>
            segment.kind === "code" ? (
              <CodeCard key={index} lang={segment.lang} code={segment.code} />
            ) : (
              <p key={index} className="whitespace-pre-wrap break-words">
                {segment.text}
              </p>
            ),
          )
        )}
        {tools.length && !isUser ? (
          <ul aria-label="Herramientas invocadas" className="mt-2 flex flex-wrap gap-1.5 text-xs">
            {tools.map((tool, index) => (
              <li key={index} className="rounded-full border bg-muted/40 px-2 py-0.5 font-mono text-muted-foreground">
                ⚡ {String(tool.name ?? "herramienta")}
              </li>
            ))}
          </ul>
        ) : null}
      </div>
    </article>
  );
}

export function StudioChatLog({ messages, streamingContent }: { messages: Message[]; streamingContent: string | null }) {
  const logRef = useRef<HTMLDivElement | null>(null);

  useEffect(() => {
    const log = logRef.current;
    if (!log) return;
    const behavior: ScrollBehavior = mediaMatches(REDUCED_MOTION_QUERY, false) ? "auto" : "smooth";
    if (typeof log.scrollTo === "function") log.scrollTo({ top: log.scrollHeight, behavior });
    else log.scrollTop = log.scrollHeight;
  }, [messages.length, streamingContent]);

  return (
    <div
      ref={logRef}
      role="log"
      aria-live="polite"
      aria-label="Mensajes de la conversación"
      data-testid="chat-messages"
      className="min-h-0 flex-1 space-y-3 overflow-y-auto p-4"
    >
      {messages.map((message) => (
        <StudioMessage key={message.id} message={message} />
      ))}
      {streamingContent !== null ? (
        streamingContent.length ? (
          <article data-role="assistant" aria-label="Mensaje del asistente" className="flex w-full justify-start">
            <div className="max-w-[92%] rounded-lg border bg-card px-3 py-2 text-sm shadow-sm">
              <p className="whitespace-pre-wrap break-words">{streamingContent}</p>
            </div>
          </article>
        ) : (
          <StudioMessage message={{ id: "__streaming__", role: "assistant", content: "" }} pending />
        )
      ) : null}
    </div>
  );
}

export function ApprovalCard({
  approval,
  disabled,
  onApprove,
  onReject,
}: {
  approval: PendingApproval;
  disabled: boolean;
  onApprove: () => void;
  onReject: () => void;
}) {
  return (
    <section
      aria-label="Cambio pendiente de aprobación"
      data-testid="assistant-approval-card"
      className="space-y-2 rounded-md border border-warning/40 bg-warning/10 p-3 text-xs"
    >
      <p className="flex items-center gap-1.5 text-sm font-semibold">
        <ShieldAlert aria-hidden className="h-4 w-4 text-warning" /> Cambio pendiente de aprobación
      </p>
      <dl className="grid grid-cols-[auto_minmax(0,1fr)] gap-x-3 gap-y-1">
        {approval.tool ? (
          <>
            <dt className="text-muted-foreground">Herramienta</dt>
            <dd className="break-all font-mono">{approval.tool}</dd>
          </>
        ) : null}
        {approval.riskLevel ? (
          <>
            <dt className="text-muted-foreground">Riesgo</dt>
            <dd>{approval.riskLevel}</dd>
          </>
        ) : null}
        {approval.stepId !== null ? (
          <>
            <dt className="text-muted-foreground">Paso</dt>
            <dd>Paso {String(approval.stepId)}</dd>
          </>
        ) : null}
        {approval.reason ? (
          <>
            <dt className="text-muted-foreground">Motivo</dt>
            <dd className="break-words">{approval.reason}</dd>
          </>
        ) : null}
      </dl>
      <div className="flex flex-wrap gap-2">
        <button type="button" className={primaryButtonClass} disabled={disabled} onClick={onApprove}>
          <Check aria-hidden className="h-4 w-4" /> Aplicar cambio
        </button>
        <button type="button" className={cn(dangerButtonClass)} disabled={disabled} onClick={onReject}>
          <X aria-hidden className="h-4 w-4" /> Rechazar
        </button>
      </div>
    </section>
  );
}

export const quickPromptButtonClass = cn(
  buttonClass,
  "min-h-[36px] rounded-full border-primary/30 bg-primary/5 px-3 py-1 text-left text-xs font-normal text-foreground hover:bg-primary/10",
);
