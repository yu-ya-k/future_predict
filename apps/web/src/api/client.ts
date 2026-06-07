/**
 * fetch wrapper for the research API (ui_plan.md A2 / A6).
 *
 * Responsibilities:
 *  - Prefix requests with VITE_API_BASE_URL (env.ts).
 *  - Normalise non-2xx responses into a typed ApiError carrying the status,
 *    so callers can branch on 401 / 404 / 409 (GAP-4, A4 guards).
 */

import { env } from "../env";
import { getResearchApiKey } from "../researchApiKey";

export class ApiError extends Error {
  readonly status: number;
  readonly detail: string | undefined;

  constructor(status: number, message: string, detail?: string) {
    super(message);
    this.name = "ApiError";
    this.status = status;
    this.detail = detail;
  }

  get isUnauthorized(): boolean {
    return this.status === 401;
  }

  get isConflict(): boolean {
    return this.status === 409;
  }

  get isNotFound(): boolean {
    return this.status === 404;
  }
}

interface RequestOptions {
  method?: "DELETE" | "GET" | "POST";
  body?: unknown;
  signal?: AbortSignal;
}

async function request<T>(path: string, options: RequestOptions = {}): Promise<T> {
  const { method = "GET", body, signal } = options;

  const headers: Record<string, string> = {};
  const apiKey = getResearchApiKey();
  if (apiKey) {
    headers["X-API-Key"] = apiKey;
  }
  const isFormData = body instanceof FormData;
  if (body !== undefined && !isFormData) {
    headers["Content-Type"] = "application/json";
  }

  let response: Response;
  try {
    response = await fetch(`${env.apiBaseUrl}${path}`, {
      method,
      headers,
      body: body === undefined ? undefined : isFormData ? body : JSON.stringify(body),
      signal,
    });
  } catch (error) {
    if (error instanceof DOMException && error.name === "AbortError") throw error;
    const message = error instanceof Error ? error.message : "Network error";
    throw new ApiError(0, message);
  }

  if (!response.ok) {
    const detail = await readErrorDetail(response);
    throw new ApiError(response.status, detail ?? `API returned ${response.status}`, detail);
  }

  if (response.status === 204) {
    return undefined as T;
  }

  return (await response.json()) as T;
}

async function readErrorDetail(response: Response): Promise<string | undefined> {
  try {
    const data = (await response.json()) as { detail?: unknown };
    if (typeof data.detail === "string") return data.detail;
    if (data.detail != null) return JSON.stringify(data.detail);
  } catch {
    /* non-JSON body */
  }
  return undefined;
}

export const apiClient = { request };
