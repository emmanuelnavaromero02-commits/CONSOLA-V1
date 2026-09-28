"use client";

import { cn } from "@/lib/utils";
import type { Message as MessageType } from "@/lib/copilot/types";
import { CitationCard } from "./CitationCard";

interface Props {
  message: MessageType;
  pending?: boolean;
}

interface AppCardPayload {
  url: string;
  name?: string;
  title?: string;
}

const APP_VIEWER_PREFIX = "/analytics/viewer?app=";
const APP_FORGE_TOOL = "generar_app_analitica";

export function appCardFromMessage(message: MessageType): AppCardPayload | null {
  for (const result of message.tool_results ?? []) {
    const raw = typeof result?.content === "string" ? result.content : "";
    if (!raw.includes(APP_FORGE_TOOL)) continue;
    try {
      const parsed: unknown = JSON.parse(raw);
      if (!parsed || typeof parsed !== "object") continue;
      const payload = parsed as Record<string, unknown>;
      // Keyed off the tool name in the payload: only generar_app_analitica
      // results become a card, never other tools that echo viewer URLs.
      if (payload.tool !== APP_FORGE_TOOL) continue;
      const url =
        typeof payload.app_url === "string"
          ? payload.app_url
          : typeof payload.name === "string" && typeof payload.url === "string"
            ? payload.url
            : "";
      if (!url.startsWith(APP_VIEWER_PREFIX)) continue;
      return {
        url,
        name: typeof payload.name === "string" ? payload.name : undefined,
        title: typeof payload.title === "string" ? payload.title : undefined,
      };
    } catch {
      continue;
    }
  }
  return null;
}

function AppResultCard({ payload }: { payload: AppCardPayload }) {
  const label = payload.title || payload.name || "Aplicación analítica";
  return (
    <div
      data-testid="app-result-card"
      className="mt-2 flex flex-col gap-2 rounded-lg border bg-card p-3 text-card-foreground shadow-sm"
    >
      <p className="text-sm font-semibold">{label}</p>
      <p className="text-xs text-muted-foreground">
        Aplicación publicada en este workspace.
      </p>
      <a
        href={payload.url}
        target="_self"
        className="inline-flex min-h-[44px] items-center justify-center rounded-md bg-primary px-4 text-sm font-semibold text-primary-foreground hover:bg-primary/90 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
      >
        Abrir aplicación
      </a>
    </div>
  );
}

export function Message({ message, pending }: Props) {
  const isUser      = message.role === "user";
  const isAssistant = message.role === "assistant";
  const toolCalls   = message.tool_calls   ?? [];
  const citations   = message.citations    ?? [];
  const appCard     = appCardFromMessage(message);

  return (
    <article
      data-role={message.role}
      aria-label={isUser ? "Tu mensaje" : "Mensaje del copiloto"}
      className={cn(
        "flex w-full",
        isUser ? "justify-end" : "justify-start",
      )}
    >
      <div
        className={cn(
          "max-w-[85%] rounded-lg px-4 py-3 text-sm",
          isUser
            ? "bg-primary text-primary-foreground"
            : "border bg-card text-card-foreground shadow-sm",
        )}
      >
        {pending ? (
          <p className="flex items-center gap-2 text-muted-foreground">
            <span
              className="h-2 w-2 animate-pulse rounded-full bg-current"
              aria-hidden
            />
            Pensando…
          </p>
        ) : (
          <p className="whitespace-pre-wrap break-words">{message.content}</p>
        )}

        {appCard ? <AppResultCard payload={appCard} /> : null}

        {toolCalls.length > 0 && isAssistant ? (
          <ul
            aria-label="Herramientas invocadas"
            className="mt-2 flex flex-wrap gap-1.5 text-xs"
          >
            {toolCalls.map((t, i) => (
              <li
                key={i}
                className="rounded-full border border-border bg-muted/40 px-2 py-0.5 font-mono text-muted-foreground"
              >
                ⚡ {String(t.name ?? "tool")}
              </li>
            ))}
          </ul>
        ) : null}

        {citations.length > 0 && isAssistant ? (
          <div className="mt-3 space-y-1.5">
            {citations.map((c, i) => (
              <CitationCard key={i} citation={c} />
            ))}
          </div>
        ) : null}
      </div>
    </article>
  );
}
