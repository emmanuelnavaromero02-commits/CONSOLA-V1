"use client";

import { useEffect, useRef } from "react";

import type { Message as MessageType } from "@/lib/copilot/types";

import { Message } from "./Message";

interface Props {
  messages:  MessageType[];
  pending?:  boolean;
}

/**
 * v1.44.4 Task A — scrollable message list.
 *
 * Renders the conversation history and auto-scrolls to the
 * bottom whenever a new message lands (including the "pending"
 * placeholder we inject while sendMutation is in flight).
 *
 * Uses a sentinel element + ``scrollIntoView`` rather than
 * fighting layout heights — works with arbitrary message
 * lengths and survives window resizes without manual
 * recalculation.
 */
export function ChatMessages({ messages, pending }: Props) {
  const endRef = useRef<HTMLDivElement | null>(null);

  useEffect(() => {
    endRef.current?.scrollIntoView({ behavior: "smooth", block: "end" });
  }, [messages.length, pending]);

  return (
    <div
      role="log"
      aria-live="polite"
      aria-label="Mensajes de la conversación"
      className="flex-1 space-y-4 overflow-y-auto p-4"
    >
      {messages.map((m) => (
        <Message key={m.id} message={m} />
      ))}
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
