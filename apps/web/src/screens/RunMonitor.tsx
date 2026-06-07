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

import { useCallback, useRef, useState } from "react";

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
  cancelRun,
} from "../api/research";
import { ApiError } from "../api/client";
import { usePolling } from "../hooks/usePolling";
import { useElapsed, formatElapsed } from "../hooks/useElapsed";
import { statusPollInterval } from "../polling";
import { routes, Link } from "../router";
import { getTrackedRun, updateTrackedStatus } from "../runStore";
import { notify } from "../notifications";
import {
  isTerminal,
  type AuditResponse,
  type ResearchRunStatusResponse,
  type ResearchAttempt,
  type ResearchItem,
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
type DagNodeTone = "brief" | "research" | "review" | "patch" | "verify" | "finalize";
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
  tone: DagNodeTone;
  lane: number;
  col: number;
  href?: string;
  ariaLabel?: string;
  score?: number;
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

function countHistorySteps(
  history: AuditResponse["history"],
  step: string,
): number {
  return history.filter((event) => historyStep(event) === step).length;
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

function executedFollowUpLimits(
  progress: ResearchRunStatusResponse["progress"] | undefined,
  history: AuditResponse["history"],
): Record<FollowUpKind, number> {
  return {
    llm_patch: Math.max(progress?.llm_patch_runs ?? 0, countHistorySteps(history, "llm_patch")),
    verification: Math.max(
      progress?.verification_runs ?? 0,
      countHistorySteps(history, "verification_completed"),
    ),
    targeted_rerun: progress?.targeted_rerun_runs ?? 0,
    full_rerun: progress?.full_rerun_runs ?? 0,
    item_revision: 0,
    human_review: 0,
    finalize_with_limitation: countHistorySteps(history, "finalized_with_limitation"),
  };
}

function hasExecutedFollowUp(
  kind: FollowUpKind,
  counts: Record<FollowUpKind, number>,
  limits: Record<FollowUpKind, number>,
): boolean {
  return counts[kind] < limits[kind];
}

function buildExecutionDag({
  status,
  attempts,
  reviews,
  history,
  progress,
  runId,
}: {
  status: RunStatus;
  attempts: ResearchAttempt[];
  reviews: ReviewRecord[];
  history: AuditResponse["history"];
  progress?: ResearchRunStatusResponse["progress"];
  runId: string;
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

  nodes.push({
    id: "brief",
    title: "指示内容",
    meta: "Objective contract / ResearchItems",
    status: activeId === "brief" ? "active" : "done",
    tone: "brief",
    lane: 1,
    col: 1,
  });

  let previousId = "brief";
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
    const attempt =
      attemptsByRunNo.get(runNo) ??
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
    nodes.push({
      id: researchId,
      title: `Deep Research ${attempt.run_no}回目`,
      meta: `status: ${attempt.status}`,
      status: activeId === researchId ? "active" : "done",
      tone: "research",
      lane: 1,
      col,
      href: routes().report(runId, { attempt: attempt.run_no }),
    });
    edges.push({
      id: `${previousId}-${researchId}`,
      from: previousId,
      to: researchId,
      status: activeId === researchId ? "active" : "done",
    });
    previousId = researchId;
    col += 1;
  }

  if (minimumResearchCount > 0) {
    appendResearchNode();
  }

  for (const review of reviews) {
    const reviewId = `review-${review.review_no}`;
    nodes.push({
      id: reviewId,
      title: `LLMレビュー ${review.review_no}回目`,
      meta: review.verdict,
      status: "done",
      tone: "review",
      lane: 1,
      col,
      href: routes().audit(runId, { tab: "reviews", review: review.review_no }),
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
    if (!followUp || !hasExecutedFollowUp(followUp, followUpCounts, executedLimits)) {
      continue;
    }

    followUpCounts[followUp] += 1;
    const followUpIndex = followUpCounts[followUp];
    const followId = `followup-${review.review_no}`;
    const label = FOLLOW_UP_LABEL[followUp];
    nodes.push({
      id: followId,
      title: followUpTitle(followUp, followUpIndex),
      meta: label,
      status: "done",
      tone: followUpTone(followUp),
      lane: review.review_no % 2 === 0 ? 2 : 0,
      col,
      href: routes().audit(runId, { tab: "reviews", review: review.review_no }),
    });
    edges.push({
      id: `${previousId}-${followId}`,
      from: previousId,
      to: followId,
      status: "done",
      label,
    });
    previousId = followId;
    col += 1;

    if (followUp === "targeted_rerun" || followUp === "full_rerun") {
      appendResearchNode();
    }
  }

  while (nextResearchRunNo <= minimumResearchCount) {
    appendResearchNode();
  }

  if (activeId === `review-${reviews.length + 1}`) {
    const reviewId = `review-${reviews.length + 1}`;
    nodes.push({
      id: reviewId,
      title: `LLMレビュー ${reviews.length + 1}回目`,
      meta: "running",
      status: "active",
      tone: "review",
      lane: 1,
      col,
      href: routes().audit(runId, { tab: "reviews", review: reviews.length + 1 }),
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
      href: routes().review(runId),
      ariaLabel: "人間判断画面を開く",
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
    href: status === "completed" ? routes().report(runId) : undefined,
  });
  edges.push({
    id: `${previousId}-final`,
    from: previousId,
    to: "final",
    status: status === "completed" ? "done" : activeId === "final" ? "active" : "pending",
  });

  return { nodes, edges };
}

function ExecutionDag({
  status,
  attempts,
  reviews,
  history,
  progress,
  runId,
}: {
  status: RunStatus;
  attempts: ResearchAttempt[];
  reviews: ReviewRecord[];
  history: AuditResponse["history"];
  progress?: ResearchRunStatusResponse["progress"];
  runId: string;
}) {
  const { nodes, edges } = buildExecutionDag({
    status,
    attempts,
    reviews,
    history,
    progress,
    runId,
  });
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
          const className = `execution-dag-node execution-dag-node--${node.status} execution-dag-node--${node.tone}${node.href ? " execution-dag-node-link" : ""}`;
          const style = {
            gridColumn: node.col,
            gridRow: node.lane + 1,
          };
          const content = (
            <>
            <div className="execution-dag-node-topline">
              <span className="execution-dag-dot" aria-hidden="true" />
              <span className="execution-dag-state">{DAG_STATUS_LABEL[node.status]}</span>
            </div>
            <h3 className="execution-dag-title">{node.title}</h3>
            <p className="execution-dag-meta">{node.meta}</p>
            {node.score !== undefined && (
              <span className="execution-dag-score" aria-label={`レビュー点数 ${node.score}点`}>
                {node.score}点
              </span>
            )}
            </>
          );
          if (node.href) {
            return (
              <Link
                key={node.id}
                to={node.href}
                className={className}
                style={style}
                aria-label={node.ariaLabel ?? `${node.title}の結果を開く`}
              >
                {content}
              </Link>
            );
          }
          return (
            <article key={node.id} className={className} style={style}>
              {content}
            </article>
          );
        })}
      </div>
    </section>
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
  const sortedReviews = Array.isArray(audit?.reviews)
    ? [...audit.reviews].sort((a, b) => a.review_no - b.review_no)
    : [];
  const auditHistory = Array.isArray(audit?.history) ? audit.history : [];
  const sortedAttempts = Array.isArray(dagAttempts)
    ? dagAttempts
    : [];

  const elapsed = useElapsed(tracked?.created_at, !isTerminal(status));
  const currentDeepResearchStartedAt =
    runStatus?.deep_research_submitted_at ?? tracked?.created_at;
  const currentDeepResearchElapsed = useElapsed(
    currentDeepResearchStartedAt,
    !isTerminal(status),
  );

  const isWaiting =
    status === "waiting_deep_research" || status === "collecting";

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
        <BackLink to={routes().dashboard} label="ダッシュボードへ戻る" />
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
                実際にDeep Researchへ送信したブリーフとtargeted rerun指示です。
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
                    <span className="prompt-attempt-title">Deep Research {attempt.run_no}回目</span>
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

      <ExecutionDag
        status={status}
        attempts={sortedAttempts}
        reviews={sortedReviews}
        history={auditHistory}
        progress={progress}
        runId={runId}
      />

      {/* ── Wait banner ───────────────────────────────── */}
      {isWaiting && (
        <WaitBanner
          elapsedMinutes={currentDeepResearchElapsed}
          startedAt={currentDeepResearchStartedAt}
          totalToolCalls={progress?.total_tool_calls ?? 0}
        />
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
