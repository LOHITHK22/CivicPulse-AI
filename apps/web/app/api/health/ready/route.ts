import { proxy } from "@/lib/server/proxy";

// Readiness of the analytics API as seen through this frontend (used by the UI and uptime checks).
export async function GET(req: Request): Promise<Response> {
  return proxy(req, "/health/ready");
}
