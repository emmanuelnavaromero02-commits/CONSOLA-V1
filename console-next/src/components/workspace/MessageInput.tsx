"use client";

import TextareaAutosize from "react-textarea-autosize";
import { useEffect, useState, type KeyboardEvent } from "react";

interface Props {
  onSend:       (text: string) => void;
  disabled?:    boolean;
  placeholder?: string;
  initialValue?: string;
  /** Called when the user types "/" at the start of an empty
   *  input — opens the slash-command palette. */
  onSlash?:     () => void;
}

/**
 * Round 1 security P2 — bound the message length so a single
 * paste can't blow up DB row size + downstream LLM token cost.
 * 8 000 chars matches the documented backend MAX_USER_MESSAGE_CHARS
 * referenced by copilot_service.py.
 */
const MAX_MESSAGE_CHARS = 8_000;

/**
 * v1.44.4 Task A — chat input.
 *
 * Autoresizing textarea via react-textarea-autosize so the
 * input grows with the message and shrinks on submit. Submit
 * is Enter (Shift+Enter inserts a newline). Empty / whitespace
 * messages are blocked client-side so we don't waste a backend
 * round-trip on a 400.
 *
 * Slash-command interception: when the input is empty and the
 * user presses "/" we let the keydown propagate up via
 * ``onSlash`` so the page can open the SlashCommandsPalette.
 * The "/" character STILL types into the field so the
 * palette can render it as the first search keystroke.
 */
export function MessageInput({
  onSend,
  disabled,
  placeholder,
  initialValue,
  onSlash,
}: Props) {
  const [value, setValue] = useState("");

  useEffect(() => {
    const seed = initialValue?.trim();
    if (!seed) return;
    setValue((current) => (current.trim() ? current : seed.slice(0, MAX_MESSAGE_CHARS)));
  }, [initialValue]);

  function submit() {
    const trimmed = value.trim();
    if (!trimmed) return;
    onSend(trimmed);
    setValue("");
  }

  function handleKey(e: KeyboardEvent<HTMLTextAreaElement>) {
    if (e.key === "Enter" && !e.shiftKey && !e.nativeEvent.isComposing) {
      e.preventDefault();
      submit();
      return;
    }
    if (e.key === "/" && value === "" && onSlash) {
      // Round 1 review: previously, "/" both opened the palette
      // AND typed into the textarea — confusing dual state.
      // Eat the keystroke so the palette becomes the SOLE
      // input surface for the command.
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
        onChange={(e) => setValue(e.target.value.slice(0, MAX_MESSAGE_CHARS))}
        onKeyDown={handleKey}
        disabled={disabled}
        placeholder={placeholder ?? "Pregunta algo o escribe / para comandos…"}
        aria-label="Mensaje para el copiloto"
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
