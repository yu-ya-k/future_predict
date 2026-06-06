/**
 * TypeScript mirror of apps/api/src/api/research/schemas.py.
 *
 * Field names are kept in snake_case to match the JSON wire format exactly,
 * so API responses can be consumed without key transformation. See ui_plan.md
 * Part A (A4) — this file is the source of truth for run-state semantics on the
 * frontend and must stay in sync with schemas.py.
 */

// ── Enums (schemas.py:11-38) ────────────────────────────────────────────────

export type RunStatus =
  | "queued"
  | "submitted"
  | "waiting_deep_research"
  | "collecting"
  | "reviewing"
  | "needs_action"
  | "needs_human_review"
  | "completed"
  | "cancelled"
  | "failed";

export type Verdict = "pass" | "needs_llm_fix" | "needs_deep_research" | "human_review";

export type HumanReviewAction =
  | "approve"
  | "request_review"
  | "request_llm_fix"
  | "request_deep_research"
  | "reject";

// ── Request / response models ───────────────────────────────────────────────

export interface ResearchRunOptions {
  max_deep_research_runs?: number | null; // 1-5
  max_llm_fix_runs?: number | null; // 0-10
  max_total_iterations?: number | null; // 1-20
  max_no_progress_rounds?: number | null; // 1-10
  max_total_tool_calls?: number | null;
}

export interface CreateResearchRunRequest {
  user_prompt: string; // 1-50000 chars
  options?: ResearchRunOptions;
}

export interface CreateResearchRunResponse {
  run_id: string;
  thread_id: string;
  status: RunStatus;
  created_at: string;
}

export interface RunProgress {
  deep_research_runs: number;
  llm_fix_runs: number;
  total_reviews: number;
  latest_verdict: Verdict | null;
  latest_score: number | null;
  total_tool_calls: number;
  estimated_cost_usd: number;
}

export interface ResearchRunStatusResponse {
  run_id: string;
  status: RunStatus;
  done_reason: string | null;
  needs_human_review: boolean;
  deep_research_submitted_at?: string | null;
  progress: RunProgress;
}

export interface ReportResponse {
  run_id: string;
  status: RunStatus;
  final_report: string | null;
  report: string | null;
  warnings: string[];
}

export interface Citation {
  title?: string | null;
  url?: string | null;
  start_index?: number | null;
  end_index?: number | null;
  source_type?: string | null;
  retrieved_at?: string | null;
}

export interface ToolCallSummary {
  type: string;
  status?: string | null;
  query?: string | null;
  url?: string | null;
  server_label?: string | null;
  duration_ms?: number | null;
  step?: string | null;
  response_id?: string | null;
}

export interface ResearchAttempt {
  run_no: number;
  response_id?: string | null;
  status: string;
  model: string;
  prompt: string;
  report: string;
  citations: Citation[];
  tool_calls_summary: ToolCallSummary[];
  error?: string | null;
  raw_response_artifact_path?: string | null;
}

export interface ReviewResult {
  verdict: Verdict;
  goal_achieved: boolean;
  score: number; // 0-100
  rationale: string;
  gaps: string[];
  factuality_concerns: string[];
  source_quality_concerns: string[];
  next_instructions: string | null;
  can_be_fixed_by_llm: boolean;
  requires_new_external_research: boolean;
  reviewer_confidence: number; // 0-100
  high_risk_flags: string[];
  public_web_search_used: boolean;
}

export interface ReviewRecord extends ReviewResult {
  review_no: number;
  recommended_route: Verdict;
  reviewer_response_id?: string | null;
  report_hash?: string | null;
}

export interface CostEvent {
  step: string;
  model: string;
  response_id?: string | null;
  input_tokens: number;
  output_tokens: number;
  tool_calls: number;
  estimated_cost_usd: number;
  created_at?: string | null;
}

export interface HumanReviewDecision {
  decision_no: number;
  action: HumanReviewAction;
  comment?: string | null;
  reviewer_id?: string | null;
  created_at: string;
}

export interface AuditResponse {
  run_id: string;
  attempts: ResearchAttempt[];
  reviews: ReviewRecord[];
  citations: Citation[];
  tool_calls: ToolCallSummary[];
  cost_events: CostEvent[];
  human_decisions: HumanReviewDecision[];
  history: Array<Record<string, unknown>>;
}

export interface CancelResponse {
  run_id: string;
  status: RunStatus;
}

export interface HumanReviewResumeAPIRequest {
  action: HumanReviewAction;
  comment?: string | null; // max 10000 chars
}

export interface HumanReviewResumeResponse {
  run_id: string;
  status: RunStatus;
  done_reason: string | null;
  needs_human_review: boolean;
}

export interface HumanReviewAuditSummary {
  deep_research_runs: number;
  llm_fix_runs: number;
  total_reviews: number;
  no_progress_count: number;
  total_tool_calls: number;
  estimated_cost_usd: number;
}

export interface HumanReviewQueueItem {
  run_id: string;
  status: RunStatus;
  done_reason: string | null;
  latest_verdict: Verdict | null;
  latest_score: number | null;
  latest_rationale: string | null;
  audit_summary: HumanReviewAuditSummary;
  created_at: string;
  updated_at: string;
}

export interface HumanReviewPayload {
  run_id: string;
  reason: string;
  latest_report: string;
  latest_review: ReviewRecord | null;
  allowed_actions: HumanReviewAction[];
  audit_summary: HumanReviewAuditSummary;
  warnings: string[];
}

// ── Option constraints (schemas.py ResearchRunOptions Field bounds) ──────────

export const OPTION_BOUNDS = {
  max_deep_research_runs: { min: 1, max: 5 },
  max_llm_fix_runs: { min: 0, max: 10 },
  max_total_iterations: { min: 1, max: 20 },
  max_no_progress_rounds: { min: 1, max: 10 },
} as const;

// ── Terminal-state helpers (A6 polling) ─────────────────────────────────────

export const TERMINAL_STATUSES: ReadonlySet<RunStatus> = new Set([
  "completed",
  "cancelled",
  "failed",
]);

export function isTerminal(status: RunStatus): boolean {
  return TERMINAL_STATUSES.has(status);
}
