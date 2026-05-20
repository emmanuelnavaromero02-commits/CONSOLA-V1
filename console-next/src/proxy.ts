/**
 * v1.44.2 — proxy that enforces "must be authenticated" on every
 * page except the login screen and a handful of public assets.
 *
 * The auth signal is the presence of the FastAPI-issued JWT cookie.
 * Verification of the JWT itself stays server-side (route handlers
 * + RSC fetches); the proxy only checks for cookie presence so
 * we never decode keys at the edge.
 */
import { NextResponse, type NextRequest } from "next/server";

// v1.44.3.3 R-Mac-Round-3 Task D: forgot/reset-password pages
// must be reachable without an auth cookie — they're literally
// how a user without an active session recovers access.
const PUBLIC_PATHS = [
  "/login",
  "/api/health",
  "/login-proxy",
  "/forgot-password",
  "/reset-password",
];

const PUBLIC_PREFIXES = [
  "/_next/",
  "/static/",
  "/favicon",
  // v1.44.3.2.2 R-Mac-4: the /auth/* proxy must be reachable
  // without a session cookie — that's literally how you GET a
  // session cookie (POST /auth/login). The proxy itself simply
  // forwards to FastAPI; the backend is the source of truth for
  // auth, and it will reject unauthenticated requests on
  // protected endpoints (e.g. /auth/refresh without a refresh
  // cookie) the same as it always has.
  "/auth/",
];

// v1.44.3.2.2 R-Mac-4: the FastAPI backend (post-R-Mac CSRF
// dance) sets ``mod_session`` and ``refresh_token`` on a
// successful POST /auth/login. Without these names in the
// candidate list, the proxy redirects an authenticated
// user straight back to /login on the next navigation —
// silent infinite-loop bug.
const AUTH_COOKIE_CANDIDATES = [
  "mod_session",
  "refresh_token",
  "access_token",
  "session",
  "jwt",
  "auth_token",
];

function isPublic(pathname: string): boolean {
  if (PUBLIC_PATHS.includes(pathname)) return true;
  return PUBLIC_PREFIXES.some((prefix) => pathname.startsWith(prefix));
}

function hasAuthCookie(req: NextRequest): boolean {
  return AUTH_COOKIE_CANDIDATES.some((name) => Boolean(req.cookies.get(name)?.value));
}

export function proxy(req: NextRequest) {
  const { pathname } = req.nextUrl;

  if (isPublic(pathname)) return NextResponse.next();

  if (!hasAuthCookie(req)) {
    const loginUrl = req.nextUrl.clone();
    loginUrl.pathname = "/login";
    loginUrl.searchParams.set("next", pathname);
    return NextResponse.redirect(loginUrl);
  }
  return NextResponse.next();
}

export const config = {
  // Match every route EXCEPT the explicit static prefixes the matcher
  // can express. The runtime check in isPublic() is the source of truth
  // for /login + /api/health.
  matcher: ["/((?!_next/static|_next/image|favicon.ico).*)"],
};
