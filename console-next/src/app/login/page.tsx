"use client";

import { Suspense, useState, type FormEvent } from "react";
import { useRouter, useSearchParams } from "next/navigation";
import { toast } from "sonner";
import { loginUser, type LoginError } from "@/lib/auth-flow";

/**
 * v1.44.2 — login screen.
 *
 * v1.44.3.2.2 (R-Mac): used to POST /api/auth/login via the axios
 * client; the real endpoint is /auth/login AND requires a CSRF
 * round-trip Codex's diagnostic uncovered. Both fixes land in
 * lib/auth-flow.ts:loginUser — this component just calls it.
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
      // v1.44.3.2.2 R-Mac: loginUser handles the GET /login → read
      // csrf cookie → POST /auth/login dance internally. Returns
      // on 200 + cookies set; throws LoginError with a
      // status-specific human-readable message on every failure.
      await loginUser(email, password);
      window.localStorage.setItem("omega_user_email", email);
      toast.success("Sesión iniciada.");
      router.push(nextPath);
      router.refresh();
    } catch (err: unknown) {
      const e = err as LoginError;
      toast.error(e?.message || "No se pudo iniciar sesión. Intenta de nuevo.");
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
          className="inline-flex min-h-[44px] w-full items-center justify-center rounded-md bg-primary text-sm font-medium text-primary-foreground shadow transition-colors hover:bg-primary/90 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring disabled:pointer-events-none disabled:opacity-60"
        >
          {submitting ? "Iniciando sesión…" : "Iniciar sesión"}
        </button>

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
