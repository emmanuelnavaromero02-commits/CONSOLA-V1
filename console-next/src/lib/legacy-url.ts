/**
 * v1.44.4 Group 1 Round 1 Security P2 — central legacy-URL
 * resolver with scheme validation.
 *
 * The brief routes operators to the legacy console at :8000 for
 * features not yet migrated (Studio, Workspaces, Monitor,
 * Settings, Vault rotate/reveal). Naive concatenation of
 * ``NEXT_PUBLIC_LEGACY_CONSOLE_URL`` into anchor href values lets
 * a misconfigured env (or a compromised build pipeline) inject
 * ``javascript:alert(1)`` or any attacker-controlled origin into
 * every "Abrir en la consola clásica" tile.
 *
 * This module exports a single ``legacyConsoleUrl(path)`` helper
 * that:
 *   - rejects anything that isn't http(s)://
 *   - falls back to the same-origin /legacy redirector if the env
 *     value fails validation, avoiding localhost links in deployed
 *     builds without baking an AWS hostname into the client bundle
 *   - guarantees the returned string starts with the validated
 *     base so downstream renderers don't have to defend on their
 *     own.
 */
const DEFAULT_LEGACY = "/legacy";


function validateBase(raw: string | undefined | null): string {
  if (!raw || typeof raw !== "string") return DEFAULT_LEGACY;
  const trimmed = raw.trim();
  if (!trimmed) return DEFAULT_LEGACY;
  try {
    const url = new URL(trimmed);
    if (url.protocol === "http:" || url.protocol === "https:") {
      // Strip a trailing slash for predictable concatenation.
      return trimmed.replace(/\/+$/, "");
    }
  } catch {
    /* fall through */
  }
  if (typeof console !== "undefined") {
    // eslint-disable-next-line no-console
    console.warn(
      "[legacy-url] NEXT_PUBLIC_LEGACY_CONSOLE_URL rejected; using deployment-safe fallback",
      { raw: trimmed },
    );
  }
  return DEFAULT_LEGACY;
}


const LEGACY_BASE = validateBase(process.env.NEXT_PUBLIC_LEGACY_CONSOLE_URL);


/**
 * Compose a legacy console URL. ``path`` should start with "/"
 * (anything else is treated as same-base relative and prefixed
 * with a slash defensively). Query strings + hash are preserved
 * verbatim — caller is responsible for URL-encoding any user
 * input it inlines.
 */
export function legacyConsoleUrl(path = "/"): string {
  if (!path) return LEGACY_BASE;
  const normalised = path.startsWith("/") ? path : `/${path}`;
  return `${LEGACY_BASE}${normalised}`;
}


export const LEGACY_CONSOLE_BASE = LEGACY_BASE;
