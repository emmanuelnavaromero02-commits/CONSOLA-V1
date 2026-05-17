"use client";

import { useState, useMemo } from "react";
import { formatDistanceToNow } from "date-fns";
import { es } from "date-fns/locale";

import type { Conversation } from "@/lib/copilot/types";
import { cn } from "@/lib/utils";

interface Props {
  conversations:    Conversation[];
  activeId:         string | null;
  onSelect:         (id: string) => void;
  onCreate:         () => void;
  loading?:         boolean;
}

/**
 * v1.44.4 Task A — conversation list sidebar.
 *
 * Renders a scrollable list of the operator's conversations,
 * each with title + relative-time updated stamp ("hace 4 h").
 * Includes a search filter that narrows by title substring.
 *
 * Empty state: gives a clear CTA so a brand-new user sees
 * "Sin conversaciones todavía" + the "Nueva conversación"
 * button highlighted.
 *
 * The "Nueva conversación" button at the top fires onCreate
 * which calls the page's createConversationMutation. The
 * sidebar itself is stateless except for the local search
 * input — the page owns activeId and conversation selection.
 */
export function ConversationSidebar({
  conversations,
  activeId,
  onSelect,
  onCreate,
  loading,
}: Props) {
  const [query, setQuery] = useState("");

  const filtered = useMemo(() => {
    const q = query.trim().toLowerCase();
    if (!q) return conversations;
    return conversations.filter((c) =>
      (c.title ?? "").toLowerCase().includes(q),
    );
  }, [conversations, query]);

  return (
    <aside
      aria-label="Conversaciones"
      className="flex h-full w-full flex-col border-r bg-background"
    >
      <header className="flex flex-col gap-2 border-b p-3">
        <button
          type="button"
          onClick={onCreate}
          className="inline-flex min-h-[44px] items-center justify-center gap-1.5 rounded-md bg-primary px-3 text-sm font-medium text-primary-foreground shadow hover:bg-primary/90 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
        >
          <span aria-hidden>＋</span>
          Nueva conversación
        </button>
        <label className="sr-only" htmlFor="conv-search">
          Buscar conversaciones
        </label>
        <input
          id="conv-search"
          type="search"
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          placeholder="Buscar…"
          className="min-h-[36px] rounded-md border border-input bg-background px-3 text-xs focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
        />
      </header>

      <ul className="flex-1 overflow-y-auto p-2" aria-busy={loading}>
        {loading && conversations.length === 0 ? (
          <li className="space-y-1.5 px-2">
            {Array.from({ length: 4 }).map((_, i) => (
              <span
                key={i}
                className="block h-8 animate-pulse rounded bg-muted"
                aria-hidden
              />
            ))}
          </li>
        ) : null}

        {!loading && filtered.length === 0 ? (
          <li className="px-3 py-6 text-center text-xs text-muted-foreground">
            {query
              ? "Sin coincidencias para esa búsqueda."
              : "Sin conversaciones todavía. Empieza una nueva."}
          </li>
        ) : null}

        {filtered.map((c) => {
          const ts = c.updated_at || c.created_at;
          const rel = ts
            ? formatDistanceToNow(new Date(ts), { addSuffix: true, locale: es })
            : "";
          const isActive = c.id === activeId;
          return (
            <li key={c.id}>
              <button
                type="button"
                onClick={() => onSelect(c.id)}
                aria-current={isActive ? "true" : undefined}
                className={cn(
                  "block w-full rounded-md px-3 py-2 text-left text-sm transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring",
                  isActive
                    ? "bg-accent/15 font-medium"
                    : "hover:bg-accent/5",
                )}
              >
                <span className="line-clamp-1">
                  {c.title?.trim() || "Sin título"}
                </span>
                {rel ? (
                  <span className="block text-[11px] text-muted-foreground">
                    {rel}
                  </span>
                ) : null}
              </button>
            </li>
          );
        })}
      </ul>
    </aside>
  );
}
