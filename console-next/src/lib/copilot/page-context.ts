export type PageContextValue = string | number | boolean;
export type PageContext = Record<string, PageContextValue>;

export const PAGE_CONTEXT_STORAGE_KEY = "omega-copilot-page-context";
// Two below the backend's 24-key reject so route + title always fit on top.
const MAX_KEYS = 22;
const MAX_VALUE_CHARS = 800;

interface StoredPageContext {
  path: string;
  ctx: PageContext;
}

let store: StoredPageContext | null = null;

function currentPath(): string {
  if (typeof window === "undefined") return "";
  return window.location.pathname || "";
}

function sanitize(partial: Record<string, unknown>): PageContext {
  const out: PageContext = {};
  for (const [key, value] of Object.entries(partial)) {
    if (Object.keys(out).length >= MAX_KEYS) break;
    if (!key) continue;
    if (typeof value === "boolean" || typeof value === "number") {
      if (typeof value === "number" && !Number.isFinite(value)) continue;
      out[key.slice(0, 64)] = value;
    } else if (typeof value === "string") {
      out[key.slice(0, 64)] = value.slice(0, MAX_VALUE_CHARS);
    }
  }
  return out;
}

function persist(): void {
  try {
    if (store) {
      window.sessionStorage.setItem(PAGE_CONTEXT_STORAGE_KEY, JSON.stringify(store));
    } else {
      window.sessionStorage.removeItem(PAGE_CONTEXT_STORAGE_KEY);
    }
  } catch {
    /* best-effort mirror */
  }
}

function restore(): StoredPageContext | null {
  try {
    const raw = window.sessionStorage.getItem(PAGE_CONTEXT_STORAGE_KEY);
    if (!raw) return null;
    const parsed = JSON.parse(raw) as StoredPageContext;
    if (!parsed || typeof parsed.path !== "string" || typeof parsed.ctx !== "object" || parsed.ctx === null) {
      return null;
    }
    return { path: parsed.path, ctx: sanitize(parsed.ctx as Record<string, unknown>) };
  } catch {
    return null;
  }
}

export function publishPageContext(partial: Record<string, PageContextValue>): void {
  store = { path: currentPath(), ctx: sanitize(partial) };
  persist();
}

export function clearPageContext(): void {
  store = null;
  persist();
}

export function readPageContext(): PageContext {
  const entry = store ?? restore();
  if (!entry) return {};
  if (entry.path !== currentPath()) return {};
  return { ...entry.ctx };
}
