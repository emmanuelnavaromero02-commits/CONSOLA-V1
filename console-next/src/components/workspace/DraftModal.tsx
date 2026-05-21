"use client";

import { useEffect, useRef, useState } from "react";

import { toast } from "sonner";

import { generateDraft } from "@/lib/copilot/client";
import type { Draft, DraftTone } from "@/lib/copilot/types";

interface Props {
  open:    boolean;
  onClose: () => void;
  /** Optional initial ``about`` prompt that seeds the request. */
  seed?:   string;
}

/**
 * v1.44.4 Task A — draft modal.
 *
 * Wraps POST /api/copilot/drafts/generate. Backend requires
 * ``{kind, about, tone?, audience?, title?, metadata?}`` and
 * returns ``{ok, draft}``. ``kind`` is required; ``about`` is
 * the free-form prompt; ``audience`` is the recipient; ``title``
 * seeds the subject line where relevant.
 *
 * Tone presets match the backend allowlist:
 *   formal | neutral | friendly | urgent
 *
 * "Enviar por email" stays disabled with a "Próximamente"
 * badge — the backend mail-out helper is v1.44.4.1 scope.
 *
 * Accessibility:
 *   - role=dialog + aria-modal.
 *   - Focus lands on the textarea (the action that needs
 *     attention) and returns to the trigger on close.
 *   - Escape closes via onClose.
 *   - Backdrop click closes (separate child div with the
 *     handler so the panel itself can be clicked without
 *     dismissing).
 */
const KINDS: { id: string; label: string; description: string }[] = [
  { id: "email",   label: "Email",   description: "Correo formal o casual" },
  { id: "memo",    label: "Memo",    description: "Memorando interno breve" },
  { id: "note",    label: "Nota",    description: "Nota interna o memo" },
  { id: "report",  label: "Reporte", description: "Informe ejecutivo breve" },
];

const TONES: { id: DraftTone; label: string }[] = [
  { id: "formal",   label: "Formal"   },
  { id: "neutral",  label: "Neutro"   },
  { id: "friendly", label: "Cercano"  },
  { id: "urgent",   label: "Urgente"  },
];

const MAX_ABOUT_CHARS    = 2_000;
const MAX_AUDIENCE_CHARS = 120;
const MAX_TITLE_CHARS    = 120;


export function DraftModal({ open, onClose, seed }: Props) {
  const [kind,     setKind]     = useState<string>("email");
  const [about,    setAbout]    = useState(seed ?? "");
  const [audience, setAudience] = useState("");
  const [title,    setTitle]    = useState("");
  const [tone,     setTone]     = useState<DraftTone>("formal");

  const [draft, setDraft] = useState<Draft | null>(null);
  const [busy,  setBusy]  = useState(false);

  const previousFocus = useRef<HTMLElement | null>(null);
  const textareaRef   = useRef<HTMLTextAreaElement | null>(null);

  useEffect(() => {
    if (!open) return;

    previousFocus.current = (document.activeElement instanceof HTMLElement)
      ? document.activeElement
      : null;

    setAbout(seed ?? "");
    setAudience("");
    setTitle("");
    setKind("email");
    setTone("formal");
    setDraft(null);
    setBusy(false);

    requestAnimationFrame(() => textareaRef.current?.focus());

    function onKey(e: KeyboardEvent) {
      if (e.key === "Escape") onClose();
    }
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("keydown", onKey);
      previousFocus.current?.focus();
    };
  }, [open, seed, onClose]);

  async function generate() {
    const aboutTrimmed = about.trim();
    if (!aboutTrimmed) return;
    setBusy(true);
    try {
      const result = await generateDraft({
        kind,
        about:    aboutTrimmed,
        tone,
        audience: audience.trim() || undefined,
        title:    title.trim() || undefined,
      });
      setDraft(result);
    } catch (err) {
      const msg = err instanceof Error ? err.message : "Error desconocido.";
      toast.error(`No se pudo generar el borrador: ${msg}`);
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
      className="fixed inset-0 z-50 flex items-center justify-center p-4"
    >
      <div
        className="absolute inset-0 bg-black/50"
        onClick={onClose}
        aria-hidden
      />
      <div className="relative flex max-h-[85vh] w-full max-w-2xl flex-col rounded-lg border bg-card shadow-lg">
        <header className="flex items-center justify-between border-b px-5 py-4">
          <h2 id="draft-modal-title" className="text-base font-semibold tracking-tight">
            Redactar con el copiloto
          </h2>
          <button
            type="button"
            onClick={onClose}
            aria-label="Cerrar redactor"
            className="inline-flex min-h-[44px] min-w-[44px] items-center justify-center rounded-md hover:bg-accent/10 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
          >
            ✕
          </button>
        </header>

        <div className="flex flex-1 flex-col gap-4 overflow-y-auto p-5">
          <fieldset className="space-y-1.5 text-sm">
            <legend className="font-medium">Tipo</legend>
            <div className="flex flex-wrap gap-2">
              {KINDS.map((k) => (
                <button
                  key={k.id}
                  type="button"
                  onClick={() => setKind(k.id)}
                  aria-pressed={kind === k.id}
                  title={k.description}
                  className={
                    "inline-flex min-h-[44px] items-center justify-center rounded-md border px-4 text-sm font-medium transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring " +
                    (kind === k.id
                      ? "bg-primary text-primary-foreground"
                      : "bg-background hover:bg-accent/5")
                  }
                >
                  {k.label}
                </button>
              ))}
            </div>
          </fieldset>

          <label className="space-y-1.5 text-sm">
            <span className="font-medium">¿Qué quieres redactar?</span>
            <textarea
              ref={textareaRef}
              value={about}
              onChange={(e) => setAbout(e.target.value)}
              rows={3}
              maxLength={MAX_ABOUT_CHARS}
              placeholder="Describe el destinatario, el contexto y el objetivo. Ej. Email al equipo de finanzas pidiendo el corte de caja semanal con cierre del viernes."
              className="w-full resize-y rounded-md border border-input bg-background px-3 py-2 text-sm focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
            />
          </label>

          <div className="grid gap-3 sm:grid-cols-2">
            <label className="space-y-1.5 text-sm">
              <span className="font-medium">Destinatario</span>
              <input
                value={audience}
                onChange={(e) => setAudience(e.target.value)}
                maxLength={MAX_AUDIENCE_CHARS}
                placeholder="Ej. Equipo de finanzas"
                className="min-h-[44px] w-full rounded-md border border-input bg-background px-3 text-sm focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
              />
            </label>
            <label className="space-y-1.5 text-sm">
              <span className="font-medium">Asunto (opcional)</span>
              <input
                value={title}
                onChange={(e) => setTitle(e.target.value)}
                maxLength={MAX_TITLE_CHARS}
                placeholder="Ej. Corte de caja semanal"
                className="min-h-[44px] w-full rounded-md border border-input bg-background px-3 text-sm focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
              />
            </label>
          </div>

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
              {draft.title ? (
                <div className="rounded-md border bg-background p-3 text-sm">
                  <span className="text-xs font-semibold uppercase tracking-wider text-muted-foreground">
                    Asunto
                  </span>
                  <p className="mt-1 font-medium">{draft.title}</p>
                </div>
              ) : null}
              <div className="whitespace-pre-wrap rounded-md border bg-background p-3 text-sm">
                {draft.body}
              </div>
            </section>
          ) : null}
        </div>

        <footer className="flex flex-col-reverse gap-2 border-t px-5 py-4 sm:flex-row sm:justify-between">
          <button
            type="button"
            disabled
            aria-disabled="true"
            title="Disponible en v1.44.4.1"
            className="inline-flex min-h-[44px] items-center justify-center rounded-md border bg-background px-4 text-sm font-medium text-muted-foreground"
          >
            Enviar por email
            <span className="ml-2 rounded-full bg-muted px-2 py-0.5 text-[10px] font-medium uppercase tracking-wider">
              Próximamente
            </span>
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
              disabled={busy || !about.trim()}
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
