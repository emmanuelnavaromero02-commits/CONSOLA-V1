"use client";

import { SquareTerminal } from "lucide-react";
import { useRef } from "react";

import { plural } from "@/lib/studio/format";

const LINE_HEIGHT_PX = 20;

export function CodeEditor({
  id,
  name,
  value,
  onChange,
  rows = 12,
  placeholder,
}: {
  id: string;
  name: string;
  value: string;
  onChange: (value: string) => void;
  rows?: number;
  placeholder?: string;
}) {
  const gutterRef = useRef<HTMLDivElement | null>(null);
  const lines = Math.max(1, value.split("\n").length);
  return (
    <div className="overflow-hidden rounded-md border border-zinc-800 bg-zinc-950 text-zinc-100 shadow-inner focus-within:ring-2 focus-within:ring-ring">
      <div className="flex items-center justify-between gap-2 border-b border-zinc-800 bg-zinc-900/80 px-3 py-1.5 text-[11px] text-zinc-400">
        <span className="inline-flex items-center gap-1.5 font-medium">
          <SquareTerminal aria-hidden className="h-3.5 w-3.5 text-emerald-400" />
          Consola SQL · {plural(lines, "línea", "líneas")}
        </span>
        <span aria-hidden className="flex gap-1">
          <span className="h-2 w-2 rounded-full bg-zinc-700" />
          <span className="h-2 w-2 rounded-full bg-zinc-700" />
          <span className="h-2 w-2 rounded-full bg-emerald-500/70" />
        </span>
      </div>
      <div className="flex font-mono text-xs leading-5">
        <div className="relative w-12 shrink-0 border-r border-zinc-800 bg-zinc-900/60">
          <div
            ref={gutterRef}
            aria-hidden
            data-testid="sql-gutter"
            className="absolute inset-0 select-none overflow-hidden py-3 pr-2 text-right text-zinc-500"
          >
            {Array.from({ length: lines }, (_, index) => (
              <div key={index}>{index + 1}</div>
            ))}
          </div>
        </div>
        <textarea
          id={id}
          name={name}
          value={value}
          onChange={(event) => onChange(event.target.value)}
          onScroll={(event) => {
            if (gutterRef.current) gutterRef.current.scrollTop = event.currentTarget.scrollTop;
          }}
          rows={rows}
          wrap="off"
          spellCheck={false}
          autoCapitalize="off"
          autoCorrect="off"
          placeholder={placeholder}
          style={{ minHeight: `${rows * LINE_HEIGHT_PX + 24}px` }}
          className="min-w-0 flex-1 resize-y overflow-auto whitespace-pre bg-transparent px-3 py-3 text-zinc-100 caret-emerald-400 placeholder:text-zinc-500 focus:outline-none"
        />
      </div>
    </div>
  );
}
