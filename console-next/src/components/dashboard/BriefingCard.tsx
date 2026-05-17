"use client";

import { useMemo } from "react";

import Link from "next/link";
import { useRouter } from "next/navigation";
import type { LucideIcon } from "lucide-react";
import {
  AlertTriangle,
  ArrowRight,
  ExternalLink,
  Info,
  OctagonAlert,
  X,
} from "lucide-react";

import type {
  BriefingHighlight,
  Severity,
} from "@/lib/copilot/types";
import { cn } from "@/lib/utils";

interface Props {
  highlight: BriefingHighlight;
  onDismiss: (highlight: BriefingHighlight) => void;
}

/**
 * v1.44.4 Task B — single briefing card.
 *
 * Real backend shape per proactive_service.py:_make_highlight:
 *   ``{id, severity, title, body, category, cartridge,
 *      action_label, action_href}``
 *
 * Action interpretation (the brief's nested ``primary_action.kind``
 * pattern does NOT exist on the backend — the analyzer emits a
 * single label + href):
 *
 *   - ``action_href`` starts with ``/workspace?prompt=…`` →
 *     route through the Next.js router so the chat page reads
 *     the prompt from the query string. (Defensive: canonicalise
 *     the URL and require ``pathname === '/workspace'`` so a
 *     ``/workspace-admin?prompt=…`` can't slip past the gate.)
 *   - Other same-origin ``/...`` → render as <Link> for
 *     client-side nav. Same canonicalisation guards against
 *     ``..`` traversal segments.
 *   - External ``http(s)://`` → <a target="_blank">
 *     with ``rel="noopener noreferrer"``.
 *   - Missing / unsafe scheme → no action button rendered AND a
 *     ``console.warn`` so QA sees the drop in DOM via
 *     ``data-action-dropped`` (Round 1 review P1).
 *
 * Severity → icon + accent color (the brief asked for
 * info/warn/alert; backend emits info/warning/critical).
 */
type SeverityVisual = {
  icon:           LucideIcon;
  border:         string;
  iconBg:         string;
  iconFg:         string;
  /** Tailwind class on the action button so a critical card
   *  doesn't get an unrelated blue/neutral CTA. */
  actionBg:       string;
  label:          string;
};

const SEVERITY_VISUAL: Record<Severity, SeverityVisual> = {
  info: {
    icon:    Info,
    border:  "border-l-blue-500 dark:border-l-blue-400",
    iconBg:  "bg-blue-500/10",
    iconFg:  "text-blue-600 dark:text-blue-400",
    actionBg:
      "bg-primary text-primary-foreground hover:bg-primary/90 focus-visible:ring-ring",
    label:   "Información",
  },
  warning: {
    icon:    AlertTriangle,
    border:  "border-l-amber-500 dark:border-l-amber-400",
    iconBg:  "bg-amber-500/10",
    iconFg:  "text-amber-600 dark:text-amber-400",
    actionBg:
      "bg-amber-600 text-white hover:bg-amber-600/90 focus-visible:ring-amber-600/40",
    label:   "Atención",
  },
  critical: {
    icon:    OctagonAlert,
    border:  "border-l-red-500 dark:border-l-red-400",
    iconBg:  "bg-red-500/10",
    iconFg:  "text-red-600 dark:text-red-400",
    actionBg:
      "bg-destructive text-destructive-foreground hover:bg-destructive/90 focus-visible:ring-destructive/40",
    label:   "Crítico",
  },
};

/** Defensive fallback when the backend emits a severity outside
 *  the documented enum — keeps the card rendering. */
const SEVERITY_FALLBACK: SeverityVisual = SEVERITY_VISUAL.info;


type ClassifiedHref =
  | { kind: "prompt";   href: string; prompt: string }
  | { kind: "internal"; href: string }
  | { kind: "external"; href: string };


/**
 * Allow-list classifier. Same-origin paths are canonicalised
 * via ``new URL`` so ``..`` traversal segments don't survive,
 * and the prompt-special-case requires the literal pathname to
 * be ``/workspace`` (not just ``startsWith``). External URLs
 * must be ``http(s):``.
 */
function classifyHref(value: string | null): ClassifiedHref | null {
  if (!value || typeof value !== "string") return null;
  const trimmed = value.trim();
  if (!trimmed) return null;

  // Protocol-relative — reject (would resolve to current scheme
  // + arbitrary host).
  if (trimmed.startsWith("//")) return null;

  // Same-origin path reference: canonicalise to strip
  // ``..`` traversal. Use a placeholder base so this works in
  // both server + client environments.
  if (trimmed.startsWith("/")) {
    let canonical: URL;
    try {
      canonical = new URL(trimmed, "https://placeholder.local");
    } catch {
      return null;
    }
    const path = canonical.pathname + canonical.search + canonical.hash;
    // Prompt-prefill shape: pathname MUST equal '/workspace'.
    if (canonical.pathname === "/workspace") {
      const prompt = canonical.searchParams.get("prompt");
      if (prompt) return { kind: "prompt", href: path, prompt };
    }
    return { kind: "internal", href: path };
  }

  // External absolute URL — only http(s) allowed.
  try {
    const url = new URL(trimmed);
    if (url.protocol === "http:" || url.protocol === "https:") {
      return { kind: "external", href: url.toString() };
    }
  } catch {
    /* not a valid absolute URL */
  }

  return null;
}


export function BriefingCard({ highlight, onDismiss }: Props) {
  const router  = useRouter();
  const visual  = SEVERITY_VISUAL[highlight.severity] ?? SEVERITY_FALLBACK;
  const Icon    = visual.icon;

  const action = useMemo(
    () => classifyHref(highlight.action_href),
    [highlight.action_href],
  );

  // Log + flag dropped action hrefs so QA can detect them in
  // the DOM via ``data-action-dropped`` and in the console.
  const actionDropped =
    !action && Boolean(highlight.action_href);
  if (actionDropped) {
    if (typeof window !== "undefined") {
      // Defensive — runs once per render. The cost is
      // negligible (a single console.warn) and the alternative
      // (silent drop) cost us a real briefing-card audit P1.
      // eslint-disable-next-line no-console
      console.warn(
        "[BriefingCard] dropping unsafe action_href",
        { id: highlight.id, href: highlight.action_href },
      );
    }
  }

  const actionLabel = highlight.action_label?.trim() || (action ? "Abrir" : null);

  function handleDismiss() {
    onDismiss(highlight);
  }

  function renderAction() {
    if (!action || !actionLabel) return null;
    const baseClass = cn(
      "inline-flex min-h-[44px] max-w-full items-center justify-center gap-1.5 rounded-md px-4 text-sm font-medium transition-colors focus-visible:outline-none focus-visible:ring-2 line-clamp-1",
      visual.actionBg,
    );
    const arrowIcon = action.kind === "external"
      ? <ExternalLink aria-hidden className="h-4 w-4 shrink-0" />
      : <ArrowRight   aria-hidden className="h-4 w-4 shrink-0" />;

    if (action.kind === "prompt") {
      return (
        <button
          type="button"
          onClick={() => router.push(action.href)}
          className={baseClass}
        >
          <span className="line-clamp-1 text-left">{actionLabel}</span>
          {arrowIcon}
        </button>
      );
    }
    if (action.kind === "internal") {
      return (
        <Link href={action.href} className={baseClass}>
          <span className="line-clamp-1 text-left">{actionLabel}</span>
          {arrowIcon}
        </Link>
      );
    }
    // external
    return (
      <a
        href={action.href}
        target="_blank"
        rel="noopener noreferrer"
        className={baseClass}
      >
        <span className="line-clamp-1 text-left">{actionLabel}</span>
        {arrowIcon}
      </a>
    );
  }

  return (
    <article
      aria-labelledby={`briefing-title-${highlight.id}`}
      className={cn(
        "relative flex flex-col gap-3 rounded-lg border border-l-4 bg-card p-4 shadow-sm transition-shadow hover:shadow-md",
        visual.border,
      )}
      data-testid="briefing-card"
      data-severity={highlight.severity}
      data-action-dropped={actionDropped ? "true" : undefined}
    >
      <button
        type="button"
        onClick={handleDismiss}
        aria-label={`Descartar aviso: ${highlight.title}`}
        className="absolute right-2 top-2 inline-flex min-h-[44px] min-w-[44px] items-center justify-center rounded-md text-muted-foreground hover:bg-accent/10 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
      >
        <X aria-hidden className="h-4 w-4" />
      </button>

      {/* pr-14 (56 px) reserves 44 px tap target + 8 px gap so a
          long single-word title can't slide under the dismiss
          button on narrow viewports. */}
      <div className="flex items-start gap-3 pr-14">
        <span
          aria-hidden
          className={cn(
            "inline-flex h-9 w-9 shrink-0 items-center justify-center rounded-full",
            visual.iconBg,
            visual.iconFg,
          )}
        >
          <Icon className="h-5 w-5" />
        </span>
        <div className="flex-1 space-y-1">
          <span className="sr-only">{visual.label}.</span>
          <h3
            id={`briefing-title-${highlight.id}`}
            className="text-sm font-semibold tracking-tight"
          >
            {highlight.title}
          </h3>
          <p className="text-sm text-muted-foreground">{highlight.body}</p>
        </div>
      </div>

      {action && actionLabel ? (
        <div className="pt-1">{renderAction()}</div>
      ) : null}
    </article>
  );
}
