"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { useQuery } from "@tanstack/react-query";
import { useEffect, useRef, useState, type ReactNode } from "react";
import { ChevronDown, Moon, Sun, UserCircle } from "lucide-react";

import { AppSidebar } from "@/components/AppSidebar";
import { LogoutButton } from "@/components/auth/LogoutButton";
import { api } from "@/lib/api";
import { getMeAccess, type MeAccessResponse } from "@/lib/admin-surfaces";
import { cn } from "@/lib/utils";

const PUBLIC_PREFIXES = ["/login", "/forgot-password", "/reset-password", "/activate"];
const THEME_STORAGE_KEY = "mod-theme";
const SIDEBAR_STORAGE_KEY = "omega-sidebar-collapsed";

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

function readSidebarCollapsedPreference(): boolean {
  if (typeof window === "undefined") return false;
  try {
    return window.localStorage.getItem(SIDEBAR_STORAGE_KEY) === "true";
  } catch {
    return false;
  }
}

function applyThemePreference(themePreference: string): "light" | "dark" {
  const resolvedTheme = resolveThemePreference(themePreference);
  document.documentElement.classList.toggle("dark", resolvedTheme === "dark");
  document.documentElement.dataset.theme = resolvedTheme;
  document.documentElement.dataset.themePreference = themePreference;
  return resolvedTheme;
}

function userLabel(email: string): string {
  const trimmed = email.trim();
  if (!trimmed) return "Usuario";
  return trimmed.split("@")[0] || trimmed;
}

function canSeeSettings(access: MeAccessResponse | undefined): boolean {
  if (!access) return false;
  return access.role?.is_platform_admin === true || (access.permissions ?? []).includes("settings.read");
}

function UserMenu({
  email,
  dark,
  access,
  onToggleDark,
}: {
  email: string;
  dark: boolean;
  access?: MeAccessResponse;
  onToggleDark: () => void;
}) {
  const [open, setOpen] = useState(false);
  const menuRef = useRef<HTMLDivElement | null>(null);

  useEffect(() => {
    if (!open) return;

    function onPointerDown(event: MouseEvent) {
      if (!menuRef.current?.contains(event.target as Node)) {
        setOpen(false);
      }
    }

    function onKeyDown(event: KeyboardEvent) {
      if (event.key === "Escape") setOpen(false);
    }

    document.addEventListener("mousedown", onPointerDown);
    document.addEventListener("keydown", onKeyDown);
    return () => {
      document.removeEventListener("mousedown", onPointerDown);
      document.removeEventListener("keydown", onKeyDown);
    };
  }, [open]);

  return (
    <div ref={menuRef} className="relative">
      <button
        type="button"
        aria-haspopup="menu"
        aria-expanded={open}
        onClick={() => setOpen((current) => !current)}
        className="inline-flex min-h-[44px] max-w-[220px] items-center gap-2 rounded-md border bg-card px-3 text-sm font-medium text-foreground shadow-sm hover:bg-muted focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
      >
        <UserCircle aria-hidden className="h-4 w-4 shrink-0 text-muted-foreground" />
        <span className="hidden min-w-0 truncate sm:inline">{userLabel(email)}</span>
        <ChevronDown aria-hidden className={cn("h-4 w-4 shrink-0 text-muted-foreground transition-transform", open && "rotate-180")} />
      </button>

      {open ? (
        <div
          role="menu"
          aria-label="Menú de usuario"
          className="absolute right-0 z-50 mt-2 w-64 overflow-hidden rounded-md border bg-popover text-popover-foreground shadow-lg"
        >
          <div className="border-b px-3 py-2">
            <p className="text-xs font-medium uppercase text-muted-foreground">Cuenta</p>
            <p className="mt-1 truncate text-sm font-medium">{userLabel(email)}</p>
          </div>
          <div className="p-1">
            <Link
              prefetch={false}
              href="/my-access"
              role="menuitem"
              onClick={() => setOpen(false)}
              className="flex min-h-[40px] items-center rounded-md px-3 text-sm hover:bg-accent/10 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
            >
              Mi acceso
            </Link>
            {canSeeSettings(access) ? (
              <Link
                prefetch={false}
                href="/settings"
                role="menuitem"
                onClick={() => setOpen(false)}
                className="flex min-h-[40px] items-center rounded-md px-3 text-sm hover:bg-accent/10 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
              >
                Ajustes globales
              </Link>
            ) : null}
            <button
              type="button"
              role="menuitem"
              onClick={() => {
                onToggleDark();
                setOpen(false);
              }}
              className="flex min-h-[40px] w-full items-center gap-2 rounded-md px-3 text-left text-sm hover:bg-accent/10 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
            >
              {dark ? <Sun aria-hidden className="h-4 w-4" /> : <Moon aria-hidden className="h-4 w-4" />}
              {dark ? "Modo claro" : "Modo oscuro"}
            </button>
            <LogoutButton className="flex min-h-[40px] w-full items-center justify-start rounded-md px-3 text-sm hover:bg-accent/10 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring disabled:pointer-events-none disabled:opacity-50" />
          </div>
        </div>
      ) : null}
    </div>
  );
}

export function AppChrome({ children }: { children: ReactNode }) {
  const pathname = usePathname() || "/";
  const isPublic = PUBLIC_PREFIXES.some((prefix) => pathname.startsWith(prefix));
  const [email,        setEmail]        = useState(readCachedEmail);
  const [dark,         setDark]         = useState(() => (
    resolveThemePreference(readThemePreference()) === "dark"
  ));
  const [mobileOpen,   setMobileOpen]   = useState(false);
  const [sidebarCollapsed, setSidebarCollapsed] = useState(readSidebarCollapsedPreference);
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

  function toggleSidebarCollapsed() {
    setSidebarCollapsed((current) => {
      const next = !current;
      try {
        window.localStorage.setItem(SIDEBAR_STORAGE_KEY, String(next));
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
        className={cn(
          "sr-only focus:not-sr-only focus:fixed focus:left-4 focus:top-4 focus:z-50 focus:rounded-md focus:bg-background focus:px-3 focus:py-2 focus:text-sm focus:shadow",
          sidebarCollapsed ? "md:left-24" : "md:left-72",
        )}
      >
        Saltar al contenido
      </a>
      <AppSidebar
        pathname={pathname}
        access={access.data}
        collapsed={sidebarCollapsed}
        onToggleCollapsed={toggleSidebarCollapsed}
        className={cn(
          "fixed inset-y-0 left-0 z-30 hidden md:flex",
          sidebarCollapsed ? "w-20" : "w-72",
        )}
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
            <UserMenu
              email={email}
              dark={dark}
              access={access.data}
              onToggleDark={toggleDarkMode}
            />
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
            collapsed={false}
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

      <div
        id="main-content"
        className={cn(
          "min-w-0 transition-[padding-left] duration-200",
          sidebarCollapsed ? "md:pl-20" : "md:pl-72",
        )}
      >
        <header
          role="banner"
          className="sticky top-0 z-20 hidden min-h-14 items-center justify-end border-b bg-background/95 px-4 backdrop-blur supports-[backdrop-filter]:bg-background/80 md:flex"
        >
          <UserMenu
            email={email}
            dark={dark}
            access={access.data}
            onToggleDark={toggleDarkMode}
          />
        </header>
        {children}
      </div>
    </>
  );
}
