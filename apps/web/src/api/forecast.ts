import type {
  CommitVersionResponse,
  EstimateSetResponse,
  EvidenceExtractResponse,
  ForecastAuditResponse,
  ForecastCreateRequest,
  ForecastCreateResponse,
  ForecastDetail,
  ForecastReviewRequest,
  ForecastReviewResponse,
  ForecastSummary,
  ResearchPackResponse,
  ResolveForecastResponse,
  ScenarioGenerateResponse,
} from "../types";
import { apiClient } from "./client";

const BASE = "/forecasts";

export interface ForecastCommandOptions {
  signal?: AbortSignal;
  idempotencyKey?: string;
}

function key(prefix: string): string {
  return `${prefix}-${crypto.randomUUID()}`;
}

function optionsFrom(input?: AbortSignal | ForecastCommandOptions): ForecastCommandOptions {
  if (!input) return {};
  if (typeof AbortSignal !== "undefined" && input instanceof AbortSignal) {
    return { signal: input };
  }
  return input as ForecastCommandOptions;
}

function commandKey(prefix: string, options: ForecastCommandOptions): string {
  const stableKey = options.idempotencyKey?.trim();
  return stableKey || key(prefix);
}

export function listForecasts(signal?: AbortSignal): Promise<ForecastSummary[]> {
  return apiClient.request(BASE, { signal });
}

export function createForecast(
  request: ForecastCreateRequest,
  input?: AbortSignal | ForecastCommandOptions,
): Promise<ForecastCreateResponse> {
  const options = optionsFrom(input);
  return apiClient.request(BASE, {
    method: "POST",
    body: request,
    signal: options.signal,
    idempotencyKey: commandKey("forecast-create", options),
  });
}

export function getForecast(
  forecastId: string,
  signal?: AbortSignal,
): Promise<ForecastDetail> {
  return apiClient.request(`${BASE}/${forecastId}`, { signal });
}

export function reviewForecast(
  forecastId: string,
  request: ForecastReviewRequest,
  input?: AbortSignal | ForecastCommandOptions,
): Promise<ForecastReviewResponse> {
  const options = optionsFrom(input);
  return apiClient.request(`${BASE}/${forecastId}/review`, {
    method: "POST",
    body: request,
    signal: options.signal,
    idempotencyKey: commandKey(`forecast-review-${request.action}`, options),
  });
}

export function dispatchCurrentStatePack(
  forecastId: string,
  input?: AbortSignal | ForecastCommandOptions,
): Promise<ResearchPackResponse> {
  const options = optionsFrom(input);
  return apiClient.request(`${BASE}/${forecastId}/research-packs`, {
    method: "POST",
    body: { pack_role: "current_state", tool_profile: "public" },
    signal: options.signal,
    idempotencyKey: commandKey("forecast-pack", options),
  });
}

export function extractEvidence(
  forecastId: string,
  input?: AbortSignal | ForecastCommandOptions,
): Promise<EvidenceExtractResponse> {
  const options = optionsFrom(input);
  return apiClient.request(`${BASE}/${forecastId}/evidence/extract`, {
    method: "POST",
    signal: options.signal,
    idempotencyKey: commandKey("forecast-evidence", options),
  });
}

export function generateScenarios(
  forecastId: string,
  input?: AbortSignal | ForecastCommandOptions,
): Promise<ScenarioGenerateResponse> {
  const options = optionsFrom(input);
  return apiClient.request(`${BASE}/${forecastId}/scenarios/generate`, {
    method: "POST",
    signal: options.signal,
    idempotencyKey: commandKey("forecast-scenarios", options),
  });
}

export function computeProbabilities(
  forecastId: string,
  input?: AbortSignal | ForecastCommandOptions,
): Promise<EstimateSetResponse> {
  const options = optionsFrom(input);
  return apiClient.request(`${BASE}/${forecastId}/probabilities/compute`, {
    method: "POST",
    signal: options.signal,
    idempotencyKey: commandKey("forecast-probability", options),
  });
}

export function getForecastEstimateSet(
  forecastId: string,
  signal?: AbortSignal,
): Promise<EstimateSetResponse> {
  return apiClient.request(`${BASE}/${forecastId}/estimate-set`, { signal });
}

export function commitForecastVersion(
  forecastId: string,
  request: { estimate_set_id: string; expected_input_snapshot_hash: string },
  input?: AbortSignal | ForecastCommandOptions,
): Promise<CommitVersionResponse> {
  const options = optionsFrom(input);
  return apiClient.request(`${BASE}/${forecastId}/versions/commit`, {
    method: "POST",
    body: request,
    signal: options.signal,
    idempotencyKey: commandKey("forecast-commit", options),
  });
}

export function resolveForecast(
  forecastId: string,
  request: { outcome_id: string; resolution_notes?: string | null },
  input?: AbortSignal | ForecastCommandOptions,
): Promise<ResolveForecastResponse> {
  const options = optionsFrom(input);
  return apiClient.request(`${BASE}/${forecastId}/resolve`, {
    method: "POST",
    body: request,
    signal: options.signal,
    idempotencyKey: commandKey("forecast-resolve", options),
  });
}

export function getForecastAudit(
  forecastId: string,
  signal?: AbortSignal,
): Promise<ForecastAuditResponse> {
  return apiClient.request(`${BASE}/${forecastId}/audit`, { signal });
}
