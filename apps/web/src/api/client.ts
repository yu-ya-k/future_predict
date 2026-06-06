/**
 * fetch wrapper for the research API (ui_plan.md A2 / A6).
 *
 * Responsibilities:
 *  - Prefix requests with VITE_API_BASE_URL (env.ts).
 *  - Auto-inject the `X-Reviewer-Id` header on endpoints that require it
 *    (GAP-4). Throws ReviewerRequiredError before the request if missing.
 *  - Normalise non-2xx responses into a typed ApiError carrying the status,
 *    so callers can branch on 401 / 404 / 409 (GAP-4, A4 guards).
 */

import { env } from "../env";
import { getReviewerId } from "../reviewer";

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

/** Raised client-side when a reviewer-scoped endpoint is called without an id. */
export class ReviewerRequiredError extends ApiError {
  constructor() {
    super(401, "Reviewer identity is required.");
    this.name = "ReviewerRequiredError";
  }
}

interface RequestOptions {
  method?: "GET" | "POST";
  body?: unknown;
  /** Require + inject the X-Reviewer-Id header (GAP-4). */
  reviewer?: boolean;
  signal?: AbortSignal;
}

async function request<T>(path: string, options: RequestOptions = {}): Promise<T> {
  const { method = "GET", body, reviewer = false, signal } = options;

  const headers: Record<string, string> = {};
  if (body !== undefined) {
    headers["Content-Type"] = "application/json";
  }

  if (reviewer) {
    const reviewerId = getReviewerId();
    if (!reviewerId) {
      throw new ReviewerRequiredError();
    }
    headers["X-Reviewer-Id"] = reviewerId;
  }

  let response: Response;
  try {
    response = await fetch(`${env.apiBaseUrl}${path}`, {
      method,
      headers,
      body: body === undefined ? undefined : JSON.stringify(body),
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
