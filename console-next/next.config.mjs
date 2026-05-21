/** @type {import('next').NextConfig} */
const nextConfig = {
  reactStrictMode: true,
  // v1.44.2: standalone output so the Dockerfile can ship only the
  // runtime artefacts (no node_modules in the final image).
  output: "standalone",
  // v1.44.3.2.2 R-Mac-4: the FastAPI backend still hosts /api/*
  // and /auth/*, but the browser never talks to it directly
  // anymore. The Next.js app serves those paths as itself via
  // catch-all route handlers (app/api/[...path], app/auth/[...path],
  // app/login-proxy) and proxies to FastAPI server-side over the
  // docker network. See console-next/src/lib/proxy.ts.
  poweredByHeader: false,
  // v1.44.3.2.2+ hardening (R-Mac CSP hotfix + R-Mac-4 proxy pivot):
  //
  // R-Mac: the v1.44.2 CSP set ``script-src 'self'`` which
  // blocked Next.js 14's hydration inline bootstrap script. Symptom reproduced
  // on the Mac:
  //   - /login renders a skeleton forever
  //   - DevTools console: "Executing inline script violates
  //     Content Security Policy directive 'script-src 'self''"
  //   - 0/319 E2E tests pass because the form never mounts
  // Fix: allow 'unsafe-inline' for hydration, but keep 'unsafe-eval'
  // and localhost websocket origins out of production.
  //
  // R-Mac-4: connect-src used to list the public backend origin
  // (NEXT_PUBLIC_API_BASE → http://localhost:8000) because the
  // browser fired XHRs cross-origin. With the same-origin proxy
  // pivot, every XHR resolves against the Next.js origin, so
  // 'self' is the only source we need. WebSocket origins kept
  // for Next.js HMR + dev tooling.
  //
  // The frame-ancestors / form-action / base-uri tighten
  // clickjacking + form-hijacking — the bits the audit actually
  // cared about.
  async headers() {
    const isProd = process.env.NODE_ENV === "production";
    if (!isProd) return [];
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
              "script-src 'self' 'unsafe-inline'; " +
              "style-src 'self' 'unsafe-inline'; " +
              "img-src 'self' data: blob:; " +
              "font-src 'self' data:; " +
              "connect-src 'self'; " +
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
