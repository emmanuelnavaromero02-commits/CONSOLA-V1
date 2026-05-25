"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import {
  approveAction,
  createConversation,
  getConversation,
  listConversations,
  sendMessage,
} from "./client";
import type {
  Conversation,
  ConversationDetailResponse,
  SendMessageResponse,
} from "./types";

/**
 * v1.44.4 Task A — chat orchestration hook.
 *
 * Exposes the four operations the chat UI needs:
 *   - listConversationsQuery: the sidebar's conversation list
 *   - conversationQuery: messages + metadata for the currently
 *                        open conversation
 *   - sendMutation:       legacy JSON POST for non-stream callers
 *   - approveMutation:    confirm a pending destructive action
 *
 * The interactive chat surface uses streamMessage directly so token
 * deltas can render while the turn is still running.
 *
 * The hook intentionally does NOT manage "which conversation is
 * open" — the page passes a ``conversationId`` in. That keeps
 * the hook reusable from a future route param + lets the page
 * own the "no conversation yet → show suggested prompts" empty
 * state.
 */
export function useChat(conversationId: string | null) {
  const qc = useQueryClient();

  const listConversationsQuery = useQuery<Conversation[]>({
    queryKey: ["copilot", "conversations"],
    queryFn:  listConversations,
    staleTime: 30_000,
  });

  const conversationQuery = useQuery<ConversationDetailResponse>({
    queryKey: ["copilot", "conversation", conversationId],
    queryFn:  () => getConversation(conversationId as string),
    enabled:  Boolean(conversationId),
  });

  const sendMutation = useMutation<
    SendMessageResponse,
    Error,
    { conversationId: string; message: string }
  >({
    mutationFn: ({ conversationId, message }) =>
      sendMessage(conversationId, message),
    onSuccess: (_data, vars) => {
      qc.invalidateQueries({
        queryKey: ["copilot", "conversation", vars.conversationId],
      });
      qc.invalidateQueries({ queryKey: ["copilot", "conversations"] });
    },
  });

  const approveMutation = useMutation<
    SendMessageResponse,
    Error,
    { conversationId: string; messageId: string }
  >({
    mutationFn: ({ conversationId, messageId }) =>
      approveAction(conversationId, messageId),
    onSuccess: (_data, vars) => {
      qc.invalidateQueries({
        queryKey: ["copilot", "conversation", vars.conversationId],
      });
    },
  });

  const createConversationMutation = useMutation<Conversation, Error, { title?: string }>({
    mutationFn: ({ title }) => createConversation(title),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["copilot", "conversations"] });
    },
  });

  return {
    listConversationsQuery,
    conversationQuery,
    sendMutation,
    approveMutation,
    createConversationMutation,
  };
}
