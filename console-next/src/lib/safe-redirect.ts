const MAX_REDIRECT_LENGTH = 2048;

function hasControlCharacter(value: string): boolean {
  for (let index = 0; index < value.length; index += 1) {
    const code = value.charCodeAt(index);
    if (code < 0x20 || code === 0x7f) return true;
  }
  return false;
}

/**
 * Returns `raw` as a same-origin path (pathname + search + hash) or `fallback`.
 * Rejects anything a browser could resolve to another origin: backslashes,
 * control characters (the URL parser strips tabs/newlines) and paths that
 * normalise to a protocol-relative `//host`.
 */
export function safeInternalPath(
  raw: unknown,
  origin: string,
  fallback = "/dashboard",
): string {
  if (typeof raw !== "string" || raw.length === 0 || raw.length > MAX_REDIRECT_LENGTH) {
    return fallback;
  }
  if (hasControlCharacter(raw) || raw.includes("\\") || !raw.startsWith("/")) {
    return fallback;
  }
  let base: URL;
  let resolved: URL;
  try {
    base = new URL(origin);
    resolved = new URL(raw, base);
  } catch {
    return fallback;
  }
  if (resolved.origin !== base.origin || resolved.pathname.startsWith("//")) {
    return fallback;
  }
  return `${resolved.pathname}${resolved.search}${resolved.hash}`;
}
