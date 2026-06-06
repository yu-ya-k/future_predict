/**
 * SCR-2: Dashboard — run overview.
 *
 * Two data sources:
 *  1. listHumanReviews() — "要対応" band (reviewer-scoped, polled at HUMAN_REVIEW_QUEUE_INTERVAL).
 *  2. getTrackedRuns() from localStorage — "進行中" / "完了/終了" sections.
 *     Non-terminal runs are individually polled at TRACKED_RUN_INTERVAL.
 *     Terminal runs render statically from last_status.
 *
 * GAP-1: No GET /research-runs list endpoint — localStorage-based fallback.
 * ReviewerRequiredError on the queue section shows a prompt instead of crashing.
 */

import { useCallback, useEffect, useState, useSyncExternalStore } from "react";

import {
  EmptyState,
  ScoreChip,
  Skeleton,
  StatusPill,
} from "../components";
import { listHumanReviews, getRunStatus } from "../api/research";
import { ApiError } from "../api/client";
import { usePolling } from "../hooks/usePolling";
import { useElapsed, formatElapsed } from "../hooks/useElapsed";
import {
  HUMAN_REVIEW_QUEUE_INTERVAL,
  TRACKED_RUN_INTERVAL,
} from "../polling";
import { navigate, routes, Link } from "../router";
import {
  getTrackedRuns,
  subscribeRuns,
  updateTrackedStatus,
  type TrackedRun,
} from "../runStore";
import { getReviewerId, subscribeReviewer } from "../reviewer";
import {
  isTerminal,
  type HumanReviewQueueItem,
  type ResearchRunStatusResponse,
} from "../types";

// ── Reviewer-ID setup ─────────────────────────────────────────────────────────

function useReviewerId() {
  return useSyncExternalStore(subscribeReviewer, getReviewerId);
}

// ── Tracked runs from localStorage (manual subscription to avoid snapshot cache issue) ──

function useTrackedRuns() {
  const [runs, setRuns] = useState<TrackedRun[]>(() => getTrackedRuns());

  useEffect(() => {
    // Re-read on mount to catch any changes before subscription
    setRuns(getTrackedRuns());
    return subscribeRuns(() => setRuns(getTrackedRuns()));
  }, []);

  return runs;
}

// ── Single run card (non-terminal, live status) ───────────────────────────────

interface RunCardProps {
  tracked: TrackedRun;
  liveStatus?: ResearchRunStatusResponse;
}

function RunCard({ tracked, liveStatus }: RunCardProps) {
  const status = liveStatus?.status ?? tracked.last_status ?? "queued";
  const score =
    liveStatus?.progress.latest_score ?? tracked.last_latest_score ?? null;
  const totalReviews = liveStatus?.progress.total_reviews ?? 0;
  const elapsed = useElapsed(tracked.created_at, !isTerminal(status));

  return (
    <Link
      to={routes().monitor(tracked.run_id)}
      className="run-card"
      aria-label={`${tracked.title} — ${status}`}
    >
      <div className="run-card-header">
        <div className="run-card-badges">
          <StatusPill status={status} />
        </div>
        {score !== null && <ScoreChip score={score} />}
      </div>

      <p className="run-card-title">{tracked.title}</p>

      <div className="run-card-meta">
        <span className="run-card-id">{tracked.run_id}</span>
        <span className="run-card-elapsed">{formatElapsed(elapsed)}</span>
        {totalReviews > 0 && (
          <span className="run-card-reviews">レビュー {totalReviews}回</span>
        )}
      </div>
    </Link>
  );
}

// ── Queue item card (reviewer-scoped) ─────────────────────────────────────────

function QueueCard({ item }: { item: HumanReviewQueueItem }) {
  const elapsed = useElapsed(item.updated_at, false);

  return (
    <Link
      to={routes().review(item.run_id)}
      className="queue-card"
      aria-label={`要対応: ${item.run_id}`}
    >
      <div className="queue-card-header">
        <StatusPill status={item.status} />
        {item.latest_score !== null && <ScoreChip score={item.latest_score} />}
      </div>
      <p className="queue-card-id">{item.run_id}</p>
      {item.latest_rationale && (
        <p className="queue-card-rationale">{item.latest_rationale}</p>
      )}
      <div className="queue-card-meta">
        <span>更新 {formatElapsed(elapsed)} 前</span>
        {item.audit_summary.no_progress_count >= 2 && (
          <span className="queue-card-stall" role="note">
            改善停滞 {item.audit_summary.no_progress_count}回
          </span>
        )}
      </div>
    </Link>
  );
}

// ── Dashboard live polling for non-terminal tracked runs ──────────────────────

function useTrackedRunStatuses(trackedRuns: TrackedRun[]) {
  const [statuses, setStatuses] = useState<Map<string, ResearchRunStatusResponse>>(new Map());

  const activeRuns = trackedRuns.filter((r) => !isTerminal(r.last_status ?? "queued"));

  const fetchAll = useCallback(
    async (signal: AbortSignal) => {
      const results = await Promise.allSettled(
        activeRuns.map((r) => getRunStatus(r.run_id, signal)),
      );

      const updates = new Map<string, ResearchRunStatusResponse>();
      results.forEach((result, i) => {
        if (result.status === "fulfilled") {
          const run = activeRuns[i];
          updates.set(run.run_id, result.value);
          updateTrackedStatus(run.run_id, result.value.status, {
            estimated_cost_usd: result.value.progress.estimated_cost_usd,
            latest_score: result.value.progress.latest_score,
          });
        }
      });

      return updates;
    },
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [activeRuns.map((r) => r.run_id).join(",")],
  );

  const { data } = usePolling({
    fetcher: fetchAll,
    interval: () => TRACKED_RUN_INTERVAL,
    enabled: activeRuns.length > 0,
    onData: (data) => setStatuses(data),
  });

  useEffect(() => {
    if (data) setStatuses(data);
  }, [data]);

  return statuses;
}

// ── Main Dashboard component ──────────────────────────────────────────────────

export function Dashboard() {
  const reviewerId = useReviewerId();
  const trackedRuns = useTrackedRuns();

  // Human review queue (reviewer-scoped)
  const {
    data: queueItems,
    loading: queueLoading,
    error: queueError,
  } = usePolling<HumanReviewQueueItem[]>({
    fetcher: (signal) => listHumanReviews(signal),
    interval: () => HUMAN_REVIEW_QUEUE_INTERVAL,
    enabled: !!reviewerId,
  });

  // Live statuses for non-terminal tracked runs
  const liveStatuses = useTrackedRunStatuses(trackedRuns);

  // Partition tracked runs
  const activeRuns = trackedRuns.filter(
    (r) => !isTerminal(r.last_status ?? "queued"),
  );
  const terminalRuns = trackedRuns.filter((r) => isTerminal(r.last_status ?? "queued"));

  // Session cost total (best-effort)
  const sessionCost = trackedRuns.reduce((sum, r) => {
    const live = liveStatuses.get(r.run_id);
    return (
      sum +
      (live?.progress.estimated_cost_usd ?? r.last_estimated_cost_usd ?? 0)
    );
  }, 0);

  const isQueueReviewerError =
    queueError instanceof ApiError && queueError.isUnauthorized;

  return (
    <div className="screen-dashboard">
      {/* ── 要対応バンド ───────────────────────────── */}
      <section className="dashboard-section dashboard-section--urgent" aria-labelledby="queue-heading">
        <div className="section-header">
          <h2 id="queue-heading" className="section-title">
            要対応
            {queueItems && queueItems.length > 0 && (
              <span className="badge-count" aria-label={`${queueItems.length}件`}>
                {queueItems.length}
              </span>
            )}
          </h2>
        </div>

        {!reviewerId ? (
          <div className="reviewer-prompt">
            <p>要対応キューを表示するにはレビュアーIDが必要です。</p>
            <p className="reviewer-prompt-hint">
              画面右上の「レビュアーID」から設定してください。
            </p>
          </div>
        ) : isQueueReviewerError ? (
          <div className="reviewer-prompt">
            <p>レビュアーIDが無効です。再設定してください。</p>
          </div>
        ) : queueLoading && !queueItems ? (
          <div className="queue-skeleton">
            <Skeleton width="100%" height="80px" />
            <Skeleton width="100%" height="80px" />
          </div>
        ) : queueItems && queueItems.length > 0 ? (
          <div className="queue-list">
            {queueItems.map((item) => (
              <QueueCard key={item.run_id} item={item} />
            ))}
          </div>
        ) : (
          <EmptyState
            title="要対応なし"
            description="現在、人間によるレビューが必要なrunはありません。"
            icon="ti-check"
          />
        )}
      </section>

      {/* ── 進行中 ─────────────────────────────────── */}
      <section className="dashboard-section" aria-labelledby="active-heading">
        <div className="section-header">
          <h2 id="active-heading" className="section-title">
            進行中
          </h2>
          <button
            type="button"
            className="btn-secondary"
            onClick={() => navigate(routes().new)}
          >
            + 新規リサーチ
          </button>
        </div>

        {activeRuns.length === 0 ? (
          <EmptyState
            title="進行中のrunなし"
            description="新規リサーチを開始すると、ここに表示されます。"
            action={{ label: "新規リサーチを開始", onClick: () => navigate(routes().new) }}
          />
        ) : (
          <div className="run-list">
            {activeRuns.map((tracked) => (
              <RunCard
                key={tracked.run_id}
                tracked={tracked}
                liveStatus={liveStatuses.get(tracked.run_id)}
              />
            ))}
          </div>
        )}
      </section>

      {/* ── 完了 / 終了 ─────────────────────────────── */}
      {terminalRuns.length > 0 && (
        <section className="dashboard-section" aria-labelledby="done-heading">
          <div className="section-header">
            <h2 id="done-heading" className="section-title">完了 / 終了</h2>
            {sessionCost > 0 && (
              <span className="session-cost" aria-label={`セッションコスト合計: $${sessionCost.toFixed(3)}`}>
                合計 ${sessionCost.toFixed(3)}
              </span>
            )}
          </div>

          <div className="run-list">
            {terminalRuns.map((tracked) => (
              <RunCard key={tracked.run_id} tracked={tracked} />
            ))}
          </div>
        </section>
      )}
    </div>
  );
}
