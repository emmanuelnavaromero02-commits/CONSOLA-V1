"use client";

import { useEffect, useRef, useState, type FormEvent } from "react";
import { loginUser, type LoginError } from "@/lib/auth-flow";

/**
 * v1.44.2 — login screen.
 *
 * v1.44.3.2.2 (R-Mac): the real endpoint is /auth/login and requires
 * a CSRF round-trip. That flow lives in lib/auth-flow.ts:loginUser;
 * this component just calls it.
 *
 * Keep this page dependency-light: it is the public gateway and
 * carries a strict first-load JS budget in E2E.
 */
export default function LoginPage() {
  return (
    <main className="flex min-h-screen items-center justify-center bg-background px-4">
      <a
        href="#email"
        className="sr-only focus:not-sr-only focus:fixed focus:left-4 focus:top-4 focus:z-50 focus:rounded-md focus:bg-background focus:px-3 focus:py-2 focus:text-sm focus:shadow"
      >
        Ir al formulario
      </a>
      <LoginCard />
    </main>
  );
}

function safeNextPath(): string {
  if (typeof window === "undefined") return "/dashboard";
  const nextPath = new URLSearchParams(window.location.search).get("next");
  const referrer = document.referrer ? new URL(document.referrer, window.location.origin) : null;

  if (nextPath && nextPath.startsWith("/") && !nextPath.startsWith("//")) {
    if (
      referrer
      && referrer.origin === window.location.origin
      && referrer.pathname === nextPath
      && referrer.search
    ) {
      return `${nextPath}${referrer.search}`;
    }
    return nextPath;
  }

  if (nextPath) {
    try {
      const url = new URL(nextPath);
      const sameHost = url.protocol === window.location.protocol && url.hostname === window.location.hostname;
      const trustedWorkspacePort = url.port === "8001" || url.port === "8000";
      if (sameHost && trustedWorkspacePort) return url.href;
    } catch {
      return "/dashboard";
    }
  }

  return "/dashboard";
}

function LoginCard() {
  const emailRef = useRef<HTMLInputElement>(null);

  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [authError, setAuthError] = useState<string | null>(null);

  useEffect(() => {
    emailRef.current?.focus();
  }, []);

  async function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!email || !password) {
      const message = "Ingresa tu email y contraseña.";
      setAuthError(message);
      return;
    }
    setAuthError(null);
    setSubmitting(true);
    try {
      // v1.44.3.2.2 R-Mac: loginUser handles the GET /login → read
      // csrf cookie → POST /auth/login dance internally. Returns
      // on 200 + cookies set; throws LoginError with a
      // status-specific human-readable message on every failure.
      await loginUser(email, password);
      window.localStorage.setItem("omega_user_email", email);
      window.location.replace(safeNextPath());
    } catch (err: unknown) {
      const e = err as LoginError;
      const message = e?.message || "No se pudo iniciar sesión. Intenta de nuevo.";
      setAuthError(message);
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <div className="w-full max-w-sm rounded-lg border bg-card p-6 shadow-sm">
      <header className="mb-6 text-center">
        <h1 className="text-2xl font-semibold tracking-tight">OMEGA</h1>
        <p className="mt-1 text-sm text-muted-foreground">
          Inicia sesión para continuar
        </p>
      </header>

      <form className="space-y-4" onSubmit={handleSubmit} noValidate>
        <div className="space-y-1.5">
          <label className="text-sm font-medium" htmlFor="email">
            Email
          </label>
          <input
            ref={emailRef}
            id="email"
            type="email"
            autoFocus
            autoComplete="email"
            required
            value={email}
            onChange={(e) => {
              setEmail(e.target.value);
              setAuthError(null);
            }}
            className="w-full rounded-md border border-input bg-background px-3 py-2 text-sm shadow-sm focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
            disabled={submitting}
          />
        </div>

        <div className="space-y-1.5">
          <label className="text-sm font-medium" htmlFor="password">
            Contraseña
          </label>
          <input
            id="password"
            type="password"
            autoComplete="current-password"
            required
            value={password}
            onChange={(e) => {
              setPassword(e.target.value);
              setAuthError(null);
            }}
            className="w-full rounded-md border border-input bg-background px-3 py-2 text-sm shadow-sm focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
            disabled={submitting}
          />
        </div>

        <button
          type="submit"
          disabled={submitting}
          className="inline-flex min-h-[44px] w-full items-center justify-center rounded-md bg-primary text-sm font-medium text-primary-foreground shadow transition-colors hover:bg-primary/90 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring disabled:pointer-events-none disabled:opacity-60"
        >
          {submitting ? "Iniciando sesión…" : "Iniciar sesión"}
        </button>

        {authError ? (
          <p
            className="rounded-md border border-destructive/30 bg-destructive/10 px-3 py-2 text-sm text-destructive"
            data-sonner-toast=""
            role="alert"
          >
            {authError}
          </p>
        ) : null}

        {/* v1.44.3.3 R-Mac-Round-3 Task D — forgot-password
            affordance. The backend exposes GET /forgot-password
            (renders the legacy HTML form) and
            POST /auth/forgot-password (sends the reset email
            via Mailhog). The Next.js console proxies both via
            /auth/[...path], so a relative link Just Works. */}
        <a
          href="/forgot-password"
          className="block text-center text-xs font-medium text-muted-foreground underline-offset-2 hover:text-foreground hover:underline"
        >
          ¿Olvidaste tu contraseña?
        </a>
      </form>
    </div>
  );
}
