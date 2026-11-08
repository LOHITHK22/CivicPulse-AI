import { apiPathFromSegments, proxy } from "@/lib/server/proxy";

// Server-side proxy for the analytics API (see lib/server/proxy.ts). Replaces the former rewrite so that
// secrets are attached on the server and the backend URL is read at runtime, not baked into the build.
export const maxDuration = 60;

type Ctx = { params: Promise<{ path: string[] }> };

async function handle(req: Request, { params }: Ctx): Promise<Response> {
  const apiPath = apiPathFromSegments((await params).path);
  if (!apiPath) return Response.json({ detail: "not found" }, { status: 404 });
  return proxy(req, apiPath);
}

export const GET = handle;
export const HEAD = handle;
export const POST = handle;
