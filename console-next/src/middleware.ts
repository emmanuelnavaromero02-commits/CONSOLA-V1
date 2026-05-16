/**
 * v1.44.2 — middleware that enforces "must be authenticated" on every
 * page except the login screen and a handful of public assets.
 *
 * The auth signal is the presence of the FastAPI-issued JWT cookie.
 * Verification of the JWT itself stays server-side (route handlers
 * + RSC fetches); the middleware only checks for cookie presence so
 * we never decode keys at the edge.
 */
import { NextResponse, type NextRequest } from "next/server";

const PUBLIC_PATHS = ["/login", "/api/health"];

const PUBLIC_PREFIXES = ["/_next/", "/static/", "/favicon"];

const AUTH_COOKIE_CANDIDATES = ["access_token", "session", "jwt", "auth_token"];

function isPublic(pathname: string): boolean {
  if (PUBLIC_PATHS.includes(pathname)) return true;
  return PUBLIC_PREFIXES.some((prefix) => pathname.startsWith(prefix));
}

function hasAuthCookie(req: NextRequest): boolean {
  return AUTH_COOKIE_CANDIDATES.some((name) => Boolean(req.cookies.get(name)?.value));
}

export function middleware(req: NextRequest) {
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
