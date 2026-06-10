/**
 * SCR-3: Run Monitor — central hub for a single research run.
 *
 * Polling: statusPollInterval(status) drives getRunStatus; audit data feeds the DAG.
 * Header: StatusPill + run metadata.
 * Notifications: fires on transition to completed / needs_human_review.
 * Cancel: calls cancelRun, then refetches.
 * No-progress heuristic: banner shown when needs_human_review OR latest two
 *   review scores didn't improve.
 */

import {
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
  type KeyboardEvent,
  type ReactNode,
} from "react";

import {
  CostMeter,
  MetricCard,
  ReviewHistoryItem,
  Skeleton,
  StatusPill,
  WaitBanner,
  BackLink,
  EmptyState,
} from "../components";
import {
  getRunStatus,
  getAudit,
  getAttempts,
  getItems,
  getRunCheckpoints,
  getRunLineage,
  previewCheckpointFork,
  createCheckpointFork,
  cancelRun,
} from "../api/research";
import { ApiError } from "../api/client";
import { usePolling } from "../hooks/usePolling";
import { useElapsed, formatElapsed } from "../hooks/useElapsed";
import { statusPollInterval } from "../polling";
import { navigate, routes, Link } from "../router";
import { getTrackedRun, trackRun, updateTrackedStatus } from "../runStore";
import { notify } from "../notifications";
import {
  isTerminal,
  type AuditResponse,
  type ResearchRunStatusResponse,
  type ResearchAttempt,
  type ResearchCheckpoint,
  type ResearchForkPreviewResponse,
  type ResearchForkSubmitResponse,
  type ResearchItem,
  type ResearchRunLineage,
  type ReviewRecord,
  type RunStatus,
} from "../types";

// ── No-progress heuristic ─────────────────────────────────────────────────────

function hasNoProgressSignal(
  status: RunStatus,
  reviews: ReviewRecord[],
): boolean {
  if (status === "needs_human_review") return true;
  const latest = reviews[reviews.length - 1];
  if (latest?.item_assessments.length) {
    return latest.item_assessments.some(
      (item) =>
        item.severity === "blocker" &&
        item.status !== "answered" &&
        item.status !== "out_of_scope",
    );
  }
  return false;
}

function coalesceAttemptsByRunNo(attempts: ResearchAttempt[]): ResearchAttempt[] {
  const byRunNo = new Map<number, ResearchAttempt>();
  for (const attempt of attempts) {
    const existing = byRunNo.get(attempt.run_no);
    if (!existing) {
      byRunNo.set(attempt.run_no, attempt);
      continue;
    }
    byRunNo.set(attempt.run_no, {
      ...attempt,
      prompt: existing.prompt || attempt.prompt,
    });
  }
  return Array.from(byRunNo.values()).sort((a, b) => a.run_no - b.run_no);
}

function countItemsByStatus(items: ResearchItem[]) {
  return items.reduce(
    (acc, item) => {
      acc.total += 1;
      acc[item.status] += 1;
      if (
        item.severity === "blocker" &&
        item.status !== "answered" &&
        item.status !== "out_of_scope"
      ) {
        acc.blockers_unresolved += 1;
      }
      return acc;
    },
    {
      total: 0,
      not_started: 0,
      answered: 0,
      partial: 0,
      unanswered: 0,
      unverifiable: 0,
      out_of_scope: 0,
      blockers_unresolved: 0,
    },
  );
}

const ITEM_STATUS_LABEL: Record<ResearchItem["status"], string> = {
  not_started: "未開始",
  answered: "回答済み",
  partial: "一部回答",
  unanswered: "未回答",
  unverifiable: "確認不能",
  out_of_scope: "対象外",
};

const ITEM_SEVERITY_LABEL: Record<ResearchItem["severity"], string> = {
  blocker: "Blocker",
  major: "Major",
  minor: "Minor",
};

function isUnresolved(item: ResearchItem): boolean {
  return item.status !== "answered" && item.status !== "out_of_scope";
}

type DagNodeStatus = "done" | "active" | "pending" | "blocked";
type DagNodeTone =
  | "brief"
  | "research"
  | "review"
  | "patch"
  | "verify"
  | "finalize"
  | "fork";
type FollowUpKind =
  | "llm_patch"
  | "verification"
  | "targeted_rerun"
  | "full_rerun"
  | "item_revision"
  | "human_review"
  | "finalize_with_limitation";

interface ExecutionDagNode {
  id: string;
  title: string;
  meta: string;
  status: DagNodeStatus;
  statusLabel?: string;
  tone: DagNodeTone;
  lane: number;
  col: number;
  nodeAnchor?: string;
  resultHref?: string;
  auditHref?: string;
  ariaLabel?: string;
  score?: number;
  checkpoint?: ResearchCheckpoint;
  forkCount?: number;
}

interface ExecutionDagEdge {
  id: string;
  from: string;
  to: string;
  status: "done" | "active" | "pending";
  label?: string;
}

const FOLLOW_UP_LABEL: Record<FollowUpKind, string> = {
  llm_patch: "LLMパッチ",
  verification: "検証",
  targeted_rerun: "Targeted rerun",
  full_rerun: "Full rerun",
  item_revision: "Item revision",
  human_review: "Human review",
  finalize_with_limitation: "制約付き最終化",
};

const ROUTE_FOLLOW_UP_KIND: Record<string, FollowUpKind | null> = {
  finalize: null,
  llm_patch: "llm_patch",
  verify_items: "verification",
  build_targeted_rerun_plan: "targeted_rerun",
  full_rerun_submit: "full_rerun",
  revise_research_items: "item_revision",
  human_review: "human_review",
  finalize_with_limitation: "finalize_with_limitation",
};

const DAG_STATUS_LABEL: Record<DagNodeStatus, string> = {
  done: "完了",
  active: "実行中",
  pending: "待機",
  blocked: "停止",
};

// Mirrors StatusPill labels (not exported) for screen-reader announcements.
const RUN_STATUS_LABEL: Record<RunStatus, string> = {
  queued: "待機中",
  submitted: "処理中",
  waiting_deep_research: "調査中",
  collecting: "収集中",
  reviewing: "レビュー中",
  needs_action: "対応待ち",
  needs_human_review: "要対応",
  completed: "完了",
  cancelled: "キャンセル",
  failed: "失敗",
};

const CHECKPOINT_KIND_LABEL: Record<string, string> = {
  deep_research_collected: "Deep Research収集後",
  review_recorded: "LLMレビュー後",
  llm_patch_applied: "LLMパッチ後",
  verification_completed: "検証後",
  human_review_required: "人間判断要求",
  finalized: "最終化",
};

function checkpointKindLabel(kind: string): string {
  return CHECKPOINT_KIND_LABEL[kind] ?? kind;
}

function attemptSourceLabel(source?: string | null): string {
  if (source === "manual_upload") return "手動取り込み";
  if (source === "manual_chatgpt_rerun") return "ChatGPT手動rerun";
  return "API";
}

function shortId(value: string | null | undefined, length = 10): string | null {
  if (!value) return null;
  return value.length > length ? `${value.slice(0, length)}...` : value;
}

function formatDateTime(value: string | null | undefined): string {
  if (!value) return "不明";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  return new Intl.DateTimeFormat("ja-JP", {
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
  }).format(date);
}

function asRecord(value: unknown): Record<string, unknown> | null {
  return value && typeof value === "object" && !Array.isArray(value)
    ? (value as Record<string, unknown>)
    : null;
}

function snapshotNumber(snapshot: Record<string, unknown> | null | undefined, key: string): number | null {
  const value = snapshot?.[key];
  return typeof value === "number" && Number.isFinite(value) ? value : null;
}

function checkpointChildForks(checkpoint: ResearchCheckpoint | undefined) {
  return Array.isArray(checkpoint?.child_forks) ? checkpoint.child_forks : [];
}

function checkpointForkCount(checkpoint: ResearchCheckpoint | undefined): number {
  return checkpointChildForks(checkpoint).length;
}

function nodeNumber(nodeId: string, prefix: string): number | null {
  const match = nodeId.match(new RegExp(`^${prefix}-(\\d+)$`));
  if (!match) return null;
  const value = Number(match[1]);
  return Number.isInteger(value) ? value : null;
}

function checkpointMatchesNodeAnchor(checkpoint: ResearchCheckpoint, node: ExecutionDagNode): boolean {
  const anchors = new Set([node.id, node.nodeAnchor].filter(Boolean));
  return anchors.has(checkpoint.node_anchor);
}

function checkpointMatchesNodeFallback(
  checkpoint: ResearchCheckpoint,
  node: ExecutionDagNode,
): boolean {
  const researchNo = nodeNumber(node.id, "research");
  if (
    researchNo !== null &&
    checkpoint.kind === "deep_research_collected" &&
    checkpoint.source_attempt_no === researchNo
  ) {
    return true;
  }

  const reviewNo = nodeNumber(node.id, "review");
  if (
    reviewNo !== null &&
    checkpoint.kind === "review_recorded" &&
    checkpoint.source_review_no === reviewNo
  ) {
    return true;
  }

  if (node.id.startsWith("followup-")) {
    const sourceReviewNo = nodeNumber(node.id, "followup");
    if (checkpoint.source_review_no !== sourceReviewNo) return false;
    if (node.tone === "patch" && checkpoint.kind === "llm_patch_applied") return true;
    if (node.tone === "verify" && checkpoint.kind === "verification_completed") return true;
  }

  if (node.id === "human-review") return checkpoint.kind === "human_review_required";
  if (node.id === "final") return checkpoint.kind === "finalized";
  return false;
}

function enrichDagNodes(
  nodes: ExecutionDagNode[],
  checkpoints: ResearchCheckpoint[],
): ExecutionDagNode[] {
  const used = new Set<string>();
  const exactMatches = new Map<string, ResearchCheckpoint>();
  for (const node of nodes) {
    const checkpoint = checkpoints.find(
      (candidate) =>
        !used.has(candidate.checkpoint_id) && checkpointMatchesNodeAnchor(candidate, node),
    );
    if (!checkpoint) continue;
    exactMatches.set(node.id, checkpoint);
    used.add(checkpoint.checkpoint_id);
  }

  return nodes.map((node) => {
    const checkpoint = exactMatches.get(node.id) ?? checkpoints.find(
      (candidate) =>
        !used.has(candidate.checkpoint_id) && checkpointMatchesNodeFallback(candidate, node),
    );
    if (!checkpoint) return node;
    used.add(checkpoint.checkpoint_id);
    return {
      ...node,
      checkpoint,
      nodeAnchor: checkpoint.node_anchor,
      forkCount: checkpointForkCount(checkpoint),
    };
  });
}

function forkDisabledReason(checkpoint: ResearchCheckpoint | undefined): string {
  if (!checkpoint) return "このノードには保存済みcheckpointがありません。";
  if (!checkpoint.forkable) return "このcheckpointはフォーク対象外です。";
  return "";
}

function activeDagNodeId(
  status: RunStatus,
  attempts: ResearchAttempt[],
  reviews: ReviewRecord[],
  progress?: ResearchRunStatusResponse["progress"],
) {
  if (status === "waiting_deep_research" || status === "collecting") {
    const latestAttemptNo = Math.max(0, ...attempts.map((attempt) => attempt.run_no));
    return `research-${Math.max(latestAttemptNo, progress?.deep_research_runs ?? 0, 1)}`;
  }
  if (status === "reviewing" || status === "needs_action") {
    return `review-${reviews.length + 1}`;
  }
  if (status === "needs_human_review") return "human-review";
  if (status === "completed" || status === "cancelled" || status === "failed") {
    return "final";
  }
  return "brief";
}

function followUpTone(kind: FollowUpKind): DagNodeTone {
  if (kind === "llm_patch") return "patch";
  if (kind === "verification") return "verify";
  if (kind === "targeted_rerun" || kind === "full_rerun") return "research";
  return "review";
}

function followUpTitle(kind: FollowUpKind, index: number) {
  if (kind === "llm_patch") return `LLMパッチ ${index}回目`;
  if (kind === "verification") return `検証 ${index}回目`;
  if (kind === "targeted_rerun") return `Targeted rerun ${index}`;
  if (kind === "full_rerun") return `Full rerun ${index}`;
  if (kind === "item_revision") return `Item revision ${index}`;
  if (kind === "finalize_with_limitation") return `Limit finalize ${index}`;
  return `Human review ${index}`;
}

function historyString(
  event: AuditResponse["history"][number] | undefined,
  key: string,
): string | null {
  const value = event?.[key];
  return typeof value === "string" ? value : null;
}

function historyNumber(
  event: AuditResponse["history"][number] | undefined,
  key: string,
): number | null {
  const value = event?.[key];
  return typeof value === "number" && Number.isFinite(value) ? value : null;
}

function historyStep(event: AuditResponse["history"][number] | undefined): string | null {
  return historyString(event, "step");
}

function routeFollowUpForReview(
  history: AuditResponse["history"],
  reviewNo: number,
): FollowUpKind | null {
  const routedByReviewNo = history.find(
    (event) =>
      historyStep(event) === "route_after_review" &&
      historyNumber(event, "total_reviews") === reviewNo,
  );
  const fallbackRoute = history.filter(
    (event) => historyStep(event) === "route_after_review",
  )[reviewNo - 1];
  const route = historyString(routedByReviewNo ?? fallbackRoute, "route");
  return route ? ROUTE_FOLLOW_UP_KIND[route] ?? null : null;
}

function eventCompletesFollowUp(
  event: AuditResponse["history"][number],
  kind: FollowUpKind,
): boolean {
  const step = historyStep(event);
  if (kind === "llm_patch") return step === "llm_patch";
  if (kind === "verification") return step === "verification_completed";
  if (kind === "finalize_with_limitation") return step === "finalized_with_limitation";
  return false;
}

function countCompletedFollowUps(
  history: AuditResponse["history"],
  kind: FollowUpKind,
): number {
  const completed = history.filter((event) => eventCompletesFollowUp(event, kind));
  if (!isRerunFollowUp(kind)) return completed.length;

  const unique = new Set<string>();
  completed.forEach((event, index) => {
    const runNo = historyNumber(event, "run_no");
    unique.add(
      historyString(event, "rerun_id") ??
        (runNo !== null ? `run:${runNo}` : `event:${index}`),
    );
  });
  return unique.size;
}

function executedFollowUpLimits(
  progress: ResearchRunStatusResponse["progress"] | undefined,
  history: AuditResponse["history"],
): Record<FollowUpKind, number> {
  return {
    llm_patch: Math.max(progress?.llm_patch_runs ?? 0, countCompletedFollowUps(history, "llm_patch")),
    verification: Math.max(
      progress?.verification_runs ?? 0,
      countCompletedFollowUps(history, "verification"),
    ),
    targeted_rerun: Math.max(
      progress?.targeted_rerun_runs ?? 0,
      countCompletedFollowUps(history, "targeted_rerun"),
    ),
    full_rerun: Math.max(
      progress?.full_rerun_runs ?? 0,
      countCompletedFollowUps(history, "full_rerun"),
    ),
    item_revision: 0,
    human_review: 0,
    finalize_with_limitation: countCompletedFollowUps(history, "finalize_with_limitation"),
  };
}

function hasExecutedFollowUp(
  kind: FollowUpKind,
  counts: Record<FollowUpKind, number>,
  limits: Record<FollowUpKind, number>,
): boolean {
  return counts[kind] < limits[kind];
}

function isRerunFollowUp(kind: FollowUpKind): boolean {
  return kind === "targeted_rerun" || kind === "full_rerun";
}

function historyHasAttemptForRunNo(
  history: AuditResponse["history"],
  runNo: number,
): boolean {
  return history.some((event) => {
    const step = historyStep(event);
    return (
      (step === "attempt_recorded" || step === "attempt_updated") &&
      historyNumber(event, "run_no") === runNo
    );
  });
}

function shouldShowRoutedIncompleteFollowUp(
  status: RunStatus,
  kind: FollowUpKind,
  isLatestReview: boolean,
): boolean {
  return (
    isLatestReview &&
    (status === "reviewing" || status === "needs_action") &&
    (kind === "llm_patch" || kind === "verification")
  );
}

function buildExecutionDag({
  status,
  attempts,
  reviews,
  history,
  progress,
  runId,
  checkpoints,
  lineage,
  deepResearchSubmitWaiting,
}: {
  status: RunStatus;
  attempts: ResearchAttempt[];
  reviews: ReviewRecord[];
  history: AuditResponse["history"];
  progress?: ResearchRunStatusResponse["progress"];
  runId: string;
  checkpoints: ResearchCheckpoint[];
  lineage: ResearchRunLineage | null;
  deepResearchSubmitWaiting: boolean;
}): { nodes: ExecutionDagNode[]; edges: ExecutionDagEdge[] } {
  const nodes: ExecutionDagNode[] = [];
  const edges: ExecutionDagEdge[] = [];
  const activeId = activeDagNodeId(status, attempts, reviews, progress);
  const maxAttemptRunNo = Math.max(0, ...attempts.map((attempt) => attempt.run_no));
  const minimumResearchCount =
    status === "queued" || status === "submitted"
      ? maxAttemptRunNo
      : Math.max(maxAttemptRunNo, progress?.deep_research_runs ?? 1, 1);
  const attemptsByRunNo = new Map(attempts.map((attempt) => [attempt.run_no, attempt]));
  const executedLimits = executedFollowUpLimits(progress, history);

  if (lineage?.parent_run_id) {
    nodes.push({
      id: "fork-source",
      title: "フォーク元",
      meta: `checkpoint ${shortId(lineage.forked_from_checkpoint_id, 8) ?? ""}`,
      status: "done",
      tone: "fork",
      lane: 1,
      col: 1,
      resultHref: routes().monitor(lineage.parent_run_id),
      ariaLabel: "フォーク元runを選択",
    });
  } else {
    nodes.push({
      id: "brief",
      title: "指示内容",
      meta: "Objective contract / ResearchItems",
      status: activeId === "brief" ? "active" : "done",
      tone: "brief",
      lane: 1,
      col: 1,
    });
  }

  let previousId = lineage?.parent_run_id ? "fork-source" : "brief";
  let col = 2;
  const followUpCounts: Record<FollowUpKind, number> = {
    llm_patch: 0,
    verification: 0,
    targeted_rerun: 0,
    full_rerun: 0,
    item_revision: 0,
    human_review: 0,
    finalize_with_limitation: 0,
  };
  let nextResearchRunNo = 1;

  function appendResearchNode() {
    const runNo = nextResearchRunNo;
    const existingAttempt = attemptsByRunNo.get(runNo);
    const attempt =
      existingAttempt ??
      ({
        run_no: runNo,
        status: activeId === `research-${runNo}` ? "running" : "pending",
        model: "Deep Research",
        prompt: "",
        report: "",
        citations: [],
        tool_calls_summary: [],
      } satisfies ResearchAttempt);
    nextResearchRunNo += 1;
    const researchId = `research-${attempt.run_no}`;
    const researchSubmitWaiting =
      deepResearchSubmitWaiting &&
      attempt.run_no === Math.max(maxAttemptRunNo, progress?.deep_research_runs ?? 0, 1);
    const researchStatus =
      researchSubmitWaiting ? "active" : activeId === researchId ? "active" : existingAttempt ? "done" : "pending";
    nodes.push({
      id: researchId,
      title: `Deep Research ${attempt.run_no}回目`,
      meta: researchSubmitWaiting ? "Deep Research送信待ち" : `status: ${attempt.status}`,
      status: researchStatus,
      statusLabel: researchSubmitWaiting ? "Deep Research送信待ち" : undefined,
      tone: "research",
      lane: 1,
      col,
      nodeAnchor: researchId,
      resultHref: routes().report(runId, { attempt: attempt.run_no }),
    });
    edges.push({
      id: `${previousId}-${researchId}`,
      from: previousId,
      to: researchId,
      status: researchStatus === "active" ? "active" : researchStatus === "done" ? "done" : "pending",
    });
    previousId = researchId;
    col += 1;
  }

  if (minimumResearchCount > 0) {
    appendResearchNode();
  }

  for (const review of reviews) {
    const hasNextReviewRecord = reviews.some(
      (candidate) => candidate.review_no === review.review_no + 1,
    );
    const reviewId = `review-${review.review_no}`;
    nodes.push({
      id: reviewId,
      title: `LLMレビュー ${review.review_no}回目`,
      meta: review.verdict,
      status: "done",
      tone: "review",
      lane: 1,
      col,
      nodeAnchor: reviewId,
      auditHref: routes().audit(runId, { tab: "reviews", review: review.review_no }),
      score: review.score,
    });
    edges.push({
      id: `${previousId}-${reviewId}`,
      from: previousId,
      to: reviewId,
      status: "done",
    });
    previousId = reviewId;
    col += 1;

    const followUp = routeFollowUpForReview(history, review.review_no);
    if (!followUp) {
      continue;
    }

    const completedByHistory = hasExecutedFollowUp(followUp, followUpCounts, {
      ...executedLimits,
      [followUp]: countCompletedFollowUps(history, followUp),
    });
    const completedByActualAttempt =
      isRerunFollowUp(followUp) &&
      (attemptsByRunNo.has(nextResearchRunNo) ||
        historyHasAttemptForRunNo(history, nextResearchRunNo));
    const completedByNextReview = hasNextReviewRecord;
    const completedByProgressFallback = hasExecutedFollowUp(
      followUp,
      followUpCounts,
      executedLimits,
    );
    const followUpIsDone =
      completedByHistory ||
      completedByActualAttempt ||
      completedByNextReview ||
      completedByProgressFallback;
    const showIncompleteFollowUp = shouldShowRoutedIncompleteFollowUp(
      status,
      followUp,
      review.review_no === reviews.length,
    );
    if (!followUpIsDone && !showIncompleteFollowUp) {
      continue;
    }

    const followUpIndex = followUpCounts[followUp] + 1;
    const followId = `followup-${review.review_no}`;
    const label = FOLLOW_UP_LABEL[followUp];
    const followUpStatus =
      followUpIsDone ? "done" : activeId === `review-${review.review_no + 1}` ? "active" : "pending";
    nodes.push({
      id: followId,
      title: followUpTitle(followUp, followUpIndex),
      meta: label,
      status: followUpStatus,
      tone: followUpTone(followUp),
      lane: review.review_no % 2 === 0 ? 2 : 0,
      col,
      nodeAnchor: followId,
      auditHref: routes().audit(runId, { tab: "reviews", review: review.review_no }),
    });
    edges.push({
      id: `${previousId}-${followId}`,
      from: previousId,
      to: followId,
      status: followUpIsDone ? "done" : followUpStatus === "active" ? "active" : "pending",
      label,
    });
    previousId = followId;
    col += 1;

    if (followUpIsDone) {
      followUpCounts[followUp] += 1;
    }

    if (isRerunFollowUp(followUp) && followUpIsDone) {
      appendResearchNode();
    }
  }

  while (nextResearchRunNo <= minimumResearchCount) {
    appendResearchNode();
  }

  const hasOpenFollowUp = nodes.some(
    (node) => node.id.startsWith("followup-") && node.status !== "done",
  );
  if (activeId === `review-${reviews.length + 1}` && !hasOpenFollowUp) {
    const reviewId = `review-${reviews.length + 1}`;
    nodes.push({
      id: reviewId,
      title: `LLMレビュー ${reviews.length + 1}回目`,
      meta: "running",
      status: "active",
      tone: "review",
      lane: 1,
      col,
      nodeAnchor: reviewId,
      auditHref: routes().audit(runId, { tab: "reviews", review: reviews.length + 1 }),
    });
    edges.push({
      id: `${previousId}-${reviewId}`,
      from: previousId,
      to: reviewId,
      status: "active",
    });
    previousId = reviewId;
    col += 1;
  }

  if (activeId === "human-review") {
    nodes.push({
      id: "human-review",
      title: "人間判断",
      meta: "判断待ち / review画面",
      status: "active",
      tone: "review",
      lane: 1,
      col,
      nodeAnchor: "human-review",
      resultHref: routes().review(runId),
      ariaLabel: "人間判断を選択",
    });
    edges.push({
      id: `${previousId}-human-review`,
      from: previousId,
      to: "human-review",
      status: "active",
      label: "Human review",
    });
    previousId = "human-review";
    col += 1;
  }

  const finalStatus: DagNodeStatus =
    status === "completed" ? "done" : status === "failed" || status === "cancelled" ? "blocked" : "pending";
  nodes.push({
    id: "final",
    title: status === "completed" ? "最終レポート" : "最終化",
    meta: status === "completed" ? "生成済み / レポート履歴" : status,
    status: activeId === "final" ? finalStatus : "pending",
    tone: "finalize",
    lane: 1,
    col,
    nodeAnchor: "final",
    resultHref: status === "completed" ? routes().report(runId) : undefined,
  });
  edges.push({
    id: `${previousId}-final`,
    from: previousId,
    to: "final",
    status: status === "completed" ? "done" : activeId === "final" ? "active" : "pending",
  });

  return { nodes: enrichDagNodes(nodes, checkpoints), edges };
}

function ExecutionDag({
  nodes,
  edges,
  progress,
  attempts,
  reviews,
  selectedNodeId,
  onSelectNode,
  inspector,
}: {
  nodes: ExecutionDagNode[];
  edges: ExecutionDagEdge[];
  attempts: ResearchAttempt[];
  reviews: ReviewRecord[];
  progress?: ResearchRunStatusResponse["progress"];
  selectedNodeId: string | null;
  onSelectNode: (nodeId: string) => void;
  inspector: ReactNode;
}) {
  const maxCol = Math.max(...nodes.map((node) => node.col), 1);
  const maxLane = Math.max(...nodes.map((node) => node.lane), 1);
  const gridStyle = {
    gridTemplateColumns: `repeat(${maxCol}, minmax(var(--dag-node-width), 1fr))`,
    gridTemplateRows: `repeat(${maxLane + 1}, minmax(96px, auto))`,
  };
  const byId = new Map(nodes.map((node) => [node.id, node]));

  return (
    <section className="execution-dag" aria-labelledby="execution-dag-heading">
      <div className="section-heading-row">
        <h2 id="execution-dag-heading" className="section-title">
          具体的な実行フロー
        </h2>
        <span className="item-progress-summary execution-dag-summary">
          Deep Research {progress?.deep_research_runs ?? attempts.length}回 / Review {reviews.length}回
        </span>
      </div>
      <div className="execution-dag-legend" aria-hidden="true">
        <span className="execution-dag-legend-item execution-dag-legend-item--research">
          Deep Research
        </span>
        <span className="execution-dag-legend-item execution-dag-legend-item--review">
          LLMレビュー
        </span>
        <span className="execution-dag-legend-item execution-dag-legend-item--repair">
          補正/検証
        </span>
      </div>
      <div className="execution-dag-layout">
        <div
          className="execution-dag-grid"
          style={gridStyle}
          aria-label="具体的な実行フロー"
        >
          {edges.map((edge) => {
            const from = byId.get(edge.from);
            const to = byId.get(edge.to);
            if (!from || !to) return null;
            const row = Math.min(from.lane, to.lane) + 1;
            const rowSpan = Math.abs(from.lane - to.lane) + 1;
            return (
              <div
                key={edge.id}
                className={`execution-dag-edge execution-dag-edge--${edge.status}`}
                style={{
                  gridColumn: `${from.col} / ${to.col + 1}`,
                  gridRow: `${row} / span ${rowSpan}`,
                }}
                aria-hidden="true"
              >
                <span className="execution-dag-edge-line" />
                {edge.label && <span className="execution-dag-edge-label">{edge.label}</span>}
              </div>
            );
          })}
          {nodes.map((node) => {
            const selected = selectedNodeId === node.id;
            const className = [
              "execution-dag-node",
              "execution-dag-node-button",
              `execution-dag-node--${node.status}`,
              `execution-dag-node--${node.tone}`,
              selected ? "execution-dag-node--selected" : "",
            ].filter(Boolean).join(" ");
            const style = {
              gridColumn: node.col,
              gridRow: node.lane + 1,
            };
            return (
              <button
                key={node.id}
                type="button"
                className={className}
                style={style}
                aria-pressed={selected}
                aria-label={node.ariaLabel ?? `${node.title}を選択`}
                onClick={() => onSelectNode(node.id)}
              >
                <div className="execution-dag-node-topline">
                  <span className="execution-dag-dot" aria-hidden="true" />
                  <span className="execution-dag-state">
                    {node.statusLabel ?? DAG_STATUS_LABEL[node.status]}
                  </span>
                </div>
                <h3 className="execution-dag-title">{node.title}</h3>
                <p className="execution-dag-meta">{node.meta}</p>
                {node.score !== undefined && (
                  <span className="execution-dag-score" aria-label={`レビュー点数 ${node.score}点`}>
                    {node.score}点
                  </span>
                )}
                {(node.checkpoint || (node.forkCount ?? 0) > 0) && (
                  <span className="execution-dag-node-footer" aria-hidden="true">
                    {node.checkpoint && (
                      <span className="execution-dag-chip">保存済み</span>
                    )}
                    {node.checkpoint?.forkable && (
                      <span className="execution-dag-chip">分岐可</span>
                    )}
                    {(node.forkCount ?? 0) > 0 && (
                      <span className="execution-dag-chip execution-dag-chip--branch">
                        派生 {node.forkCount}
                      </span>
                    )}
                  </span>
                )}
                {(node.forkCount ?? 0) > 0 && (
                  <span className="execution-dag-branch-stub" aria-hidden="true">
                    child {node.forkCount}
                  </span>
                )}
              </button>
            );
          })}
        </div>
        {inspector}
      </div>
    </section>
  );
}

interface CheckpointInspectorProps {
  node: ExecutionDagNode | null;
  checkpointsError: unknown;
  onOpenFork: () => void;
  forkButtonRef: React.RefObject<HTMLButtonElement | null>;
}

function CheckpointInspector({
  node,
  checkpointsError,
  onOpenFork,
  forkButtonRef,
}: CheckpointInspectorProps) {
  const checkpoint = node?.checkpoint;
  const disabledReason = forkDisabledReason(checkpoint);
  const childForks = checkpointChildForks(checkpoint);

  return (
    <aside className="checkpoint-inspector" aria-label="選択checkpoint詳細">
      {!node ? (
        <div className="checkpoint-inspector-empty">
          <p className="checkpoint-inspector-title">ノードを選択</p>
          <p className="checkpoint-inspector-note">
            実行フローのノードを選ぶと、保存checkpointとフォーク操作を確認できます。
          </p>
        </div>
      ) : (
        <>
          <div className="checkpoint-inspector-header">
            <span className="checkpoint-inspector-kicker">選択中</span>
            <p className="checkpoint-inspector-title">{node.title}</p>
            <p className="checkpoint-inspector-note">{node.meta}</p>
          </div>

          <div className="checkpoint-inspector-links" aria-label="関連リンク">
            {node.resultHref && (
              <Link to={node.resultHref} className="btn-secondary btn-sm">
                結果を開く
              </Link>
            )}
            {node.auditHref && (
              <Link to={node.auditHref} className="btn-secondary btn-sm">
                監査ログを開く
              </Link>
            )}
          </div>

          {checkpoint ? (
            <div className="checkpoint-detail">
              <dl className="checkpoint-detail-list">
                <div>
                  <dt>Checkpoint</dt>
                  <dd>#{checkpoint.checkpoint_no} / {checkpointKindLabel(checkpoint.kind)}</dd>
                </div>
                <div>
                  <dt>保存時刻</dt>
                  <dd>{formatDateTime(checkpoint.created_at)}</dd>
                </div>
                <div>
                  <dt>Anchor</dt>
                  <dd className="mono">{checkpoint.node_anchor}</dd>
                </div>
                {(checkpoint.source_attempt_no ?? null) !== null && (
                  <div>
                    <dt>Attempt</dt>
                    <dd>{checkpoint.source_attempt_no}回目</dd>
                  </div>
                )}
                {(checkpoint.source_review_no ?? null) !== null && (
                  <div>
                    <dt>Review</dt>
                    <dd>{checkpoint.source_review_no}回目</dd>
                  </div>
                )}
                {checkpoint.source_response_id && (
                  <div>
                    <dt>Response</dt>
                    <dd className="mono">{shortId(checkpoint.source_response_id)}</dd>
                  </div>
                )}
                {checkpoint.report_hash && (
                  <div>
                    <dt>Report hash</dt>
                    <dd className="mono">{shortId(checkpoint.report_hash, 12)}</dd>
                  </div>
                )}
              </dl>

              <div className="checkpoint-fork-actions">
                <button
                  type="button"
                  className="btn-primary btn-sm"
                  onClick={onOpenFork}
                  disabled={!checkpoint.forkable}
                  aria-describedby={disabledReason ? "fork-disabled-reason" : undefined}
                  ref={forkButtonRef}
                >
                  ここからフォーク
                </button>
                {disabledReason && (
                  <p id="fork-disabled-reason" className="checkpoint-disabled-reason" role="status">
                    {disabledReason}
                  </p>
                )}
              </div>

              <div className="checkpoint-child-forks">
                <p className="checkpoint-child-title">派生run</p>
                {childForks.length > 0 ? (
                  <ul>
                    {childForks.map((fork) => (
                      <li key={fork.run_id}>
                        <Link to={routes().monitor(fork.run_id)} className="checkpoint-child-link">
                          {fork.run_id}
                        </Link>
                        {fork.status && (
                          <span className="checkpoint-child-status">{fork.status}</span>
                        )}
                      </li>
                    ))}
                  </ul>
                ) : (
                  <p className="checkpoint-inspector-note">まだ派生runはありません。</p>
                )}
              </div>
            </div>
          ) : (
            <div className="checkpoint-missing" role={checkpointsError ? "alert" : "note"}>
              {checkpointsError
                ? "checkpoint一覧を取得できませんでした。再試行中です。"
                : "このノードに保存済みcheckpointはありません。"}
            </div>
          )}
        </>
      )}
    </aside>
  );
}

interface ForkModalProps {
  runId: string;
  checkpoint: ResearchCheckpoint;
  parentTitle: string;
  onClose: () => void;
  onCreated: (response: ResearchForkSubmitResponse) => void;
}

function createIdempotencyKey(): string {
  if (typeof crypto !== "undefined" && "randomUUID" in crypto) {
    return crypto.randomUUID();
  }
  return `fork-${Date.now()}-${Math.random().toString(16).slice(2)}`;
}

function queryPolicyStatus(preview: ResearchForkPreviewResponse | null): string {
  if (!preview) return "未確認";
  const policy = preview.policy_decision ?? preview.query_policy;
  if (policy.status === "allowed") return "許可";
  if (policy.status === "blocked") return "ブロック";
  return policy.status;
}

function ForkModal({
  runId,
  checkpoint,
  parentTitle,
  onClose,
  onCreated,
}: ForkModalProps) {
  const [additionalPrompt, setAdditionalPrompt] = useState("");
  const [idempotencyKey] = useState(createIdempotencyKey);
  const [preview, setPreview] = useState<ResearchForkPreviewResponse | null>(null);
  const [previewPrompt, setPreviewPrompt] = useState<string | null>(null);
  const [previewing, setPreviewing] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const dialogRef = useRef<HTMLDivElement | null>(null);
  const textareaRef = useRef<HTMLTextAreaElement | null>(null);
  const latestPromptRef = useRef("");
  const previewRequestSeqRef = useRef(0);
  const promptEditSeqRef = useRef(0);
  const trimmedPrompt = additionalPrompt.trim();
  const previewMatchesPrompt =
    !!preview && !!trimmedPrompt && previewPrompt === trimmedPrompt;

  useEffect(() => {
    textareaRef.current?.focus();
  }, []);

  function focusableElements() {
    return Array.from(
      dialogRef.current?.querySelectorAll<HTMLElement>(
        'a[href], button:not([disabled]), textarea:not([disabled]), input:not([disabled]), [tabindex]:not([tabindex="-1"])',
      ) ?? [],
    );
  }

  function handleDialogKeyDown(event: KeyboardEvent<HTMLDivElement>) {
    if (event.key === "Escape") {
      event.preventDefault();
      onClose();
      return;
    }
    if (event.key !== "Tab") return;
    const elements = focusableElements();
    if (elements.length === 0) return;
    const first = elements[0];
    const last = elements[elements.length - 1];
    if (event.shiftKey && document.activeElement === first) {
      event.preventDefault();
      last.focus();
    } else if (!event.shiftKey && document.activeElement === last) {
      event.preventDefault();
      first.focus();
    }
  }

  async function handlePreview() {
    const prompt = trimmedPrompt;
    if (!prompt) {
      setError("追加指示を入力してください。");
      return;
    }
    const requestSeq = previewRequestSeqRef.current + 1;
    const promptEditSeq = promptEditSeqRef.current;
    previewRequestSeqRef.current = requestSeq;
    latestPromptRef.current = prompt;
    setPreviewing(true);
    setError(null);
    setPreview(null);
    setPreviewPrompt(null);
    try {
      const nextPreview = await previewCheckpointFork(runId, checkpoint.checkpoint_id, {
        additional_prompt: prompt,
      });
      if (
        previewRequestSeqRef.current !== requestSeq ||
        promptEditSeqRef.current !== promptEditSeq ||
        latestPromptRef.current !== prompt
      ) {
        return;
      }
      setPreview(nextPreview);
      setPreviewPrompt(prompt);
    } catch (err) {
      if (
        previewRequestSeqRef.current !== requestSeq ||
        promptEditSeqRef.current !== promptEditSeq ||
        latestPromptRef.current !== prompt
      ) {
        return;
      }
      if (err instanceof ApiError) {
        setError(err.detail ?? err.message);
      } else {
        setError("プレビューを作成できませんでした。");
      }
    } finally {
      if (previewRequestSeqRef.current === requestSeq) {
        setPreviewing(false);
      }
    }
  }

  async function handleSubmit() {
    if (!previewMatchesPrompt || !preview || !previewPrompt) return;
    setSubmitting(true);
    setError(null);
    try {
      const response = await createCheckpointFork(runId, checkpoint.checkpoint_id, {
        additional_prompt: previewPrompt,
        idempotency_key: idempotencyKey,
        confirmed_preview_hash: preview.preview_hash,
      });
      onCreated(response);
    } catch (err) {
      if (err instanceof ApiError) {
        const prefix = err.isConflict ? "プレビューが古くなっています。" : "";
        setError(`${prefix}${err.detail ?? err.message}`);
      } else {
        setError("フォークを作成できませんでした。");
      }
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <div className="fork-modal-backdrop" role="presentation">
      <div
        className="fork-modal"
        role="dialog"
        aria-modal="true"
        aria-labelledby="fork-modal-title"
        ref={dialogRef}
        onKeyDown={handleDialogKeyDown}
      >
        <div className="fork-modal-header">
          <div>
            <p className="fork-modal-kicker">新しいchild run</p>
            <h2 id="fork-modal-title" className="fork-modal-title">
              checkpointからフォーク
            </h2>
          </div>
          <button type="button" className="btn-secondary btn-sm" onClick={onClose}>
            閉じる
          </button>
        </div>

        <div className="fork-modal-source">
          <span>{parentTitle}</span>
          <span>checkpoint #{checkpoint.checkpoint_no}</span>
          <span>{checkpointKindLabel(checkpoint.kind)}</span>
        </div>

        <label className="fork-field">
          <span>追加指示</span>
          <textarea
            ref={textareaRef}
            value={additionalPrompt}
            onChange={(event) => {
              const nextPrompt = event.target.value;
              promptEditSeqRef.current += 1;
              latestPromptRef.current = nextPrompt.trim();
              setAdditionalPrompt(nextPrompt);
              setPreview(null);
              setPreviewPrompt(null);
              setError(null);
            }}
            rows={5}
            className="comment-textarea fork-textarea"
            placeholder="このcheckpoint以降で追加調査したい観点を入力してください。"
          />
        </label>

        <div className="fork-modal-actions">
          <button
            type="button"
            className="btn-secondary"
            onClick={handlePreview}
            disabled={previewing || submitting || !trimmedPrompt}
          >
            {previewing ? "プレビュー中..." : "フォーク内容をプレビュー"}
          </button>
          <button
            type="button"
            className="btn-primary"
            onClick={handleSubmit}
            disabled={!previewMatchesPrompt || submitting || previewing}
          >
            {submitting
              ? "child run作成中..."
              : "child runで新しいDeep Researchを開始"}
          </button>
        </div>

        {error && (
          <div className="review-submit-error" role="alert">
            {error}
          </div>
        )}

        <div className="fork-preview-panel" aria-live="polite">
          <p className="fork-preview-title">プレビュー</p>
          {preview ? (
            <>
              <dl className="checkpoint-detail-list">
                <div>
                  <dt>Query policy</dt>
                  <dd>{queryPolicyStatus(preview)}</dd>
                </div>
                {(preview.policy_decision ?? preview.query_policy).blocked_reason && (
                  <div>
                    <dt>Blocked reason</dt>
                    <dd>{(preview.policy_decision ?? preview.query_policy).blocked_reason}</dd>
                  </div>
                )}
                <div>
                  <dt>Preview hash</dt>
                  <dd className="mono">{shortId(preview.preview_hash, 12)}</dd>
                </div>
              </dl>
              {preview.warnings.length > 0 && (
                <div className="fork-preview-warnings" role="alert">
                  {preview.warnings.map((warning) => (
                    <p key={warning}>{warning}</p>
                  ))}
                </div>
              )}
              <div className="fork-preview-grid">
                <section>
                  <p className="fork-preview-subtitle">元の指示</p>
                  <pre>{preview.source_prompt_excerpt}</pre>
                </section>
                <section>
                  <p className="fork-preview-subtitle">元レポート</p>
                  <pre>{preview.source_report_excerpt}</pre>
                </section>
                <section className="fork-preview-composed">
                  <p className="fork-preview-subtitle">送信される指示</p>
                  <pre>{preview.composed_prompt}</pre>
                </section>
              </div>
            </>
          ) : (
            <p className="checkpoint-inspector-note">
              送信前にプレビューが必要です。入力を変更すると再プレビューが必要になります。
            </p>
          )}
        </div>
      </div>
    </div>
  );
}

// ── RunMonitor ────────────────────────────────────────────────────────────────

interface RunMonitorProps {
  runId: string;
}

export function RunMonitor({ runId }: RunMonitorProps) {
  const tracked = getTrackedRun(runId);
  const prevStatusRef = useRef<RunStatus | undefined>(undefined);

  const [cancelling, setCancelling] = useState(false);
  const [cancelError, setCancelError] = useState<string | null>(null);
  const [promptPanelOpen, setPromptPanelOpen] = useState(false);
  const [attempts, setAttempts] = useState<ResearchAttempt[] | null>(null);
  const [attemptsLoading, setAttemptsLoading] = useState(false);
  const [attemptsError, setAttemptsError] = useState<string | null>(null);
  const [selectedDagNodeId, setSelectedDagNodeId] = useState<string | null>(null);
  const [forkCheckpoint, setForkCheckpoint] = useState<ResearchCheckpoint | null>(null);
  const forkButtonRef = useRef<HTMLButtonElement | null>(null);
  const userSelectedDagNodeRef = useRef(false);

  useEffect(() => {
    setSelectedDagNodeId(null);
    userSelectedDagNodeRef.current = false;
    setForkCheckpoint(null);
  }, [runId]);

  // ── Status polling ────────────────────────────────────────────────────────

  const {
    data: runStatus,
    loading: statusLoading,
    error: statusError,
    connectionUnstable,
    refetch,
  } = usePolling<ResearchRunStatusResponse>({
    fetcher: (signal) => getRunStatus(runId, signal),
    interval: (data) => statusPollInterval(data?.status),
    key: runId,
    onData: useCallback(
      (data: ResearchRunStatusResponse) => {
        updateTrackedStatus(runId, data.status, {
          estimated_cost_usd: data.progress?.estimated_cost_usd,
          latest_score: data.progress?.latest_score,
        });

        const prev = prevStatusRef.current;
        if (prev !== undefined && prev !== data.status) {
          if (data.status === "completed") {
            notify("リサーチ完了", `run ${runId} のレポートが完成しました。`);
          } else if (data.status === "needs_human_review") {
            notify("人間レビューが必要", `run ${runId} の判断が必要です。`);
          }
        }
        prevStatusRef.current = data.status;
      },
      [runId],
    ),
  });

  // ── Review data polling ───────────────────────────────────────────────────

  const { data: audit } = usePolling<AuditResponse>({
    fetcher: (signal) => getAudit(runId, signal),
    key: `review-audit:${runId}`,
    interval: (data) => {
      if (runStatus && isTerminal(runStatus.status)) return null;
      // Slow down when in long phases
      const s = runStatus?.status;
      if (s === "waiting_deep_research" || s === "collecting") return 30_000;
      return data !== undefined ? 10_000 : 5_000;
    },
  });

  // ── Attempts polling ───────────────────────────────────────────────────────

  const { data: dagAttempts } = usePolling<ResearchAttempt[]>({
    fetcher: async (signal) => coalesceAttemptsByRunNo(await getAttempts(runId, signal)),
    key: `attempts:${runId}`,
    interval: () => {
      if (runStatus && isTerminal(runStatus.status)) return null;
      const s = runStatus?.status;
      if (s === "waiting_deep_research" || s === "collecting") return 30_000;
      return 15_000;
    },
  });

  // ── Checkpoint polling ───────────────────────────────────────────────────

  const {
    data: checkpoints,
    error: checkpointsError,
    refetch: refetchCheckpoints,
  } = usePolling<ResearchCheckpoint[]>({
    fetcher: async (signal) => {
      try {
        return await getRunCheckpoints(runId, true, signal);
      } catch (err) {
        if (err instanceof ApiError && err.isNotFound) return [];
        throw err;
      }
    },
    key: `checkpoints:${runId}`,
    interval: () => (runStatus && isTerminal(runStatus.status) ? null : 15_000),
  });

  const { data: lineage } = usePolling<ResearchRunLineage | null>({
    fetcher: async (signal) => {
      try {
        return await getRunLineage(runId, signal);
      } catch (err) {
        if (err instanceof ApiError && err.isNotFound) return null;
        throw err;
      }
    },
    key: `lineage:${runId}`,
    interval: () => null,
  });

  // ── Item polling ──────────────────────────────────────────────────────────

  const { data: items } = usePolling<ResearchItem[]>({
    fetcher: (signal) => getItems(runId, signal),
    key: `items:${runId}`,
    interval: () => {
      if (runStatus && isTerminal(runStatus.status)) return null;
      const s = runStatus?.status;
      if (s === "waiting_deep_research" || s === "collecting") return 30_000;
      return 10_000;
    },
  });

  // ── Derived values ────────────────────────────────────────────────────────

  const status = runStatus?.status ?? (tracked?.last_status ?? "queued");
  const progress = runStatus?.progress;
  const forecastContext = runStatus?.forecast_context ?? null;
  const sortedReviews = useMemo(
    () =>
      Array.isArray(audit?.reviews)
        ? [...audit.reviews].sort((a, b) => a.review_no - b.review_no)
        : [],
    [audit?.reviews],
  );
  const auditHistory = useMemo(
    () => (Array.isArray(audit?.history) ? audit.history : []),
    [audit?.history],
  );
  const sortedAttempts = useMemo(
    () => (Array.isArray(dagAttempts) ? dagAttempts : []),
    [dagAttempts],
  );
  const runStartedAt = runStatus?.created_at ?? tracked?.created_at;
  const elapsed = useElapsed(runStartedAt, !isTerminal(status));
  const currentDeepResearchStartedAt =
    runStatus?.deep_research_submitted_at ?? undefined;
  const currentDeepResearchElapsed = useElapsed(
    currentDeepResearchStartedAt,
    Boolean(currentDeepResearchStartedAt) && !isTerminal(status),
  );

  const isWaiting =
    Boolean(currentDeepResearchStartedAt) &&
    (status === "waiting_deep_research" || status === "collecting");
  const isSubmitWaiting =
    !isTerminal(status) &&
    !currentDeepResearchStartedAt &&
    (status === "queued" ||
      status === "submitted" ||
      status === "waiting_deep_research" ||
      status === "collecting");
  const dagData = useMemo(
    () =>
      buildExecutionDag({
        status,
        attempts: sortedAttempts,
        reviews: sortedReviews,
        history: auditHistory,
        progress,
        runId,
        checkpoints: checkpoints ?? [],
        lineage: lineage ?? null,
        deepResearchSubmitWaiting: isSubmitWaiting,
      }),
    [
      auditHistory,
      checkpoints,
      isSubmitWaiting,
      lineage,
      progress,
      runId,
      sortedAttempts,
      sortedReviews,
      status,
    ],
  );
  const selectedDagNodeById = dagData.nodes.find((node) => node.id === selectedDagNodeId);
  const preferredDagNode =
    dagData.nodes.find((node) => node.checkpoint) ??
    dagData.nodes.find((node) => node.status === "active") ??
    dagData.nodes[0] ??
    null;
  const selectedDagNode =
    userSelectedDagNodeRef.current && selectedDagNodeById
      ? selectedDagNodeById
      : preferredDagNode;
  const lineageSnapshot = asRecord(lineage?.source_snapshot_json);

  useEffect(() => {
    if (!preferredDagNode) {
      if (selectedDagNodeId !== null) setSelectedDagNodeId(null);
      return;
    }
    if (!selectedDagNodeById) {
      userSelectedDagNodeRef.current = false;
    }
    if (!userSelectedDagNodeRef.current && preferredDagNode.id !== selectedDagNodeId) {
      setSelectedDagNodeId(preferredDagNode.id);
    }
  }, [preferredDagNode, selectedDagNodeById, selectedDagNodeId]);

  const showHumanReviewBanner = status === "needs_human_review";
  const showNoProgressNote =
    !!runStatus && hasNoProgressSignal(runStatus.status, sortedReviews);

  const estimatedCost = progress?.estimated_cost_usd ?? 0;
  const itemCounts = Array.isArray(items) ? countItemsByStatus(items) : null;
  const itemsTotal = itemCounts?.total ?? progress?.items_total ?? 0;
  const itemsAnswered = itemCounts?.answered ?? progress?.items_answered ?? 0;
  const itemsPartial = itemCounts?.partial ?? progress?.items_partial ?? 0;
  const itemsUnanswered = itemCounts?.unanswered ?? progress?.items_unanswered ?? 0;
  const itemsUnverifiable =
    itemCounts?.unverifiable ?? progress?.items_unverifiable ?? 0;
  const blockersUnresolved =
    itemCounts?.blockers_unresolved ?? progress?.blockers_unresolved ?? 0;
  const unresolvedItems = Array.isArray(items)
    ? items.filter(isUnresolved).sort((a, b) => {
        const severityOrder = { blocker: 0, major: 1, minor: 2 };
        return (
          severityOrder[a.severity] - severityOrder[b.severity] ||
          a.item_id.localeCompare(b.item_id)
        );
      })
    : [];

  // Screen-reader progress summary. Built from values already shown visually so
  // assistive tech can follow the long-running poll. Kept to meaningful,
  // slow-changing units (status / phase counts / item totals) to avoid the
  // chatter that fast-updating seconds would cause.
  const deepResearchRuns = progress?.deep_research_runs ?? sortedAttempts.length;
  const reviewRuns = progress?.total_reviews ?? sortedReviews.length;
  const progressAnnouncement = `状態: ${RUN_STATUS_LABEL[status]} / Deep Research ${deepResearchRuns}回 / レビュー ${reviewRuns}回${
    itemsTotal > 0 ? ` / 回答 ${itemsAnswered}/${itemsTotal}件` : ""
  }${blockersUnresolved > 0 ? ` / 未解決Blocker ${blockersUnresolved}件` : ""}`;

  // ── Cancel action ─────────────────────────────────────────────────────────

  async function handleCancel() {
    if (!window.confirm("このrunをキャンセルしますか？")) return;
    setCancelling(true);
    setCancelError(null);
    try {
      await cancelRun(runId);
      refetch();
    } catch (err) {
      if (err instanceof ApiError) {
        setCancelError(err.detail ?? err.message);
      } else {
        setCancelError("キャンセルに失敗しました");
      }
    } finally {
      setCancelling(false);
    }
  }

  async function handleTogglePromptPanel() {
    const nextOpen = !promptPanelOpen;
    setPromptPanelOpen(nextOpen);
    if (!nextOpen || attempts || attemptsLoading) return;

    setAttemptsLoading(true);
    setAttemptsError(null);
    try {
      setAttempts(coalesceAttemptsByRunNo(await getAttempts(runId)));
    } catch (err) {
      if (err instanceof ApiError) {
        setAttemptsError(err.detail ?? err.message);
      } else {
        setAttemptsError("指示内容を取得できませんでした");
      }
    } finally {
      setAttemptsLoading(false);
    }
  }

  function handleForkCreated(response: ResearchForkSubmitResponse) {
    trackRun({
      run_id: response.child_run_id,
      title: `フォーク: ${tracked?.title ?? runId}`,
      created_at: new Date().toISOString(),
      last_status: response.status,
    });
    setForkCheckpoint(null);
    refetchCheckpoints();
    navigate(routes().monitor(response.child_run_id));
  }

  // Initial loading state
  if (statusLoading && !runStatus) {
    return (
      <div className="screen-monitor">
        <BackLink to={routes().dashboard} label="ダッシュボードへ戻る" />
        <div className="monitor-skeleton">
          <Skeleton width="60%" height="28px" />
          <Skeleton width="100%" height="120px" />
          <Skeleton width="100%" height="200px" />
        </div>
      </div>
    );
  }

  if (statusError && !runStatus) {
    return (
      <div className="screen-monitor">
        <BackLink to={routes().dashboard} label="ダッシュボードへ戻る" />
        <div className="monitor-error" role="alert">
          <p>runの状態を取得できませんでした。</p>
          <button type="button" className="btn-secondary" onClick={refetch}>
            再試行
          </button>
        </div>
      </div>
    );
  }

  return (
    <div className="screen-monitor">
      {/* ── Header ───────────────────────────────────── */}
      <header className="monitor-header">
        <nav className="monitor-back-links" aria-label="戻り先">
          <BackLink to={routes().dashboard} label="ダッシュボードへ戻る" />
          {forecastContext && (
            <BackLink
              to={routes().forecastDetail(forecastContext.forecast_id)}
              label="Forecastへ戻る"
            />
          )}
        </nav>
        <div className="monitor-header-top">
          <div className="monitor-title-row">
            <StatusPill status={status} />
            {connectionUnstable && (
              <span className="connection-warning" role="status">
                接続が不安定です — 自動再試行中
              </span>
            )}
          </div>

          <div className="monitor-actions">
            <button
              type="button"
              className="btn-secondary btn-sm"
              onClick={handleTogglePromptPanel}
              aria-expanded={promptPanelOpen}
              aria-controls="deep-research-prompts"
            >
              指示内容
            </button>
            <Link to={routes().report(runId)} className="btn-secondary btn-sm">
              レポート履歴
            </Link>
            <Link to={routes().audit(runId)} className="btn-secondary btn-sm">
              監査ログ
            </Link>
            {!isTerminal(status) && (
              <button
                type="button"
                className="btn-danger btn-sm"
                onClick={handleCancel}
                disabled={cancelling}
              >
                {cancelling ? "キャンセル中..." : "キャンセル"}
              </button>
            )}
          </div>
        </div>

        {tracked?.title && (
          <h1 className="monitor-run-title">{tracked.title}</h1>
        )}
        <p className="monitor-run-id">{runId}</p>
      </header>

      {cancelError && (
        <div className="monitor-error-banner" role="alert">
          {cancelError}
        </div>
      )}

      {lineage?.parent_run_id && (
        <section className="lineage-banner" aria-labelledby="lineage-banner-title">
          <div className="lineage-banner-main">
            <span className="lineage-banner-kicker">child run</span>
            <h2 id="lineage-banner-title" className="lineage-banner-title">
              checkpointからフォークされたrunです
            </h2>
            <dl className="lineage-banner-meta">
              <div>
                <dt>Parent</dt>
                <dd>
                  <Link to={routes().monitor(lineage.parent_run_id)} className="lineage-banner-link">
                    {lineage.parent_run_id}
                  </Link>
                </dd>
              </div>
              <div>
                <dt>Checkpoint</dt>
                <dd className="mono">{shortId(lineage.forked_from_checkpoint_id, 12)}</dd>
              </div>
              <div>
                <dt>Source</dt>
                <dd>
                  {snapshotNumber(lineageSnapshot, "source_attempt_no")
                    ? `Attempt ${snapshotNumber(lineageSnapshot, "source_attempt_no")} `
                    : ""}
                  {snapshotNumber(lineageSnapshot, "source_review_no")
                    ? `Review ${snapshotNumber(lineageSnapshot, "source_review_no")}`
                    : ""}
                  {!snapshotNumber(lineageSnapshot, "source_attempt_no") &&
                    !snapshotNumber(lineageSnapshot, "source_review_no") &&
                    "snapshot保存済み"}
                </dd>
              </div>
            </dl>
          </div>
          <div className="lineage-banner-prompt">
            <span>追加指示</span>
            <p>{lineage.additional_prompt}</p>
          </div>
        </section>
      )}

      {promptPanelOpen && (
        <section
          id="deep-research-prompts"
          className="prompt-panel"
          aria-labelledby="deep-research-prompts-heading"
        >
          <div className="prompt-panel-header">
            <div>
              <h2 id="deep-research-prompts-heading" className="prompt-panel-title">
                Deep Researchへの指示内容
              </h2>
              <p className="prompt-panel-description">
                Deep Researchへ送信したブリーフと手動実行用rerun指示です。
              </p>
            </div>
            <button
              type="button"
              className="btn-secondary btn-sm"
              onClick={() => setPromptPanelOpen(false)}
            >
              閉じる
            </button>
          </div>

          {attemptsLoading ? (
            <div className="prompt-panel-loading">
              <Skeleton width="100%" height="140px" />
            </div>
          ) : attemptsError ? (
            <div className="monitor-error-banner" role="alert">
              {attemptsError}
            </div>
          ) : attempts && attempts.length > 0 ? (
            <div className="prompt-attempt-list">
              {attempts.map((attempt) => (
                <article key={`${attempt.run_no}-${attempt.response_id ?? "pending"}`} className="prompt-attempt">
                  <div className="prompt-attempt-header">
                    <span className="prompt-attempt-title">
                      Deep Research {attempt.run_no}回目
                      {attempt.source === "manual_upload" ? " 手動取り込み" : ""}
                      {attempt.source === "manual_chatgpt_rerun" ? " ChatGPT手動rerun" : ""}
                    </span>
                    <span className="prompt-attempt-status">
                      {attemptSourceLabel(attempt.source)}
                    </span>
                    <span className="prompt-attempt-status">{attempt.status}</span>
                  </div>
                  <pre className="prompt-attempt-body">{attempt.prompt}</pre>
                </article>
              ))}
            </div>
          ) : (
            <EmptyState
              title="指示内容なし"
              description="Deep Researchへの送信記録がまだありません。"
            />
          )}
        </section>
      )}

      {/* ── Human review banner ───────────────────────── */}
      {showHumanReviewBanner && (
        <div className="human-review-banner" role="alert">
          <div className="human-review-banner-content">
            <span className="human-review-banner-label">人間による判断が必要です</span>
            <p className="human-review-banner-reason">
              {runStatus?.done_reason ?? "AIが自動継続を停止しました。"}
            </p>
          </div>
          <Link
            to={routes().review(runId)}
            className="btn-primary"
            aria-label="判断画面へ"
          >
            判断する →
          </Link>
        </div>
      )}

      {/* ── No-progress note ──────────────────────────── */}
      {showNoProgressNote && !showHumanReviewBanner && (
        <div className="no-progress-note" role="note">
          未解決のblocker itemが残っています。
          人間レビューが必要になる可能性があります。
        </div>
      )}

      {/* ── Item progress ────────────────────────────── */}
      <section className="item-progress" aria-labelledby="item-progress-heading">
        <div className="section-heading-row">
          <h2 id="item-progress-heading" className="section-title">
            ResearchItem進捗
          </h2>
          {itemsTotal > 0 && (
            <span className="item-progress-summary">
              {itemsAnswered}/{itemsTotal} answered
            </span>
          )}
        </div>
        <div className="metrics-row metrics-row--items">
          <MetricCard label="回答済み" value={itemsAnswered} icon="ti-check" />
          <MetricCard label="一部回答" value={itemsPartial} icon="ti-progress" />
          <MetricCard label="未回答" value={itemsUnanswered} icon="ti-alert-circle" />
          <MetricCard
            label="未解決Blocker"
            value={blockersUnresolved}
            icon="ti-alert-triangle"
            warn={blockersUnresolved > 0}
          />
        </div>
        {itemsUnverifiable > 0 && (
          <p className="item-progress-note">
            {itemsUnverifiable}件は公開情報での確認不能として扱われています。
          </p>
        )}
        {unresolvedItems.length > 0 && (
          <div className="item-table-wrap">
            <table className="item-table">
              <thead>
                <tr>
                  <th scope="col">Item</th>
                  <th scope="col">Severity</th>
                  <th scope="col">Status</th>
                  <th scope="col">Failure mode</th>
                  <th scope="col">Question</th>
                </tr>
              </thead>
              <tbody>
                {unresolvedItems.slice(0, 8).map((item) => (
                  <tr key={item.item_id}>
                    <td className="item-table-id">{item.item_id}</td>
                    <td>
                      <span className={`item-severity item-severity--${item.severity}`}>
                        {ITEM_SEVERITY_LABEL[item.severity]}
                      </span>
                    </td>
                    <td>{ITEM_STATUS_LABEL[item.status]}</td>
                    <td className="item-table-mono">
                      {item.failure_mode ?? "none"}
                      {item.failure_mode_confidence !== null &&
                        ` (${item.failure_mode_confidence}%)`}
                    </td>
                    <td>{item.question}</td>
                  </tr>
                ))}
              </tbody>
            </table>
            {unresolvedItems.length > 8 && (
              <p className="item-progress-note">
                ほか{unresolvedItems.length - 8}件の未解決itemがあります。
              </p>
            )}
          </div>
        )}
      </section>

      {/* ── Metrics row ───────────────────────────────── */}
      <div className="metrics-row">
        <MetricCard
          label="トータル経過時間"
          value={formatElapsed(elapsed)}
          icon="ti-clock"
        />
        <MetricCard
          label="レビュー回数"
          value={progress?.total_reviews ?? 0}
          icon="ti-repeat"
        />
        <MetricCard
          label="Targeted rerun"
          value={progress?.targeted_rerun_runs ?? 0}
          icon="ti-search"
        />
        <MetricCard
          label="Verification"
          value={progress?.verification_runs ?? 0}
          icon="ti-shield-check"
        />
        <div className="metric-cost-wrap">
          <CostMeter estimated={estimatedCost} />
        </div>
      </div>

      <p className="sr-only" aria-live="polite" role="status">
        {progressAnnouncement}
      </p>

      <ExecutionDag
        nodes={dagData.nodes}
        edges={dagData.edges}
        progress={progress}
        attempts={sortedAttempts}
        reviews={sortedReviews}
        selectedNodeId={selectedDagNode?.id ?? null}
        onSelectNode={(nodeId) => {
          userSelectedDagNodeRef.current = true;
          setSelectedDagNodeId(nodeId);
        }}
        inspector={
          <CheckpointInspector
            node={selectedDagNode}
            checkpointsError={checkpointsError}
            onOpenFork={() => {
              if (selectedDagNode?.checkpoint?.forkable) {
                setForkCheckpoint(selectedDagNode.checkpoint);
              }
            }}
            forkButtonRef={forkButtonRef}
          />
        }
      />

      {forkCheckpoint && (
        <ForkModal
          runId={runId}
          checkpoint={forkCheckpoint}
          parentTitle={tracked?.title ?? runId}
          onClose={() => {
            setForkCheckpoint(null);
            window.setTimeout(() => forkButtonRef.current?.focus(), 0);
          }}
          onCreated={handleForkCreated}
        />
      )}

      {/* ── Wait banner ───────────────────────────────── */}
      {isWaiting && (
        <WaitBanner
          elapsedMinutes={currentDeepResearchElapsed}
          startedAt={currentDeepResearchStartedAt}
          totalToolCalls={progress?.total_tool_calls ?? 0}
        />
      )}
      {isSubmitWaiting && (
        <div className="alert" role="status">
          Deep Researchへの送信を待っています。Research runは作成済みですが、
          Deep Research開始時刻はまだ記録されていません。トータル経過時間:{" "}
          {formatElapsed(elapsed)}
        </div>
      )}

      {/* ── Review history ───────────────────────────── */}
      <section className="monitor-reviews" aria-labelledby="history-heading">
        <h2 id="history-heading" className="section-title">レビュー履歴</h2>

        {sortedReviews.length === 0 ? (
          <EmptyState
            title="レビュー履歴なし"
            description="レビューが完了すると、ここに表示されます。"
          />
        ) : (
          <div className="review-list">
            {[...sortedReviews].reverse().map((review, i) => (
              <ReviewHistoryItem
                key={review.review_no}
                review={review}
                showTrend={i < sortedReviews.length - 1}
                previousScore={
                  i < sortedReviews.length - 1
                    ? sortedReviews[sortedReviews.length - 2 - i]?.score
                    : undefined
                }
              />
            ))}
          </div>
        )}
      </section>

      {/* ── Done reason ───────────────────────────────── */}
      {isTerminal(status) && runStatus?.done_reason && (
        <div className="done-reason" role="note">
          <span className="done-reason-label">終了理由:</span>{" "}
          {runStatus.done_reason}
          {status === "completed" && (
            <Link to={routes().report(runId)} className="done-report-link">
              最終レポートを開く
            </Link>
          )}
        </div>
      )}
    </div>
  );
}
