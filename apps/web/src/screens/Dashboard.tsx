/**
 * SCR-2: Dashboard — run overview.
 *
 * Two data sources:
 *  1. listHumanReviews() — "要対応" band (polled at HUMAN_REVIEW_QUEUE_INTERVAL).
 *  2. getTrackedRuns() from localStorage — "進行中" / "完了/終了" sections.
 *     Non-terminal runs are individually polled at TRACKED_RUN_INTERVAL.
 *     Terminal runs render statically from last_status.
 *
 * GAP-1: No GET /research-runs list endpoint — localStorage-based fallback.
 */

import { useCallback, useEffect, useState } from "react";

import {
  EmptyState,
  ScoreChip,
  Skeleton,
  StatusPill,
} from "../components";
import { deleteRun, listHumanReviews, getRunStatus } from "../api/research";
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
  untrackRun,
  updateTrackedStatus,
  type TrackedRun,
} from "../runStore";
import {
  isTerminal,
  type HumanReviewQueueItem,
  type ResearchRunStatusResponse,
} from "../types";

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
  deleting: boolean;
  onDelete: (runId: string) => void;
}

function RunCard({ tracked, liveStatus, deleting, onDelete }: RunCardProps) {
  const status = liveStatus?.status ?? tracked.last_status ?? "queued";
  const score =
    liveStatus?.progress.latest_score ?? tracked.last_latest_score ?? null;
  const totalReviews = liveStatus?.progress.total_reviews ?? 0;
  const elapsed = useElapsed(tracked.created_at, !isTerminal(status));
  const deleteLabel = isTerminal(status)
    ? `削除: ${tracked.title}`
    : `停止して削除: ${tracked.title}`;

  return (
    <article className="run-card">
      <Link
        to={routes().monitor(tracked.run_id)}
        className="run-card-link"
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
      <button
        type="button"
        className="run-card-remove"
        aria-label={deleteLabel}
        title={isTerminal(status) ? "削除" : "停止して削除"}
        disabled={deleting}
        onClick={(event) => {
          event.preventDefault();
          event.stopPropagation();
          onDelete(tracked.run_id);
        }}
      >
        ×
      </button>
    </article>
  );
}

// ── Queue item card (reviewer-scoped) ─────────────────────────────────────────

interface QueueCardProps {
  item: HumanReviewQueueItem;
  deleting: boolean;
  onDelete: (runId: string) => void;
}

function QueueCard({ item, deleting, onDelete }: QueueCardProps) {
  const elapsed = useElapsed(item.updated_at, false);

  return (
    <article className="queue-card">
      <Link
        to={routes().review(item.run_id)}
        className="queue-card-link"
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
      <button
        type="button"
        className="run-card-remove queue-card-remove"
        aria-label={`削除: ${item.run_id}`}
        title="削除"
        disabled={deleting}
        onClick={(event) => {
          event.preventDefault();
          event.stopPropagation();
          onDelete(item.run_id);
        }}
      >
        ×
      </button>
    </article>
  );
}

function QueueEmptyCompact() {
  return (
    <div className="queue-empty-compact" role="status">
      <span className="queue-empty-compact__title">要対応なし</span>
      <span className="queue-empty-compact__description">
        レビュー待ちはありません。
      </span>
    </div>
  );
}

interface QueueFetchErrorProps {
  error: unknown;
  connectionUnstable: boolean;
  retrying: boolean;
  onRetry: () => void;
  title?: string;
}

function QueueFetchError({
  error,
  connectionUnstable,
  retrying,
  onRetry,
  title = "要対応の取得に失敗しました",
}: QueueFetchErrorProps) {
  return (
    <div className="queue-empty-compact" role="alert">
      <span className="queue-empty-compact__title">
        {connectionUnstable ? "接続が不安定です" : title}
      </span>
      <span className="queue-empty-compact__description">
        {formatQueueFetchError(error)}
      </span>
      <button
        type="button"
        className="btn-secondary"
        onClick={onRetry}
        disabled={retrying}
      >
        再試行
      </button>
    </div>
  );
}

function confirmRunDelete(runId: string): boolean {
  return window.confirm(
    [
      "このrunを削除しますか？",
      "",
      runId,
      "",
      "進行中の場合は停止してから削除されます。この操作は元に戻せません。",
    ].join("\n"),
  );
}

function formatDeleteError(runId: string, error: unknown): string {
  const detail =
    error instanceof ApiError
      ? error.detail ?? error.message
      : error instanceof Error
        ? error.message
        : "Unknown error";
  return `run ${runId} の削除に失敗しました。${detail}`;
}

function formatQueueFetchError(error: unknown): string {
  if (error instanceof ApiError) {
    return error.detail ?? error.message;
  }
  if (error instanceof Error) {
    return error.message;
  }
  return "要対応キューを取得できませんでした。";
}

function formatTrackedRunPollError(error: unknown): string {
  if (error instanceof ApiError) {
    return error.detail ?? error.message;
  }
  if (error instanceof Error) {
    return error.message;
  }
  return "進行中runの状態を取得できませんでした。";
}

class TrackedRunPollError extends Error {
  constructor(readonly failures: Array<{ runId: string; error: unknown }>) {
    super(
      failures.length === 1 && failures[0]
        ? `run ${failures[0].runId} の状態取得に失敗しました。${formatTrackedRunPollError(failures[0].error)}`
        : `${failures.length}件の進行中runの状態取得に失敗しました。`,
    );
    this.name = "TrackedRunPollError";
  }
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
      const failures: Array<{ runId: string; error: unknown }> = [];
      results.forEach((result, i) => {
        const run = activeRuns[i];
        if (result.status === "fulfilled") {
          updates.set(run.run_id, result.value);
          updateTrackedStatus(run.run_id, result.value.status, {
            estimated_cost_usd: result.value.progress.estimated_cost_usd,
            latest_score: result.value.progress.latest_score,
          });
        } else if (result.reason instanceof ApiError && result.reason.isNotFound) {
          untrackRun(run.run_id);
        } else {
          failures.push({ runId: run.run_id, error: result.reason });
        }
      });

      if (failures.length > 0) {
        if (updates.size > 0) {
          setStatuses(updates);
        }
        throw new TrackedRunPollError(failures);
      }

      return updates;
    },
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [activeRuns.map((r) => r.run_id).join(",")],
  );

  const {
    data,
    error,
    loading,
    connectionUnstable,
    refetch,
  } = usePolling({
    fetcher: fetchAll,
    key: activeRuns.map((r) => r.run_id).join(","),
    interval: () => TRACKED_RUN_INTERVAL,
    enabled: activeRuns.length > 0,
    onData: (data) => setStatuses(data),
  });

  useEffect(() => {
    if (data) setStatuses(data);
  }, [data]);

  return { statuses, error, loading, connectionUnstable, refetch };
}

// ── Main Dashboard component ──────────────────────────────────────────────────

export function Dashboard() {
  const trackedRuns = useTrackedRuns();
  const [deletingRunIds, setDeletingRunIds] = useState<Set<string>>(() => new Set());
  const [hiddenQueueRunIds, setHiddenQueueRunIds] = useState<Set<string>>(() => new Set());
  const [deleteError, setDeleteError] = useState<string | null>(null);

  const removeRun = useCallback(async (runId: string) => {
    if (!confirmRunDelete(runId)) return;

    setDeleteError(null);
    setDeletingRunIds((current) => new Set(current).add(runId));
    let shouldRemoveLocally = false;
    try {
      await deleteRun(runId);
      shouldRemoveLocally = true;
    } catch (error) {
      if (error instanceof ApiError && error.isNotFound) {
        shouldRemoveLocally = true;
      } else {
        setDeleteError(formatDeleteError(runId, error));
      }
    } finally {
      if (shouldRemoveLocally) {
        untrackRun(runId);
        setHiddenQueueRunIds((current) => new Set(current).add(runId));
      }
      setDeletingRunIds((current) => {
        const next = new Set(current);
        next.delete(runId);
        return next;
      });
    }
  }, []);

  // Human review queue
  const {
    data: queueItems,
    loading: queueLoading,
    error: queueError,
    connectionUnstable: queueConnectionUnstable,
    refetch: refetchQueue,
  } = usePolling<HumanReviewQueueItem[]>({
    fetcher: (signal) => listHumanReviews(signal),
    interval: () => HUMAN_REVIEW_QUEUE_INTERVAL,
  });

  // Live statuses for non-terminal tracked runs
  const {
    statuses: liveStatuses,
    error: trackedRunError,
    loading: trackedRunLoading,
    connectionUnstable: trackedRunConnectionUnstable,
    refetch: refetchTrackedRuns,
  } = useTrackedRunStatuses(trackedRuns);

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

  const visibleQueueItems = (queueItems ?? []).filter(
    (item) => !hiddenQueueRunIds.has(item.run_id),
  );
  const visibleQueueRunIds = new Set(visibleQueueItems.map((item) => item.run_id));
  const isQueueEmpty =
    !queueLoading && !queueError && visibleQueueItems.length === 0;

  const visibleActiveRuns = activeRuns.filter(
    (run) => !visibleQueueRunIds.has(run.run_id),
  );

  return (
    <div className="screen-dashboard">
      {deleteError && (
        <div className="form-error" role="alert">
          {deleteError}
        </div>
      )}

      {/* ── 要対応バンド ───────────────────────────── */}
      <section
        className={[
          "dashboard-section",
          "dashboard-section--urgent",
          isQueueEmpty ? "dashboard-section--urgent-empty" : "",
        ].join(" ")}
        aria-labelledby="queue-heading"
      >
        <div className="section-header">
          <h2 id="queue-heading" className="section-title">
            要対応
            {visibleQueueItems.length > 0 && (
              <span className="badge-count" aria-label={`${visibleQueueItems.length}件`}>
                {visibleQueueItems.length}
              </span>
            )}
          </h2>
        </div>

        {queueError !== undefined && (
          <QueueFetchError
            error={queueError}
            connectionUnstable={queueConnectionUnstable}
            retrying={queueLoading}
            onRetry={refetchQueue}
          />
        )}

        {queueLoading && !queueItems ? (
          <div className="queue-skeleton">
            <Skeleton width="100%" height="80px" />
            <Skeleton width="100%" height="80px" />
          </div>
        ) : visibleQueueItems.length > 0 ? (
          <div className="queue-list">
            {visibleQueueItems.map((item) => (
              <QueueCard
                key={item.run_id}
                item={item}
                deleting={deletingRunIds.has(item.run_id)}
                onDelete={removeRun}
              />
            ))}
          </div>
        ) : queueError === undefined ? (
          <QueueEmptyCompact />
        ) : null}
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

        {trackedRunError !== undefined && (
          <QueueFetchError
            error={trackedRunError}
            connectionUnstable={trackedRunConnectionUnstable}
            retrying={trackedRunLoading}
            onRetry={refetchTrackedRuns}
            title="進行中runの取得に失敗しました"
          />
        )}

        {visibleActiveRuns.length === 0 ? (
          <EmptyState
            title="進行中のrunなし"
            description="新規リサーチを開始すると、ここに表示されます。"
            action={{ label: "新規リサーチを開始", onClick: () => navigate(routes().new) }}
          />
        ) : (
          <div className="run-list">
            {visibleActiveRuns.map((tracked) => (
              <RunCard
                key={tracked.run_id}
                tracked={tracked}
                liveStatus={liveStatuses.get(tracked.run_id)}
                deleting={deletingRunIds.has(tracked.run_id)}
                onDelete={removeRun}
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
              <RunCard
                key={tracked.run_id}
                tracked={tracked}
                deleting={deletingRunIds.has(tracked.run_id)}
                onDelete={removeRun}
              />
            ))}
          </div>
        </section>
      )}
    </div>
  );
}
