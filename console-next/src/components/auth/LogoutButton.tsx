"use client";

import { useState } from "react";
import { useRouter } from "next/navigation";
import { toast } from "sonner";
import { api } from "@/lib/api";
import { deleteCookie } from "@/lib/cookies";
import { ACTIVE_WORKSPACE_COOKIE } from "@/lib/workspace-context";

export function LogoutButton({ className }: { className?: string }) {
  const router = useRouter();
  const [submitting, setSubmitting] = useState(false);

  async function handleLogout() {
    if (submitting) return;
    setSubmitting(true);
    try {
      await api.post<{ logged_out: boolean }>("/auth/logout", {});
      try {
        window.localStorage.removeItem("omega_user_email");
      } catch {
        /* Safari private mode / SSR — best-effort cleanup */
      }
      deleteCookie(ACTIVE_WORKSPACE_COOKIE);
      router.replace("/login");
      router.refresh();
    } catch {
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
        "inline-flex min-h-[44px] items-center justify-center rounded-md border border-border bg-background px-3 text-sm font-medium text-foreground transition-colors hover:bg-accent/10 disabled:pointer-events-none disabled:opacity-50"
      }
    >
      {submitting ? "Cerrando…" : "Cerrar sesión"}
    </button>
  );
}
