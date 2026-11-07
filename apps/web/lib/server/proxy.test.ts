// Unit tests for the server-side API proxy. Run: npm run test:unit. Uses a local HTTP server as the backend.
import assert from "node:assert/strict";
import { createServer, type IncomingMessage } from "node:http";
import type { AddressInfo } from "node:net";
import { after, before, test } from "node:test";

import { apiPathFromSegments, clientIp, proxy, upstreamHeaders } from "./proxy.ts";

const SECRET = "s".repeat(40);
const TOKEN = "t".repeat(40);
let seen: IncomingMessage | undefined;
let seenBody = "";
const server = createServer((req, res) => {
  seen = req;
  seenBody = "";
  req.on("data", (c) => (seenBody += c));
  req.on("end", () => {
    res.setHeader("content-type", "application/json");
    res.setHeader("set-cookie", "leak=1");
    res.setHeader("x-request-id", "abc");
    res.end(JSON.stringify({ ok: true, path: req.url }));
  });
});

before(async () => {
  await new Promise<void>((r) => server.listen(0, "127.0.0.1", r));
  process.env.API_BASE_URL = `http://127.0.0.1:${(server.address() as AddressInfo).port}`;
  process.env.CIVICPULSE_PROXY_SECRET = SECRET;
  process.env.CIVICPULSE_AI_ACCESS_TOKEN = TOKEN;
  process.env.CIVICPULSE_CLIENT_IP_HEADER = "x-forwarded-for";
});
after(() => server.close());

test("client address comes only from the configured edge header, rightmost entry, IPs only", () => {
  const h = new Headers({ "x-forwarded-for": "6.6.6.6, 203.0.113.9", "x-real-ip": "1.1.1.1" });
  assert.equal(clientIp(h, "x-forwarded-for"), "203.0.113.9");
  assert.equal(clientIp(h, "x-real-ip"), "1.1.1.1");
  assert.equal(clientIp(h, ""), null); // not configured
  assert.equal(clientIp(new Headers({ "x-forwarded-for": "<script>" }), "x-forwarded-for"), null);
});

test("secrets are attached server-side; browser credentials are dropped", () => {
  const incoming = new Headers({
    authorization: "Bearer from-browser",
    cookie: "session=1",
    "x-civicpulse-proxy-auth": "forged",
    "x-civicpulse-client": "9.9.9.9",
    "x-forwarded-for": "203.0.113.9",
    origin: "https://civicpulse.example",
  });
  const ai = upstreamHeaders(incoming, "/api/v1/ai/investigations");
  assert.equal(ai.get("authorization"), `Bearer ${TOKEN}`);
  assert.equal(ai.get("x-civicpulse-proxy-auth"), SECRET);
  assert.equal(ai.get("x-civicpulse-client"), "203.0.113.9");
  assert.equal(ai.get("cookie"), null);
  assert.equal(ai.get("origin"), "https://civicpulse.example");
  const data = upstreamHeaders(incoming, "/api/v1/metrics/kpis");
  assert.equal(data.get("authorization"), null); // the AI token only goes to AI routes
});

test("dot segments cannot escape /api/v1", () => {
  assert.equal(apiPathFromSegments(["metrics", "kpis"]), "/api/v1/metrics/kpis");
  assert.equal(apiPathFromSegments(["..", "docs"]), null);
  assert.equal(apiPathFromSegments([]), null);
  assert.equal(apiPathFromSegments(["a/b"]), "/api/v1/a%2Fb");
});

test("proxies method, query and body; filters response headers", async () => {
  const req = new Request("https://web.example/api/v1/ai/investigations?x=1", {
    method: "POST",
    headers: { "content-type": "application/json", "x-forwarded-for": "198.51.100.7" },
    body: JSON.stringify({ question: "q" }),
  });
  const res = await proxy(req, "/api/v1/ai/investigations");
  assert.equal(res.status, 200);
  assert.deepEqual(await res.json(), { ok: true, path: "/api/v1/ai/investigations?x=1" });
  assert.equal(res.headers.get("set-cookie"), null);
  assert.equal(res.headers.get("x-request-id"), "abc");
  assert.equal(seen?.headers["authorization"], `Bearer ${TOKEN}`);
  assert.equal(seen?.headers["x-civicpulse-client"], "198.51.100.7");
  assert.equal(seen?.headers["content-length"], String(seenBody.length));
  assert.equal(seenBody, '{"question":"q"}');
});

test("oversized bodies are refused before reaching the backend", async () => {
  seen = undefined;
  const req = new Request("https://web.example/api/v1/ai/investigations", {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: "x".repeat(70_000),
  });
  assert.equal((await proxy(req, "/api/v1/ai/investigations")).status, 413);
  assert.equal(seen, undefined);
});

test("backend unavailable yields a JSON 502, not a crash or a hang", async () => {
  const saved = process.env.API_BASE_URL;
  process.env.API_BASE_URL = "http://127.0.0.1:9"; // discard port: connection refused
  const res = await proxy(new Request("https://web.example/api/v1/metrics/kpis"), "/api/v1/metrics/kpis");
  process.env.API_BASE_URL = saved;
  assert.equal(res.status, 502);
  assert.equal((await res.json()).code, "backend_unavailable");
});
