"use client";

import { useCallback, useEffect, useRef, useState } from "react";

import type { ApiError } from "@/lib/api";
import {
  approveAction,
  createConversation,
  getConversation,
  streamMessage,
  type CopilotPageContext,
} from "@/lib/copilot/client";
import { readPageContext } from "@/lib/copilot/page-context";
import type { Message as MessageType, PendingAction } from "@/lib/copilot/types";

import { ApprovalGateDialog } from "@/components/workspace/ApprovalGateDialog";
import { Message } from "@/components/workspace/Message";

const CONVERSATION_KEY = "omega-copilot-dock-conversation";
const DRAFT_KEY = "omega-copilot-dock-draft";
const MAX_MESSAGE_CHARS = 8_000;
const OFFLINE_ERROR = "Sin conexión al asistente";

function readSession(key: string): string {
  try {
    return window.sessionStorage.getItem(key) || "";
  } catch {
    return "";
  }
}

function writeSession(key: string, value: string): void {
  try {
    if (value) {
      window.sessionStorage.setItem(key, value);
    } else {
      window.sessionStorage.removeItem(key);
    }
  } catch {
    /* best-effort persistence */
  }
}

function errorStatus(error: unknown): number | undefined {
  return error instanceof Error ? (error as ApiError).status : undefined;
}

function publicError(error: unknown): string {
  const status = errorStatus(error);
  // No HTTP status (network failure) or 5xx: honest offline copy, never raw messages.
  if (status === undefined || status >= 500) return OFFLINE_ERROR;
  if (error instanceof Error && error.message) return error.message;
  return OFFLINE_ERROR;
}

export function CopilotDockPanel({ route }: { route: string }) {
  const [conversationId, setConversationId] = useState<string | null>(null);
  const [messages, setMessages] = useState<MessageType[]>([]);
  const [draft, setDraftState] = useState(() => readSession(DRAFT_KEY));
  const [streaming, setStreaming] = useState<string | null>(null);
  const [sending, setSending] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [pendingApproval, setPendingApproval] = useState<{
    conversationId: string;
    messageId: string;
    actions: PendingAction[];
  } | null>(null);
  const [approving, setApproving] = useState(false);
  const [approveError, setApproveError] = useState<string | null>(null);
  const scrollRef = useRef<HTMLDivElement | null>(null);
  const mountedRef = useRef(true);
  const abortRef = useRef<AbortController | null>(null);

  useEffect(() => {
    mountedRef.current = true;
    return () => {
      mountedRef.current = false;
      abortRef.current?.abort();
    };
  }, []);

  const setDraft = useCallback((value: string) => {
    setDraftState(value);
    writeSession(DRAFT_KEY, value);
  }, []);

  const forgetConversation = useCallback(() => {
    setConversationId(null);
    writeSession(CONVERSATION_KEY, "");
  }, []);

  useEffect(() => {
    const stored = readSession(CONVERSATION_KEY);
    if (!stored) return;
    let cancelled = false;
    getConversation(stored)
      .then((detail) => {
        if (cancelled) return;
        setConversationId(stored);
        setMessages(detail.messages.filter((m) => m.role !== "system"));
      })
      .catch((err: unknown) => {
        if (cancelled) return;
        const status = errorStatus(err);
        if (status === 403 || status === 404) {
          forgetConversation();
        } else {
          setError(publicError(err));
        }
      });
    return () => {
      cancelled = true;
    };
  }, [forgetConversation]);

  useEffect(() => {
    const el = scrollRef.current;
    if (el) el.scrollTop = el.scrollHeight;
  }, [messages, streaming]);

  const handleSend = useCallback(async () => {
    const text = draft.trim();
    if (!text || sending) return;
    setSending(true);
    setError(null);
    setDraft("");
    setMessages((current) => [...current, {
      id: `__dock_user_${Date.now()}`,
      role: "user",
      content: text,
      created_at: new Date().toISOString(),
    }]);
    try {
      let cid = conversationId;
      if (!cid) {
        const created = await createConversation();
        cid = created.id;
        setConversationId(cid);
        writeSession(CONVERSATION_KEY, cid);
      }
      const pageContext: CopilotPageContext = {
        route,
        title: typeof document !== "undefined" ? document.title : "",
        ...readPageContext(),
      };
      setStreaming("");
      const controller = new AbortController();
      abortRef.current = controller;
      const data = await streamMessage(
        cid,
        text,
        {
          onToken: (delta) => setStreaming((current) => (current ?? "") + delta),
          onText: (content) => setStreaming(content),
        },
        pageContext,
        { signal: controller.signal },
      );
      if (!mountedRef.current) return;
      setMessages((current) => [...current, {
        id: data.message_id || `__dock_assistant_${Date.now()}`,
        role: "assistant",
        content: data.reply || "",
        created_at: new Date().toISOString(),
      }]);
      if (data.requires_approval && data.pending_actions.length > 0) {
        setPendingApproval({
          conversationId: cid,
          messageId: data.message_id,
          actions: data.pending_actions,
        });
        setApproveError(null);
      }
    } catch (err) {
      const aborted = err instanceof Error && err.name === "AbortError";
      if (!mountedRef.current || aborted) return;
      const status = errorStatus(err);
      if (status === 403 || status === 404) forgetConversation();
      setError(publicError(err));
      setDraft(text);
    } finally {
      abortRef.current = null;
      if (mountedRef.current) {
        setStreaming(null);
        setSending(false);
      }
    }
  }, [conversationId, draft, forgetConversation, route, sending, setDraft]);

  const handleApprove = useCallback(async () => {
    if (!pendingApproval) return;
    setApproving(true);
    setApproveError(null);
    try {
      const data = await approveAction(
        pendingApproval.conversationId,
        pendingApproval.messageId,
      );
      setPendingApproval(null);
      if (data.reply) {
        setMessages((current) => [...current, {
          id: data.message_id || `__dock_assistant_${Date.now()}`,
          role: "assistant",
          content: data.reply,
          created_at: new Date().toISOString(),
        }]);
      }
    } catch (err) {
      setApproveError(publicError(err));
    } finally {
      setApproving(false);
    }
  }, [pendingApproval]);

  return (
    <div className="flex min-h-0 flex-1 flex-col" data-testid="copilot-dock-panel">
      <div
        ref={scrollRef}
        role="log"
        aria-live="polite"
        aria-label="Mensajes del copiloto"
        className="flex-1 space-y-3 overflow-y-auto p-4"
      >
        {messages.length === 0 && streaming === null ? (
          <p className="text-sm text-muted-foreground">
            Pregunta sobre lo que estás viendo: el copiloto recibe el contexto
            de esta pantalla con cada mensaje.
          </p>
        ) : null}
        {messages.map((message) => (
          <Message key={message.id} message={message} />
        ))}
        {streaming !== null ? (
          <Message
            message={{ id: "__dock_streaming", role: "assistant", content: streaming }}
            pending={streaming === ""}
          />
        ) : null}
      </div>

      {error ? (
        <div role="alert" className="mx-4 mb-2 rounded-md border border-destructive/30 bg-destructive/5 p-3 text-sm">
          <p className="font-medium text-destructive">{error}</p>
        </div>
      ) : null}

      <form
        onSubmit={(event) => {
          event.preventDefault();
          void handleSend();
        }}
        className="flex items-end gap-2 border-t bg-background p-3"
      >
        <textarea
          value={draft}
          onChange={(event) => setDraft(event.target.value.slice(0, MAX_MESSAGE_CHARS))}
          onKeyDown={(event) => {
            if (event.key === "Enter" && !event.shiftKey && !event.nativeEvent.isComposing) {
              event.preventDefault();
              void handleSend();
            }
          }}
          disabled={sending}
          rows={2}
          maxLength={MAX_MESSAGE_CHARS}
          placeholder="Pregúntale al copiloto…"
          aria-label="Mensaje para el copiloto"
          className="min-h-[44px] flex-1 resize-none rounded-md border border-input bg-background px-3 py-2 text-sm shadow-sm focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring disabled:cursor-not-allowed disabled:opacity-60"
        />
        <button
          type="submit"
          disabled={sending || draft.trim() === ""}
          className="inline-flex min-h-[44px] items-center justify-center rounded-md bg-primary px-4 text-sm font-medium text-primary-foreground shadow hover:bg-primary/90 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring disabled:pointer-events-none disabled:opacity-60"
        >
          {sending ? "Enviando…" : "Enviar"}
        </button>
      </form>

      <ApprovalGateDialog
        open={pendingApproval !== null}
        messageId={pendingApproval?.messageId ?? ""}
        pending={pendingApproval?.actions ?? []}
        onApprove={handleApprove}
        onCancel={() => {
          setPendingApproval(null);
          setApproveError(null);
        }}
        submitting={approving}
        error={approveError}
      />
    </div>
  );
}
