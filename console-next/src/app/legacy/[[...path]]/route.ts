import { NextResponse, type NextRequest } from "next/server";

type Ctx = { params: { path?: string[] } };

function configuredLegacyBase(): string | null {
  const raw =
    process.env.LEGACY_CONSOLE_URL ||
    process.env.NEXT_PUBLIC_LEGACY_CONSOLE_URL ||
    "";
  if (!raw.trim()) return null;
  try {
    const url = new URL(raw.trim());
    if (url.protocol === "http:" || url.protocol === "https:") {
      return raw.trim().replace(/\/+$/, "");
    }
  } catch {
    return null;
  }
  return null;
}

function requestDerivedLegacyBase(request: NextRequest): string {
  const proto =
    request.headers.get("x-forwarded-proto") ||
    request.nextUrl.protocol.replace(/:$/, "") ||
    "http";
  const hostHeader =
    request.headers.get("x-forwarded-host") ||
    request.headers.get("host") ||
    request.nextUrl.host;
  let hostname = request.nextUrl.hostname;
  try {
    hostname = new URL(`${proto}://${hostHeader}`).hostname;
  } catch {
    hostname = hostHeader.split(":")[0] || hostname;
  }
  const host = hostname.includes(":") && !hostname.startsWith("[")
    ? `[${hostname}]`
    : hostname;
  const port = process.env.LEGACY_CONSOLE_PORT || "8000";
  return `${proto}://${host}:${port}`;
}

export function GET(request: NextRequest, { params }: Ctx) {
  const subpath = (params.path || []).join("/");
  const base = configuredLegacyBase() || requestDerivedLegacyBase(request);
  const target = new URL(`/${subpath}${request.nextUrl.search}`, base);
  return NextResponse.redirect(target);
}

export const dynamic = "force-dynamic";
