import { NextResponse } from "next/server";

/**
 * Next.js healthcheck consumed by docker-compose. Returns 200 once
 * the runtime is up. We do NOT proxy the FastAPI health here —
 * docker-compose has a separate healthcheck on the console service
 * for that. This route is strictly "is the Next.js process serving?".
 */
export async function GET() {
  return NextResponse.json({ ok: true, service: "console-next" }, { status: 200 });
}

/** v1.44.2: explicitly mark this route as dynamic — never cached. */
export const dynamic = "force-dynamic";
