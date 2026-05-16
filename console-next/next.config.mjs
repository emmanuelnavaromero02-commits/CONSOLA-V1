/** @type {import('next').NextConfig} */
const nextConfig = {
  reactStrictMode: true,
  // v1.44.2: standalone output so the Dockerfile can ship only the
  // runtime artefacts (no node_modules in the final image).
  output: "standalone",
  // The Python FastAPI backend still hosts /api/*. When console-next
  // runs in the same docker network it talks to the internal host
  // (API_INTERNAL_URL); from the browser it goes through the public
  // CONSOLE_URL (NEXT_PUBLIC_API_BASE). All API calls server-side go
  // through src/lib/api.ts which picks the right base.
  // No rewrites here — the Next.js process never serves /api/* directly
  // beyond its own healthcheck.
  poweredByHeader: false,
  // v1.44.3.2.2 (R-Mac CSP hotfix): the v1.44.2 CSP set
  // ``script-src 'self'`` which blocked Next.js 14's hydration
  // inline bootstrap script + the App-Router runtime's eval()
  // calls. Symptom Codex reproduced on the Mac:
  //   - /login renders a skeleton forever
  //   - DevTools console: "Executing inline script violates
  //     Content Security Policy directive 'script-src 'self''"
  //   - 0/319 E2E tests pass because the form never mounts
  //
  // Fix: allow 'unsafe-inline' + 'unsafe-eval' in script-src for
  // v1.0. The v1.45 sprint introduces a nonce middleware so
  // these can come back off without breaking hydration. The
  // CSP frame-ancestors / form-action / base-uri still defend
  // against clickjacking and form-hijacking — the bits the audit
  // actually cared about.
  //
  // connect-src additionally allows the LOCAL backend origin so
  // axios calls from the browser to http://localhost:8000 work
  // in dev. The CONSOLE_PUBLIC_URL takes over in prod.
  async headers() {
    if (process.env.NODE_ENV !== "production") return [];
    const apiBase = process.env.NEXT_PUBLIC_API_BASE || "http://localhost:8000";
    return [
      {
        source: "/(.*)",
        headers: [
          { key: "X-Frame-Options", value: "DENY" },
          { key: "X-Content-Type-Options", value: "nosniff" },
          { key: "Referrer-Policy", value: "strict-origin-when-cross-origin" },
          {
            key: "Content-Security-Policy",
            value:
              "default-src 'self'; " +
              "script-src 'self' 'unsafe-inline' 'unsafe-eval'; " +
              "style-src 'self' 'unsafe-inline'; " +
              "img-src 'self' data: blob:; " +
              "font-src 'self' data:; " +
              `connect-src 'self' ${apiBase} ws://localhost:* wss://localhost:*; ` +
              "frame-ancestors 'none'; " +
              "base-uri 'self'; " +
              "form-action 'self';",
          },
        ],
      },
    ];
  },
};

export default nextConfig;
