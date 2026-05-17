"use client";

import { ChatLayout } from "@/components/workspace/ChatLayout";

/**
 * v1.44.4 Task A — /workspace.
 *
 * The brief positions /workspace as the "centro" of the
 * console — the place an operator goes to ask the copilot
 * anything. All flow logic lives in ChatLayout; the page file
 * is a thin shell so future route params (e.g. /workspace/[cid])
 * can wrap the same component.
 *
 * SSE streaming is documented in copilot_workflows.py:6-7 as
 * next-session backend work; useChat awaits the full
 * non-streaming run_turn response and renders the assistant
 * reply once it lands. The "pensando…" placeholder ChatMessages
 * shows while sendMutation.isPending covers the perceived-
 * latency gap.
 */
export default function WorkspacePage() {
  return <ChatLayout />;
}
