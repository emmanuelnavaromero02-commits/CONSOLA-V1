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
 *   - sendMutation:       POST a new user message (non-streaming)
 *   - approveMutation:    confirm a pending destructive action
 *
 * Conversation list is invalidated on every send so the sidebar
 * stays in sync with new conversations + bumped updated_at
 * timestamps.
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
