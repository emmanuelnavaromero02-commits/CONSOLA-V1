"use client";

import { useEffect, useMemo, useState } from "react";

import { toast } from "sonner";

import { useChat } from "@/lib/copilot/useChat";
import type { Message as MessageType, PendingAction } from "@/lib/copilot/types";

import { ApprovalGateDialog } from "./ApprovalGateDialog";
import { ChatMessages }       from "./ChatMessages";
import { ConversationSidebar } from "./ConversationSidebar";
import { DraftModal }          from "./DraftModal";
import { MemoryDrawer }        from "./MemoryDrawer";
import { MessageInput }        from "./MessageInput";
import { SlashCommandsPalette, type SlashCommand } from "./SlashCommandsPalette";
import { SuggestedPrompts }    from "./SuggestedPrompts";

/**
 * v1.44.4 Task A — chat surface orchestrator.
 *
 * Owns the "which conversation is open" state plus the four
 * modal/drawer toggles (memory drawer, draft modal, slash
 * palette, approval gate). Wires up the slash-command catalog
 * including the enterprise prompt templates the brief specifies
 * (/reporte_mensual, /turnover_analysis, /cash_position, …).
 *
 * Flow:
 *   1. Page mounts → useChat loads conversation list.
 *   2. Empty state: SuggestedPrompts. Click → creates a new
 *      conversation and sends the prompt as the first message.
 *   3. Active conversation: ChatMessages + MessageInput.
 *   4. Send response carries requires_approval → ApprovalGateDialog
 *      opens with the pending_actions; "Sí, ejecutar" calls
 *      approveMutation.
 *
 * The component is intentionally thick — it owns flow state so
 * the leaf components (Message, CitationCard, etc.) stay
 * presentational and the page file stays a one-liner.
 */
export function ChatLayout() {
  const [activeId, setActiveId] = useState<string | null>(null);

  const {
    listConversationsQuery,
    conversationQuery,
    sendMutation,
    approveMutation,
    createConversationMutation,
  } = useChat(activeId);

  const conversations = listConversationsQuery.data ?? [];
  const messages: MessageType[] =
    conversationQuery.data?.messages ?? [];

  // Auto-select the most recent conversation on first load so
  // operators don't land on a confusing empty state when they
  // already have history.
  useEffect(() => {
    if (activeId) return;
    const first = conversations[0];
    if (first) setActiveId(first.id);
  }, [activeId, conversations]);

  // ── Modal / drawer state ────────────────────────────────────
  const [paletteOpen, setPaletteOpen] = useState(false);
  const [paletteQuery, setPaletteQuery] = useState("");
  const [memoryOpen,  setMemoryOpen]  = useState(false);
  const [draftOpen,   setDraftOpen]   = useState(false);
  const [draftSeed,   setDraftSeed]   = useState<string | undefined>();

  // ── Approval gate state ─────────────────────────────────────
  const [pendingApproval, setPendingApproval] = useState<{
    messageId: string;
    actions:   PendingAction[];
  } | null>(null);

  async function ensureConversation(): Promise<string> {
    if (activeId) return activeId;
    const created = await createConversationMutation.mutateAsync({});
    setActiveId(created.id);
    return created.id;
  }

  async function handleSend(text: string) {
    try {
      const cid  = await ensureConversation();
      const data = await sendMutation.mutateAsync({
        conversationId: cid,
        message:        text,
      });
      if (data.requires_approval && data.pending_actions.length > 0) {
        setPendingApproval({
          messageId: data.message_id,
          actions:   data.pending_actions,
        });
      }
    } catch (err) {
      const msg = err instanceof Error ? err.message : "Error desconocido.";
      toast.error(`No se pudo enviar: ${msg}`);
    }
  }

  async function handleApprove() {
    if (!pendingApproval || !activeId) return;
    try {
      await approveMutation.mutateAsync({
        conversationId: activeId,
        messageId:      pendingApproval.messageId,
      });
      setPendingApproval(null);
      toast.success("Acción ejecutada.");
    } catch (err) {
      const msg = err instanceof Error ? err.message : "Error desconocido.";
      toast.error(`No se pudo aprobar: ${msg}`);
    }
  }

  // ── Slash command catalog ───────────────────────────────────
  const commands = useMemo<SlashCommand[]>(() => [
    // Básicos
    { id: "help",      group: "Básicos",  label: "Ayuda",
      description: "Lista todos los comandos disponibles.",
      onSelect: () => setPaletteQuery("") },
    { id: "memoria",   group: "Básicos",  label: "Memoria",
      description: "Ver y editar lo que sabe el copiloto.",
      onSelect: () => setMemoryOpen(true) },
    { id: "redactar",  group: "Básicos",  label: "Redactar",
      description: "Generar un borrador (email, mensaje, nota).",
      onSelect: () => { setDraftSeed(undefined); setDraftOpen(true); } },
    { id: "clear",     group: "Básicos",  label: "Nueva conversación",
      description: "Inicia una conversación limpia.",
      onSelect: () => { setActiveId(null); } },
    { id: "briefing",  group: "Básicos",  label: "Briefing del día",
      description: "Ver alertas y novedades de hoy.",
      onSelect: () => { void handleSend("Dame el briefing del día."); } },
    { id: "historial", group: "Básicos",  label: "Conversaciones recientes",
      description: "Lista de las últimas conversaciones.",
      onSelect: () => { /* sidebar ya está visible */ } },

    // Reportes enterprise — cada uno envía un prompt prearmado.
    ...[
      { id: "reporte_mensual",   label: "Reporte mensual",
        prompt: "Genera el reporte ejecutivo del mes con KPIs principales." },
      { id: "turnover_analysis", label: "Análisis de rotación",
        prompt: "Análisis de rotación de personal del último trimestre." },
      { id: "cash_position",     label: "Estado de caja",
        prompt: "Dame el estado actual de caja por banco y moneda." },
      { id: "payroll_summary",   label: "Resumen de nómina",
        prompt: "Resumen de nómina del último período por departamento." },
      { id: "headcount",         label: "Headcount",
        prompt: "Empleados activos por departamento y por país." },
      { id: "aging_report",      label: "Antigüedad de cuentas",
        prompt: "Reporte de antigüedad de cuentas por cobrar (aging)." },
      { id: "audit_compliance",  label: "Compliance GDPR/SOX",
        prompt: "Reporte de cumplimiento GDPR y SOX del último período." },
      { id: "forecast",          label: "Forecast Q+1",
        prompt: "Forecast operativo del próximo trimestre." },
    ].map<SlashCommand>((c) => ({
      id:          c.id,
      label:       c.label,
      description: c.prompt,
      group:       "Reportes",
      onSelect:    () => { void handleSend(c.prompt); },
    })),
  // handleSend is stable enough — sendMutation identity doesn't
  // change between renders. eslint will rightly warn; the deps
  // are intentional.
  // eslint-disable-next-line react-hooks/exhaustive-deps
  ], []);

  // ── Render ──────────────────────────────────────────────────
  const showEmpty =
    !activeId ||
    (!conversationQuery.isLoading && messages.length === 0);

  return (
    <div className="flex h-full">
      <div className="hidden w-72 shrink-0 md:block">
        <ConversationSidebar
          conversations={conversations}
          activeId={activeId}
          onSelect={setActiveId}
          onCreate={() => setActiveId(null)}
          loading={listConversationsQuery.isLoading}
        />
      </div>

      <main
        aria-label="Conversación"
        className="flex min-w-0 flex-1 flex-col bg-background"
      >
        <header className="flex items-center justify-between gap-3 border-b px-4 py-3">
          <h1 className="text-sm font-semibold tracking-tight">
            {conversationQuery.data?.conversation.title?.trim()
              || "Nueva conversación"}
          </h1>
          <div className="flex items-center gap-1">
            <button
              type="button"
              onClick={() => setMemoryOpen(true)}
              aria-label="Abrir memoria del copiloto"
              className="inline-flex min-h-[44px] items-center justify-center rounded-md px-3 text-xs font-medium hover:bg-accent/10 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
            >
              Memoria
            </button>
            <button
              type="button"
              onClick={() => { setDraftSeed(undefined); setDraftOpen(true); }}
              aria-label="Abrir redactor"
              className="inline-flex min-h-[44px] items-center justify-center rounded-md px-3 text-xs font-medium hover:bg-accent/10 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
            >
              Redactar
            </button>
          </div>
        </header>

        {showEmpty ? (
          <div className="flex flex-1 items-start justify-center overflow-y-auto p-6">
            <div className="w-full max-w-2xl space-y-6">
              <div>
                <h2 className="text-lg font-semibold tracking-tight">
                  ¿Qué necesitas hoy?
                </h2>
                <p className="mt-1 text-sm text-muted-foreground">
                  Pídele algo al copiloto o usa una sugerencia para empezar.
                </p>
              </div>
              <SuggestedPrompts onSelect={(p) => void handleSend(p)} />
            </div>
          </div>
        ) : (
          <ChatMessages messages={messages} pending={sendMutation.isPending} />
        )}

        <MessageInput
          onSend={(t) => void handleSend(t)}
          disabled={sendMutation.isPending}
          onSlash={() => {
            setPaletteQuery("");
            setPaletteOpen(true);
          }}
        />
      </main>

      <SlashCommandsPalette
        open={paletteOpen}
        query={paletteQuery}
        commands={commands}
        onClose={() => setPaletteOpen(false)}
      />

      <MemoryDrawer
        open={memoryOpen}
        onClose={() => setMemoryOpen(false)}
      />

      <DraftModal
        open={draftOpen}
        seed={draftSeed}
        onClose={() => setDraftOpen(false)}
      />

      <ApprovalGateDialog
        open={pendingApproval !== null}
        messageId={pendingApproval?.messageId ?? ""}
        pending={pendingApproval?.actions ?? []}
        onApprove={handleApprove}
        onCancel={() => setPendingApproval(null)}
        submitting={approveMutation.isPending}
      />
    </div>
  );
}
