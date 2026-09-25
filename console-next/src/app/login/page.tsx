"use client";

import { useEffect, useRef, useState, type FormEvent } from "react";
import { loginUser, type LoginError } from "@/lib/auth-flow";

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
  if (!nextPath || !nextPath.startsWith("/") || nextPath.startsWith("//")) {
    return "/dashboard";
  }
  return nextPath;
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
      await loginUser(email, password);
      window.localStorage.setItem("omega_user_email", email);
      window.location.assign(safeNextPath());
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
