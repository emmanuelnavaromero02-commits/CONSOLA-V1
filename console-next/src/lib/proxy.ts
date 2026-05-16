/**
 * v1.44.3.2.2 R-Mac-4 — Next.js server-side proxy helper.
 *
 * After three rounds of CORS firefighting (R-Mac, R-Mac-3, review
 * fixes) the browser was still rejecting credentialed
 * cross-origin requests on Codex's Mac. Strategy pivot: stop
 * fighting CORS. The Next.js app at :3000 now serves the API
 * surface AS ITSELF, then proxies to the FastAPI backend
 * server-to-server inside the docker network. Same-origin from
 * the browser's perspective → no preflight, no Allow-Origin
 * negotiation, no Set-Cookie domain-rewriting headaches.
 *
 * Used by:
 *   - app/api/[...path]/route.ts    → ${BACKEND}/api/...
 *   - app/auth/[...path]/route.ts   → ${BACKEND}/auth/...
 *   - app/login-proxy/route.ts      → ${BACKEND}/login (HTML)
 */
import { NextResponse, type NextRequest } from "next/server";

export const BACKEND_URL =
  process.env.BACKEND_INTERNAL_URL
  || process.env.API_INTERNAL_URL
  || "http://console:8000";

// Hop-by-hop request headers we strip before forwarding. `host`
// would point the upstream at :3000 instead of the backend;
// `content-length` / `transfer-encoding` would lie about the
// re-encoded body length.
const REQ_DROP = new Set([
  "host",
  "connection",
  "keep-alive",
  "transfer-encoding",
  "content-length",
  "te",
  "trailer",
  "upgrade",
  "proxy-authorization",
  "proxy-authenticate",
]);

// Response headers we strip from upstream. Node's fetch already
// decoded `content-encoding`, so leaving the header in place
// would tell the browser to decode again and corrupt the body.
// `content-length` / `transfer-encoding` are similarly framing
// claims that no longer match what we forward.
const RES_DROP = new Set([
  "content-length",
  "content-encoding",
  "transfer-encoding",
  "connection",
  "keep-alive",
]);

// Status codes that MUST NOT carry a body per RFC 7230. Streaming
// any bytes for these into NextResponse triggers undici's
// "Response with null body status cannot have body" abort.
const NO_BODY_STATUS = new Set([204, 205, 304]);

export interface ProxyOptions {
  /** Absolute upstream URL to forward to. */
  targetUrl: string;
}

/**
 * Rewrite a `Location` header so the browser doesn't see the
 * docker-internal hostname (e.g. `http://console:8000/dashboard`).
 * Strategy: if the value starts with `${BACKEND_URL}`, strip that
 * prefix and return the path only — same-origin from the
 * browser's view. Anything else (path-relative, an external URL)
 * is returned verbatim.
 */
function rewriteLocation(loc: string): string {
  if (loc.startsWith(BACKEND_URL)) {
    return loc.slice(BACKEND_URL.length) || "/";
  }
  return loc;
}

export async function proxyTo(
  request: NextRequest,
  { targetUrl }: ProxyOptions,
): Promise<NextResponse> {
  const headers = new Headers();
  request.headers.forEach((value, key) => {
    if (!REQ_DROP.has(key.toLowerCase())) {
      headers.set(key, value);
    }
  });

  let body: BodyInit | null = null;
  if (request.method !== "GET" && request.method !== "HEAD") {
    body = await request.text();
  }

  let upstream: Response;
  try {
    upstream = await fetch(targetUrl, {
      method: request.method,
      headers,
      body,
      redirect: "manual",
    });
  } catch (err) {
    return NextResponse.json(
      {
        error: "Proxy error",
        detail: err instanceof Error ? err.message : String(err),
        target: targetUrl,
      },
      { status: 502 },
    );
  }

  const responseHeaders = new Headers();
  upstream.headers.forEach((value, key) => {
    const lower = key.toLowerCase();
    if (RES_DROP.has(lower)) return;
    // Don't copy Set-Cookie here — we re-append each value below
    // via getSetCookie() so multi-cookie responses don't get
    // collapsed into one comma-joined header. Don't copy Location
    // either — we rewrite it below to strip the docker hostname.
    if (lower === "set-cookie" || lower === "location") return;
    responseHeaders.set(key, value);
  });

  // Set-Cookie can appear multiple times in the upstream response
  // (FastAPI emits separate headers for csrf_token, mod_session,
  // refresh_token). `Headers.forEach` collapses them with ", "
  // which breaks the browser's cookie parser. Re-append each value
  // individually via `getSetCookie()` (Node ≥18.14, supported by
  // Next 14's runtime).
  const getSetCookie = (upstream.headers as { getSetCookie?: () => string[] }).getSetCookie;
  if (typeof getSetCookie === "function") {
    for (const cookie of getSetCookie.call(upstream.headers)) {
      responseHeaders.append("set-cookie", cookie);
    }
  }

  // Rewrite the Location header to strip the docker-internal
  // hostname. FastAPI's RedirectResponse emits an absolute URL
  // built from the request's Host header (`Host: console:8000`
  // inside the docker network) — without rewriting, the browser
  // would chase a hostname it can't resolve.
  const upstreamLocation = upstream.headers.get("location");
  if (upstreamLocation !== null) {
    responseHeaders.set("location", rewriteLocation(upstreamLocation));
  }

  // RFC 7230: 204 / 205 / 304 + HEAD requests carry no body.
  // Streaming bytes for these into NextResponse trips undici's
  // null-body-status assertion. For everything else we forward
  // upstream.body as-is — keeps binary downloads intact (CSV /
  // PDF / asset blobs) and avoids buffering the whole response
  // in memory.
  const noBody =
    NO_BODY_STATUS.has(upstream.status) || request.method === "HEAD";

  return new NextResponse(noBody ? null : upstream.body, {
    status: upstream.status,
    statusText: upstream.statusText,
    headers: responseHeaders,
  });
}
