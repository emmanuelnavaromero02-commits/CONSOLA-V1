"use client";

import { useEffect, useMemo, useRef, useState } from "react";

export interface SlashCommand {
  /** Token typed after "/", lowercase, no spaces. */
  id:          string;
  /** Human-readable label rendered in the palette. */
  label:       string;
  /** Short description shown below the label. */
  description: string;
  /** Group header — palette renders commands grouped by this. */
  group:       "Básicos" | "Reportes";
  /** Fired when the user picks the command. The page decides
   *  whether to send a prompt, open a drawer, etc. */
  onSelect:    () => void;
}

interface Props {
  open:     boolean;
  /** Initial query — typically "/" when triggered by the input. */
  query?:   string;
  commands: SlashCommand[];
  onClose:  () => void;
}

/**
 * v1.44.4 Task A — slash-command palette.
 *
 * Lightweight cmdk-like surface implemented inline (no cmdk
 * dependency). Filters the registered commands by a fuzzy
 * substring match, groups them under "Básicos" / "Reportes"
 * sections, and dispatches the selection through ``onSelect``.
 *
 * Accessibility:
 *   - role=dialog + aria-modal,
 *   - the input keeps focus after each filter pass,
 *   - Arrow Up/Down + Enter for keyboard nav,
 *   - Escape closes via onClose,
 *   - Each result has role=option + aria-selected on the
 *     active index.
 *
 * Why inline and not cmdk: keeping the dependency surface
 * small until shadcn/cmdk lands a polished, accessible
 * primitive into this codebase. The palette logic here is
 * intentionally tiny so a future replacement is mechanical.
 */
export function SlashCommandsPalette({
  open,
  query,
  commands,
  onClose,
}: Props) {
  const inputRef     = useRef<HTMLInputElement | null>(null);
  const [search, setSearch]     = useState(query ?? "");
  const [activeIdx, setActiveIdx] = useState(0);

  // Re-seed the search when the palette opens with a fresh
  // query (e.g. when triggered by the input typing "/").
  useEffect(() => {
    if (open) {
      setSearch(query ?? "");
      setActiveIdx(0);
      // Defer focus to next tick so the input is mounted.
      requestAnimationFrame(() => inputRef.current?.focus());
    }
  }, [open, query]);

  const filtered = useMemo(() => {
    const q = search.replace(/^\/+/, "").trim().toLowerCase();
    if (!q) return commands;
    return commands.filter((c) =>
      c.id.includes(q) ||
      c.label.toLowerCase().includes(q) ||
      c.description.toLowerCase().includes(q),
    );
  }, [search, commands]);

  // Group the filtered list, preserving registration order
  // within each group.
  const grouped = useMemo(() => {
    const out: Record<string, SlashCommand[]> = {};
    for (const cmd of filtered) {
      (out[cmd.group] = out[cmd.group] ?? []).push(cmd);
    }
    return out;
  }, [filtered]);

  function pick(idx: number) {
    const cmd = filtered[idx];
    if (!cmd) return;
    cmd.onSelect();
    onClose();
  }

  function handleKey(e: React.KeyboardEvent<HTMLInputElement>) {
    if (e.key === "ArrowDown") {
      e.preventDefault();
      setActiveIdx((i) => Math.min(i + 1, Math.max(filtered.length - 1, 0)));
      return;
    }
    if (e.key === "ArrowUp") {
      e.preventDefault();
      setActiveIdx((i) => Math.max(i - 1, 0));
      return;
    }
    if (e.key === "Enter") {
      e.preventDefault();
      pick(activeIdx);
      return;
    }
    if (e.key === "Escape") {
      e.preventDefault();
      onClose();
    }
  }

  if (!open) return null;

  // Flat-index helper so the per-group rendering knows which
  // row in the global filtered list it represents.
  let flatIdx = -1;

  return (
    <div
      role="dialog"
      aria-modal="true"
      aria-label="Paleta de comandos"
      className="fixed inset-0 z-50 flex items-start justify-center bg-black/40 p-4 pt-[10vh]"
      onClick={(e) => {
        if (e.target === e.currentTarget) onClose();
      }}
    >
      <div className="w-full max-w-lg overflow-hidden rounded-lg border bg-card shadow-xl">
        <input
          ref={inputRef}
          type="text"
          value={search}
          onChange={(e) => {
            setSearch(e.target.value);
            setActiveIdx(0);
          }}
          onKeyDown={handleKey}
          aria-label="Buscar comando"
          placeholder="Buscar comando…"
          className="w-full border-b bg-transparent px-4 py-3 text-sm focus-visible:outline-none"
        />

        <div
          role="listbox"
          aria-label="Comandos disponibles"
          className="max-h-[60vh] overflow-y-auto p-2"
        >
          {filtered.length === 0 ? (
            <p className="px-3 py-6 text-center text-sm text-muted-foreground">
              Sin coincidencias.
            </p>
          ) : (
            Object.entries(grouped).map(([group, list]) => (
              <div key={group} className="mb-2 last:mb-0">
                <p className="px-2 pb-1 pt-2 text-[10px] font-semibold uppercase tracking-wider text-muted-foreground">
                  {group}
                </p>
                <ul>
                  {list.map((cmd) => {
                    flatIdx += 1;
                    const isActive = flatIdx === activeIdx;
                    return (
                      <li
                        key={cmd.id}
                        role="option"
                        aria-selected={isActive}
                        onMouseEnter={() => setActiveIdx(flatIdx)}
                        onClick={() => pick(flatIdx)}
                        className={
                          "cursor-pointer rounded-md px-3 py-2 text-sm " +
                          (isActive ? "bg-accent/10" : "")
                        }
                      >
                        <p className="font-mono text-xs text-muted-foreground">
                          /{cmd.id}
                        </p>
                        <p className="font-medium">{cmd.label}</p>
                        <p className="text-xs text-muted-foreground">
                          {cmd.description}
                        </p>
                      </li>
                    );
                  })}
                </ul>
              </div>
            ))
          )}
        </div>
      </div>
    </div>
  );
}
