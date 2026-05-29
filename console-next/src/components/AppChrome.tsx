"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { useQuery } from "@tanstack/react-query";
import { useEffect, useRef, useState, type ReactNode } from "react";

import { AppSidebar } from "@/components/AppSidebar";
import { api } from "@/lib/api";
import { getMeAccess } from "@/lib/admin-surfaces";

const PUBLIC_PREFIXES = ["/login", "/forgot-password", "/reset-password", "/activate"];
const THEME_STORAGE_KEY = "mod-theme";

function resolveThemePreference(value: string | null): "light" | "dark" {
  if (value === "dark") return "dark";
  if (value === "system") {
    return window.matchMedia?.("(prefers-color-scheme: dark)").matches ? "dark" : "light";
  }
  return "light";
}

function readCachedEmail(): string {
  if (typeof window === "undefined") return "";
  try {
    return window.localStorage.getItem("omega_user_email") || "";
  } catch {
    return "";
  }
}

function readThemePreference(): string {
  if (typeof document === "undefined") return "light";
  try {
    return (
      window.localStorage.getItem(THEME_STORAGE_KEY)
      || (document.documentElement.classList.contains("dark") ? "dark" : "light")
    );
  } catch {
    return document.documentElement.classList.contains("dark") ? "dark" : "light";
  }
}

function applyThemePreference(themePreference: string): "light" | "dark" {
  const resolvedTheme = resolveThemePreference(themePreference);
  document.documentElement.classList.toggle("dark", resolvedTheme === "dark");
  document.documentElement.dataset.theme = resolvedTheme;
  document.documentElement.dataset.themePreference = themePreference;
  return resolvedTheme;
}

export function AppChrome({ children }: { children: ReactNode }) {
  const pathname = usePathname() || "/";
  const isPublic = PUBLIC_PREFIXES.some((prefix) => pathname.startsWith(prefix));
  const [email,        setEmail]        = useState(readCachedEmail);
  const [dark,         setDark]         = useState(() => (
    resolveThemePreference(readThemePreference()) === "dark"
  ));
  const [mobileOpen,   setMobileOpen]   = useState(false);
  const access = useQuery({
    queryKey: ["me", "access"],
    queryFn: getMeAccess,
    enabled: !isPublic,
    staleTime: 60_000,
  });

  // Refs for the mobile-drawer accessibility plumbing
  // (focus management on open + restoration on close).
  const hamburgerRef = useRef<HTMLButtonElement | null>(null);
  const drawerCloseRef = useRef<HTMLButtonElement | null>(null);

  useEffect(() => {
    if (!email) {
      api.get<{ email?: string; user?: { email?: string } }>("/auth/me")
        .then((body) => {
          const nextEmail = body.data.email || body.data.user?.email;
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

    applyThemePreference(readThemePreference());
  }, [email]);

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
    const opener = hamburgerRef.current;

    return () => {
      window.clearTimeout(focusTimer);
      document.removeEventListener("keydown", onKey);
      document.body.style.overflow = previousOverflow;
      // Restore focus to the hamburger that opened the drawer.
      opener?.focus();
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
        className="sr-only focus:not-sr-only focus:fixed focus:left-4 focus:top-4 focus:z-50 focus:rounded-md focus:bg-background focus:px-3 focus:py-2 focus:text-sm focus:shadow md:left-72"
      >
        Saltar al contenido
      </a>
      <AppSidebar
        pathname={pathname}
        access={access.data}
        email={email}
        dark={dark}
        onToggleDark={toggleDarkMode}
        className="fixed inset-y-0 left-0 z-30 hidden w-72 md:flex"
      />
      <header
        role="banner"
        className="sticky top-0 z-30 border-b bg-background/95 backdrop-blur supports-[backdrop-filter]:bg-background/80 md:hidden"
      >
        <div className="flex min-h-14 items-center gap-2 px-3 py-2">
          <button
            ref={hamburgerRef}
            type="button"
            onClick={() => setMobileOpen(true)}
            aria-label="Abrir navegación"
            aria-expanded={mobileOpen}
            className="inline-flex min-h-[44px] min-w-[44px] items-center justify-center rounded-md hover:bg-accent/10 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring md:hidden"
          >
            <span aria-hidden className="text-lg leading-none">☰</span>
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

          <div className="ml-auto flex items-center gap-2 text-sm">
            <button
              type="button"
              aria-label={dark ? "Cambiar a modo claro" : "Cambiar a modo oscuro"}
              onClick={toggleDarkMode}
              className="inline-flex min-h-[44px] items-center justify-center rounded-md border bg-background px-3 text-xs font-medium hover:bg-muted focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
            >
              {dark ? "Claro" : "Oscuro"}
            </button>
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
          <AppSidebar
            pathname={pathname}
            access={access.data}
            email={email}
            dark={dark}
            onToggleDark={toggleDarkMode}
            onNavigate={() => setMobileOpen(false)}
            closeButtonRef={drawerCloseRef}
            className="w-80 max-w-[86vw] shadow-xl"
          />
          <div
            className="flex-1 bg-black/40"
            onClick={() => setMobileOpen(false)}
            aria-hidden
          />
        </div>
      ) : null}

      <div id="main-content" className="min-w-0 md:pl-72">{children}</div>
    </>
  );
}
