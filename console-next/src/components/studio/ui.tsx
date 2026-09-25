"use client";

import { Loader2 } from "lucide-react";
import { useEffect, useId, useRef, type ReactNode } from "react";

import { cn } from "@/lib/utils";

export const buttonClass = cn(
  "inline-flex min-h-[44px] items-center justify-center gap-2 rounded-md border bg-background px-3 text-sm font-medium",
  "hover:bg-accent/5 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring",
  "disabled:cursor-not-allowed disabled:opacity-60",
);

export const primaryButtonClass = cn(
  "inline-flex min-h-[44px] items-center justify-center gap-2 rounded-md bg-primary px-4 text-sm font-medium text-primary-foreground",
  "hover:bg-primary/90 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring",
  "disabled:cursor-not-allowed disabled:opacity-60",
);

export const dangerButtonClass = cn(
  "inline-flex min-h-[44px] items-center justify-center gap-2 rounded-md border border-destructive/40 bg-background px-3 text-sm font-medium text-destructive",
  "hover:bg-destructive/10 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-destructive/40",
  "disabled:cursor-not-allowed disabled:opacity-60",
);

export const inputClass = cn(
  "min-h-[44px] w-full rounded-md border bg-background px-3 text-sm",
  "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring disabled:cursor-not-allowed disabled:opacity-60",
);

export const codeAreaClass = cn(
  "w-full rounded-md border bg-background px-3 py-3 font-mono text-xs leading-5",
  "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring disabled:opacity-70",
);

export function Spinner({ className }: { className?: string }) {
  return <Loader2 aria-hidden className={cn("h-4 w-4 animate-spin", className)} />;
}

export function Notice({
  tone = "muted",
  title,
  children,
  action,
  testId,
}: {
  tone?: "muted" | "warning" | "error" | "success";
  title?: string;
  children?: ReactNode;
  action?: ReactNode;
  testId?: string;
}) {
  return (
    <div
      role={tone === "error" ? "alert" : "status"}
      data-testid={testId}
      className={cn(
        "rounded-md border p-3 text-sm",
        tone === "muted" && "bg-muted/30 text-muted-foreground",
        tone === "warning" && "border-warning/30 bg-warning/10 text-foreground",
        tone === "error" && "border-destructive/30 bg-destructive/5 text-destructive",
        tone === "success" && "border-success/30 bg-success/10 text-foreground",
      )}
    >
      <div className="flex flex-col gap-2 sm:flex-row sm:items-start sm:justify-between">
        <div className="min-w-0 space-y-1">
          {title ? <p className="font-medium">{title}</p> : null}
          {children ? <div className="break-words text-xs sm:text-sm">{children}</div> : null}
        </div>
        {action ? <div className="shrink-0">{action}</div> : null}
      </div>
    </div>
  );
}

export function formatCell(value: unknown): string {
  if (value === null || value === undefined) return "";
  if (typeof value === "string") return value;
  if (typeof value === "number" || typeof value === "boolean") return String(value);
  try {
    return JSON.stringify(value);
  } catch {
    return String(value);
  }
}

export function DataTable({
  columns,
  rows,
  caption,
  maxHeight = "max-h-[420px]",
}: {
  columns: string[];
  rows: Array<Record<string, unknown>>;
  caption?: string;
  maxHeight?: string;
}) {
  const keys = columns.length
    ? columns
    : [...new Set(rows.slice(0, 25).flatMap((row) => Object.keys(row)))];
  return (
    <div className={cn("overflow-auto rounded-md border", maxHeight)}>
      <table className="min-w-full divide-y text-xs">
        {caption ? <caption className="sr-only">{caption}</caption> : null}
        <thead className="sticky top-0 bg-muted text-left uppercase text-muted-foreground">
          <tr>
            {keys.map((key) => (
              <th key={key} scope="col" className="whitespace-nowrap px-3 py-2 font-medium">{key}</th>
            ))}
          </tr>
        </thead>
        <tbody className="divide-y font-mono">
          {rows.map((row, index) => (
            <tr key={index}>
              {keys.map((key) => {
                const text = formatCell(row[key]);
                return (
                  <td key={key} title={text} className="max-w-[260px] truncate whitespace-nowrap px-3 py-1.5">
                    {text}
                  </td>
                );
              })}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

export function ConfirmDialog({
  open,
  title,
  description,
  children,
  confirmLabel,
  pendingLabel = "Procesando…",
  tone = "primary",
  pending = false,
  confirmDisabled = false,
  onConfirm,
  onCancel,
  testId,
}: {
  open: boolean;
  title: string;
  description?: ReactNode;
  children?: ReactNode;
  confirmLabel: string;
  pendingLabel?: string;
  tone?: "primary" | "danger";
  pending?: boolean;
  confirmDisabled?: boolean;
  onConfirm: () => void;
  onCancel: () => void;
  testId?: string;
}) {
  const titleId = useId();
  const cancelRef = useRef<HTMLButtonElement | null>(null);
  const dialogRef = useRef<HTMLDivElement | null>(null);
  const pendingRef = useRef(pending);
  const cancelHandler = useRef(onCancel);

  useEffect(() => {
    pendingRef.current = pending;
    cancelHandler.current = onCancel;
  });

  useEffect(() => {
    if (!open) return;
    const previous = document.activeElement instanceof HTMLElement ? document.activeElement : null;
    cancelRef.current?.focus();

    function onKey(event: KeyboardEvent) {
      if (event.key === "Escape") {
        if (!pendingRef.current) cancelHandler.current();
        return;
      }
      if (event.key !== "Tab") return;
      const focusable = [
        ...(dialogRef.current?.querySelectorAll<HTMLElement>(
          'button:not([disabled]), [href], input:not([disabled]), select:not([disabled]), textarea:not([disabled]), [tabindex]:not([tabindex="-1"])',
        ) ?? []),
      ];
      if (!focusable.length) return;
      const first = focusable[0];
      const last = focusable[focusable.length - 1];
      if (event.shiftKey && document.activeElement === first) {
        event.preventDefault();
        last.focus();
      } else if (!event.shiftKey && document.activeElement === last) {
        event.preventDefault();
        first.focus();
      }
    }
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("keydown", onKey);
      previous?.focus();
    };
  }, [open]);

  if (!open) return null;

  return (
    <div
      role="dialog"
      aria-modal="true"
      aria-labelledby={titleId}
      data-testid={testId}
      className="fixed inset-0 z-50 flex items-center justify-center p-4"
    >
      <div
        aria-hidden
        className="absolute inset-0 bg-black/50"
        onClick={() => {
          if (!pending) onCancel();
        }}
      />
      <div
        ref={dialogRef}
        tabIndex={-1}
        className="relative max-h-[90vh] w-full max-w-lg overflow-y-auto rounded-lg border bg-card p-6 shadow-lg focus-visible:outline-none"
      >
        <h2 id={titleId} className="text-lg font-semibold tracking-tight">{title}</h2>
        {description ? <div className="mt-2 text-sm text-muted-foreground">{description}</div> : null}
        {children ? <div className="mt-4 space-y-3 text-sm">{children}</div> : null}
        <div className="mt-6 flex flex-col-reverse gap-2 sm:flex-row sm:justify-end">
          <button ref={cancelRef} type="button" onClick={onCancel} disabled={pending} className={buttonClass}>
            Cancelar
          </button>
          <button
            type="button"
            onClick={onConfirm}
            disabled={pending || confirmDisabled}
            className={tone === "danger"
              ? cn(dangerButtonClass, "bg-destructive text-destructive-foreground hover:bg-destructive/90")
              : primaryButtonClass}
          >
            {pending ? <Spinner /> : null}
            {pending ? pendingLabel : confirmLabel}
          </button>
        </div>
      </div>
    </div>
  );
}
