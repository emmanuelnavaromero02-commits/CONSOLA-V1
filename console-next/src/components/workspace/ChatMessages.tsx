"use client";

import { useEffect, useRef } from "react";

import type { Message as MessageType } from "@/lib/copilot/types";

import { Message } from "./Message";

interface Props {
  messages:          MessageType[];
  pending?:          boolean;
  streamingContent?: string | null;
}

export function ChatMessages({ messages, pending, streamingContent }: Props) {
  const endRef = useRef<HTMLDivElement | null>(null);

  useEffect(() => {
    endRef.current?.scrollIntoView({ behavior: "smooth", block: "end" });
  }, [messages.length, pending, streamingContent]);

  return (
    <div
      role="log"
      aria-live="polite"
      aria-label="Mensajes de la conversación"
      data-testid="chat-messages"
      className="min-h-0 flex-1 space-y-4 overflow-y-auto p-4"
    >
      {messages.map((m) => (
        <Message key={m.id} message={m} />
      ))}
      {streamingContent !== null && streamingContent !== undefined ? (
        <Message
          message={{
            id:      "__streaming__",
            role:    "assistant",
            content: streamingContent,
          }}
          pending={streamingContent.length === 0}
        />
      ) : null}
      {pending ? (
        <Message
          message={{
            id:      "__pending__",
            role:    "assistant",
            content: "",
          }}
          pending
        />
      ) : null}
      <div ref={endRef} aria-hidden />
    </div>
  );
}
