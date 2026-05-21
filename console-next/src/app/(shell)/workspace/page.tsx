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
type SearchParams =
  | Record<string, string | string[] | undefined>
  | Promise<Record<string, string | string[] | undefined>>;

function first(value: string | string[] | undefined): string | undefined {
  return Array.isArray(value) ? value[0] : value;
}

export default async function WorkspacePage({
  searchParams,
}: {
  searchParams?: SearchParams;
}) {
  const params = await Promise.resolve(searchParams ?? {});
  return <ChatLayout initialPrompt={first(params.prompt)} />;
}
