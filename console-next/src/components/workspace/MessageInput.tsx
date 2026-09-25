"use client";

import TextareaAutosize from "react-textarea-autosize";
import { useEffect, useReducer, type KeyboardEvent } from "react";

interface Props {
  onSend:       (text: string) => void;
  disabled?:    boolean;
  placeholder?: string;
  initialValue?: string;
  onSlash?:     () => void;
  ariaLabel?:   string;
}

const MAX_MESSAGE_CHARS = 8_000;

type ValueAction =
  | { type: "clear" }
  | { type: "input"; value: string }
  | { type: "seed"; value?: string };

function trimSeed(value?: string): string {
  return value?.trim().slice(0, MAX_MESSAGE_CHARS) ?? "";
}

function valueReducer(current: string, action: ValueAction): string {
  if (action.type === "clear") return "";
  if (action.type === "input") return action.value.slice(0, MAX_MESSAGE_CHARS);
  const seed = trimSeed(action.value);
  return current.trim() || !seed ? current : seed;
}

export function MessageInput({
  onSend,
  disabled,
  placeholder,
  initialValue,
  onSlash,
  ariaLabel,
}: Props) {
  const [value, dispatchValue] = useReducer(valueReducer, initialValue, trimSeed);

  useEffect(() => {
    dispatchValue({ type: "seed", value: initialValue });
  }, [initialValue]);

  function submit() {
    const trimmed = value.trim();
    if (!trimmed) return;
    onSend(trimmed);
    dispatchValue({ type: "clear" });
  }

  function handleKey(e: KeyboardEvent<HTMLTextAreaElement>) {
    if (e.key === "Enter" && !e.shiftKey && !e.nativeEvent.isComposing) {
      e.preventDefault();
      submit();
      return;
    }
    if (e.key === "/" && value === "" && onSlash) {
      e.preventDefault();
      onSlash();
    }
  }

  return (
    <form
      onSubmit={(e) => {
        e.preventDefault();
        submit();
      }}
      className="flex items-end gap-2 border-t bg-background p-3"
    >
      <TextareaAutosize
        value={value}
        onChange={(e) => dispatchValue({ type: "input", value: e.target.value })}
        onKeyDown={handleKey}
        disabled={disabled}
        placeholder={placeholder ?? "Pregunta algo o escribe / para comandos…"}
        aria-label={ariaLabel ?? "Mensaje para el copiloto"}
        maxLength={MAX_MESSAGE_CHARS}
        minRows={1}
        maxRows={8}
        className="min-h-[44px] flex-1 resize-none rounded-md border border-input bg-background px-3 py-2 text-sm shadow-sm focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring disabled:cursor-not-allowed disabled:opacity-60"
      />
      <button
        type="submit"
        disabled={disabled || value.trim() === ""}
        className="inline-flex min-h-[44px] min-w-[44px] items-center justify-center rounded-md bg-primary px-4 text-sm font-medium text-primary-foreground shadow hover:bg-primary/90 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring disabled:pointer-events-none disabled:opacity-60"
      >
        Enviar
      </button>
    </form>
  );
}
