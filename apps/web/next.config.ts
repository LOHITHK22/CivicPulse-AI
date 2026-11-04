import type { NextConfig } from "next";

// The browser only ever talks to this Next.js origin. /api/v1/* is proxied server-side to the
// FastAPI service, so no API URL or credential is shipped in the client bundle and CORS is not
// needed in production. API_BASE_URL is read at build/start time on the server only.
const API_BASE_URL = process.env.API_BASE_URL ?? "http://127.0.0.1:8000";

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
  async rewrites() {
    return [
      { source: "/api/v1/:path*", destination: `${API_BASE_URL}/api/v1/:path*` },
      { source: "/api/health/ready", destination: `${API_BASE_URL}/health/ready` },
    ];
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
        ],
      },
    ];
  },
};

export default nextConfig;
