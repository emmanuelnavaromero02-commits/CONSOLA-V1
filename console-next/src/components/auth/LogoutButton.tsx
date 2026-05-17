"use client";

import { useState } from "react";
import { useRouter } from "next/navigation";
import { toast } from "sonner";
import { readCookie } from "@/lib/api";

/**
 * v1.44.3.3 Task E — logout affordance.
 *
 * Posts to the same-origin ``/auth/logout`` (forwarded by the
 * R-Mac-4 proxy to the FastAPI backend). The backend deletes
 * ``mod_session`` and ``refresh_token`` via ``response.delete_cookie``
 * (which emits Set-Cookie headers with ``Max-Age=0``); the proxy
 * forwards those headers verbatim via ``getSetCookie()``, so the
 * browser drops the cookies and any subsequent navigation hits
 * the auth middleware → ``/login`` redirect.
 *
 * Closes the gap that ``tests-e2e/specs/01-login-deep.spec.ts:
 * 'logout clears the session cookie'`` flagged as a known
 * v1.44.4 deficit (``test.fail(true, "no logout affordance
 * found in Next.js UI")``).
 */
export function LogoutButton({ className }: { className?: string }) {
  const router = useRouter();
  const [submitting, setSubmitting] = useState(false);

  async function handleLogout() {
    if (submitting) return;
    setSubmitting(true);
    try {
      const csrf = readCookie("csrf_token");
      const headers: Record<string, string> = {};
      if (csrf) headers["X-CSRF-Token"] = csrf;
      const r = await fetch("/auth/logout", {
        method: "POST",
        credentials: "include",
        headers,
      });
      // 200 (logged out) or 302 (redirect to /login) are both
      // success paths. Any 5xx is real failure.
      if (r.status >= 500) {
        throw new Error(`HTTP ${r.status}`);
      }
      router.replace("/login");
      router.refresh();
    } catch (err) {
      toast.error("No se pudo cerrar la sesión. Intenta de nuevo.");
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <button
      type="button"
      name="logout"
      onClick={handleLogout}
      disabled={submitting}
      className={
        className ??
        // v1.44.3.3 Task H — min-h-[44px] for WCAG touch-target.
        "inline-flex min-h-[44px] items-center justify-center rounded-md border border-border bg-background px-3 text-sm font-medium text-foreground transition-colors hover:bg-accent/10 disabled:pointer-events-none disabled:opacity-50"
      }
    >
      {submitting ? "Cerrando…" : "Cerrar sesión"}
    </button>
  );
}
