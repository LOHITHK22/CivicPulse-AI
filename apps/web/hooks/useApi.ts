"use client";

import { useEffect, useState } from "react";

export class ApiError extends Error {
  constructor(
    message: string,
    public status: number,
  ) {
    super(message);
  }
}

export type ApiState<T> =
  | { status: "loading"; data: undefined; error: undefined }
  | { status: "error"; data: undefined; error: ApiError }
  | { status: "success"; data: T; error: undefined };

function describe(status: number, body: unknown): string {
  const detail = (body as { detail?: unknown } | null)?.detail;
  if (status === 503) return "The analytics warehouse is not available. Run the ingestion pipeline (see README).";
  if (status === 429) return "Too many requests — please wait a moment and retry.";
  if (status === 422 && Array.isArray(detail)) return detail.map((d) => d.msg).join("; ");
  if (typeof detail === "string") return detail;
  if (status >= 500) return "The API returned an unexpected error.";
  return `Request failed (HTTP ${status}).`;
}

export async function fetchJson<T>(path: string, signal?: AbortSignal): Promise<T> {
  let res: Response;
  try {
    res = await fetch(path, { signal, headers: { Accept: "application/json" } });
  } catch (e) {
    if ((e as Error).name === "AbortError") throw e;
    throw new ApiError("Could not reach the CivicPulse API. Is it running?", 0);
  }
  const body = await res.json().catch(() => null);
  if (!res.ok) throw new ApiError(describe(res.status, body), res.status);
  return body as T;
}

/** Fetch JSON from the API. Pass null to skip (e.g. while filter defaults are unresolved). */
export function useApi<T>(path: string | null, reloadKey = 0): ApiState<T> {
  // Each settled result is tagged with the request it belongs to; anything else is "loading".
  const key = path ? `${path}#${reloadKey}` : null;
  const [settled, setSettled] = useState<{ key: string; state: ApiState<T> } | null>(null);

  useEffect(() => {
    if (!path || !key) return;
    const ctrl = new AbortController();
    fetchJson<T>(path, ctrl.signal)
      .then((data) => setSettled({ key, state: { status: "success", data, error: undefined } }))
      .catch((e: Error) => {
        if (e.name === "AbortError") return;
        const error = e instanceof ApiError ? e : new ApiError(e.message, 0);
        setSettled({ key, state: { status: "error", data: undefined, error } });
      });
    return () => ctrl.abort();
  }, [path, key]);

  if (settled && settled.key === key) return settled.state;
  return { status: "loading", data: undefined, error: undefined };
}

/** POST JSON (used only by the AI analyst). Errors carry the API's safe message, never a stack. */
export async function postJson<T>(path: string, body: unknown, signal?: AbortSignal): Promise<T> {
  let res: Response;
  try {
    res = await fetch(path, {
      method: "POST",
      signal,
      headers: { Accept: "application/json", "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
  } catch (e) {
    if ((e as Error).name === "AbortError") throw e;
    throw new ApiError("Could not reach the CivicPulse API. Is it running?", 0);
  }
  const data = await res.json().catch(() => null);
  if (!res.ok) {
    const code = (data as { code?: string } | null)?.code;
    if (res.status === 429) throw new ApiError("You're asking questions faster than the analyst allows. Please wait a minute and retry.", 429);
    if (res.status === 503 && code) throw new ApiError("The AI analyst's daily limit has been reached. Please try again later.", 503);
    throw new ApiError(describe(res.status, data), res.status);
  }
  return data as T;
}
