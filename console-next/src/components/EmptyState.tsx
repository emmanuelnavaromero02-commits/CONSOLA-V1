"use client";

import Link from "next/link";
import { useId } from "react";
import type { LucideIcon } from "lucide-react";

import { cn } from "@/lib/utils";

export interface EmptyStateAction {
  label: string;
  href?: string;
  onClick?: () => void;
}

export interface EmptyStateProps {
  icon: LucideIcon;
  title: string;
  description?: string;
  why?: string;
  primaryAction?: EmptyStateAction;
  secondaryAction?: EmptyStateAction;
  size?: "sm" | "md";
  className?: string;
}

const ACTION_BASE =
  "inline-flex min-h-[40px] items-center justify-center rounded-md px-3 text-sm font-medium focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring";
const ACTION_TONES = {
  primary: "bg-primary text-primary-foreground hover:bg-primary/90",
  secondary: "border bg-background text-foreground hover:bg-muted",
} as const;

function ActionControl({
  action,
  tone,
}: {
  action: EmptyStateAction;
  tone: keyof typeof ACTION_TONES;
}) {
  const className = cn(ACTION_BASE, ACTION_TONES[tone]);
  if (action.href) {
    return (
      <Link href={action.href} className={className}>
        {action.label}
      </Link>
    );
  }
  return (
    <button type="button" onClick={action.onClick} className={className}>
      {action.label}
    </button>
  );
}

function Illustration({ icon: Icon, size }: { icon: LucideIcon; size: "sm" | "md" }) {
  const gradientId = `empty-${useId().replace(/[^a-zA-Z0-9_-]/g, "")}`;
  const small = size === "sm";
  return (
    <span
      aria-hidden
      className={cn("relative inline-flex items-center justify-center", small ? "h-14 w-14" : "h-20 w-20")}
    >
      <svg viewBox="0 0 96 96" className="absolute inset-0 h-full w-full" focusable="false">
        <defs>
          <radialGradient id={gradientId} cx="50%" cy="38%" r="62%">
            <stop offset="0%" stopColor="hsl(var(--primary))" stopOpacity="0.24" />
            <stop offset="100%" stopColor="hsl(var(--primary))" stopOpacity="0.04" />
          </radialGradient>
        </defs>
        <circle cx="48" cy="48" r="46" fill={`url(#${gradientId})`} />
        <circle
          cx="48"
          cy="48"
          r="33"
          fill="none"
          stroke="hsl(var(--primary))"
          strokeOpacity="0.22"
          strokeDasharray="3 6"
        />
      </svg>
      <Icon className={cn("relative text-primary", small ? "h-6 w-6" : "h-8 w-8")} />
    </span>
  );
}

export function EmptyState({
  icon,
  title,
  description,
  why,
  primaryAction,
  secondaryAction,
  size = "md",
  className,
}: EmptyStateProps) {
  return (
    <div
      data-testid="empty-state"
      className={cn(
        "flex flex-col items-center justify-center text-center",
        size === "sm" ? "gap-2 px-4 py-6" : "gap-3 px-6 py-10",
        className,
      )}
    >
      <Illustration icon={icon} size={size} />
      <p className={cn("font-medium text-foreground", size === "sm" ? "text-sm" : "text-base")}>
        {title}
      </p>
      {description ? (
        <p className="max-w-md text-sm text-muted-foreground">{description}</p>
      ) : null}
      {why ? <p className="max-w-md text-xs text-muted-foreground">{why}</p> : null}
      {primaryAction || secondaryAction ? (
        <div className="mt-1 flex flex-wrap items-center justify-center gap-2">
          {primaryAction ? <ActionControl action={primaryAction} tone="primary" /> : null}
          {secondaryAction ? <ActionControl action={secondaryAction} tone="secondary" /> : null}
        </div>
      ) : null}
    </div>
  );
}
