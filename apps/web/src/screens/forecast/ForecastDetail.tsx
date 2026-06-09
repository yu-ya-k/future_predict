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
import { formatElapsed, useElapsed } from "../../hooks/useElapsed";
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

type CurrentStepAction =
  | Command
  | "refresh"
  | "researchRun"
  | "resolvePanel"
  | null;

const PACK_SUBMISSION_POLL_MS = 1_000;

interface CurrentStepModel {
  title: string;
  description: string;
  stateLabel: string;
  tone: "ready" | "running" | "blocked" | "done" | "neutral";
  action: CurrentStepAction;
  actionLabel?: string;
}

function stableKey(forecastId: string, action: Command): string {
  return `forecast-${forecastId}-${action}-${crypto.randomUUID()}`;
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
  submitting,
  blocked,
  available,
}: {
  done: boolean;
  active: boolean;
  submitting?: boolean;
  blocked?: boolean;
  available?: boolean;
}): ForecastFlowStatus {
  if (done) return "done";
  if (active) return "active";
  if (submitting) return "submitting";
  if (blocked) return "blocked";
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
      return "公開情報フェーズ";
    case "evidence_ready":
      return "証拠抽出済み";
    case "scenarios_ready":
      return "シナリオ生成済み";
    case "draft_ready":
      return "確率計算済み";
    case "committed":
      return "予測版確定済み";
    case "resolved":
      return "解決済み";
    default:
      return "読み込み中";
  }
}

function packStatusLabel(status: string | null | undefined): string {
  switch (status) {
    case "submitting":
      return "サーバーに登録中";
    case "running":
      return "実行中";
    case "completed":
      return "完了";
    case "needs_human_review":
      return "要確認";
    case "failed":
      return "失敗";
    case "cancelled":
      return "中断";
    case null:
    case undefined:
      return "未収集";
    default:
      return status;
  }
}

function packFlowMeta({
  currentResearchPackStatus,
  researchPackCompleted,
  researchPackRunning,
  researchPackSubmitting,
  packSubmissionPending,
}: {
  currentResearchPackStatus: string | null | undefined;
  researchPackCompleted: boolean;
  researchPackRunning: boolean;
  researchPackSubmitting: boolean;
  packSubmissionPending: boolean;
}): string {
  if (researchPackRunning) return "公開情報を収集中";
  if (researchPackSubmitting || packSubmissionPending) return "サーバーに登録中";
  if (researchPackCompleted) return "収集完了";
  switch (currentResearchPackStatus) {
    case "needs_human_review":
      return "確認が必要";
    case "failed":
      return "収集に失敗";
    case "cancelled":
      return "収集を中断";
    case null:
    case undefined:
      return "未収集";
    default:
      return "状態を確認中";
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

function deriveCurrentStep({
  status,
  currentResearchPackStatus,
  currentResearchPackPresent,
  packSubmissionPending,
  packSubmissionIsSlow,
  researchPackSubmitting,
  researchPackRunning,
  researchPackCompleted,
  researchPackBlocked,
  canDispatch,
  canExtract,
  canGenerate,
  canApproveClaimTargets,
  canCompute,
  canRestoreDraft,
  canApproveEstimate,
  canCommit,
  canResolve,
  estimatePresent,
}: {
  status: ForecastStatus | undefined;
  currentResearchPackStatus: string | null | undefined;
  currentResearchPackPresent: boolean;
  packSubmissionPending: boolean;
  packSubmissionIsSlow: boolean;
  researchPackSubmitting: boolean;
  researchPackRunning: boolean;
  researchPackCompleted: boolean;
  researchPackBlocked: boolean;
  canDispatch: boolean;
  canExtract: boolean;
  canGenerate: boolean;
  canApproveClaimTargets: boolean;
  canCompute: boolean;
  canRestoreDraft: boolean;
  canApproveEstimate: boolean;
  canCommit: boolean;
  canResolve: boolean;
  estimatePresent: boolean;
}): CurrentStepModel {
  if (!status) {
    return {
      title: "Forecastを読み込んでいます",
      description: "現在の状態を取得しています。",
      stateLabel: "読み込み中",
      tone: "neutral",
      action: null,
    };
  }

  if (packSubmissionPending) {
    return {
      title: packSubmissionIsSlow
        ? "サーバー応答待ち。まだForecast Packは確認できません"
        : "公開情報をサーバーに登録中",
      description: packSubmissionIsSlow
        ? "Research Pack作成リクエストへの応答を待っています。最新状態は自動で確認しています。"
        : "Research Packを作成するリクエストを送っています。登録されるとResearch run IDと開始時刻が表示されます。",
      stateLabel: "サーバーに登録中",
      tone: "running",
      action: "refresh",
      actionLabel: "状態を再確認",
    };
  }

  if (researchPackSubmitting) {
    return {
      title: "公開情報をサーバーに登録中",
      description:
        "Forecast Packはサーバー側で登録処理中です。Research run IDがある場合は詳細を確認できます。",
      stateLabel: "サーバーに登録中",
      tone: "running",
      action: "researchRun",
      actionLabel: "Research runを開く",
    };
  }

  if (status === "pack_running" && !currentResearchPackPresent) {
    return {
      title: "公開情報の状態確認が必要です",
      description:
        "Forecast本体は公開情報フェーズですが、Research Packがまだ取得できていません。最新状態を再取得してください。",
      stateLabel: "状態確認が必要",
      tone: "blocked",
      action: "refresh",
      actionLabel: "状態を再確認",
    };
  }

  if (researchPackBlocked) {
    const title =
      currentResearchPackStatus === "needs_human_review"
        ? "公開情報の収集に確認が必要です"
        : currentResearchPackStatus === "cancelled"
          ? "公開情報の収集が中断されました"
          : "公開情報の収集に失敗しました";
    return {
      title,
      description:
        "Research runの詳細で原因や人手確認の要否を確認し、必要な対応後にこの画面で状態を再確認してください。",
      stateLabel: packStatusLabel(currentResearchPackStatus),
      tone: "blocked",
      action: "refresh",
      actionLabel: "状態を再確認",
    };
  }

  if (researchPackRunning) {
    return {
      title: "公開情報を収集中です",
      description:
        "Deep Researchが公開情報を収集しています。完了すると次に証拠抽出へ進めます。",
      stateLabel: "実行中",
      tone: "running",
      action: "researchRun",
      actionLabel: "Research runを開く",
    };
  }

  if (status === "pack_running" && researchPackCompleted && canExtract) {
    return {
      title: "公開情報の収集が完了しました",
      description: "収集済みの公開情報から、Forecastに使う主張とソースを抽出できます。",
      stateLabel: "完了",
      tone: "ready",
      action: "evidence",
      actionLabel: "証拠を抽出",
    };
  }

  if (canDispatch) {
    return {
      title: "公開情報はまだ収集されていません",
      description:
        "承認済みフレーミングをもとに、まず公開情報の収集を開始します。",
      stateLabel: "未収集",
      tone: "ready",
      action: "pack",
      actionLabel: "公開情報の収集を開始",
    };
  }

  if (canGenerate) {
    return {
      title: "証拠抽出が完了しました",
      description: "抽出済みの主張とソースから、解決状態ごとのシナリオを生成できます。",
      stateLabel: "次はシナリオ生成",
      tone: "ready",
      action: "scenarios",
      actionLabel: "シナリオを生成",
    };
  }

  if (canApproveClaimTargets) {
    return {
      title: "主張と結果の対応確認が必要です",
      description: "生成されたシナリオと、確率計算に使う主張の対応を確認します。",
      stateLabel: "確認待ち",
      tone: "ready",
      action: "claimTargets",
      actionLabel: "主張と結果の対応を承認",
    };
  }

  if (canCompute || canRestoreDraft) {
    return {
      title: canRestoreDraft ? "推定値を復元できます" : "確率計算の準備ができました",
      description: canRestoreDraft
        ? "保存済みの下書き推定値を読み込みます。"
        : "承認済みの対応関係をもとに、PhaseAエンジンで確率を計算します。",
      stateLabel: canRestoreDraft ? "復元可能" : "計算可能",
      tone: "ready",
      action: "compute",
      actionLabel: canRestoreDraft ? "推定値を復元" : "確率を計算",
    };
  }

  if (canApproveEstimate) {
    return {
      title: "推定結果の承認待ちです",
      description: "下の推定結果を確認し、問題なければこのまま承認できます。",
      stateLabel: "承認待ち",
      tone: "ready",
      action: estimatePresent ? "approve" : null,
      actionLabel: estimatePresent ? "推定結果を承認" : undefined,
    };
  }

  if (canCommit) {
    return {
      title: "予測版を確定できます",
      description: "承認済みの推定結果を、Forecastの確定版として保存します。",
      stateLabel: "確定待ち",
      tone: "ready",
      action: estimatePresent ? "commit" : null,
      actionLabel: estimatePresent ? "予測版を確定" : undefined,
    };
  }

  if (canResolve) {
    return {
      title: "実績結果で解決できます",
      description:
        "下の解決フォームで公開情報から確認した実績結果を選び、このForecastを採点します。",
      stateLabel: "解決待ち",
      tone: "ready",
      action: "resolvePanel",
      actionLabel: "実績結果を選ぶ",
    };
  }

  if (status === "resolved") {
    return {
      title: "Forecastは解決済みです",
      description: "実績結果による採点まで完了しています。",
      stateLabel: "完了",
      tone: "done",
      action: null,
    };
  }

  if (status === "framing_pending") {
    return {
      title: "フレーミング承認待ちです",
      description: "保存済みフレーミングを承認すると、公開情報の収集を開始できます。",
      stateLabel: "承認待ち",
      tone: "neutral",
      action: null,
    };
  }

  return {
    title: "次の操作を待っています",
    description: "現在のForecast状態を確認してください。",
    stateLabel: forecastStatusLabel(status),
    tone: "neutral",
    action: null,
  };
}

function forecastExecutionNodes({
  status,
  approvedFraming,
  researchPackCompleted,
  researchPackRunning,
  researchPackBlocked,
  researchPackSubmitting,
  currentResearchPackStatus,
  claimTargetsApproved,
  hasEstimate,
  phaseAApproved,
  packSubmissionPending,
  busy,
}: {
  status: ForecastStatus | undefined;
  approvedFraming: boolean;
  researchPackCompleted: boolean;
  researchPackRunning: boolean;
  researchPackBlocked: boolean;
  researchPackSubmitting: boolean;
  currentResearchPackStatus: string | null | undefined;
  claimTargetsApproved: boolean;
  hasEstimate: boolean;
  phaseAApproved: boolean;
  packSubmissionPending: boolean;
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
      title: "公開情報の収集",
      meta: packFlowMeta({
        currentResearchPackStatus,
        researchPackCompleted,
        researchPackRunning,
        researchPackSubmitting,
        packSubmissionPending,
      }),
      status: flowStatus({
        done: researchPackCompleted || statusAtLeast(status, "evidence_ready"),
        active: researchPackRunning,
        submitting: researchPackSubmitting || packSubmissionPending,
        blocked: researchPackBlocked,
        available: status === "framing_approved",
      }),
      tone: "research",
    },
    {
      id: "evidence",
      title: "証拠を抽出",
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
      title: "シナリオを生成",
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
      title: "主張と結果の対応を承認",
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
      title: "確率を計算",
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
      title: "推定結果を承認",
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
      title: "予測版を確定",
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
      title: "実績結果で解決",
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
  const [busyStartedAt, setBusyStartedAt] = useState<Date | null>(null);
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
    setError(null);
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
  const researchPackSubmitting = currentResearchPackStatus === "submitting";
  const researchPackRunning =
    status === "pack_running" && currentResearchPackStatus === "running";
  const packSubmissionPending = busy === "pack" && !currentResearchPack;
  const packSubmissionElapsed = useElapsed(
    packSubmissionPending ? (busyStartedAt ?? undefined) : undefined,
    packSubmissionPending,
  );
  const packSubmissionIsSlow = packSubmissionElapsed >= 0.5;
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

  const shouldPollCurrentResearchPack = researchPackRunning || researchPackSubmitting;

  useEffect(() => {
    if (!shouldPollCurrentResearchPack) return undefined;
    const interval = window.setInterval(() => {
      void load().catch((err) => setError(formatForecastError(err)));
    }, researchPackSubmitting ? PACK_SUBMISSION_POLL_MS : 3_000);
    return () => window.clearInterval(interval);
  }, [shouldPollCurrentResearchPack, researchPackSubmitting, load]);

  useEffect(() => {
    if (busy !== "pack" || currentResearchPack) return undefined;
    const interval = window.setInterval(() => {
      void load().catch((err) => setError(formatForecastError(err)));
    }, PACK_SUBMISSION_POLL_MS);
    return () => window.clearInterval(interval);
  }, [busy, currentResearchPack, load]);

  useEffect(() => {
    if (busy !== "pack" || !currentResearchPack) return;
    setBusy(null);
    setBusyStartedAt(null);
  }, [busy, currentResearchPack]);

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
    setBusyStartedAt(new Date(Date.now()));
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
      setBusyStartedAt(null);
    }
  }

  function runCommand(command: Command) {
    switch (command) {
      case "pack":
        return runStep("pack", () =>
          dispatchCurrentStatePack(forecastId, {
            idempotencyKey: idempotencyKeys.current.pack,
          }),
        );
      case "evidence":
        return runStep("evidence", () =>
          extractEvidence(forecastId, {
            idempotencyKey: idempotencyKeys.current.evidence,
          }),
        );
      case "scenarios":
        return runStep("scenarios", () =>
          generateScenarios(forecastId, {
            idempotencyKey: idempotencyKeys.current.scenarios,
          }),
        );
      case "claimTargets":
        return runStep("claimTargets", () =>
          reviewForecast(
            forecastId,
            { action: "approve_claim_target_links" },
            { idempotencyKey: idempotencyKeys.current.claimTargets },
          ),
        );
      case "compute":
        return runStep("compute", () =>
          computeProbabilities(forecastId, {
            idempotencyKey: idempotencyKeys.current.compute,
          }),
        );
      case "approve":
        if (!estimate) return Promise.resolve();
        return runStep("approve", () =>
          reviewForecast(
            forecastId,
            {
              action: "approve_phase_a_version",
              estimate_set_id: estimate.estimate_set_id,
            },
            { idempotencyKey: idempotencyKeys.current.approve },
          ),
        );
      case "commit":
        if (!estimate) return Promise.resolve();
        return runStep("commit", () =>
          commitForecastVersion(
            forecastId,
            {
              estimate_set_id: estimate.estimate_set_id,
              expected_input_snapshot_hash: estimate.input_snapshot_hash,
            },
            { idempotencyKey: idempotencyKeys.current.commit },
          ),
        );
      case "resolve":
        return runStep("resolve", () =>
          resolveForecast(
            forecastId,
            {
              outcome_id: selectedOutcomeId,
              resolution_notes: resolutionNotes.trim() || null,
            },
            { idempotencyKey: idempotencyKeys.current.resolve },
          ),
        );
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
  const flowNodes = forecastExecutionNodes({
    status,
    approvedFraming,
    researchPackCompleted,
    researchPackRunning,
    researchPackBlocked,
    researchPackSubmitting,
    currentResearchPackStatus,
    claimTargetsApproved: effectiveClaimTargetsApproved,
    hasEstimate: Boolean(estimate),
    phaseAApproved,
    packSubmissionPending,
    busy,
  });
  const currentStep = deriveCurrentStep({
    status,
    currentResearchPackStatus,
    currentResearchPackPresent: Boolean(currentResearchPack),
    packSubmissionPending,
    packSubmissionIsSlow,
    researchPackSubmitting,
    researchPackRunning,
    researchPackCompleted,
    researchPackBlocked,
    canDispatch,
    canExtract,
    canGenerate,
    canApproveClaimTargets,
    canCompute,
    canRestoreDraft,
    canApproveEstimate,
    canCommit,
    canResolve,
    estimatePresent: Boolean(estimate),
  });
  const researchPackUpdatedLabel = formatStartedAt(
    currentResearchPack?.research_run_updated_at ??
      currentResearchPack?.pack_updated_at ??
      undefined,
  );
  const forecastDisplayStatus = forecastStatusLabel(status);
  const currentStepDetails = [
    { label: "Forecast本体状態", value: forecastDisplayStatus },
    {
      label: "公開情報パック状態",
      value: packSubmissionPending ? "登録中" : packStatusLabel(currentResearchPackStatus),
    },
    currentResearchPack?.research_run_id
      ? { label: "Research run ID", value: currentResearchPack.research_run_id }
      : null,
    currentResearchPack?.research_run_status
      ? { label: "Research run状態", value: currentResearchPack.research_run_status }
      : null,
    researchPackStartedLabel
      ? { label: "開始時刻", value: researchPackStartedLabel }
      : null,
    packSubmissionPending
      ? { label: "経過時間", value: formatElapsed(packSubmissionElapsed) }
      : researchPackRunning
        ? { label: "経過時間", value: `${Math.round(researchPackElapsed)}分` }
        : null,
    researchPackUpdatedLabel
      ? { label: "最終更新", value: researchPackUpdatedLabel }
      : null,
    currentResearchPack
      ? {
          label: "処理ステップ",
          value: `${currentResearchPack.total_tool_calls ?? 0}件`,
        }
      : null,
    currentResearchPack?.done_reason
      ? { label: "完了理由", value: currentResearchPack.done_reason }
      : null,
    currentResearchPack?.needs_human_review
      ? { label: "人による確認", value: "必要" }
      : null,
  ].filter(
    (item): item is { label: string; value: string } =>
      Boolean(item && item.value),
  );

  function handleCurrentStepAction(action: CurrentStepAction) {
    if (!action) return;
    if (action === "refresh") {
      void load().catch((err) => setError(formatForecastError(err)));
      return;
    }
    if (action === "resolvePanel") {
      document
        .getElementById("forecast-resolve-panel")
        ?.scrollIntoView({ block: "start", behavior: "smooth" });
      return;
    }
    if (action === "researchRun") return;
    void runCommand(action);
  }

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
            {forecastDisplayStatus}
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
          <span className="metric-label">Forecast本体</span>
          <strong>{forecastDisplayStatus}</strong>
        </div>
        <div className="metric-card">
          <span className="metric-label">フレーミング</span>
          <strong>{forecast?.approved_framing_version ? "承認済み" : "承認待ち"}</strong>
        </div>
        <div className="metric-card">
          <span className="metric-label">公開情報パック</span>
          <strong>
            {packSubmissionPending ? "登録中" : packStatusLabel(currentResearchPackStatus)}
          </strong>
        </div>
        <div className="metric-card">
          <span className="metric-label">確率エンジン</span>
          <strong>{estimate?.engine_version ?? "未計算"}</strong>
        </div>
      </div>

      <section
        className={`forecast-current-step forecast-current-step--${currentStep.tone}`}
        aria-labelledby="forecast-current-step-heading"
      >
        <div className="forecast-current-step__header">
          <div>
            <span className="metric-label">現在のステップ</span>
            <h2 id="forecast-current-step-heading">{currentStep.title}</h2>
          </div>
          <span className="forecast-current-step__state">
            {currentStep.stateLabel}
          </span>
        </div>
        <p className="forecast-current-step__description">
          {currentStep.description}
        </p>
        {currentStep.action && currentStep.actionLabel && (
          <div className="forecast-current-step__action">
            {currentStep.action === "researchRun" && researchRunPath ? (
              <Link to={researchRunPath} className="btn-primary">
                {currentStep.actionLabel}
              </Link>
            ) : (
              <button
                type="button"
                className="btn-primary"
                disabled={!!busy && currentStep.action !== "refresh"}
                onClick={() =>
                  handleCurrentStepAction(
                    currentStep.action === "researchRun" ? "refresh" : currentStep.action,
                  )
                }
              >
                {currentStep.action === "researchRun"
                  ? "状態を再確認"
                  : currentStep.actionLabel}
              </button>
            )}
          </div>
        )}
        <dl className="forecast-current-step__details">
          {currentStepDetails.map((item) => (
            <div key={item.label} className="forecast-current-step__detail">
              <dt>{item.label}</dt>
              <dd>
                {item.label === "Research run ID" && researchRunPath ? (
                  <Link to={researchRunPath} className="forecast-inline-link">
                    {item.value}
                  </Link>
                ) : (
                  item.value
                )}
              </dd>
            </div>
          ))}
        </dl>
      </section>

      <ForecastFlowProgress
        heading="全体フロー"
        summary="Forecastが解決までのどこにいるかを確認できます。操作は上の現在ステップから行います。"
        nodes={flowNodes}
        label="Forecast実行フロー"
        layout="wrapped"
        columns={4}
      />

      {estimate && (
        <div className="form-panel" id="forecast-estimate-panel">
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
        </div>
      )}

      {(forecast?.status === "committed" || forecast?.status === "resolved") && (
        <div className="form-panel" id="forecast-resolve-panel">
          <h2>実績結果で解決</h2>
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
                onClick={() => void runCommand("resolve")}
              >
                実績結果で解決
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
