"use client";

import { Suspense, useState, type FormEvent } from "react";
import { useRouter, useSearchParams } from "next/navigation";
import { toast } from "sonner";
import { api, isApiError } from "@/lib/api";

/**
 * v1.44.2 — login screen.
 *
 * Posts to FastAPI's /api/auth/login. The JWT lands in an httpOnly
 * cookie via axios `withCredentials: true`; we never see the token
 * in JS — that's the point.
 *
 * The inner form is wrapped in <Suspense> because useSearchParams()
 * forces dynamic prerendering on App Router. Without the boundary
 * `next build` refuses to generate the page.
 */
export default function LoginPage() {
  return (
    <main className="flex min-h-screen items-center justify-center bg-background px-4">
      <Suspense fallback={<LoginCardFallback />}>
        <LoginCard />
      </Suspense>
    </main>
  );
}

function LoginCardFallback() {
  return (
    <div className="w-full max-w-sm rounded-lg border bg-card p-6 shadow-sm">
      <div className="mb-6 text-center">
        <div className="mx-auto h-7 w-24 animate-pulse rounded bg-muted" />
      </div>
      <div className="space-y-4">
        <div className="h-10 w-full animate-pulse rounded bg-muted" />
        <div className="h-10 w-full animate-pulse rounded bg-muted" />
        <div className="h-10 w-full animate-pulse rounded bg-muted" />
      </div>
    </div>
  );
}

function LoginCard() {
  const router = useRouter();
  const params = useSearchParams();
  const nextPath = params.get("next") || "/dashboard";

  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [submitting, setSubmitting] = useState(false);

  async function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!email || !password) {
      toast.error("Ingresa tu email y contraseña.");
      return;
    }
    setSubmitting(true);
    try {
      await api.post("/api/auth/login", { email, password });
      toast.success("Sesión iniciada.");
      router.push(nextPath);
      router.refresh();
    } catch (err: unknown) {
      const status = isApiError(err) ? err.response?.status : undefined;
      if (status === 401) {
        toast.error("Email o contraseña incorrectos.");
      } else if (status === 429) {
        toast.error("Demasiados intentos. Espera un momento.");
      } else {
        toast.error("No se pudo iniciar sesión. Intenta de nuevo.");
      }
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
            id="email"
            type="email"
            autoComplete="email"
            required
            value={email}
            onChange={(e) => setEmail(e.target.value)}
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
            onChange={(e) => setPassword(e.target.value)}
            className="w-full rounded-md border border-input bg-background px-3 py-2 text-sm shadow-sm focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
            disabled={submitting}
          />
        </div>

        <button
          type="submit"
          disabled={submitting}
          className="inline-flex h-10 w-full items-center justify-center rounded-md bg-primary text-sm font-medium text-primary-foreground shadow transition-colors hover:bg-primary/90 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring disabled:pointer-events-none disabled:opacity-60"
        >
          {submitting ? "Iniciando sesión…" : "Iniciar sesión"}
        </button>
      </form>
    </div>
  );
}
