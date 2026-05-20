"use client";

import { useCallback, useEffect, useMemo, useState } from "react";

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
 * Module-scope catalog of enterprise prompt commands. Keeping
 * the data outside the component so the slash-palette catalog
 * can't accidentally capture a stale `handleSend` closure.
 * Built command instances reference the live page handlers via
 * dispatch keys rather than direct function references.
 */
type CommandKind =
  | "open-palette"
  | "open-memory"
  | "open-draft"
  | "clear-conversation"
  | "show-history"
  | "send-prompt";

interface CommandMeta {
  id:          string;
  label:       string;
  description: string;
  group:       "Básicos" | "Reportes";
  kind:        CommandKind;
  /** Prompt body for ``kind === "send-prompt"`` commands. */
  prompt?:     string;
}

const COMMAND_CATALOG: CommandMeta[] = [
  // Básicos — open drawer / modal / palette / reset
  { id: "help",      group: "Básicos", kind: "open-palette",
    label: "Ayuda",
    description: "Lista todos los comandos disponibles." },
  { id: "memoria",   group: "Básicos", kind: "open-memory",
    label: "Memoria",
    description: "Ver y editar lo que sabe el copiloto." },
  { id: "redactar",  group: "Básicos", kind: "open-draft",
    label: "Redactar",
    description: "Generar un borrador (email, mensaje, nota)." },
  { id: "clear",     group: "Básicos", kind: "clear-conversation",
    label: "Nueva conversación",
    description: "Inicia una conversación limpia." },
  { id: "briefing",  group: "Básicos", kind: "send-prompt",
    label: "Briefing del día",
    description: "Ver alertas y novedades de hoy.",
    prompt: "Dame el briefing del día." },
  { id: "historial", group: "Básicos", kind: "show-history",
    label: "Conversaciones recientes",
    description: "Lista de las últimas conversaciones." },

  // Reportes enterprise
  { id: "reporte_mensual",   group: "Reportes", kind: "send-prompt",
    label: "Reporte mensual",
    description: "Genera el reporte ejecutivo del mes con KPIs principales.",
    prompt: "Genera el reporte ejecutivo del mes con KPIs principales." },
  { id: "turnover_analysis", group: "Reportes", kind: "send-prompt",
    label: "Análisis de rotación",
    description: "Análisis de rotación de personal del último trimestre.",
    prompt: "Análisis de rotación de personal del último trimestre." },
  { id: "cash_position",     group: "Reportes", kind: "send-prompt",
    label: "Estado de caja",
    description: "Estado actual de caja por banco y moneda.",
    prompt: "Dame el estado actual de caja por banco y moneda." },
  { id: "payroll_summary",   group: "Reportes", kind: "send-prompt",
    label: "Resumen de nómina",
    description: "Resumen de nómina del último período por departamento.",
    prompt: "Resumen de nómina del último período por departamento." },
  { id: "headcount",         group: "Reportes", kind: "send-prompt",
    label: "Headcount",
    description: "Empleados activos por departamento y por país.",
    prompt: "Empleados activos por departamento y por país." },
  { id: "aging_report",      group: "Reportes", kind: "send-prompt",
    label: "Antigüedad de cuentas",
    description: "Aging de cuentas por cobrar.",
    prompt: "Reporte de antigüedad de cuentas por cobrar (aging)." },
  { id: "audit_compliance",  group: "Reportes", kind: "send-prompt",
    label: "Compliance GDPR/SOX",
    description: "Reporte de cumplimiento del último período.",
    prompt: "Reporte de cumplimiento GDPR y SOX del último período." },
  { id: "forecast",          group: "Reportes", kind: "send-prompt",
    label: "Forecast Q+1",
    description: "Forecast operativo del próximo trimestre.",
    prompt: "Forecast operativo del próximo trimestre." },
];

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

  // Round 1 review: do NOT auto-select on cold mount — a
  // returning user might want a fresh thread, and a brand-new
  // user must see SuggestedPrompts (the onboarding surface)
  // rather than being yanked into stale history. The sidebar
  // gives one-tap access to past conversations. If the brief
  // ever wants "land on last conversation", make it opt-in via
  // ?continue=last query param.

  // ── Modal / drawer state ────────────────────────────────────
  const [paletteOpen, setPaletteOpen] = useState(false);
  const [paletteQuery, setPaletteQuery] = useState("");
  const [memoryOpen,  setMemoryOpen]  = useState(false);
  const [draftOpen,   setDraftOpen]   = useState(false);
  const [draftSeed,   setDraftSeed]   = useState<string | undefined>();

  // Mobile sidebar toggle — sub-md the sidebar is hidden by
  // default; this state opens it as a Sheet-style overlay so
  // phone users can still navigate conversations + start a
  // new one. (Frontend P0 from Round 1 review.)
  const [mobileSidebarOpen, setMobileSidebarOpen] = useState(false);

  // ── Approval gate state ─────────────────────────────────────
  // Captures the conversation id at the moment the gate opened
  // so a user switching activeId mid-flight can't approve
  // against the wrong conversation. (Round 1 review P2.)
  const [pendingApproval, setPendingApproval] = useState<{
    conversationId: string;
    messageId:      string;
    actions:        PendingAction[];
  } | null>(null);
  const [approveError, setApproveError] = useState<string | null>(null);

  const ensureConversation = useCallback(async (): Promise<string> => {
    if (activeId) return activeId;
    const created = await createConversationMutation.mutateAsync({});
    setActiveId(created.id);
    return created.id;
  }, [activeId, createConversationMutation]);

  const handleSend = useCallback(async (text: string) => {
    try {
      const cid  = await ensureConversation();
      const data = await sendMutation.mutateAsync({
        conversationId: cid,
        message:        text,
      });
      if (data.requires_approval && data.pending_actions.length > 0) {
        setPendingApproval({
          conversationId: cid,
          messageId:      data.message_id,
          actions:        data.pending_actions,
        });
        setApproveError(null);
      }
    } catch (err) {
      const msg = err instanceof Error ? err.message : "Error desconocido.";
      toast.error(`No se pudo enviar: ${msg}`);
    }
  }, [ensureConversation, sendMutation]);

  const handleApprove = useCallback(async () => {
    if (!pendingApproval) return;
    setApproveError(null);
    try {
      await approveMutation.mutateAsync({
        conversationId: pendingApproval.conversationId,
        messageId:      pendingApproval.messageId,
      });
      setPendingApproval(null);
      toast.success("Acción ejecutada.");
    } catch (err) {
      const msg = err instanceof Error ? err.message : "Error desconocido.";
      // Keep the dialog open + render the error inline so the
      // user can retry or cancel. (Frontend P0 from Round 1.)
      setApproveError(msg);
      toast.error(`No se pudo aprobar: ${msg}`);
    }
  }, [approveMutation, pendingApproval]);

  // ── Slash command dispatcher ────────────────────────────────
  // ``dispatchCommand`` reads the latest handlers at call time
  // so a slash-palette click from a stale catalog still
  // operates against the current activeId / pending state.
  const dispatchCommand = useCallback((cmd: CommandMeta) => {
    switch (cmd.kind) {
      case "open-palette":
        setPaletteQuery("");
        setPaletteOpen(true);
        return;
      case "open-memory":
        setMemoryOpen(true);
        return;
      case "open-draft":
        setDraftSeed(undefined);
        setDraftOpen(true);
        return;
      case "clear-conversation":
        setActiveId(null);
        return;
      case "show-history":
        // Sidebar already shows history; open the mobile
        // sidebar so phone users see it, and give desktop users
        // visible feedback instead of a no-op.
        setMobileSidebarOpen(true);
        toast.info("El historial está visible en la barra lateral.");
        return;
      case "send-prompt":
        if (cmd.prompt) void handleSend(cmd.prompt);
        return;
    }
  }, [handleSend]);

  const commands = useMemo<SlashCommand[]>(() =>
    COMMAND_CATALOG.map((meta) => ({
      id:          meta.id,
      label:       meta.label,
      description: meta.description,
      group:       meta.group,
      onSelect:    () => dispatchCommand(meta),
    })),
  [dispatchCommand]);

  // ── Render ──────────────────────────────────────────────────
  const showEmpty =
    !activeId ||
    (!conversationQuery.isLoading && messages.length === 0);

  return (
    <div className="flex h-full">
      {/* Desktop sidebar — visible from md (≥768 px). */}
      <div className="hidden w-72 shrink-0 md:block">
        <ConversationSidebar
          conversations={conversations}
          activeId={activeId}
          onSelect={(id) => {
            setActiveId(id);
            setMobileSidebarOpen(false);
          }}
          onCreate={() => {
            setActiveId(null);
            setMobileSidebarOpen(false);
          }}
          loading={listConversationsQuery.isLoading}
          errorLoading={listConversationsQuery.isError}
          onRetry={() => listConversationsQuery.refetch()}
        />
      </div>

      {/* Mobile sidebar — drawer overlay (Round 1 P0 fix). */}
      {mobileSidebarOpen ? (
        <div
          role="dialog"
          aria-modal="true"
          aria-label="Conversaciones"
          className="fixed inset-0 z-40 flex md:hidden"
        >
          <div
            className="flex-1 bg-black/40"
            onClick={() => setMobileSidebarOpen(false)}
            aria-hidden
          />
          <div className="w-80 max-w-[80vw] shrink-0">
            <ConversationSidebar
              conversations={conversations}
              activeId={activeId}
              onSelect={(id) => {
                setActiveId(id);
                setMobileSidebarOpen(false);
              }}
              onCreate={() => {
                setActiveId(null);
                setMobileSidebarOpen(false);
              }}
              loading={listConversationsQuery.isLoading}
              errorLoading={listConversationsQuery.isError}
              onRetry={() => listConversationsQuery.refetch()}
            />
          </div>
        </div>
      ) : null}

      <main
        aria-label="Conversación"
        className="flex min-w-0 flex-1 flex-col bg-background"
      >
        <header className="flex items-center justify-between gap-2 border-b px-3 py-3 sm:px-4">
          <div className="flex min-w-0 items-center gap-1.5">
            {/* Mobile hamburger to open the sidebar drawer. */}
            <button
              type="button"
              onClick={() => setMobileSidebarOpen(true)}
              aria-label="Abrir conversaciones"
              className="inline-flex min-h-[44px] min-w-[44px] items-center justify-center rounded-md hover:bg-accent/10 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring md:hidden"
            >
              ☰
            </button>
            <h1 className="truncate text-sm font-semibold tracking-tight">
              {conversationQuery.data?.conversation.title?.trim()
                || "Nueva conversación"}
            </h1>
          </div>
          <div className="flex shrink-0 items-center gap-1">
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
                <p className="mt-2 text-xs text-muted-foreground">
                  Tip: escribe <kbd className="rounded border bg-muted px-1.5 py-0.5 font-mono text-[10px]">/</kbd>
                  {" "}para ver los comandos disponibles, o pulsa{" "}
                  <span className="font-medium">Memoria</span> arriba para
                  enseñarle algo sobre tu operación.
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
        onCancel={() => {
          setPendingApproval(null);
          setApproveError(null);
        }}
        submitting={approveMutation.isPending}
        error={approveError}
      />
    </div>
  );
}
