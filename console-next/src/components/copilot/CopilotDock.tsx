"use client";

import dynamic from "next/dynamic";
import { usePathname } from "next/navigation";
import { useCallback, useEffect, useRef, useState } from "react";
import { Bot } from "lucide-react";

const DOCK_OPEN_KEY = "omega-copilot-dock-open";

const CopilotDockPanel = dynamic(
  () => import("./CopilotDockPanel").then((mod) => mod.CopilotDockPanel),
  {
    ssr: false,
    loading: () => (
      <p className="p-5 text-sm text-muted-foreground">Cargando copiloto…</p>
    ),
  },
);

function readStoredOpen(): boolean {
  if (typeof window === "undefined") return false;
  try {
    return window.sessionStorage.getItem(DOCK_OPEN_KEY) === "1";
  } catch {
    return false;
  }
}

function focusMainCopilotInput(): boolean {
  const input = document.querySelector<HTMLElement>('[data-copilot-input="true"]');
  if (!input) return false;
  input.focus();
  return true;
}

export function CopilotDock() {
  const pathname = usePathname() || "/";
  const hidden = pathname.startsWith("/copilot") || pathname.startsWith("/workspace");

  const [open, setOpenState] = useState(false);
  const [hasOpened, setHasOpened] = useState(false);
  const launcherRef = useRef<HTMLButtonElement | null>(null);
  const drawerRef = useRef<HTMLElement | null>(null);
  const closeButtonRef = useRef<HTMLButtonElement | null>(null);

  const setOpen = useCallback((next: boolean) => {
    setOpenState(next);
    if (next) setHasOpened(true);
    try {
      window.sessionStorage.setItem(DOCK_OPEN_KEY, next ? "1" : "0");
    } catch {
      /* best-effort persistence */
    }
  }, []);

  useEffect(() => {
    // Hydration-safe restore: the static export always renders closed.
    if (hidden) return undefined;
    const timer = window.setTimeout(() => {
      if (readStoredOpen()) {
        setOpenState(true);
        setHasOpened(true);
      }
    }, 0);
    return () => window.clearTimeout(timer);
  }, [hidden]);

  useEffect(() => {
    function onKey(event: KeyboardEvent) {
      if (event.key.toLowerCase() !== "k" || !(event.metaKey || event.ctrlKey)) return;
      if (event.altKey || event.shiftKey) return;
      event.preventDefault();
      if (focusMainCopilotInput()) return;
      if (!hidden) setOpen(!open);
    }
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [hidden, open, setOpen]);

  useEffect(() => {
    if (!open || hidden) return undefined;

    const previousOverflow = document.body.style.overflow;
    document.body.style.overflow = "hidden";

    const focusTimer = window.setTimeout(() => {
      closeButtonRef.current?.focus();
    }, 0);

    function innerDialogOpen(): boolean {
      return Boolean(drawerRef.current?.querySelector('[role="dialog"]'));
    }

    function onKey(event: KeyboardEvent) {
      if (innerDialogOpen()) return;
      if (event.key === "Escape") {
        setOpen(false);
        return;
      }
      if (event.key !== "Tab") return;
      const focusable = [...(drawerRef.current?.querySelectorAll<HTMLElement>(
        'button:not([disabled]), [href], input, textarea, select, [tabindex]:not([tabindex="-1"])',
      ) ?? [])];
      if (focusable.length === 0) return;
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
    const launcher = launcherRef.current;
    return () => {
      window.clearTimeout(focusTimer);
      document.removeEventListener("keydown", onKey);
      document.body.style.overflow = previousOverflow;
      launcher?.focus();
    };
  }, [open, hidden, setOpen]);

  if (hidden) return null;

  return (
    <>
      <button
        ref={launcherRef}
        type="button"
        aria-label="Abrir copiloto"
        aria-expanded={open}
        data-fab="copilot"
        onClick={() => setOpen(!open)}
        className="fixed bottom-5 right-5 z-40 inline-flex h-12 w-12 items-center justify-center rounded-full border bg-card text-foreground shadow-lg hover:bg-muted focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
      >
        <Bot aria-hidden className="h-5 w-5" />
      </button>

      {open ? (
        <div
          role="dialog"
          aria-modal="true"
          aria-label="Copiloto OMEGA"
          className="fixed inset-0 z-40 flex"
        >
          <div
            className="flex-1 bg-black/40"
            onClick={() => setOpen(false)}
            aria-hidden
          />
          <aside
            ref={drawerRef}
            className="flex h-full w-full flex-col border-l bg-background shadow-xl sm:max-w-md"
          >
            <header className="flex items-center justify-between border-b px-5 py-4">
              <h2 className="text-base font-semibold tracking-tight">
                Copiloto OMEGA
              </h2>
              <button
                ref={closeButtonRef}
                type="button"
                onClick={() => setOpen(false)}
                aria-label="Cerrar copiloto"
                className="inline-flex min-h-[44px] min-w-[44px] items-center justify-center rounded-md hover:bg-accent/10 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
              >
                ✕
              </button>
            </header>
            {hasOpened ? <CopilotDockPanel route={pathname} /> : null}
          </aside>
        </div>
      ) : null}
    </>
  );
}
