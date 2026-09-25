const DEFAULT_LEGACY = "";


function validateBase(raw: string | undefined | null): string {
  if (!raw || typeof raw !== "string") return DEFAULT_LEGACY;
  const trimmed = raw.trim();
  if (!trimmed) return DEFAULT_LEGACY;
  try {
    const url = new URL(trimmed);
    if (url.protocol === "http:" || url.protocol === "https:") {
      return trimmed.replace(/\/+$/, "");
    }
  } catch {
    /* fall through */
  }
  if (typeof console !== "undefined") {
    console.warn(
      "[legacy-url] NEXT_PUBLIC_LEGACY_CONSOLE_URL rejected; using deployment-safe fallback",
      { raw: trimmed },
    );
  }
  return DEFAULT_LEGACY;
}


const LEGACY_BASE = validateBase(process.env.NEXT_PUBLIC_LEGACY_CONSOLE_URL);


export function legacyConsoleUrl(path = "/"): string {
  if (!path) return LEGACY_BASE;
  const normalised = path.startsWith("/") ? path : `/${path}`;
  return `${LEGACY_BASE}${normalised}`;
}


export const LEGACY_CONSOLE_BASE = LEGACY_BASE;
