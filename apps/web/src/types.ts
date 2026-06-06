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

export type Verdict =
  | "pass"
  | "needs_llm_patch"
  | "needs_verification"
  | "needs_targeted_rerun"
  | "needs_full_rerun"
  | "needs_item_revision"
  | "finalize_with_limitation"
  | "human_review";

export type HumanReviewAction =
  | "approve"
  | "approve_with_limitation"
  | "request_review"
  | "request_llm_patch"
  | "request_verification"
  | "request_targeted_rerun"
  | "request_item_revision"
  | "reject";

export type ResearchItemStatus =
  | "not_started"
  | "answered"
  | "partial"
  | "unanswered"
  | "unverifiable"
  | "out_of_scope";

export type ResearchSeverity = "blocker" | "major" | "minor";

export type ExpectedAnswerType =
  | "fact"
  | "comparison"
  | "timeline"
  | "market_size"
  | "pros_cons"
  | "risk"
  | "recommendation"
  | "synthesis"
  | "other";

export type FailureMode =
  | "none"
  | "format_only"
  | "in_report_but_lost"
  | "needs_targeted_verification"
  | "needs_different_sources"
  | "needs_deeper_search"
  | "needs_query_reformulation"
  | "source_contradiction"
  | "likely_not_publicly_available"
  | "criterion_too_ambiguous"
  | "requires_human_judgment";

export type RecommendedAction =
  | "none"
  | "llm_patch"
  | "verify"
  | "targeted_rerun"
  | "full_rerun"
  | "human_review"
  | "finalize_with_limitation"
  | "revise_items";

export type TaskType =
  | "market_research"
  | "competitive_analysis"
  | "technical_research"
  | "regulatory_research"
  | "paper_survey"
  | "mixed_source_research"
  | "other";

export type VerificationMethod =
  | "semantic_answer"
  | "citation_required"
  | "freshness_check"
  | "source_quality_check"
  | "comparative_coverage"
  | "numerical_validation"
  | "risk_review";

export type ContractGeneratedBy =
  | "user_prompt"
  | "task_template"
  | "security_policy"
  | "freshness_policy"
  | "human_override";

export type RerunScope =
  | "targeted_gap_closure"
  | "source_refresh"
  | "contradiction_resolution"
  | "full_rerun";

export type RerunOutputMode = "delta_sections_only" | "evidence_summary_only";

// ── Request / response models ───────────────────────────────────────────────

export interface ResearchRunOptions {
  max_targeted_rerun_runs?: number | null; // 0-5
  max_full_rerun_runs?: number | null; // 0-3
  max_llm_patch_runs?: number | null; // 0-10
  max_verification_runs?: number | null; // 0-10
  max_total_iterations?: number | null; // 1-20
  max_total_tool_calls?: number | null; // 1-1000
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
  items_total: number;
  items_answered: number;
  items_partial: number;
  items_unanswered: number;
  items_unverifiable: number;
  blockers_unresolved: number;
  targeted_rerun_runs: number;
  full_rerun_runs: number;
  llm_patch_runs: number;
  verification_runs: number;
  total_reviews: number;
  latest_verdict: Verdict | null;
  latest_score: number | null;
  total_tool_calls: number;
  estimated_cost_usd: number;
}

export interface ResearchRunStatusResponse {
  run_id: string;
  status: RunStatus;
  terminal_status?: string | null;
  done_reason: string | null;
  needs_human_review: boolean;
  deep_research_submitted_at?: string | null;
  progress: RunProgress;
}

export interface AcceptanceCriterion {
  criterion_id: string;
  description: string;
  verification_method: VerificationMethod;
  severity: ResearchSeverity;
  required_evidence_type: string[];
  required_freshness: string | null;
  generated_by: ContractGeneratedBy;
  confidence: number;
}

export interface ObjectiveContract {
  contract_id: string;
  original_user_prompt: string;
  normalized_objective: string;
  task_type: TaskType;
  acceptance_criteria: AcceptanceCriterion[];
  source_policy?: Record<string, unknown>;
  freshness_policy?: Record<string, unknown>;
  security_policy?: Record<string, unknown>;
  output_requirements: string[];
  explicit_out_of_scope: string[];
  contract_confidence: number;
  contract_frozen: boolean;
}

export interface ResearchItem {
  item_id: string;
  criterion_id: string;
  question: string;
  expected_answer_type: ExpectedAnswerType;
  status: ResearchItemStatus;
  severity: ResearchSeverity;
  confidence: number;
  evidence_summary: string | null;
  citation_ids: string[];
  failure_mode: FailureMode | null;
  failure_mode_confidence: number | null;
  unresolved_reason: string | null;
  tried_queries: string[];
  tried_source_types: string[];
  last_attempt_no: number | null;
  last_review_no: number | null;
}

export interface RerunPlan {
  rerun_id: string;
  scope: RerunScope;
  target_item_ids: string[];
  preserve_item_ids: string[];
  target_questions: string[];
  missing_evidence: string[];
  preferred_source_types: string[];
  freshness_requirements: string[];
  already_tried_queries: string[];
  already_used_source_domains: string[];
  negative_source_hints: string[];
  forbidden_changes: string[];
  output_mode: RerunOutputMode;
  max_tool_calls: number;
  rerun_reason: string;
  created_at?: string | null;
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
  created_at?: string | null;
}

export interface ReviewResult {
  verdict: Verdict;
  goal_achieved: boolean;
  score: number; // 0-100
  rationale: string;
  route_rationale?: string | null;
  item_assessments: ItemAssessment[];
  gaps: string[];
  factuality_concerns: string[];
  source_quality_concerns: string[];
  freshness_concerns?: string[];
  security_concerns?: string[];
  next_instructions: string | null;
  reviewer_confidence: number; // 0-100
  high_risk_flags: string[];
  public_web_search_used: boolean;
}

export interface ItemAssessment {
  item_id: string;
  status: Exclude<ResearchItemStatus, "not_started">;
  severity: ResearchSeverity;
  failure_mode: FailureMode;
  failure_mode_confidence: number;
  recommended_action: RecommendedAction;
  evidence_summary: string | null;
  missing_evidence: string[];
  rationale: string;
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
  llm_calls: CostEvent[];
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
  targeted_rerun_runs: number;
  full_rerun_runs: number;
  llm_patch_runs: number;
  verification_runs: number;
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
  unresolved_items?: ResearchItem[];
  allowed_actions: HumanReviewAction[];
  audit_summary: HumanReviewAuditSummary;
  warnings: string[];
}

// ── Option constraints (schemas.py ResearchRunOptions Field bounds) ──────────

export const OPTION_BOUNDS = {
  max_targeted_rerun_runs: { min: 0, max: 5 },
  max_full_rerun_runs: { min: 0, max: 3 },
  max_llm_patch_runs: { min: 0, max: 10 },
  max_verification_runs: { min: 0, max: 10 },
  max_total_iterations: { min: 1, max: 20 },
  max_total_tool_calls: { min: 1, max: 1000 },
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
