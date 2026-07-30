// @vitest-environment jsdom

import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type { PendingAction } from "@/lib/copilot/types";

import { ApprovalGateDialog } from "./ApprovalGateDialog";

const pending: PendingAction[] = [
  {
    tool: "delete_records",
    server: "warehouse",
    risk_level: "high",
    requires_approval: true,
    approval_key: "key-1",
  },
];

let container: HTMLDivElement;
let root: Root;

interface HarnessProps {
  open?: boolean;
  submitting?: boolean;
  onApprove?: () => void;
  onCancel?: () => void;
  error?: string | null;
}

function Harness({
  open = true,
  submitting = false,
  onApprove = () => undefined,
  onCancel = () => undefined,
  error = null,
}: HarnessProps) {
  return (
    <>
      <button type="button" data-testid="opener">Abrir aprobación</button>
      <ApprovalGateDialog
        open={open}
        messageId="msg-1"
        pending={pending}
        onApprove={onApprove}
        onCancel={onCancel}
        submitting={submitting}
        error={error}
      />
    </>
  );
}

async function render(props: HarnessProps = {}) {
  await act(async () => {
    root.render(<Harness {...props} />);
  });
}

function button(name: string) {
  return [...container.querySelectorAll("button")].find(
    (candidate) => candidate.textContent?.trim() === name,
  );
}

function pressKey(key: string, init: KeyboardEventInit = {}) {
  return act(async () => {
    document.dispatchEvent(new KeyboardEvent("keydown", { key, bubbles: true, ...init }));
  });
}

beforeEach(() => {
  container = document.createElement("div");
  document.body.append(container);
  root = createRoot(container);
});

afterEach(async () => {
  await act(async () => root.unmount());
  container.remove();
});

describe("ApprovalGateDialog focus trap", () => {
  it("moves focus to Cancelar on open and cycles Tab inside the dialog", async () => {
    await render();

    const cancel = button("Cancelar");
    const approve = button("Sí, ejecutar");
    expect(cancel).toBeDefined();
    expect(approve).toBeDefined();
    expect(document.activeElement).toBe(cancel);

    // Shift+Tab desde el primer foco salta al último elemento.
    await pressKey("Tab", { shiftKey: true });
    expect(document.activeElement).toBe(approve);

    // Tab desde el último elemento vuelve al primero.
    await pressKey("Tab");
    expect(document.activeElement).toBe(cancel);
  });

  it("returns focus to the opener when the dialog closes", async () => {
    await render({ open: false });
    const opener = container.querySelector<HTMLButtonElement>('[data-testid="opener"]');
    opener?.focus();
    expect(document.activeElement).toBe(opener);

    await render({ open: true });
    expect(document.activeElement).toBe(button("Cancelar"));

    await render({ open: false });
    expect(document.activeElement).toBe(opener);
  });
});

describe("ApprovalGateDialog dismissal guards", () => {
  it("closes with Escape while idle", async () => {
    const onCancel = vi.fn();
    await render({ onCancel });

    await pressKey("Escape");
    expect(onCancel).toHaveBeenCalledTimes(1);
  });

  it("ignores Escape while submitting", async () => {
    const onCancel = vi.fn();
    await render({ onCancel, submitting: true });

    await pressKey("Escape");
    expect(onCancel).not.toHaveBeenCalled();
    expect(container.querySelector('[role="dialog"]')).not.toBeNull();
  });

  it("closes on backdrop click while idle but not while submitting", async () => {
    const onCancel = vi.fn();
    await render({ onCancel, submitting: true });
    const backdrop = container.querySelector<HTMLElement>('[aria-hidden=""], [aria-hidden="true"]');
    expect(backdrop).not.toBeNull();

    await act(async () => backdrop?.click());
    expect(onCancel).not.toHaveBeenCalled();

    await render({ onCancel, submitting: false });
    await act(async () => backdrop?.click());
    expect(onCancel).toHaveBeenCalledTimes(1);
  });

  it("keeps Tab from escaping the dialog while submitting", async () => {
    await render({ submitting: true });
    const dialogPanel = container.querySelector<HTMLElement>('[role="dialog"] [tabindex="-1"]');
    expect(dialogPanel).not.toBeNull();

    await pressKey("Tab");
    expect(document.activeElement).toBe(dialogPanel);
  });
});
