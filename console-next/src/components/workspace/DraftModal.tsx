"use client";

import { useEffect, useRef, useState } from "react";

import { toast } from "sonner";

import { generateDraft } from "@/lib/copilot/client";
import type { Draft, DraftTone } from "@/lib/copilot/types";

interface Props {
  open:    boolean;
  onClose: () => void;
  /** Optional initial prompt that seeds the request body. */
  seed?:   string;
}

/**
 * v1.44.4 Task A — draft modal.
 *
 * Wraps POST /api/copilot/drafts/generate so the operator can
 * ask the copilot to produce an email / message draft without
 * leaving the chat surface. Tone is selectable
 * (formal / friendly / concise) and a "Regenerar" button issues
 * a fresh request with the same prompt + new tone.
 *
 * The result body is copyable via the OS clipboard API; "Enviar
 * por email" is intentionally NOT wired yet — the backend lacks
 * a generic outbound-mail helper in this sprint, so we surface a
 * "próximamente" message rather than ship a broken affordance.
 *
 * Native dialog shape (no Radix dependency) — same rationale as
 * the ApprovalGateDialog.
 */
const TONES: { id: DraftTone; label: string }[] = [
  { id: "formal",   label: "Formal"   },
  { id: "friendly", label: "Cercano"  },
  { id: "concise",  label: "Conciso"  },
];


export function DraftModal({ open, onClose, seed }: Props) {
  const [prompt, setPrompt] = useState(seed ?? "");
  const [tone,   setTone]   = useState<DraftTone>("formal");
  const [draft,  setDraft]  = useState<Draft | null>(null);
  const [busy,   setBusy]   = useState(false);

  const cancelRef = useRef<HTMLButtonElement | null>(null);

  useEffect(() => {
    if (!open) return;
    setPrompt(seed ?? "");
    setDraft(null);
    setBusy(false);
    cancelRef.current?.focus();

    function onKey(e: KeyboardEvent) {
      if (e.key === "Escape") onClose();
    }
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [open, seed, onClose]);

  async function generate() {
    if (!prompt.trim()) return;
    setBusy(true);
    try {
      const result = await generateDraft({ prompt: prompt.trim(), tone });
      setDraft(result);
    } catch (err) {
      toast.error("No se pudo generar el borrador. Intenta de nuevo.");
    } finally {
      setBusy(false);
    }
  }

  async function copyBody() {
    if (!draft?.body) return;
    try {
      await navigator.clipboard.writeText(draft.body);
      toast.success("Borrador copiado al portapapeles.");
    } catch {
      toast.error("No se pudo copiar. Selecciona y copia manualmente.");
    }
  }

  if (!open) return null;

  return (
    <div
      role="dialog"
      aria-modal="true"
      aria-labelledby="draft-modal-title"
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/50 p-4"
      onClick={(e) => {
        if (e.target === e.currentTarget) onClose();
      }}
    >
      <div className="flex max-h-[85vh] w-full max-w-2xl flex-col rounded-lg border bg-card shadow-lg">
        <header className="flex items-center justify-between border-b px-5 py-4">
          <h2 id="draft-modal-title" className="text-base font-semibold tracking-tight">
            Redactar con el copiloto
          </h2>
          <button
            ref={cancelRef}
            type="button"
            onClick={onClose}
            aria-label="Cerrar redactor"
            className="inline-flex min-h-[44px] min-w-[44px] items-center justify-center rounded-md hover:bg-accent/10 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
          >
            ✕
          </button>
        </header>

        <div className="flex flex-1 flex-col gap-4 overflow-y-auto p-5">
          <label className="space-y-1.5 text-sm">
            <span className="font-medium">¿Qué deberías redactar?</span>
            <textarea
              value={prompt}
              onChange={(e) => setPrompt(e.target.value)}
              rows={3}
              placeholder="Ej. Email al equipo de finanzas pidiendo el corte de caja semanal."
              className="w-full resize-y rounded-md border border-input bg-background px-3 py-2 text-sm focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
            />
          </label>

          <fieldset className="space-y-1.5 text-sm">
            <legend className="font-medium">Tono</legend>
            <div className="flex flex-wrap gap-2">
              {TONES.map((t) => (
                <button
                  key={t.id}
                  type="button"
                  onClick={() => setTone(t.id)}
                  aria-pressed={tone === t.id}
                  className={
                    "inline-flex min-h-[44px] items-center justify-center rounded-md border px-4 text-sm font-medium transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring " +
                    (tone === t.id
                      ? "bg-primary text-primary-foreground"
                      : "bg-background hover:bg-accent/5")
                  }
                >
                  {t.label}
                </button>
              ))}
            </div>
          </fieldset>

          {draft ? (
            <section aria-label="Borrador generado" className="space-y-2">
              {draft.subject ? (
                <div className="rounded-md border bg-background p-3 text-sm">
                  <span className="text-xs font-semibold uppercase tracking-wider text-muted-foreground">
                    Asunto
                  </span>
                  <p className="mt-1 font-medium">{draft.subject}</p>
                </div>
              ) : null}
              <div className="rounded-md border bg-background p-3 text-sm whitespace-pre-wrap">
                {draft.body}
              </div>
            </section>
          ) : null}
        </div>

        <footer className="flex flex-col-reverse gap-2 border-t px-5 py-4 sm:flex-row sm:justify-between">
          <button
            type="button"
            onClick={() => toast("Envío por email disponible en v1.44.4.1.")}
            disabled={!draft}
            className="inline-flex min-h-[44px] items-center justify-center rounded-md border bg-background px-4 text-sm font-medium hover:bg-accent/5 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring disabled:pointer-events-none disabled:opacity-60"
          >
            Enviar por email
          </button>
          <div className="flex flex-col-reverse gap-2 sm:flex-row">
            <button
              type="button"
              onClick={copyBody}
              disabled={!draft?.body}
              className="inline-flex min-h-[44px] items-center justify-center rounded-md border bg-background px-4 text-sm font-medium hover:bg-accent/5 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring disabled:pointer-events-none disabled:opacity-60"
            >
              Copiar
            </button>
            <button
              type="button"
              onClick={() => void generate()}
              disabled={busy || !prompt.trim()}
              className="inline-flex min-h-[44px] items-center justify-center rounded-md bg-primary px-4 text-sm font-medium text-primary-foreground hover:bg-primary/90 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring disabled:pointer-events-none disabled:opacity-60"
            >
              {busy ? "Generando…" : draft ? "Regenerar" : "Generar"}
            </button>
          </div>
        </footer>
      </div>
    </div>
  );
}
