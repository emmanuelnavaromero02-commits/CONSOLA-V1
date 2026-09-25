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
