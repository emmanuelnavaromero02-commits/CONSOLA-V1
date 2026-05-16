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
  // v1.44.2 (Security R1 pre-emption): CSP must not allow unsafe-eval.
  // dev mode injects react-refresh which uses eval; CSP locks down only
  // in production. Adjust via headers() once Sentry/etc. are wired.
  async headers() {
    if (process.env.NODE_ENV !== "production") return [];
    return [
      {
        source: "/(.*)",
        headers: [
          { key: "X-Frame-Options", value: "DENY" },
          { key: "X-Content-Type-Options", value: "nosniff" },
          { key: "Referrer-Policy", value: "strict-origin-when-cross-origin" },
          {
            key: "Content-Security-Policy",
            // No unsafe-eval; unsafe-inline tolerated for styled-jsx
            // until we wire a nonce middleware in v1.45.
            value:
              "default-src 'self'; " +
              "script-src 'self'; " +
              "style-src 'self' 'unsafe-inline'; " +
              "img-src 'self' data:; " +
              "connect-src 'self' " +
              (process.env.NEXT_PUBLIC_API_BASE || "http://localhost:8000") +
              "; " +
              "font-src 'self' data:; " +
              "frame-ancestors 'none';",
          },
        ],
      },
    ];
  },
};

export default nextConfig;
