"use client";

import { cn } from "@/lib/utils";
import type { Message as MessageType } from "@/lib/copilot/types";
import { CitationCard } from "./CitationCard";

interface Props {
  message: MessageType;
  pending?: boolean;
}

/**
 * v1.44.4 Task A — single chat message.
 *
 * Renders role-aware bubble + (optionally) citations attached
 * by the backend's run_turn response. Tool calls and tool
 * results are summarised inline as a small "[tool] name" tag
 * so the operator sees what the assistant invoked without
 * clicking into raw JSON. Pending=true renders the
 * "pensando..." placeholder while sendMutation is in flight.
 */
export function Message({ message, pending }: Props) {
  const isUser      = message.role === "user";
  const isAssistant = message.role === "assistant";
  const toolCalls   = message.tool_calls   ?? [];
  const citations   = message.citations    ?? [];

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
