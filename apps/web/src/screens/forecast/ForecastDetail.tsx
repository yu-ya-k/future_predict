import { useCallback, useEffect, useRef, useState } from "react";

import {
  commitForecastVersion,
  computeProbabilities,
  dispatchCurrentStatePack,
  extractEvidence,
  generateScenarios,
  getForecast,
  getForecastEstimateSet,
  resolveForecast,
  reviewForecast,
} from "../../api/forecast";
import { Link, routes } from "../../router";
import { useElapsed } from "../../hooks/useElapsed";
import type {
  EstimateSetResponse,
  ForecastDetail as ForecastDetailType,
  ForecastStatus,
  ResolveForecastResponse,
} from "../../types";
import { formatForecastError } from "./errors";
import {
  ForecastFlowProgress,
  type ForecastFlowNode,
  type ForecastFlowStatus,
} from "./ForecastFlowProgress";

type Command =
  | "pack"
  | "evidence"
  | "scenarios"
  | "claimTargets"
  | "compute"
  | "approve"
  | "commit"
  | "resolve";

function stableKey(forecastId: string, action: Command): string {
  return `forecast-${forecastId}-${action}-${crypto.randomUUID()}`;
}

function isMutatingClosed(status: ForecastStatus | undefined): boolean {
  return status === "resolved";
}

function statusReason(status: ForecastStatus | undefined, needed: string): string {
  if (!status) return "Forecast状態を読み込み中です。";
  if (status === "resolved") return "このForecastは解決済みです。";
  return needed;
}

function hasEstimateSet(status: ForecastStatus): boolean {
  return status === "draft_ready" || status === "committed" || status === "resolved";
}

const FORECAST_STATUS_ORDER: ForecastStatus[] = [
  "framing_pending",
  "framing_approved",
  "pack_running",
  "evidence_ready",
  "scenarios_ready",
  "draft_ready",
  "committed",
  "resolved",
];

function statusAtLeast(
  current: ForecastStatus | undefined,
  target: ForecastStatus,
): boolean {
  if (!current) return false;
  return FORECAST_STATUS_ORDER.indexOf(current) >= FORECAST_STATUS_ORDER.indexOf(target);
}

function flowStatus({
  done,
  active,
  available,
}: {
  done: boolean;
  active: boolean;
  available?: boolean;
}): ForecastFlowStatus {
  if (done) return "done";
  if (active) return "active";
  if (available) return "available";
  return "pending";
}

function forecastStatusLabel(status: ForecastStatus | undefined): string {
  switch (status) {
    case "framing_pending":
      return "フレーミング待ち";
    case "framing_approved":
      return "フレーミング承認済み";
    case "pack_running":
      return "公開情報パック実行中";
    case "evidence_ready":
      return "証拠抽出済み";
    case "scenarios_ready":
      return "シナリオ生成済み";
    case "draft_ready":
      return "確率計算済み";
    case "committed":
      return "コミット済み";
    case "resolved":
      return "解決済み";
    default:
      return "読み込み中";
  }
}

function packStatusLabel(status: string | null | undefined): string {
  switch (status) {
    case "running":
      return "公開情報パック実行中";
    case "completed":
      return "公開情報パック完了";
    case "needs_human_review":
      return "要確認";
    case "failed":
      return "失敗";
    case "cancelled":
      return "中断";
    case null:
    case undefined:
      return "未投入";
    default:
      return status;
  }
}

function packFlowMeta({
  currentResearchPackStatus,
  researchPackCompleted,
  researchPackRunning,
}: {
  currentResearchPackStatus: string | null | undefined;
  researchPackCompleted: boolean;
  researchPackRunning: boolean;
}): string {
  if (researchPackRunning) return "公開情報パックを実行中";
  if (researchPackCompleted) return "公開情報パック完了";
  switch (currentResearchPackStatus) {
    case "needs_human_review":
      return "公開情報パックは要確認です";
    case "failed":
      return "公開情報パックは失敗しました";
    case "cancelled":
      return "公開情報パックは中断されました";
    case null:
    case undefined:
      return "公開情報パックを投入";
    default:
      return "公開情報パックの状態を確認中";
  }
}

function formatStartedAt(value: string | null | undefined): string | null {
  if (!value) return null;
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return null;
  return new Intl.DateTimeFormat("ja-JP", {
    hour: "2-digit",
    minute: "2-digit",
  }).format(date);
}

function forecastExecutionNodes({
  status,
  approvedFraming,
  researchPackCompleted,
  researchPackRunning,
  currentResearchPackStatus,
  claimTargetsApproved,
  hasEstimate,
  phaseAApproved,
  busy,
}: {
  status: ForecastStatus | undefined;
  approvedFraming: boolean;
  researchPackCompleted: boolean;
  researchPackRunning: boolean;
  currentResearchPackStatus: string | null | undefined;
  claimTargetsApproved: boolean;
  hasEstimate: boolean;
  phaseAApproved: boolean;
  busy: Command | null;
}): ForecastFlowNode[] {
  const isResolved = status === "resolved";
  const estimateReady = hasEstimate || statusAtLeast(status, "draft_ready");
  return [
    {
      id: "framing",
      title: "フレーミング承認",
      meta: approvedFraming ? "保存済み前提を承認済み" : "保存済み前提の承認待ち",
      status: flowStatus({
        done: approvedFraming || statusAtLeast(status, "framing_approved"),
        active: status === "framing_pending",
      }),
      tone: "brief",
    },
    {
      id: "pack",
      title: "公開情報パック投入",
      meta: packFlowMeta({
        currentResearchPackStatus,
        researchPackCompleted,
        researchPackRunning,
      }),
      status: flowStatus({
        done: researchPackCompleted || statusAtLeast(status, "evidence_ready"),
        active: busy === "pack" || researchPackRunning,
        available: status === "framing_approved",
      }),
      tone: "research",
    },
    {
      id: "evidence",
      title: "証拠抽出",
      meta: "公開情報から主張とソースを抽出",
      status: flowStatus({
        done: statusAtLeast(status, "evidence_ready"),
        active: busy === "evidence",
        available: status === "pack_running" && researchPackCompleted,
      }),
      tone: "review",
    },
    {
      id: "scenarios",
      title: "シナリオ生成",
      meta: "結果別のPhaseAシナリオを生成",
      status: flowStatus({
        done: statusAtLeast(status, "scenarios_ready"),
        active: busy === "scenarios",
        available: status === "evidence_ready",
      }),
      tone: "research",
    },
    {
      id: "claim-links",
      title: "Claim link承認",
      meta: claimTargetsApproved ? "リンク承認済み" : "シナリオと主張の対応を確認",
      status: flowStatus({
        done: claimTargetsApproved || statusAtLeast(status, "draft_ready"),
        active: busy === "claimTargets",
        available: status === "scenarios_ready" && !claimTargetsApproved,
      }),
      tone: "review",
    },
    {
      id: "compute",
      title: "確率計算",
      meta: estimateReady ? "下書き推定値あり" : "PhaseAエンジンで計算",
      status: flowStatus({
        done: estimateReady,
        active: busy === "compute",
        available: status === "scenarios_ready" && claimTargetsApproved,
      }),
      tone: "verify",
    },
    {
      id: "approve-phase-a",
      title: "PhaseA承認",
      meta: phaseAApproved ? "PhaseA下書き承認済み" : "下書き推定値の承認待ち",
      status: flowStatus({
        done: phaseAApproved || statusAtLeast(status, "committed"),
        active: busy === "approve",
        available: status === "draft_ready" && estimateReady && !phaseAApproved,
      }),
      tone: "review",
    },
    {
      id: "commit",
      title: "コミット",
      meta: statusAtLeast(status, "committed")
        ? "バージョン固定済み"
        : "承認済み推定値をバージョン化",
      status: flowStatus({
        done: statusAtLeast(status, "committed"),
        active: busy === "commit",
        available: status === "draft_ready" && phaseAApproved,
      }),
      tone: "finalize",
    },
    {
      id: "resolve",
      title: "解決",
      meta: isResolved ? "実績結果で解決済み" : "実績結果を選んで採点",
      status: flowStatus({
        done: isResolved,
        active: busy === "resolve",
        available: status === "committed",
      }),
      tone: "finalize",
    },
  ];
}

export function ForecastDetail({ forecastId }: { forecastId: string }) {
  const [forecast, setForecast] = useState<ForecastDetailType | null>(null);
  const [estimate, setEstimate] = useState<EstimateSetResponse | null>(null);
  const [claimTargetsApproved, setClaimTargetsApproved] = useState(false);
  const [phaseAApprovedEstimateId, setPhaseAApprovedEstimateId] = useState<string | null>(null);
  const [resolution, setResolution] = useState<ResolveForecastResponse | null>(null);
  const [selectedOutcomeId, setSelectedOutcomeId] = useState("");
  const [resolutionNotes, setResolutionNotes] = useState("");
  const [busy, setBusy] = useState<Command | null>(null);
  const [error, setError] = useState<string | null>(null);
  const idempotencyKeys = useRef<Record<Command, string>>({
    pack: stableKey(forecastId, "pack"),
    evidence: stableKey(forecastId, "evidence"),
    scenarios: stableKey(forecastId, "scenarios"),
    claimTargets: stableKey(forecastId, "claimTargets"),
    compute: stableKey(forecastId, "compute"),
    approve: stableKey(forecastId, "approve"),
    commit: stableKey(forecastId, "commit"),
    resolve: stableKey(forecastId, "resolve"),
  });

  const load = useCallback(async () => {
    const nextForecast = await getForecast(forecastId);
    setForecast(nextForecast);
    if (hasEstimateSet(nextForecast.status)) {
      setEstimate(await getForecastEstimateSet(forecastId));
    } else {
      setEstimate(null);
    }
  }, [forecastId]);

  useEffect(() => {
    void load().catch((err) => setError(formatForecastError(err)));
  }, [load]);

  const status = forecast?.status;
  const approvedFraming = Boolean(forecast?.approved_framing_version);
  const currentResearchPack = forecast?.current_research_pack ?? null;
  const currentResearchPackStatus =
    currentResearchPack?.effective_status ?? forecast?.current_research_pack_status;
  const researchPackCompleted = currentResearchPackStatus === "completed";
  const researchPackBlocked =
    currentResearchPackStatus === "failed" ||
    currentResearchPackStatus === "cancelled" ||
    currentResearchPackStatus === "needs_human_review";
  const researchPackRunning =
    status === "pack_running" && currentResearchPackStatus === "running";
  const researchPackStartedAt =
    currentResearchPack?.deep_research_started_at ??
    currentResearchPack?.research_run_created_at ??
    currentResearchPack?.pack_created_at ??
    undefined;
  const researchPackElapsed = useElapsed(researchPackStartedAt, researchPackRunning);
  const researchPackStartedLabel = formatStartedAt(researchPackStartedAt);
  const researchRunPath = currentResearchPack?.research_run_id
    ? routes().monitor(currentResearchPack.research_run_id)
    : null;

  useEffect(() => {
    if (!researchPackRunning) return undefined;
    const interval = window.setInterval(() => {
      void load().catch((err) => setError(formatForecastError(err)));
    }, 3_000);
    return () => window.clearInterval(interval);
  }, [researchPackRunning, load]);

  useEffect(() => {
    if (!selectedOutcomeId && forecast?.outcomes[0]) {
      setSelectedOutcomeId(forecast.outcomes[0].outcome_id);
    }
  }, [forecast, selectedOutcomeId]);

  useEffect(() => {
    if (forecast?.status && forecast.status !== "scenarios_ready") {
      setClaimTargetsApproved(false);
    }
  }, [forecast?.status]);

  async function runStep(step: Command, fn: () => Promise<unknown>) {
    setBusy(step);
    setError(null);
    try {
      const result = await fn();
      if (step === "claimTargets") setClaimTargetsApproved(true);
      if (step === "approve" && estimate) {
        setPhaseAApprovedEstimateId(estimate.estimate_set_id);
      }
      if (step === "compute") setEstimate(result as EstimateSetResponse);
      if (step === "resolve") setResolution(result as ResolveForecastResponse);
      await load();
    } catch (err) {
      setError(formatForecastError(err));
    } finally {
      setBusy(null);
    }
  }

  const canDispatch = approvedFraming && status === "framing_approved";
  const canExtract = status === "pack_running" && researchPackCompleted;
  const canGenerate = status === "evidence_ready";
  const effectiveClaimTargetsApproved =
    claimTargetsApproved ||
    (forecast?.approved_claim_target_link_count ?? 0) > 0 ||
    statusAtLeast(status, "draft_ready");
  const phaseAApproved =
    statusAtLeast(status, "committed") ||
    Boolean(
      estimate &&
        (estimate.approved || phaseAApprovedEstimateId === estimate.estimate_set_id),
    );
  const canApproveClaimTargets =
    status === "scenarios_ready" && !effectiveClaimTargetsApproved;
  const canCompute = status === "scenarios_ready" && effectiveClaimTargetsApproved;
  const canRestoreDraft = status === "draft_ready" && !estimate;
  const canApproveEstimate = status === "draft_ready" && Boolean(estimate) && !phaseAApproved;
  const canCommit = status === "draft_ready" && Boolean(estimate) && phaseAApproved;
  const canResolve = status === "committed" && Boolean(selectedOutcomeId);
  const closed = isMutatingClosed(status);
  const flowNodes = forecastExecutionNodes({
    status,
    approvedFraming,
    researchPackCompleted,
    researchPackRunning,
    currentResearchPackStatus,
    claimTargetsApproved: effectiveClaimTargetsApproved,
    hasEstimate: Boolean(estimate),
    phaseAApproved,
    busy,
  });
  const nextActions = [
    {
      label: "公開情報パック投入",
      status: canDispatch
        ? "実行できます"
        : statusReason(status, "フレーミング承認後に実行できます。"),
    },
    {
      label: "証拠抽出",
      status: canExtract
        ? "実行できます"
        : status === "pack_running"
          ? researchPackBlocked
            ? "Research runの状態確認が必要です。"
            : "公開情報パックの完了待ちです。"
          : statusReason(status, "公開情報パック投入後に実行できます。"),
    },
    {
      label: "シナリオ生成",
      status: canGenerate
        ? "実行できます"
        : statusReason(status, "証拠抽出後に実行できます。"),
    },
    {
      label: "Claim link承認",
      status: canApproveClaimTargets
        ? "実行できます"
        : effectiveClaimTargetsApproved
          ? "承認済みです。"
          : statusReason(status, "シナリオ生成後に実行できます。"),
    },
    {
      label: "確率計算",
      status: canCompute
        ? "実行できます"
        : canRestoreDraft
          ? "作成済みの下書き推定値を読み込めます。"
          : statusReason(status, "Claim link承認後に実行できます。"),
    },
    {
      label: "PhaseA承認",
      status: phaseAApproved
        ? "承認済みです。"
        : canApproveEstimate
          ? "実行できます"
          : statusReason(status, "確率計算後に実行できます。"),
    },
    {
      label: "コミット",
      status: canCommit
        ? "実行できます"
        : statusAtLeast(status, "committed")
          ? "コミット済みです。"
          : statusReason(status, "PhaseA承認後に実行できます。"),
    },
    {
      label: "解決",
      status: canResolve
        ? "実行できます"
        : status === "resolved"
          ? "解決済みです。"
          : statusReason(status, "PhaseAバージョンのコミット後に実行できます。"),
    },
  ];

  return (
    <section className="screen">
      <div className="screen-header forecast-detail-header">
        <div>
          <p className="forecast-detail-kicker">
            Forecast ID <code>{forecastId}</code>
          </p>
          <h1>Forecast</h1>
          <p className="screen-subtitle">{forecast?.question ?? forecastId}</p>
        </div>
        <div className="forecast-detail-actions">
          <span className="forecast-status-pill">
            {forecastStatusLabel(forecast?.status)}
          </span>
          {researchRunPath && (
            <Link to={researchRunPath} className="btn-secondary">
              Research run詳細
            </Link>
          )}
          <Link to={routes().forecastAudit(forecastId)} className="btn-secondary">
            監査ログ
          </Link>
        </div>
      </div>

      {error && (
        <div className="alert alert-error" role="alert" style={{ whiteSpace: "pre-wrap" }}>
          {error}
        </div>
      )}

      <div className="metric-grid">
        <div className="metric-card">
          <span className="metric-label">ステータス</span>
          <strong>{forecastStatusLabel(forecast?.status)}</strong>
        </div>
        <div className="metric-card">
          <span className="metric-label">フレーミング</span>
          <strong>{forecast?.approved_framing_version ? "承認済み" : "承認待ち"}</strong>
        </div>
        <div className="metric-card">
          <span className="metric-label">公開情報パック</span>
          <strong>{packStatusLabel(currentResearchPackStatus)}</strong>
        </div>
        <div className="metric-card">
          <span className="metric-label">確率エンジン</span>
          <strong>{estimate?.engine_version ?? "未計算"}</strong>
        </div>
      </div>

      {researchPackRunning && (
        <div
          className="forecast-wait-banner"
          aria-live="polite"
          aria-atomic="false"
          role="status"
        >
          <span className="forecast-wait-banner__pulse" aria-hidden="true" />
          <div className="forecast-wait-banner__body">
            <p className="forecast-wait-banner__main">
              公開情報パックを実行中です。
            </p>
            <p className="forecast-wait-banner__sub">
              {researchPackStartedLabel && (
                <>開始時刻: {researchPackStartedLabel} ・ </>
              )}
              経過: {Math.round(researchPackElapsed)}分 ・ 処理ステップ:{" "}
              {currentResearchPack?.total_tool_calls ?? 0}件
            </p>
            {researchRunPath && (
              <Link to={researchRunPath} className="forecast-inline-link">
                Research runを開く
              </Link>
            )}
          </div>
        </div>
      )}

      {status === "pack_running" && researchPackCompleted && (
        <div className="forecast-pack-notice" role="status">
          <strong>公開情報パックは完了しました。</strong>
          <span>次に証拠抽出を実行できます。</span>
          {currentResearchPack?.research_run_updated_at && (
            <span>
              最終更新: {formatStartedAt(currentResearchPack.research_run_updated_at)}
            </span>
          )}
        </div>
      )}

      {status === "pack_running" && researchPackBlocked && (
        <div className="alert alert-error" role="status">
          公開情報パックの実行状態を確認してください。
          {currentResearchPack?.done_reason && (
            <> 理由: {currentResearchPack.done_reason}</>
          )}
          {researchRunPath && (
            <>
              {" "}
              <Link to={researchRunPath} className="forecast-inline-link">
                Research runを開く
              </Link>
            </>
          )}
        </div>
      )}

      <ForecastFlowProgress
        heading="PhaseA実行フロー"
        summary="現在のForecastが、公開情報パックから確率計算・コミット・解決までのどこにいるかを示します。"
        nodes={flowNodes}
        label="Forecast実行フロー"
        layout="wrapped"
        columns={4}
      />

      <div className="button-row">
        <button
          type="button"
          className="btn-secondary"
          disabled={!!busy || !canDispatch || closed}
          title={
            canDispatch
              ? undefined
              : statusReason(status, "承認済みフレーミングが必要です。")
          }
          onClick={() =>
            runStep("pack", () =>
              dispatchCurrentStatePack(forecastId, {
                idempotencyKey: idempotencyKeys.current.pack,
              }),
            )
          }
        >
          公開情報パック投入
        </button>
        <button
          type="button"
          className="btn-secondary"
          disabled={!!busy || !canExtract || closed}
          title={
            canExtract
              ? undefined
              : status === "pack_running"
                ? "公開情報パックの完了待ちです。"
                : statusReason(status, "完了済みの公開情報パックが必要です。")
          }
          onClick={() =>
            runStep("evidence", () =>
              extractEvidence(forecastId, {
                idempotencyKey: idempotencyKeys.current.evidence,
              }),
            )
          }
        >
          証拠抽出
        </button>
        <button
          type="button"
          className="btn-secondary"
          disabled={!!busy || !canGenerate || closed}
          title={
            canGenerate ? undefined : statusReason(status, "抽出済み証拠が必要です。")
          }
          onClick={() =>
            runStep("scenarios", () =>
              generateScenarios(forecastId, {
                idempotencyKey: idempotencyKeys.current.scenarios,
              }),
            )
          }
        >
          シナリオ生成
        </button>
        <button
          type="button"
          className="btn-secondary"
          disabled={!!busy || !canApproveClaimTargets || closed}
          title={
            canApproveClaimTargets
              ? undefined
              : effectiveClaimTargetsApproved
                ? "Claim-target linkは承認済みです。"
                : statusReason(status, "生成済みシナリオが必要です。")
          }
          onClick={() =>
            runStep("claimTargets", () =>
              reviewForecast(
                forecastId,
                { action: "approve_claim_target_links" },
                { idempotencyKey: idempotencyKeys.current.claimTargets },
              ),
            )
          }
        >
          Claim link承認
        </button>
        <button
          type="button"
          className="btn-primary"
          disabled={!!busy || (!canCompute && !canRestoreDraft) || closed}
          title={
            canCompute || canRestoreDraft
              ? undefined
              : statusReason(status, "承認済みClaim linkが必要です。")
          }
          onClick={() =>
            runStep("compute", () =>
              computeProbabilities(forecastId, {
                idempotencyKey: idempotencyKeys.current.compute,
              }),
            )
          }
        >
          {canRestoreDraft ? "推定値を復元" : "確率計算"}
        </button>
      </div>

      <div className="form-panel">
        <h2>次にやること</h2>
        <div className="forecast-next-action-list">
          {nextActions.map((item) => (
            <div key={item.label} className="forecast-next-action-item">
              <span>{item.label}</span>
              <strong>{item.status}</strong>
            </div>
          ))}
        </div>
      </div>

      {estimate && (
        <div className="form-panel">
          <div className="run-card-meta">
            <span>{estimate.engine_version}</span>
            <span>{estimate.input_snapshot_hash}</span>
          </div>
          <div className="result-list">
            {estimate.estimates.map((item) => (
              <article key={item.estimate_id} className="run-card">
                <p className="run-card-title">{item.target_id}</p>
                <p>{(item.final_probability * 100).toFixed(1)}%</p>
                <p className="run-card-meta">
                  80%推定範囲 {item.uncertainty_range.lo80.toFixed(3)}-
                  {item.uncertainty_range.hi80.toFixed(3)}
                </p>
              </article>
            ))}
          </div>
          <div className="button-row">
            <button
              type="button"
              className="btn-secondary"
              disabled={!!busy || !canApproveEstimate}
              onClick={() =>
                runStep("approve", () =>
                  reviewForecast(
                    forecastId,
                    {
                      action: "approve_phase_a_version",
                      estimate_set_id: estimate.estimate_set_id,
                    },
                    { idempotencyKey: idempotencyKeys.current.approve },
                  ),
                )
              }
            >
              PhaseA承認
            </button>
            <button
              type="button"
              className="btn-primary"
              disabled={!!busy || !canCommit}
              onClick={() =>
                runStep("commit", () =>
                  commitForecastVersion(
                    forecastId,
                    {
                      estimate_set_id: estimate.estimate_set_id,
                      expected_input_snapshot_hash: estimate.input_snapshot_hash,
                    },
                    { idempotencyKey: idempotencyKeys.current.commit },
                  ),
                )
              }
            >
              コミット
            </button>
          </div>
        </div>
      )}

      {(forecast?.status === "committed" || forecast?.status === "resolved") && (
        <div className="form-panel">
          <h2>解決</h2>
          {forecast.status === "resolved" ? (
            <p>このForecastは解決済みです。</p>
          ) : (
            <>
              <label className="field">
                <span>実績結果</span>
                <select
                  value={selectedOutcomeId}
                  onChange={(event) => setSelectedOutcomeId(event.target.value)}
                >
                  {forecast.outcomes.map((outcome) => (
                    <option key={outcome.outcome_id} value={outcome.outcome_id}>
                      {outcome.label}
                    </option>
                  ))}
                </select>
              </label>
              <label className="field">
                <span>解決メモ</span>
                <textarea
                  value={resolutionNotes}
                  onChange={(event) => setResolutionNotes(event.target.value)}
                  rows={3}
                />
              </label>
              <button
                type="button"
                className="btn-primary"
                disabled={!!busy || !canResolve}
                onClick={() =>
                  runStep("resolve", () =>
                    resolveForecast(
                      forecastId,
                      {
                        outcome_id: selectedOutcomeId,
                        resolution_notes: resolutionNotes.trim() || null,
                      },
                      { idempotencyKey: idempotencyKeys.current.resolve },
                    ),
                  )
                }
              >
                解決
              </button>
            </>
          )}
          {resolution && (
            <div className="run-card-meta">
              <span>Brier {resolution.multiclass_brier.toFixed(4)}</span>
              <span>Log {resolution.log_score.toFixed(4)}</span>
              <span>{resolution.scorer_version}</span>
            </div>
          )}
        </div>
      )}
    </section>
  );
}
