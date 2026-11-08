import type { NextConfig } from "next";

// The browser only ever talks to this Next.js origin. /api/v1/* and /api/health/ready are served by
// route handlers (app/api/**) that proxy server-side to FastAPI and attach secrets on the server, so
// no API URL or credential is shipped in the client bundle and CORS is not needed in production.

const nextConfig: NextConfig = {
  cacheComponents: true,
  partialPrefetching: true,
  poweredByHeader: false,
  turbopack: {
    rules: {
      "*.css": {
        loaders: ["@tailwindcss/turbopack"],
        as: "*.css",
      },
    },
  },
  async headers() {
    return [
      {
        source: "/:path*",
        headers: [
          { key: "X-Content-Type-Options", value: "nosniff" },
          { key: "X-Frame-Options", value: "DENY" },
          { key: "Referrer-Policy", value: "strict-origin-when-cross-origin" },
          { key: "Permissions-Policy", value: "camera=(), microphone=(), geolocation=()" },
          // HTTPS-only once served over TLS (ignored by browsers on plain-HTTP localhost).
          { key: "Strict-Transport-Security", value: "max-age=31536000; includeSubDomains" },
          // Directives that need no script nonces; a full script-src policy is a documented follow-up.
          {
            key: "Content-Security-Policy",
            value: "frame-ancestors 'none'; base-uri 'self'; form-action 'self'; object-src 'none'",
          },
        ],
      },
    ];
  },
};

export default nextConfig;
