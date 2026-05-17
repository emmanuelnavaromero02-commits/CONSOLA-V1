"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { useEffect, useState, type ReactNode } from "react";

const PUBLIC_PREFIXES = ["/login", "/forgot-password", "/reset-password"];

function userLabel(email: string): string {
  const trimmed = email.trim();
  if (!trimmed) return "Usuario";
  return trimmed.split("@")[0] || trimmed;
}

export function AppChrome({ children }: { children: ReactNode }) {
  const pathname = usePathname() || "/";
  const isPublic = PUBLIC_PREFIXES.some((prefix) => pathname.startsWith(prefix));
  const [email, setEmail] = useState("");
  const [dark, setDark] = useState(false);

  useEffect(() => {
    const stored = window.localStorage.getItem("omega_user_email");
    if (stored) {
      setEmail(stored);
    } else {
      fetch("/auth/me", { credentials: "include" })
        .then((response) => (response.ok ? response.json() : null))
        .then((body) => {
          const nextEmail = body?.email || body?.user?.email;
          if (typeof nextEmail === "string" && nextEmail) {
            window.localStorage.setItem("omega_user_email", nextEmail);
            setEmail(nextEmail);
          }
        })
        .catch(() => null);
    }

    setDark(document.documentElement.classList.contains("dark"));
  }, []);

  function toggleDarkMode() {
    setDark((current) => {
      const next = !current;
      document.documentElement.classList.toggle("dark", next);
      return next;
    });
  }

  if (isPublic) return <>{children}</>;

  return (
    <>
      <header className="border-b bg-card">
        <div className="mx-auto flex min-h-14 max-w-6xl flex-wrap items-center gap-3 px-6 py-3">
          <Link
            prefetch={false}
            href="/dashboard"
            className="font-semibold tracking-tight text-foreground underline-offset-2 hover:underline"
          >
            OMEGA
          </Link>

          <nav className="flex items-center gap-2 text-sm text-muted-foreground">
            <Link prefetch={false} className="rounded px-2 py-1 hover:bg-muted hover:text-foreground" href="/dashboard">
              Panel
            </Link>
            <Link prefetch={false} className="rounded px-2 py-1 hover:bg-muted hover:text-foreground" href="/cartridges">
              Cartuchos
            </Link>
            <Link prefetch={false} className="rounded px-2 py-1 hover:bg-muted hover:text-foreground" href="/copilot">
              Copiloto
            </Link>
          </nav>

          <div className="ml-auto flex items-center gap-3 text-sm">
            <span className="text-muted-foreground">{userLabel(email)}</span>
            <button
              type="button"
              aria-label="Cambiar modo oscuro"
              onClick={toggleDarkMode}
              className="inline-flex min-h-[36px] min-w-[36px] items-center justify-center rounded-md border bg-background px-2 text-xs font-medium hover:bg-muted focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
            >
              {dark ? "Claro" : "Oscuro"}
            </button>
          </div>
        </div>
      </header>
      {children}
    </>
  );
}
