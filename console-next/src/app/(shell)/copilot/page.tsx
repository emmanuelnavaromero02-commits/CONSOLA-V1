import { ChatLayout } from "@/components/workspace/ChatLayout";

type SearchParams =
  | Record<string, string | string[] | undefined>
  | Promise<Record<string, string | string[] | undefined>>;

function first(value: string | string[] | undefined): string | undefined {
  return Array.isArray(value) ? value[0] : value;
}

export default async function CopilotPage({
  searchParams,
}: {
  searchParams?: SearchParams;
}) {
  const params = await Promise.resolve(searchParams ?? {});

  return (
    <div
      className="flex flex-col bg-background"
      style={{
        height: "calc(100vh - 56px)",
        minHeight: "calc(100vh - 56px)",
      }}
    >
      <ChatLayout initialPrompt={first(params.prompt)} />
    </div>
  );
}
