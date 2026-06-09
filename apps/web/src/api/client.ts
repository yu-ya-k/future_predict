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
  readonly code: string | undefined;
  readonly details: Record<string, unknown> | undefined;

  constructor(
    status: number,
    message: string,
    detail?: string,
    code?: string,
    details?: Record<string, unknown>,
  ) {
    super(message);
    this.name = "ApiError";
    this.status = status;
    this.detail = detail;
    this.code = code;
    this.details = details;
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
  idempotencyKey?: string;
}

async function request<T>(path: string, options: RequestOptions = {}): Promise<T> {
  const { method = "GET", body, signal, idempotencyKey } = options;

  const headers: Record<string, string> = {};
  const apiKey = getResearchApiKey();
  if (apiKey) {
    headers["X-API-Key"] = apiKey;
  }
  if (idempotencyKey?.trim()) {
    headers["Idempotency-Key"] = idempotencyKey.trim();
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
    const errorDetail = await readErrorDetail(response);
    throw new ApiError(
      response.status,
      errorDetail.message ?? `API returned ${response.status}`,
      errorDetail.detail,
      errorDetail.code,
      errorDetail.details,
    );
  }

  if (response.status === 204) {
    return undefined as T;
  }

  return (await response.json()) as T;
}

async function readErrorDetail(response: Response): Promise<{
  message?: string;
  detail?: string;
  code?: string;
  details?: Record<string, unknown>;
}> {
  try {
    const data = (await response.json()) as { detail?: unknown };
    if (
      data.detail &&
      typeof data.detail === "object" &&
      "code" in data.detail &&
      "message" in data.detail
    ) {
      const typed = data.detail as {
        code?: unknown;
        message?: unknown;
        details?: unknown;
      };
      return {
        message: typeof typed.message === "string" ? typed.message : undefined,
        detail: JSON.stringify(data.detail),
        code: typeof typed.code === "string" ? typed.code : undefined,
        details:
          typed.details && typeof typed.details === "object"
            ? (typed.details as Record<string, unknown>)
            : undefined,
      };
    }
    if (typeof data.detail === "string") {
      return { message: data.detail, detail: data.detail };
    }
    if (data.detail != null) {
      const detail = JSON.stringify(data.detail);
      return { message: detail, detail };
    }
  } catch {
    /* non-JSON body */
  }
  return {};
}

export const apiClient = { request };
