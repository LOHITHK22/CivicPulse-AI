// Server-side proxy from this Next.js origin to the FastAPI service. Runs only in route handlers
// (never bundled for the browser), so the secrets below stay on the server:
//   API_BASE_URL                 FastAPI base URL (private network or HTTPS)
//   CIVICPULSE_PROXY_SECRET      shared secret proving a request came through this proxy
//   CIVICPULSE_AI_ACCESS_TOKEN   bearer token the API requires on /api/v1/ai/* when configured
//   CIVICPULSE_CLIENT_IP_HEADER  header the hosting edge sets to the browser's address
//                                (e.g. x-vercel-forwarded-for on Vercel, x-forwarded-for behind an
//                                appending load balancer; unset = no client attribution)
// Browser-supplied Authorization, cookies and X-CivicPulse-* headers are never forwarded.

const REQUEST_HEADERS = ["accept", "accept-language", "content-type", "origin", "user-agent"];
const RESPONSE_HEADERS = [
  "cache-control",
  "content-disposition",
  "content-security-policy",
  "content-type",
  "referrer-policy",
  "retry-after",
  "x-content-type-options",
  "x-frame-options",
  "x-request-id",
];
export const MAX_BODY_BYTES = 65_536; // the API enforces its own, smaller limit
const TIMEOUT_MS = 55_000; // below the 60 s function limit of the hosting plan
const IP = /^[0-9A-Fa-f:.]{2,45}$/;

function env(name: string): string | undefined {
  const v = process.env[name]?.trim();
  return v ? v : undefined;
}

/** The browser address as reported by the hosting edge, or null when not configured / not an IP. */
export function clientIp(headers: Headers, headerName = env("CIVICPULSE_CLIENT_IP_HEADER")): string | null {
  if (!headerName) return null;
  const raw = headers.get(headerName.toLowerCase());
  if (!raw) return null;
  // Rightmost entry: the address the edge itself observed (earlier entries can be client-forged).
  const last = raw.split(",").map((s) => s.trim()).filter(Boolean).at(-1) ?? "";
  return IP.test(last) ? last : null;
}

export function upstreamHeaders(incoming: Headers, apiPath: string): Headers {
  const out = new Headers();
  for (const k of REQUEST_HEADERS) {
    const v = incoming.get(k);
    if (v) out.set(k, v);
  }
  const secret = env("CIVICPULSE_PROXY_SECRET");
  if (secret) {
    out.set("x-civicpulse-proxy-auth", secret);
    const ip = clientIp(incoming);
    if (ip) out.set("x-civicpulse-client", ip);
  }
  const token = env("CIVICPULSE_AI_ACCESS_TOKEN");
  if (token && apiPath.startsWith("/api/v1/ai/")) out.set("authorization", `Bearer ${token}`);
  return out;
}

function unavailable(status: number, detail: string): Response {
  return Response.json({ detail, code: "backend_unavailable" }, { status, headers: { "cache-control": "no-store" } });
}

export async function proxy(req: Request, apiPath: string): Promise<Response> {
  const base = (env("API_BASE_URL") ?? "http://127.0.0.1:8000").replace(/\/+$/, "");
  const search = new URL(req.url).search;
  const headers = upstreamHeaders(req.headers, apiPath);
  let body: ArrayBuffer | undefined;
  if (req.method === "POST") {
    const declared = Number(req.headers.get("content-length") ?? "0");
    if (declared > MAX_BODY_BYTES) return Response.json({ detail: "request body too large" }, { status: 413 });
    body = await req.arrayBuffer();
    if (body.byteLength > MAX_BODY_BYTES) return Response.json({ detail: "request body too large" }, { status: 413 });
  }
  let res: Response;
  try {
    res = await fetch(`${base}${apiPath}${search}`, {
      method: req.method,
      headers,
      body,
      redirect: "manual",
      cache: "no-store",
      signal: AbortSignal.timeout(TIMEOUT_MS),
    });
  } catch (err) {
    const timedOut = err instanceof Error && err.name === "TimeoutError";
    return timedOut
      ? unavailable(504, "analytics service timed out")
      : unavailable(502, "analytics service unavailable");
  }
  const out = new Headers();
  for (const k of RESPONSE_HEADERS) {
    const v = res.headers.get(k);
    if (v) out.set(k, v);
  }
  return new Response(req.method === "HEAD" ? null : res.body, { status: res.status, headers: out });
}

/** "/api/v1/" + encoded segments; refuses dot segments so the proxy cannot reach other API routes. */
export function apiPathFromSegments(segments: string[]): string | null {
  if (segments.length === 0 || segments.some((s) => s === "" || s === "." || s === "..")) return null;
  return `/api/v1/${segments.map(encodeURIComponent).join("/")}`;
}
