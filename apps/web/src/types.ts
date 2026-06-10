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
  | "request_full_rerun"
  | "request_manual_targeted_rerun"
  | "request_manual_full_rerun"
  | "request_item_revision"
  | "reject";

export type RerunExecutionMode = "api" | "manual_chatgpt" | "disabled";

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

export type RerunOutputMode =
  | "delta_sections_only"
  | "evidence_summary_only"
  | "targeted_delta_sections"
  | "complete_replacement_report";

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
  user_prompt: string; // 1-120000 chars
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

export interface ForecastRunContext {
  forecast_id: string;
  pack_id: string;
  pack_role: string;
  tool_profile: string;
}

export interface ResearchRunStatusResponse {
  run_id: string;
  status: RunStatus;
  terminal_status?: string | null;
  done_reason: string | null;
  needs_human_review: boolean;
  created_at: string;
  updated_at: string;
  deep_research_submitted_at?: string | null;
  progress: RunProgress;
  forecast_context: ForecastRunContext | null;
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
  source?: "api" | "manual_upload" | "manual_chatgpt_rerun" | string;
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
  deep_research_runs: number;
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

export interface ManualRerunPrompt {
  rerun_id: string;
  scope: string;
  expected_output_kind: RerunOutputMode;
  expected_run_no: number;
  prompt: string;
  prompt_artifact_path: string;
  target_item_ids: string[];
  query_policy: QueryPolicyDecision;
  base_report_hash?: string | null;
  created_at: string;
}

export interface SuggestedRerunPrompt {
  scope: string;
  expected_output_kind: RerunOutputMode;
  expected_run_no: number;
  prompt: string;
  target_item_ids: string[];
  query_policy: QueryPolicyDecision;
  base_report_hash?: string | null;
}

export interface HumanReviewActionState {
  action: HumanReviewAction;
  allowed: boolean;
  blocked_reason?: string | null;
}

export interface HumanReviewRouteSummary {
  candidate_route?: string | null;
  selected_route?: string | null;
  blocked_reason?: string | null;
  dominant_actions: string[];
  latest_review_no?: number | null;
  latest_verdict?: Verdict | null;
}

export interface HumanReviewPayload {
  run_id: string;
  reason: string;
  latest_report: string;
  latest_review: ReviewRecord | null;
  unresolved_items?: ResearchItem[];
  allowed_actions: HumanReviewAction[];
  action_states?: HumanReviewActionState[];
  route_summary?: HumanReviewRouteSummary | null;
  audit_summary: HumanReviewAuditSummary;
  warnings: string[];
  pending_manual_rerun?: ManualRerunPrompt | null;
  suggested_rerun?: SuggestedRerunPrompt | null;
}

// ── Research checkpoints / fork lineage ─────────────────────────────────────

export interface ResearchCheckpointChildFork {
  run_id: string;
  status: RunStatus;
  done_reason?: string | null;
  created_at?: string | null;
}

export interface ResearchCheckpoint {
  checkpoint_id: string;
  run_id: string;
  checkpoint_no: number;
  kind: string;
  node_anchor: string;
  forkable: boolean;
  dedupe_key: string;
  source_attempt_no?: number | null;
  source_review_no?: number | null;
  source_response_id?: string | null;
  report_hash?: string | null;
  snapshot_json?: Record<string, unknown> | null;
  created_at: string;
  child_forks?: ResearchCheckpointChildFork[];
}

export interface ResearchCheckpointListResponse {
  run_id: string;
  checkpoints: ResearchCheckpoint[];
}

export interface ResearchRunLineage {
  run_id: string;
  root_run_id: string;
  parent_run_id: string;
  forked_from_checkpoint_id: string;
  fork_mode: string;
  additional_prompt: string;
  confirmed_preview_hash: string;
  idempotency_key: string;
  source_snapshot_json?: Record<string, unknown> | null;
  source_report_artifact_path?: string | null;
  created_at: string;
}

export interface ResearchRunLineageResponse {
  run_id: string;
  lineage: ResearchRunLineage | null;
}

export interface QueryPolicyDecision {
  status: string;
  safe_queries: string[];
  blocked_reason?: string | null;
}

export interface ResearchForkPreviewRequest {
  additional_prompt: string;
}

export interface ResearchForkPreviewResponse {
  run_id?: string;
  checkpoint_id?: string;
  composed_prompt: string;
  query_policy: QueryPolicyDecision;
  policy_decision: QueryPolicyDecision;
  source_prompt_excerpt: string;
  source_report_excerpt: string;
  warnings: string[];
  preview_hash: string;
}

export interface ResearchForkSubmitRequest {
  additional_prompt: string;
  idempotency_key: string;
  confirmed_preview_hash: string;
}

export interface ResearchForkSubmitResponse {
  run_id: string;
  parent_run_id: string;
  forked_from_checkpoint_id: string;
  child_run_id: string;
  status: RunStatus;
  done_reason?: string | null;
  needs_human_review: boolean;
  source_snapshot_json: Record<string, unknown>;
  lineage: ResearchRunLineage;
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

// ── Forecast PhaseA / PhaseB ────────────────────────────────────────────────

export type ForecastStatus =
  | "framing_pending"
  | "framing_approved"
  | "pack_running"
  | "evidence_ready"
  | "scenarios_ready"
  | "draft_ready"
  | "committed"
  | "resolved";

export type ForecastPackRole =
  | "current_state"
  | "base_rate"
  | "drivers"
  | "counter_evidence"
  | "signals";

export type ForecastToolProfile = "public" | "private" | "synthesis";

export type ForecastConfidentialityClass = "public" | "internal" | "restricted";

export interface ForecastOutcome {
  outcome_id: string;
  label: string;
  definition: string;
  resolution_rule: string;
  normalization_group_id: string;
  sort_order: number;
}

export interface ForecastSummary {
  forecast_id: string;
  question: string;
  status: ForecastStatus;
  resolution_date?: string | null;
  current_framing_version: number;
  approved_framing_version?: number | null;
  committed_version_id?: string | null;
  resolved_at?: string | null;
  created_at: string;
  updated_at: string;
}

export interface ForecastCurrentResearchPack {
  pack_id: string;
  research_run_id: string;
  pack_role?: ForecastPackRole;
  tool_profile?: ForecastToolProfile;
  attempt_no?: number;
  is_active?: boolean;
  rerun_of_pack_id?: string | null;
  policy_decision_id?: string | null;
  data_classification?: ForecastConfidentialityClass;
  timeout_sec?: number | null;
  estimated_cost_budget_usd?: number | null;
  vector_store_ids?: string[];
  mcp_server_ids?: string[];
  cache_key?: string | null;
  rerun_policy?: string | null;
  pack_request_id?: string | null;
  pack_status: string;
  effective_status: string;
  research_run_status: string;
  pack_created_at: string;
  pack_updated_at: string;
  research_run_created_at?: string | null;
  research_run_updated_at?: string | null;
  deep_research_started_at?: string | null;
  total_tool_calls: number;
  estimated_cost_usd: number;
  done_reason?: string | null;
  last_error?: string | null;
  needs_human_review: boolean;
}

export interface ForecastDetail extends ForecastSummary {
  original_execution_prompt: string | null;
  target_population?: string | null;
  unit_of_analysis?: string | null;
  resolution_criteria: string;
  resolution_sources: string[];
  decision_context?: string | null;
  confidentiality_class: string;
  outcomes: ForecastOutcome[];
  current_research_pack?: ForecastCurrentResearchPack | null;
  current_research_pack_status?: string | null;
  research_packs?: ForecastCurrentResearchPack[];
  approved_claim_target_link_count: number;
}

export interface ForecastCreateRequest {
  question: string;
  original_execution_prompt?: string | null;
  resolution_date?: string | null;
  target_population?: string | null;
  unit_of_analysis?: string | null;
  resolution_criteria?: string;
  resolution_sources?: string[];
  decision_context?: string | null;
  confidentiality_class?: ForecastConfidentialityClass;
  outcomes?: string[];
}

export interface ForecastCreateResponse {
  forecast_id: string;
  status: ForecastStatus;
  framing_version: number;
  created_at: string;
}

export interface ForecastFramingDraft {
  forecast_prompt: string;
  question: string;
  resolution_criteria: string;
  resolution_sources: string[];
  target_population?: string | null;
  unit_of_analysis?: string | null;
  decision_context?: string | null;
  outcomes: string[];
  clarifying_questions: ForecastFramingDraftClarifyingQuestion[];
  confidence: number;
}

export interface ForecastFramingDraftClarifyingQuestion {
  question_id: string;
  label: string;
  prompt: string;
  why_needed: string;
  answer_type: "text" | "single_select" | "multi_select" | "number" | "date" | "boolean";
  required: boolean;
  options: string[];
}

export interface ForecastFramingDraftAnswer {
  question_id: string;
  answer: string;
}

export interface ForecastFramingDraftRequest {
  rough_question: string;
  answers?: ForecastFramingDraftAnswer[];
  previous_draft?: ForecastFramingDraft | null;
  locale?: "ja" | "en";
}

export interface ForecastFramingDraftResponse {
  draft: ForecastFramingDraft;
  create_payload?: ForecastCreateRequest | null;
  ready_to_create: boolean;
  model: string;
  response_id?: string | null;
  warnings: string[];
}

export interface ForecastReviewRequest {
  action:
    | "approve_framing"
    | "approve_phase_a_version"
    | "approve_claim_target_links"
    | "approve_private_data_use"
    | "approve_probability_publication"
    | "override_probability_with_reason"
    | "approve_external_report"
    | "approve_trusted_source";
  comment?: string | null;
  estimate_set_id?: string | null;
  version_id?: string | null;
  reviewer?: string | null;
  reviewer_auth_subject?: string | null;
  policy_decision_id?: string | null;
  review_reason?: string | null;
}

export interface ForecastReviewResponse {
  forecast_id: string;
  action: ForecastReviewRequest["action"];
  status: ForecastStatus;
  approved_framing_version?: number | null;
  estimate_set_id?: string | null;
}

export interface ResearchPackResponse {
  pack_id: string;
  forecast_id: string;
  research_run_id: string;
  pack_role: ForecastPackRole;
  tool_profile: ForecastToolProfile;
  status: string;
  policy_decision_id: string;
  attempt_no: number;
  is_active: boolean;
  data_classification: ForecastConfidentialityClass;
}

export interface ResearchPackDefaultsResponse {
  packs: ResearchPackResponse[];
}

export interface ResearchPackRerunRequest {
  expected_active_pack_id: string;
  max_tool_calls?: number;
  background?: boolean;
  timeout_sec?: number | null;
  estimated_cost_budget_usd?: number | null;
}

export interface ManualResearchPackPromptResponse {
  forecast_id: string;
  framing_version: number;
  prompt: string;
  prompt_sha256: string;
  prompt_version: string;
  pack_role: ForecastPackRole;
  tool_profile: ForecastToolProfile;
  max_report_chars: number;
  max_file_bytes: number;
  pack_id?: string | null;
  research_run_id?: string | null;
  recovering_existing_pack?: boolean;
  recoverable_status?: string | null;
}

export interface ForecastSource {
  source_id: string;
  title: string;
  publisher?: string | null;
  url?: string | null;
  source_type: string;
  source_classification: string;
  data_classification: ForecastConfidentialityClass;
  origin_tool_profile: ForecastToolProfile;
  reliability_score: number;
}

export interface ForecastClaim {
  claim_id: string;
  text: string;
  claim_type: string;
  polarity: number;
  evidence_strength: number;
  reliability_score: number;
  cluster_id: string;
  independence_group: string;
  source_ids: string[];
  review_status: string;
  data_classification: ForecastConfidentialityClass;
  origin_tool_profile: ForecastToolProfile;
}

export interface EvidenceExtractResponse {
  forecast_id: string;
  sources: ForecastSource[];
  claims: ForecastClaim[];
  quarantine_artifact_path?: string | null;
}

export interface ForecastScenario {
  scenario_id: string;
  outcome_id: string;
  label: string;
  description: string;
  probability?: number | null;
  normalized_weight: number;
  validity_status: string;
  driver_state_ids: string[];
}

export interface ScenarioGenerateResponse {
  forecast_id: string;
  scenarios: ForecastScenario[];
}

export interface ProbabilityEstimate {
  estimate_id: string;
  target_kind: string;
  target_id: string;
  prior: number;
  evidence_update: number;
  cross_impact_adjustment: number;
  simulation_adjustment: number;
  calibration_adjustment: number;
  human_adjustment: number;
  final_probability: number;
  uncertainty_range: { lo80: number; hi80: number };
  components: Record<string, unknown>;
}

export interface EstimateSetResponse {
  estimate_set_id: string;
  forecast_id: string;
  status: string;
  approved: boolean;
  engine_version: string;
  input_snapshot_hash: string;
  engine_code_hash: string;
  random_seed: number;
  normalization_group_id: string;
  estimates: ProbabilityEstimate[];
}

export interface ComputeProbabilitiesRequest {
  engine_version?: "phase_a_v1" | "phase_b_v1" | null;
}

export interface ForecastTrustedSource {
  trusted_source_id: string;
  identifier: string;
  status: "pending" | "approved" | "revoked" | "expired";
  approved_by?: string | null;
  approved_at?: string | null;
  expires_at?: string | null;
  allowed_profiles: ForecastToolProfile[];
  allowed_pack_roles: ForecastPackRole[];
  allowed_tool_names: string[];
  owner_team_id?: string | null;
}

export interface CommitVersionResponse {
  version_id: string;
  forecast_id: string;
  estimate_set_id: string;
  input_snapshot_hash: string;
  snapshot_artifact_path: string;
  committed_at: string;
}

export interface ResolveForecastResponse {
  forecast_id: string;
  outcome_id: string;
  multiclass_brier: number;
  log_score: number;
  scorer_version: string;
  resolved_at: string;
}

export interface ForecastAuditResponse {
  forecast_id: string;
  reviews: Record<string, unknown>[];
  versions: Record<string, unknown>[];
  policy_decisions: Record<string, unknown>[];
  events: Array<{
    event_id: string;
    forecast_id: string;
    event_type: string;
    event_json: Record<string, unknown>;
    created_at: string;
  }>;
}
