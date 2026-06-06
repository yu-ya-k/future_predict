/**
 * SCR-3: Run Monitor — central hub for a single research run.
 *
 * Polling: statusPollInterval(status) drives getRunStatus; getReviews for history.
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
  PipelineStepper,
  ReviewHistoryItem,
  ScoreChip,
  Skeleton,
  StatusPill,
  WaitBanner,
  BackLink,
  EmptyState,
  type PipelineStep,
} from "../components";
import {
  getRunStatus,
  getReviews,
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
  type ResearchRunStatusResponse,
  type ResearchAttempt,
  type ResearchItem,
  type ReviewRecord,
  type RunStatus,
} from "../types";

// ── Pipeline step mapping ─────────────────────────────────────────────────────

function statusToStep(status: RunStatus): PipelineStep {
  switch (status) {
    case "queued":
    case "submitted":
      return "brief";
    case "waiting_deep_research":
    case "collecting":
      return "research";
    case "reviewing":
    case "needs_action":
    case "needs_human_review":
      return "review";
    case "completed":
    case "cancelled":
    case "failed":
      return "finalize";
  }
}

function getCompletedSteps(status: RunStatus): PipelineStep[] {
  const order: PipelineStep[] = ["brief", "research", "review", "finalize"];
  const current = statusToStep(status);
  const idx = order.indexOf(current);
  return order.slice(0, idx) as PipelineStep[];
}

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

  // ── Reviews polling ────────────────────────────────────────────────────────

  const { data: reviews } = usePolling<ReviewRecord[]>({
    fetcher: (signal) => getReviews(runId, signal),
    interval: (data) => {
      if (runStatus && isTerminal(runStatus.status)) return null;
      // Slow down when in long phases
      const s = runStatus?.status;
      if (s === "waiting_deep_research" || s === "collecting") return 30_000;
      return data !== undefined ? 10_000 : 5_000;
    },
  });

  // ── Item polling ──────────────────────────────────────────────────────────

  const { data: items } = usePolling<ResearchItem[]>({
    fetcher: (signal) => getItems(runId, signal),
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
  const sortedReviews = Array.isArray(reviews)
    ? [...reviews].sort((a, b) => a.review_no - b.review_no)
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
            <Link to={routes().report(runId, "reviews")} className="btn-secondary btn-sm">
              レビュー内容
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
        {progress?.latest_score !== null && progress?.latest_score !== undefined && (
          <div className="metric-score-wrap">
            <ScoreChip score={progress.latest_score} animate />
          </div>
        )}
        <div className="metric-cost-wrap">
          <CostMeter estimated={estimatedCost} />
        </div>
      </div>

      {/* ── Pipeline stepper ─────────────────────────── */}
      <div className="monitor-pipeline">
        <PipelineStepper
          currentStep={statusToStep(status)}
          completedSteps={getCompletedSteps(status)}
          loopCount={
            (progress?.targeted_rerun_runs ?? 0) +
            (progress?.full_rerun_runs ?? 0) +
            (progress?.llm_patch_runs ?? 0) +
            (progress?.verification_runs ?? 0)
          }
          maxIterations={tracked?.max_total_iterations ?? 0}
        />
      </div>

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
        </div>
      )}
    </div>
  );
}
