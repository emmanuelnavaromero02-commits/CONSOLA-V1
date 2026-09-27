export const AGENT_SLUG_MAX = 60;

function trimUnderscores(value: string): string {
  return value.replace(/^_+|_+$/g, "");
}

export function agentSlugBase(name: string): string {
  const ascii = name.normalize("NFD").replace(/[̀-ͯ]/g, "").toLowerCase();
  const base = trimUnderscores(trimUnderscores(ascii.replace(/[^a-z0-9]+/g, "_")).slice(0, AGENT_SLUG_MAX));
  return base || "agente";
}

export function agentSlug(name: string, existing: Iterable<string | null | undefined> = []): string {
  const taken = new Set<string>();
  for (const slug of existing) if (slug) taken.add(slug);
  const base = agentSlugBase(name);
  if (!taken.has(base)) return base;
  for (let index = 2; ; index += 1) {
    const suffix = `_${index}`;
    const candidate = `${trimUnderscores(base.slice(0, AGENT_SLUG_MAX - suffix.length))}${suffix}`;
    if (!taken.has(candidate)) return candidate;
  }
}
