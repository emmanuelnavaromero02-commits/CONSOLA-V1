"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { useEffect, useRef, useState, type ReactNode } from "react";
import {
  BarChart3,
  Boxes,
  Menu,
  MessageSquareText,
  Settings2,
  Wrench,
  X,
} from "lucide-react";
import type { LucideIcon } from "lucide-react";

import { LogoutButton } from "@/components/auth/LogoutButton";
import { cn } from "@/lib/utils";

const PUBLIC_PREFIXES = ["/login", "/forgot-password", "/reset-password"];
const THEME_STORAGE_KEY = "mod-theme";

interface NavItem {
  href:  string;
  label: string;
  icon:  LucideIcon;
}

/**
 * v1.44.4 Group 1 — primary navigation. Each entry maps to a
 * real Next.js page; routes that only exist as legacy HTML on
 * :8000 (analytics, sub-modules under operations) are NOT
 * listed here until the Next.js page is wired so the navbar
 * never offers a dead link.
 */
const NAV_ITEMS: NavItem[] = [
  { href: "/dashboard",  label: "Panel",       icon: BarChart3 },
  { href: "/workspace",  label: "Copiloto",    icon: MessageSquareText },
  { href: "/cartridges", label: "Cartuchos",   icon: Boxes },
  { href: "/studio",     label: "Studio",      icon: Wrench },
  { href: "/operations", label: "Operaciones", icon: Settings2 },
];

function userLabel(email: string): string {
  const trimmed = email.trim();
  if (!trimmed) return "Usuario";
  return trimmed.split("@")[0] || trimmed;
}

function isActive(pathname: string, href: string): boolean {
  if (pathname === href) return true;
  // A nested route under the nav target counts as active so
  // /operations/users highlights "Operaciones".
  return pathname.startsWith(href + "/");
}

function resolveThemePreference(value: string | null): "light" | "dark" {
  if (value === "dark") return "dark";
  if (value === "system") {
    return window.matchMedia?.("(prefers-color-scheme: dark)").matches ? "dark" : "light";
  }
  return "light";
}

export function AppChrome({ children }: { children: ReactNode }) {
  const pathname = usePathname() || "/";
  const isPublic = PUBLIC_PREFIXES.some((prefix) => pathname.startsWith(prefix));
  const [email,        setEmail]        = useState("");
  const [dark,         setDark]         = useState(false);
  const [mobileOpen,   setMobileOpen]   = useState(false);

  // Refs for the mobile-drawer accessibility plumbing
  // (focus management on open + restoration on close).
  const hamburgerRef = useRef<HTMLButtonElement | null>(null);
  const drawerCloseRef = useRef<HTMLButtonElement | null>(null);

  useEffect(() => {
    // Round 1 P1 — Safari private mode + SSR safety: every
    // localStorage access is now wrapped so a thrown
    // SecurityError can't surface as an uncaught exception.
    let stored: string | null = null;
    try {
      stored = window.localStorage.getItem("omega_user_email");
    } catch {
      stored = null;
    }
    if (stored) {
      setEmail(stored);
    } else {
      fetch("/auth/me", { credentials: "include" })
        .then((response) => (response.ok ? response.json() : null))
        .then((body) => {
          const nextEmail = body?.email || body?.user?.email;
          if (typeof nextEmail === "string" && nextEmail) {
            try {
              window.localStorage.setItem("omega_user_email", nextEmail);
            } catch {
              /* swallow — caching is best-effort */
            }
            setEmail(nextEmail);
          }
        })
        .catch(() => null);
    }

    let themePreference = "light";
    try {
      themePreference = window.localStorage.getItem(THEME_STORAGE_KEY) || "light";
    } catch {
      themePreference = "light";
    }
    const resolvedTheme = resolveThemePreference(themePreference);
    document.documentElement.classList.toggle("dark", resolvedTheme === "dark");
    document.documentElement.dataset.theme = resolvedTheme;
    document.documentElement.dataset.themePreference = themePreference;
    setDark(resolvedTheme === "dark");
  }, []);

  // Mobile drawer accessibility — focus the close button on
  // open, restore focus to the hamburger on close, lock body
  // scroll, and handle Escape. (Frontend Round-1 P0.)
  useEffect(() => {
    if (!mobileOpen) return;

    const previousOverflow = document.body.style.overflow;
    document.body.style.overflow = "hidden";

    // Defer focus so the drawer is mounted.
    const focusTimer = window.setTimeout(() => {
      drawerCloseRef.current?.focus();
    }, 0);

    function onKey(e: KeyboardEvent) {
      if (e.key === "Escape") setMobileOpen(false);
    }
    document.addEventListener("keydown", onKey);

    return () => {
      window.clearTimeout(focusTimer);
      document.removeEventListener("keydown", onKey);
      document.body.style.overflow = previousOverflow;
      // Restore focus to the hamburger that opened the drawer.
      hamburgerRef.current?.focus();
    };
  }, [mobileOpen]);

  function toggleDarkMode() {
    setDark((current) => {
      const next = !current;
      document.documentElement.classList.toggle("dark", next);
      document.documentElement.dataset.theme = next ? "dark" : "light";
      document.documentElement.dataset.themePreference = next ? "dark" : "light";
      try {
        window.localStorage.setItem(THEME_STORAGE_KEY, next ? "dark" : "light");
      } catch {
        /* best-effort persistence */
      }
      return next;
    });
  }

  if (isPublic) return <>{children}</>;

  return (
    <>
      <a
        href="#main-content"
        className="sr-only focus:not-sr-only focus:fixed focus:left-4 focus:top-4 focus:z-50 focus:rounded-md focus:bg-background focus:px-3 focus:py-2 focus:text-sm focus:shadow"
      >
        Saltar al contenido
      </a>
      <header
        role="banner"
        className="sticky top-0 z-30 border-b bg-background/95 backdrop-blur supports-[backdrop-filter]:bg-background/80"
      >
        <div className="mx-auto flex min-h-14 max-w-6xl items-center gap-2 px-3 py-2 sm:gap-3 sm:px-6">
          <button
            ref={hamburgerRef}
            type="button"
            onClick={() => setMobileOpen(true)}
            aria-label="Abrir navegación"
            aria-expanded={mobileOpen}
            className="inline-flex min-h-[44px] min-w-[44px] items-center justify-center rounded-md hover:bg-accent/10 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring md:hidden"
          >
            <Menu aria-hidden className="h-5 w-5" />
          </button>

          <Link
            prefetch={false}
            href="/dashboard"
            className="flex items-center gap-2 font-semibold tracking-tight text-foreground"
          >
            <span
              aria-hidden
              className="inline-flex h-7 w-7 items-center justify-center rounded-md bg-primary text-primary-foreground"
            >
              Ω
            </span>
            <span className="hidden sm:inline">OMEGA</span>
          </Link>

          <nav
            aria-label="Navegación principal"
            className="ml-2 hidden flex-1 md:flex"
          >
            <ul className="flex items-center gap-0.5">
              {NAV_ITEMS.map((item) => {
                const Icon   = item.icon;
                const active = isActive(pathname, item.href);
                return (
                  <li key={item.href}>
                    <Link
                      prefetch={false}
                      href={item.href}
                      aria-current={active ? "page" : undefined}
                      className={cn(
                        "inline-flex min-h-[44px] items-center gap-1.5 rounded-md px-3 text-sm font-medium transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring",
                        active
                          ? "bg-accent/15 text-foreground"
                          : "text-muted-foreground hover:bg-accent/10 hover:text-foreground",
                      )}
                    >
                      <Icon aria-hidden className="h-4 w-4" />
                      {item.label}
                    </Link>
                  </li>
                );
              })}
            </ul>
          </nav>

          <div className="ml-auto flex items-center gap-2 text-sm">
            <span className="hidden text-muted-foreground sm:inline">
              {userLabel(email)}
            </span>
            <button
              type="button"
              aria-label={dark ? "Cambiar a modo claro" : "Cambiar a modo oscuro"}
              onClick={toggleDarkMode}
              className="inline-flex min-h-[44px] items-center justify-center rounded-md border bg-background px-3 text-xs font-medium hover:bg-muted focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
            >
              {dark ? "Claro" : "Oscuro"}
            </button>
            <LogoutButton />
          </div>
        </div>
      </header>

      {/* Mobile drawer — slides from the LEFT so it matches the
          hamburger button's position. Round 1 P1 fixed the
          drawer-on-right vs hamburger-on-left mismatch. */}
      {mobileOpen ? (
        <div
          role="dialog"
          aria-modal="true"
          aria-label="Navegación"
          className="fixed inset-0 z-40 flex md:hidden"
        >
          <aside className="flex h-full w-72 max-w-[85vw] flex-col border-r bg-background shadow-xl">
            <header className="flex items-center justify-between border-b px-4 py-3">
              <Link
                prefetch={false}
                href="/dashboard"
                onClick={() => setMobileOpen(false)}
                className="flex items-center gap-2 text-sm font-semibold tracking-tight"
              >
                <span
                  aria-hidden
                  className="inline-flex h-7 w-7 items-center justify-center rounded-md bg-primary text-primary-foreground"
                >
                  Ω
                </span>
                OMEGA
              </Link>
              <button
                ref={drawerCloseRef}
                type="button"
                onClick={() => setMobileOpen(false)}
                aria-label="Cerrar navegación"
                className="inline-flex min-h-[44px] min-w-[44px] items-center justify-center rounded-md hover:bg-accent/10 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
              >
                <X aria-hidden className="h-5 w-5" />
              </button>
            </header>
            <nav
              aria-label="Navegación principal (móvil)"
              className="flex-1 overflow-y-auto p-2"
            >
              <ul className="space-y-1">
                {NAV_ITEMS.map((item) => {
                  const Icon   = item.icon;
                  const active = isActive(pathname, item.href);
                  return (
                    <li key={item.href}>
                      <Link
                        prefetch={false}
                        href={item.href}
                        onClick={() => setMobileOpen(false)}
                        aria-current={active ? "page" : undefined}
                        className={cn(
                          "flex min-h-[44px] items-center gap-2 rounded-md px-3 text-sm font-medium transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring",
                          active
                            ? "bg-accent/20 text-foreground"
                            : "text-muted-foreground hover:bg-accent/10 hover:text-foreground",
                        )}
                      >
                        <Icon aria-hidden className="h-4 w-4" />
                        {item.label}
                      </Link>
                    </li>
                  );
                })}
              </ul>
            </nav>
          </aside>
          <div
            className="flex-1 bg-black/40"
            onClick={() => setMobileOpen(false)}
            aria-hidden
          />
        </div>
      ) : null}

      <div id="main-content">{children}</div>
    </>
  );
}
