/**
 * Read a browser cookie by name. Kept dependency-free so public pages
 * such as /login can perform the CSRF dance without pulling the full
 * authenticated data layer into their first-load bundle.
 */
export function readCookie(name: string): string | null {
  if (typeof document === "undefined") return null;
  const prefix = `${name}=`;
  for (const raw of document.cookie.split(";")) {
    const c = raw.trim();
    if (c.startsWith(prefix)) {
      return decodeURIComponent(c.slice(prefix.length));
    }
  }
  return null;
}
