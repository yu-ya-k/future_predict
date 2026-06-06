/**
 * Typed research-run endpoint functions (ui_plan.md A2-1).
 *
 * One function per real endpoint in router.py. Endpoints flagged with
 * human-review behavior are local MVP endpoints and do not require identity.
 */

import type {
  AuditResponse,
  CancelResponse,
  Citation,
  CostEvent,
  CreateResearchRunRequest,
  CreateResearchRunResponse,
  HumanReviewDecision,
  HumanReviewPayload,
  HumanReviewQueueItem,
  HumanReviewResumeAPIRequest,
  HumanReviewResumeResponse,
  ObjectiveContract,
  ReportResponse,
  ResearchAttempt,
  ResearchItem,
  RerunPlan,
  ResearchRunStatusResponse,
  ToolCallSummary,
} from "../types";
import { apiClient } from "./client";

const BASE = "/research-runs";

interface ContractResponse {
  run_id: string;
  contract: ObjectiveContract;
}

interface ItemsResponse {
  run_id: string;
  items: ResearchItem[];
}

interface RerunPlansResponse {
  run_id: string;
  rerun_plans: RerunPlan[];
}

export function createRun(
  request: CreateResearchRunRequest,
  signal?: AbortSignal,
): Promise<CreateResearchRunResponse> {
  return apiClient.request(BASE, { method: "POST", body: request, signal });
}

export function listHumanReviews(signal?: AbortSignal): Promise<HumanReviewQueueItem[]> {
  return apiClient.request(`${BASE}/human-reviews`, { signal });
}

export function getRunStatus(
  runId: string,
  signal?: AbortSignal,
): Promise<ResearchRunStatusResponse> {
  return apiClient.request(`${BASE}/${runId}`, { signal });
}

export function getReport(runId: string, signal?: AbortSignal): Promise<ReportResponse> {
  return apiClient.request(`${BASE}/${runId}/report`, { signal });
}

export async function getContract(
  runId: string,
  signal?: AbortSignal,
): Promise<ObjectiveContract> {
  const response = await apiClient.request<ContractResponse>(`${BASE}/${runId}/contract`, {
    signal,
  });
  return response.contract;
}

export async function getItems(runId: string, signal?: AbortSignal): Promise<ResearchItem[]> {
  const response = await apiClient.request<ItemsResponse>(`${BASE}/${runId}/items`, {
    signal,
  });
  return response.items;
}

export async function getRerunPlans(
  runId: string,
  signal?: AbortSignal,
): Promise<RerunPlan[]> {
  const response = await apiClient.request<RerunPlansResponse>(
    `${BASE}/${runId}/rerun-plans`,
    { signal },
  );
  return response.rerun_plans;
}

export function getAudit(runId: string, signal?: AbortSignal): Promise<AuditResponse> {
  return apiClient.request(`${BASE}/${runId}/audit`, { signal });
}

export function getCitations(runId: string, signal?: AbortSignal): Promise<Citation[]> {
  return apiClient.request(`${BASE}/${runId}/citations`, { signal });
}

export function getAttempts(runId: string, signal?: AbortSignal): Promise<ResearchAttempt[]> {
  return apiClient.request(`${BASE}/${runId}/attempts`, { signal });
}

export function getToolCalls(runId: string, signal?: AbortSignal): Promise<ToolCallSummary[]> {
  return apiClient.request(`${BASE}/${runId}/tool-calls`, { signal });
}

export function getCostEvents(runId: string, signal?: AbortSignal): Promise<CostEvent[]> {
  return apiClient.request(`${BASE}/${runId}/cost-events`, { signal });
}

export function getHumanReviewPayload(
  runId: string,
  signal?: AbortSignal,
): Promise<HumanReviewPayload> {
  return apiClient.request(`${BASE}/${runId}/human-review`, { signal });
}

export function getHumanDecisions(
  runId: string,
  signal?: AbortSignal,
): Promise<HumanReviewDecision[]> {
  return apiClient.request(`${BASE}/${runId}/human-decisions`, { signal });
}

export function cancelRun(runId: string, signal?: AbortSignal): Promise<CancelResponse> {
  return apiClient.request(`${BASE}/${runId}/cancel`, { method: "POST", signal });
}

export function deleteRun(runId: string, signal?: AbortSignal): Promise<void> {
  return apiClient.request(`${BASE}/${runId}`, { method: "DELETE", signal });
}

export function resumeRun(
  runId: string,
  request: HumanReviewResumeAPIRequest,
  signal?: AbortSignal,
): Promise<HumanReviewResumeResponse> {
  return apiClient.request(`${BASE}/${runId}/resume`, {
    method: "POST",
    body: request,
    signal,
  });
}
