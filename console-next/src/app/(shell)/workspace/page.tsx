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
 * ChatLayout opens the Copilot SSE stream for interactive turns,
 * renders token deltas as they arrive, then refreshes the persisted
 * conversation once the turn completes.
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
