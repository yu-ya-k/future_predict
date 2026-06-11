import type {
  CommitVersionResponse,
  ComputeProbabilitiesRequest,
  ComputeProjectionRequest,
  EstimateSetResponse,
  EvidenceExtractResponse,
  ForecastAuditResponse,
  ForecastCreateRequest,
  ForecastCreateResponse,
  ForecastDetail,
  ForecastFramingDraftRequest,
  ForecastFramingDraftResponse,
  ManualResearchPackPromptResponse,
  ForecastReviewRequest,
  ForecastReviewResponse,
  ForecastSummary,
  ProjectionSetResponse,
  ResearchPackDefaultsResponse,
  ResearchPackRerunRequest,
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

export function createForecastFramingDraft(
  request: ForecastFramingDraftRequest,
  input?: AbortSignal | ForecastCommandOptions,
): Promise<ForecastFramingDraftResponse> {
  const options = optionsFrom(input);
  return apiClient.request(`${BASE}/framing-drafts`, {
    method: "POST",
    body: request,
    signal: options.signal,
    idempotencyKey: commandKey("forecast-framing-draft", options),
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

export function listForecastResearchPacks(
  forecastId: string,
  signal?: AbortSignal,
): Promise<ResearchPackResponse[]> {
  return apiClient.request(`${BASE}/${forecastId}/research-packs`, { signal });
}

export function dispatchDefaultResearchPacks(
  forecastId: string,
  input?: AbortSignal | ForecastCommandOptions,
): Promise<ResearchPackDefaultsResponse> {
  const options = optionsFrom(input);
  return apiClient.request(`${BASE}/${forecastId}/research-packs/defaults`, {
    method: "POST",
    signal: options.signal,
    idempotencyKey: commandKey("forecast-packs-defaults", options),
  });
}

export function rerunForecastResearchPack(
  forecastId: string,
  packId: string,
  request: ResearchPackRerunRequest,
  input?: AbortSignal | ForecastCommandOptions,
): Promise<ResearchPackResponse> {
  const options = optionsFrom(input);
  return apiClient.request(`${BASE}/${forecastId}/research-packs/${packId}/rerun`, {
    method: "POST",
    body: request,
    signal: options.signal,
    idempotencyKey: commandKey("forecast-pack-rerun", options),
  });
}

export function getManualResearchPackPrompt(
  forecastId: string,
  signal?: AbortSignal,
): Promise<ManualResearchPackPromptResponse> {
  return apiClient.request(`${BASE}/${forecastId}/research-packs/manual-prompt`, {
    signal,
  });
}

type ManualPackReportSource =
  | { source: "text"; text: string }
  | { source: "file"; file: File };

export function importManualResearchPack(
  forecastId: string,
  request: {
    promptSha256: string;
    report: ManualPackReportSource;
  },
  input?: AbortSignal | ForecastCommandOptions,
): Promise<ResearchPackResponse> {
  const options = optionsFrom(input);
  const body = new FormData();
  body.append("prompt_sha256", request.promptSha256);
  if (request.report.source === "file") {
    body.append("report_file", request.report.file);
  } else {
    body.append("report_text", request.report.text);
  }
  return apiClient.request(`${BASE}/${forecastId}/research-packs/manual-import`, {
    method: "POST",
    body,
    signal: options.signal,
    idempotencyKey: commandKey("forecast-manual-pack", options),
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
  requestOrInput?: ComputeProbabilitiesRequest | AbortSignal | ForecastCommandOptions,
  maybeInput?: AbortSignal | ForecastCommandOptions,
): Promise<EstimateSetResponse> {
  const hasRequest =
    requestOrInput &&
    !(typeof AbortSignal !== "undefined" && requestOrInput instanceof AbortSignal) &&
    "engine_version" in requestOrInput;
  const request = hasRequest ? (requestOrInput as ComputeProbabilitiesRequest) : {};
  const options = optionsFrom(
    hasRequest
      ? maybeInput
      : (requestOrInput as AbortSignal | ForecastCommandOptions | undefined),
  );
  return apiClient.request(`${BASE}/${forecastId}/probabilities/compute`, {
    method: "POST",
    body: request,
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

export function computeProjection(
  forecastId: string,
  requestOrInput?: ComputeProjectionRequest | AbortSignal | ForecastCommandOptions,
  maybeInput?: AbortSignal | ForecastCommandOptions,
): Promise<ProjectionSetResponse> {
  const hasRequest =
    requestOrInput &&
    !(typeof AbortSignal !== "undefined" && requestOrInput instanceof AbortSignal) &&
    "engine_version" in requestOrInput;
  const request = hasRequest ? (requestOrInput as ComputeProjectionRequest) : {};
  const options = optionsFrom(
    hasRequest
      ? maybeInput
      : (requestOrInput as AbortSignal | ForecastCommandOptions | undefined),
  );
  return apiClient.request(`${BASE}/${forecastId}/projections/compute`, {
    method: "POST",
    body: request,
    signal: options.signal,
    idempotencyKey: commandKey("forecast-projection", options),
  });
}

export function getCurrentProjection(
  forecastId: string,
  signal?: AbortSignal,
): Promise<ProjectionSetResponse> {
  return apiClient.request(`${BASE}/${forecastId}/projections/current`, { signal });
}

export function approveProjection(
  forecastId: string,
  projectionSetId: string,
  input?: AbortSignal | ForecastCommandOptions,
): Promise<ForecastReviewResponse> {
  const options = optionsFrom(input);
  return apiClient.request(`${BASE}/${forecastId}/projections/${projectionSetId}/approve`, {
    method: "POST",
    signal: options.signal,
    idempotencyKey: commandKey("forecast-projection-approve", options),
  });
}

export function commitForecastVersion(
  forecastId: string,
  request:
    | { estimate_set_id: string; projection_set_id?: never; expected_input_snapshot_hash: string }
    | { projection_set_id: string; estimate_set_id?: never; expected_input_snapshot_hash: string },
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
