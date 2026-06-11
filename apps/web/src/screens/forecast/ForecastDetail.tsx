import { useCallback, useEffect, useRef, useState } from "react";

import "./forecast.css";
import {
  commitForecastVersion,
  computeProbabilities,
  computeProjection,
  dispatchCurrentStatePack,
  dispatchDefaultResearchPacks,
  extractEvidence,
  generateScenarios,
  approveProjection,
  getForecast,
  getForecastEstimateSet,
  getCurrentProjection,
  getManualResearchPackPrompt,
  importManualResearchPack,
  rerunForecastResearchPack,
  resolveForecast,
  reviewForecast,
} from "../../api/forecast";
import { MetricCard } from "../../components";
import { copyTextToClipboard } from "../../lib/clipboard";
import { Link, routes } from "../../router";
import { formatElapsed, useElapsed } from "../../hooks/useElapsed";
import type {
  EstimateSetResponse,
  ForecastCurrentResearchPack,
  ForecastDetail as ForecastDetailType,
  ForecastPackRole,
  ManualResearchPackPromptResponse,
  ProjectionSetResponse,
  ForecastStatus,
  ResolveForecastResponse,
} from "../../types";
import { formatForecastError } from "./errors";
import {
  ForecastFlowProgress,
  type ForecastFlowNode,
  type ForecastFlowStatus,
} from "./ForecastFlowProgress";
import {
  forecastStatusLabel,
  forecastStatusTone,
  localizePackStatus,
} from "./forecastStatus";
import {
  EvidenceBoard,
  ForecastReport,
  PackCollectionPanel,
  ProbabilityPanel,
  ScenarioMap,
} from "./PhaseBPanels";

type Command =
  | "pack"
  | "manualPack"
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
const MANUAL_PROMPT_COPY_FAILED =
  "コピーできませんでした。Prompt欄を選択してコピーするか、Markdownでダウンロードしてください。";
const DEFAULT_PACK_ROLES: ForecastPackRole[] = [
  "current_state",
  "base_rate",
  "drivers",
  "counter_evidence",
  "signals",
];

interface CurrentStepModel {
  title: string;
  description: string;
  stateLabel: string;
  tone: "ready" | "running" | "blocked" | "done" | "neutral";
  action: CurrentStepAction;
  actionLabel?: string;
}

type CollectionMode = "auto" | "manual";

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

function isRemoteResearchRunning(
  pack: ForecastCurrentResearchPack | null,
  effectiveStatus: string | null | undefined,
): boolean {
  return (
    effectiveStatus === "running" &&
    Boolean(pack?.deep_research_started_at) &&
    (pack?.research_run_status === "waiting_deep_research" ||
      pack?.research_run_status === "collecting")
  );
}

function isWaitingForRemoteSubmit(
  pack: ForecastCurrentResearchPack | null,
  effectiveStatus: string | null | undefined,
): boolean {
  return (
    Boolean(pack) &&
    !pack?.deep_research_started_at &&
    (effectiveStatus === "submitting" || effectiveStatus === "running") &&
    (pack?.research_run_status === "queued" ||
      pack?.research_run_status === "submitted" ||
      pack?.research_run_status === "waiting_deep_research" ||
      pack?.research_run_status === "collecting")
  );
}

function packEffectiveStatus(pack: ForecastCurrentResearchPack | null): string | null {
  return pack?.effective_status ?? pack?.pack_status ?? null;
}

function packUpdatedTime(pack: ForecastCurrentResearchPack): number {
  const time = Date.parse(pack.pack_updated_at || pack.pack_created_at);
  return Number.isNaN(time) ? 0 : time;
}

function sortedRecentPacks(
  packs: ForecastCurrentResearchPack[],
): ForecastCurrentResearchPack[] {
  return [...packs].sort((a, b) => packUpdatedTime(b) - packUpdatedTime(a));
}

function preferredPackForRole(
  current: ForecastCurrentResearchPack | undefined,
  candidate: ForecastCurrentResearchPack,
): ForecastCurrentResearchPack {
  if (!current) return candidate;
  const currentAttempt = current.attempt_no ?? 1;
  const candidateAttempt = candidate.attempt_no ?? 1;
  if (candidateAttempt !== currentAttempt) {
    return candidateAttempt > currentAttempt ? candidate : current;
  }
  return packUpdatedTime(candidate) > packUpdatedTime(current) ? candidate : current;
}

function activeDefaultPackByRole(
  packs: ForecastCurrentResearchPack[],
): Map<ForecastPackRole, ForecastCurrentResearchPack> {
  const byRole = new Map<ForecastPackRole, ForecastCurrentResearchPack>();
  for (const pack of packs) {
    if (!pack.pack_role || !DEFAULT_PACK_ROLES.includes(pack.pack_role)) continue;
    byRole.set(pack.pack_role, preferredPackForRole(byRole.get(pack.pack_role), pack));
  }
  return byRole;
}

function uniqueForecastPacks(
  forecast: ForecastDetailType | null,
): ForecastCurrentResearchPack[] {
  const packs = [...(forecast?.research_packs ?? [])];
  const current = forecast?.current_research_pack ?? null;
  if (current && !packs.some((pack) => pack.pack_id === current.pack_id)) {
    packs.push(current);
  }
  return packs;
}

function choosePrimaryPack(
  packs: ForecastCurrentResearchPack[],
  fallback: ForecastCurrentResearchPack | null,
): ForecastCurrentResearchPack | null {
  const recent = sortedRecentPacks(packs);
  return (
    recent.find((pack) => {
      const status = packEffectiveStatus(pack);
      return status === "running" || status === "submitting";
    }) ??
    recent.find((pack) => {
      const status = packEffectiveStatus(pack);
      return (
        status === "failed" ||
        status === "cancelled" ||
        status === "needs_human_review"
      );
    }) ??
    recent.find((pack) => packEffectiveStatus(pack) === "completed") ??
    fallback
  );
}

interface ForecastProgressModel {
  packs: ForecastCurrentResearchPack[];
  activeDefaultPacksByRole: Map<ForecastPackRole, ForecastCurrentResearchPack>;
  primaryPack: ForecastCurrentResearchPack | null;
  phaseBStarted: boolean;
  phaseBDefaultSetPresent: boolean;
  missingDefaultRoles: ForecastPackRole[];
  completedDefaultPackCount: number;
  currentResearchPackStatus: string | null | undefined;
  researchPackCompleted: boolean;
  researchPackBlocked: boolean;
  researchPackRunning: boolean;
  researchPackSubmitting: boolean;
  researchPackSubmitWaiting: boolean;
  researchPackSubmitStalled: boolean;
  shouldPoll: boolean;
  shouldUsePhaseBEngine: boolean;
}

function deriveForecastProgress(
  forecast: ForecastDetailType | null,
): ForecastProgressModel {
  const isProjectionForecast = forecast?.forecast_mode === "scenario_projection";
  const packs = uniqueForecastPacks(forecast);
  const activePacks = packs.filter((pack) => pack.is_active !== false);
  const activeDefaultPacksByRole = activeDefaultPackByRole(activePacks);
  const phaseBStarted =
    !isProjectionForecast &&
    DEFAULT_PACK_ROLES.some(
      (role) => role !== "current_state" && activeDefaultPacksByRole.has(role),
    );
  const phaseBDefaultSetPresent =
    !isProjectionForecast &&
    DEFAULT_PACK_ROLES.every((role) => activeDefaultPacksByRole.has(role));
  const missingDefaultRoles = phaseBStarted
    ? DEFAULT_PACK_ROLES.filter((role) => !activeDefaultPacksByRole.has(role))
    : [];
  const defaultPacks = DEFAULT_PACK_ROLES.map((role) =>
    activeDefaultPacksByRole.get(role),
  ).filter((pack): pack is ForecastCurrentResearchPack => Boolean(pack));
  const primaryPack = phaseBStarted
    ? choosePrimaryPack(defaultPacks, forecast?.current_research_pack ?? null)
    : (forecast?.current_research_pack ??
      choosePrimaryPack(activePacks, null));
  const statuses = phaseBStarted
    ? defaultPacks.map((pack) => packEffectiveStatus(pack))
    : [packEffectiveStatus(primaryPack) ?? forecast?.current_research_pack_status ?? null];
  const completedDefaultPackCount = DEFAULT_PACK_ROLES.filter(
    (role) => activeDefaultPacksByRole.get(role)?.effective_status === "completed",
  ).length;
  const anyWaiting = (phaseBStarted ? defaultPacks : primaryPack ? [primaryPack] : []).some(
    (pack) => isWaitingForRemoteSubmit(pack, packEffectiveStatus(pack)),
  );
  const anySubmitting = statuses.some((status) => status === "submitting") && !anyWaiting;
  const anyRunning = (phaseBStarted ? defaultPacks : primaryPack ? [primaryPack] : []).some(
    (pack) => isRemoteResearchRunning(pack, packEffectiveStatus(pack)),
  );
  const anyBlocked = statuses.some(
    (status) =>
      status === "failed" ||
      status === "cancelled" ||
      status === "needs_human_review",
  );
  const defaultSetGap = phaseBStarted && !phaseBDefaultSetPresent;
  const currentResearchPackStatus = phaseBStarted
    ? defaultSetGap
      ? "default_packs_missing"
      : anyBlocked
        ? statuses.find(
            (status) =>
              status === "failed" ||
              status === "cancelled" ||
              status === "needs_human_review",
          )
        : anyRunning
          ? "running"
          : anyWaiting
            ? "running"
            : anySubmitting
              ? "submitting"
              : phaseBDefaultSetPresent && completedDefaultPackCount === DEFAULT_PACK_ROLES.length
                ? "completed"
                : packEffectiveStatus(primaryPack)
    : (packEffectiveStatus(primaryPack) ?? forecast?.current_research_pack_status);
  const researchPackCompleted = phaseBStarted
    ? phaseBDefaultSetPresent && completedDefaultPackCount === DEFAULT_PACK_ROLES.length
    : currentResearchPackStatus === "completed";
  const researchPackBlocked =
    anyBlocked || (defaultSetGap && !anyRunning && !anyWaiting && !anySubmitting);
  const researchPackSubmitStalled =
    researchPackBlocked && primaryPack?.done_reason === "deep_research_submit_stalled";

  return {
    packs,
    activeDefaultPacksByRole,
    primaryPack,
    phaseBStarted,
    phaseBDefaultSetPresent,
    missingDefaultRoles,
    completedDefaultPackCount,
    currentResearchPackStatus,
    researchPackCompleted,
    researchPackBlocked,
    researchPackRunning: anyRunning,
    researchPackSubmitting: anySubmitting,
    researchPackSubmitWaiting: anyWaiting,
    researchPackSubmitStalled,
    shouldPoll: anyRunning || anySubmitting || anyWaiting,
    shouldUsePhaseBEngine: !isProjectionForecast && phaseBDefaultSetPresent,
  };
}

function packFlowMeta({
  currentResearchPackStatus,
  researchPackCompleted,
  researchPackRunning,
  researchPackSubmitting,
  researchPackSubmitWaiting,
  packSubmissionPending,
  phaseBStarted,
  completedDefaultPackCount,
  missingDefaultPacks,
}: {
  currentResearchPackStatus: string | null | undefined;
  researchPackCompleted: boolean;
  researchPackRunning: boolean;
  researchPackSubmitting: boolean;
  researchPackSubmitWaiting: boolean;
  packSubmissionPending: boolean;
  phaseBStarted: boolean;
  completedDefaultPackCount: number;
  missingDefaultPacks: number;
}): string {
  if (researchPackRunning) return "公開情報を収集中";
  if (researchPackSubmitWaiting) return "Deep Research送信待ち";
  if (researchPackSubmitting || packSubmissionPending) return "サーバーに登録中";
  if (phaseBStarted && missingDefaultPacks > 0) return "必要Pack不足";
  if (researchPackCompleted) return "収集完了";
  if (phaseBStarted) return `${completedDefaultPackCount}/5 Pack完了`;
  switch (currentResearchPackStatus) {
    case "default_packs_missing":
      return "必要Pack不足";
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
  researchPackSubmitWaiting,
  researchPackSubmitStalled,
  researchPackRunning,
  researchPackCompleted,
  researchPackBlocked,
  phaseBStarted,
  missingDefaultPacks,
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
  probabilityEngine,
  isPhaseBEstimate,
  isProjectionForecast,
}: {
  status: ForecastStatus | undefined;
  currentResearchPackStatus: string | null | undefined;
  currentResearchPackPresent: boolean;
  packSubmissionPending: boolean;
  packSubmissionIsSlow: boolean;
  researchPackSubmitting: boolean;
  researchPackSubmitWaiting: boolean;
  researchPackSubmitStalled: boolean;
  researchPackRunning: boolean;
  researchPackCompleted: boolean;
  researchPackBlocked: boolean;
  phaseBStarted: boolean;
  missingDefaultPacks: number;
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
  probabilityEngine: "phase_a_v1" | "phase_b_v1";
  isPhaseBEstimate: boolean;
  isProjectionForecast: boolean;
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

  if (researchPackSubmitWaiting) {
    return {
      title: "Deep Researchへの送信を待っています",
      description:
        "Research runは作成済みです。Deep Researchへの投入完了を待っており、状態は自動で確認しています。",
      stateLabel: "Deep Research送信待ち",
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

  if (phaseBStarted && missingDefaultPacks > 0 && !researchPackRunning) {
    return {
      title: "必要な5 Packがそろっていません",
      description:
        "Phase Bの証拠抽出には既定5 Packのactive packが必要です。Pack Collectionで不足しているpackを確認してください。",
      stateLabel: "必要Pack不足",
      tone: "blocked",
      action: "refresh",
      actionLabel: "状態を再確認",
    };
  }

  if (researchPackBlocked) {
    if (researchPackSubmitStalled) {
      return {
        title: "Deep Research送信が開始されていません",
        description:
          "Research runは作成されましたが、Deep Researchへの投入開始を確認できません。Research run詳細で理由を確認してください。",
        stateLabel: "要確認",
        tone: "blocked",
        action: "researchRun",
        actionLabel: "Research runを開く",
      };
    }
    const title =
      currentResearchPackStatus === "needs_human_review"
        ? "公開情報の収集に確認が必要です"
        : currentResearchPackStatus === "cancelled"
          ? "公開情報の収集が中断されました"
          : "公開情報の収集に失敗しました";
    return {
      title,
      description:
        "Research runの詳細で原因や人手確認の要否を確認してください。対応後にこの画面へ戻ると最新状態を確認できます。",
      stateLabel: localizePackStatus(currentResearchPackStatus),
      tone: "blocked",
      action: "researchRun",
      actionLabel: "Research run詳細",
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
      title: "公開情報の収集方法を選択",
      description:
        "アプリで自動収集するか、ChatGPT Deep Researchで手動収集した結果を取り込めます。",
      stateLabel: "未収集",
      tone: "ready",
      action: null,
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
      title: canRestoreDraft
        ? isProjectionForecast
          ? "Projection下書きを復元できます"
          : "推定値を復元できます"
        : isProjectionForecast
          ? "Projection作成の準備ができました"
          : "確率計算の準備ができました",
      description: canRestoreDraft
        ? isProjectionForecast
          ? "保存済みのProjection下書きを読み込みます。"
          : "保存済みの下書き推定値を読み込みます。"
        : isProjectionForecast
          ? "抽出済みの公開情報から、シナリオ別の指標レンジを作成します。"
          : `承認済みの対応関係をもとに、${probabilityEngine}で確率を計算します。`,
      stateLabel: canRestoreDraft ? "復元可能" : "計算可能",
      tone: "ready",
      action: "compute",
      actionLabel: canRestoreDraft
        ? isProjectionForecast
          ? "Projectionを復元"
          : "推定値を復元"
        : isProjectionForecast
          ? "Projectionを作成"
          : "確率を計算",
    };
  }

  if (canApproveEstimate) {
    return {
      title: isProjectionForecast
        ? "Projection公開の承認待ちです"
        : isPhaseBEstimate
        ? "確率公開の承認待ちです"
        : "推定結果の承認待ちです",
      description: isProjectionForecast
        ? "シナリオと指標レンジを確認し、問題なければ公開承認できます。"
        : isPhaseBEstimate
        ? "下の推定結果を確認し、問題なければ公開承認できます。"
        : "下の推定結果を確認し、問題なければこのまま承認できます。",
      stateLabel: "承認待ち",
      tone: "ready",
      action: estimatePresent ? "approve" : null,
      actionLabel: estimatePresent
        ? isProjectionForecast
          ? "Projection公開を承認"
          : isPhaseBEstimate
          ? "確率公開を承認"
          : "推定結果を承認"
        : undefined,
    };
  }

  if (canCommit) {
    return {
      title: "予測版を確定できます",
      description: isProjectionForecast
        ? "承認済みのProjectionを、Forecastの確定版として保存します。"
        : "承認済みの推定結果を、Forecastの確定版として保存します。",
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
  researchPackSubmitWaiting,
  currentResearchPackStatus,
  phaseBStarted,
  completedDefaultPackCount,
  missingDefaultPacks,
  claimTargetsApproved,
  hasEstimate,
  estimateApproved,
  packSubmissionPending,
  busy,
  probabilityEngine,
  isPhaseBEstimate,
  isProjectionForecast,
}: {
  status: ForecastStatus | undefined;
  approvedFraming: boolean;
  researchPackCompleted: boolean;
  researchPackRunning: boolean;
  researchPackBlocked: boolean;
  researchPackSubmitting: boolean;
  researchPackSubmitWaiting: boolean;
  currentResearchPackStatus: string | null | undefined;
  phaseBStarted: boolean;
  completedDefaultPackCount: number;
  missingDefaultPacks: number;
  claimTargetsApproved: boolean;
  hasEstimate: boolean;
  estimateApproved: boolean;
  packSubmissionPending: boolean;
  busy: Command | null;
  probabilityEngine: "phase_a_v1" | "phase_b_v1";
  isPhaseBEstimate: boolean;
  isProjectionForecast: boolean;
}): ForecastFlowNode[] {
  const isResolved = status === "resolved";
  const estimateReady = hasEstimate || statusAtLeast(status, "draft_ready");
  if (isProjectionForecast) {
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
        meta: researchPackCompleted
          ? "current_state Pack完了"
          : researchPackRunning
            ? "current_state Packを収集中"
            : "current_state Pack",
        status: flowStatus({
          done: researchPackCompleted || statusAtLeast(status, "evidence_ready"),
          active: researchPackRunning,
          submitting: researchPackSubmitting || researchPackSubmitWaiting || packSubmissionPending,
          blocked: researchPackBlocked,
          available: status === "framing_approved",
        }),
        statusLabel: researchPackSubmitWaiting ? "Deep Research送信待ち" : undefined,
        tone: "research",
      },
      {
        id: "evidence",
        title: "証拠を抽出",
        meta: "公開情報からProjection入力を抽出",
        status: flowStatus({
          done: statusAtLeast(status, "evidence_ready"),
          active: busy === "evidence",
          available: status === "pack_running" && researchPackCompleted,
        }),
        tone: "review",
      },
      {
        id: "compute",
        title: "Projectionを作成",
        meta: estimateReady ? "下書きProjectionあり" : "phase_c_v1で作成",
        status: flowStatus({
          done: estimateReady,
          active: busy === "compute",
          available: status === "evidence_ready",
        }),
        tone: "verify",
      },
      {
        id: "approve-estimate",
        title: "Projection公開を承認",
        meta: estimateApproved ? "Projection承認済み" : "シナリオと指標レンジの確認待ち",
        status: flowStatus({
          done: estimateApproved || statusAtLeast(status, "committed"),
          active: busy === "approve",
          available: status === "draft_ready" && estimateReady && !estimateApproved,
        }),
        tone: "review",
      },
      {
        id: "commit",
        title: "予測版を確定",
        meta: statusAtLeast(status, "committed")
          ? "バージョン固定済み"
          : "承認済みProjectionをバージョン化",
        status: flowStatus({
          done: statusAtLeast(status, "committed"),
          active: busy === "commit",
          available: status === "draft_ready" && estimateApproved,
        }),
        tone: "finalize",
      },
    ];
  }
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
        researchPackSubmitWaiting,
        packSubmissionPending,
        phaseBStarted,
        completedDefaultPackCount,
        missingDefaultPacks,
      }),
      status: flowStatus({
        done: researchPackCompleted || statusAtLeast(status, "evidence_ready"),
        active: researchPackRunning,
        submitting: researchPackSubmitting || researchPackSubmitWaiting || packSubmissionPending,
        blocked: researchPackBlocked,
        available: status === "framing_approved",
      }),
      statusLabel: researchPackSubmitWaiting ? "Deep Research送信待ち" : undefined,
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
      meta: "結果別のシナリオを生成",
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
      meta: estimateReady ? "下書き推定値あり" : `${probabilityEngine}で計算`,
      status: flowStatus({
        done: estimateReady,
        active: busy === "compute",
        available: status === "scenarios_ready" && claimTargetsApproved,
      }),
      tone: "verify",
    },
    {
      id: "approve-estimate",
      title: isPhaseBEstimate ? "確率公開を承認" : "推定結果を承認",
      meta: estimateApproved
        ? "推定結果承認済み"
        : isPhaseBEstimate
          ? "確率公開の承認待ち"
          : "下書き推定値の承認待ち",
      status: flowStatus({
        done: estimateApproved || statusAtLeast(status, "committed"),
        active: busy === "approve",
        available: status === "draft_ready" && estimateReady && !estimateApproved,
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
        available: status === "draft_ready" && estimateApproved,
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
  const [projectionSet, setProjectionSet] = useState<ProjectionSetResponse | null>(null);
  const [claimTargetsApproved, setClaimTargetsApproved] = useState(false);
  const [approvedEstimateSetId, setApprovedEstimateSetId] = useState<string | null>(null);
  const [resolution, setResolution] = useState<ResolveForecastResponse | null>(null);
  const [selectedOutcomeId, setSelectedOutcomeId] = useState("");
  const [resolutionNotes, setResolutionNotes] = useState("");
  const [busy, setBusy] = useState<Command | null>(null);
  const [busyStartedAt, setBusyStartedAt] = useState<Date | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [collectionMode, setCollectionMode] = useState<CollectionMode>("auto");
  const [manualRecoveryOpen, setManualRecoveryOpen] = useState(false);
  const [manualPrompt, setManualPrompt] =
    useState<ManualResearchPackPromptResponse | null>(null);
  const [manualPromptLoading, setManualPromptLoading] = useState(false);
  const [manualReportText, setManualReportText] = useState("");
  const [manualReportFile, setManualReportFile] = useState<File | null>(null);
  const [manualPromptCopyStatus, setManualPromptCopyStatus] = useState<{
    message: string;
    failed: boolean;
  } | null>(null);
  const manualPromptRequestId = useRef(0);
  const loadRequestId = useRef(0);
  const currentForecastIdRef = useRef(forecastId);
  const manualReportFileInputRef = useRef<HTMLInputElement | null>(null);
  const manualPromptTextareaRef = useRef<HTMLTextAreaElement | null>(null);
  currentForecastIdRef.current = forecastId;
  const idempotencyKeys = useRef<Record<Command, string>>({
    pack: stableKey(forecastId, "pack"),
    manualPack: stableKey(forecastId, "manualPack"),
    evidence: stableKey(forecastId, "evidence"),
    scenarios: stableKey(forecastId, "scenarios"),
    claimTargets: stableKey(forecastId, "claimTargets"),
    compute: stableKey(forecastId, "compute"),
    approve: stableKey(forecastId, "approve"),
    commit: stableKey(forecastId, "commit"),
    resolve: stableKey(forecastId, "resolve"),
  });

  useEffect(() => {
    manualPromptRequestId.current += 1;
    setForecast(null);
    setEstimate(null);
    setProjectionSet(null);
    setClaimTargetsApproved(false);
    setApprovedEstimateSetId(null);
    setResolution(null);
    setSelectedOutcomeId("");
    setResolutionNotes("");
    setBusy(null);
    setBusyStartedAt(null);
    setError(null);
    setCollectionMode("auto");
    setManualRecoveryOpen(false);
    setManualPrompt(null);
    setManualPromptLoading(false);
    setManualReportText("");
    setManualReportFile(null);
    setManualPromptCopyStatus(null);
    if (manualReportFileInputRef.current) {
      manualReportFileInputRef.current.value = "";
    }
    idempotencyKeys.current = {
      pack: stableKey(forecastId, "pack"),
      manualPack: stableKey(forecastId, "manualPack"),
      evidence: stableKey(forecastId, "evidence"),
      scenarios: stableKey(forecastId, "scenarios"),
      claimTargets: stableKey(forecastId, "claimTargets"),
      compute: stableKey(forecastId, "compute"),
      approve: stableKey(forecastId, "approve"),
      commit: stableKey(forecastId, "commit"),
      resolve: stableKey(forecastId, "resolve"),
    };
  }, [forecastId]);

  const load = useCallback(async () => {
    const requestId = loadRequestId.current + 1;
    loadRequestId.current = requestId;
    const requestForecastId = forecastId;
    const isCurrentRequest = () =>
      loadRequestId.current === requestId &&
      currentForecastIdRef.current === requestForecastId;

    try {
      const nextForecast = await getForecast(requestForecastId);
      if (!isCurrentRequest()) return;

      setForecast(nextForecast);
      if (
        nextForecast.forecast_mode === "discrete_outcome" &&
        hasEstimateSet(nextForecast.status)
      ) {
        const nextEstimate = await getForecastEstimateSet(requestForecastId);
        if (!isCurrentRequest()) return;
        setEstimate(nextEstimate);
        setProjectionSet(null);
      } else if (
        nextForecast.forecast_mode === "scenario_projection" &&
        hasEstimateSet(nextForecast.status)
      ) {
        const nextProjection =
          nextForecast.current_projection_set ??
          (await getCurrentProjection(requestForecastId));
        if (!isCurrentRequest()) return;
        setProjectionSet(nextProjection);
        setEstimate(null);
      } else {
        setEstimate(null);
        setProjectionSet(null);
      }

      setError(null);
    } catch (err) {
      if (isCurrentRequest()) throw err;
    }
  }, [forecastId]);

  useEffect(() => {
    void load().catch((err) => setError(formatForecastError(err)));
  }, [load]);

  const status = forecast?.status;
  const approvedFraming = Boolean(forecast?.approved_framing_version);
  const forecastProgress = deriveForecastProgress(forecast);
  const currentResearchPack = forecastProgress.primaryPack;
  const currentResearchPackStatus = forecastProgress.currentResearchPackStatus;
  const researchPackCompleted = forecastProgress.researchPackCompleted;
  const researchPackBlocked = forecastProgress.researchPackBlocked;
  const researchPackSubmitWaiting = forecastProgress.researchPackSubmitWaiting;
  const researchPackSubmitting = forecastProgress.researchPackSubmitting;
  const researchPackSubmitStalled = forecastProgress.researchPackSubmitStalled;
  const researchPackRunning =
    status === "pack_running" && forecastProgress.researchPackRunning;
  const packSubmissionPending = busy === "pack" && !currentResearchPack;
  const probabilityEngine = forecastProgress.shouldUsePhaseBEngine
    ? "phase_b_v1"
    : "phase_a_v1";
  const packSubmissionElapsed = useElapsed(
    packSubmissionPending ? (busyStartedAt ?? undefined) : undefined,
    packSubmissionPending,
  );
  const packSubmissionIsSlow = packSubmissionElapsed >= 0.5;
  const researchPackStartedAt = currentResearchPack?.deep_research_started_at ?? undefined;
  const researchPackSubmitStartedAt =
    currentResearchPack?.research_run_created_at ??
    currentResearchPack?.pack_created_at ??
    undefined;
  const researchPackElapsed = useElapsed(researchPackStartedAt, researchPackRunning);
  const researchPackSubmitElapsed = useElapsed(
    researchPackSubmitStartedAt,
    researchPackSubmitWaiting,
  );
  const researchPackStartedLabel = formatStartedAt(researchPackStartedAt);
  const researchPackSubmitStartedLabel = formatStartedAt(researchPackSubmitStartedAt);
  const researchRunPath = currentResearchPack?.research_run_id
    ? routes().monitor(currentResearchPack.research_run_id)
    : null;
  const researchRunLinkLabel =
    currentResearchPack?.research_run_status === "completed" &&
    (currentResearchPack.total_tool_calls ?? 0) === 0
      ? "取り込み記録"
      : "Research run詳細";

  const shouldPollCurrentResearchPack = forecastProgress.shouldPoll;

  useEffect(() => {
    if (!shouldPollCurrentResearchPack) return undefined;
    const interval = window.setInterval(() => {
      void load().catch((err) => setError(formatForecastError(err)));
    }, researchPackSubmitting || researchPackSubmitWaiting ? PACK_SUBMISSION_POLL_MS : 3_000);
    return () => window.clearInterval(interval);
  }, [
    shouldPollCurrentResearchPack,
    researchPackSubmitting,
    researchPackSubmitWaiting,
    load,
  ]);

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
    if (!researchPackCompleted) return;
    setManualRecoveryOpen(false);
    setManualReportText("");
    setManualReportFile(null);
    if (manualReportFileInputRef.current) {
      manualReportFileInputRef.current.value = "";
    }
  }, [researchPackCompleted]);

  useEffect(() => {
    if (!forecast) return;
    const outcomeIds = forecast.outcomes.map((outcome) => outcome.outcome_id);
    if (outcomeIds.length === 0) {
      if (selectedOutcomeId) setSelectedOutcomeId("");
      return;
    }
    if (!selectedOutcomeId || !outcomeIds.includes(selectedOutcomeId)) {
      setSelectedOutcomeId(outcomeIds[0]);
    }
  }, [forecast, selectedOutcomeId]);

  useEffect(() => {
    if (forecast?.status && forecast.status !== "scenarios_ready") {
      setClaimTargetsApproved(false);
    }
  }, [forecast?.status]);

  async function runStep(step: Command, fn: () => Promise<unknown>): Promise<boolean> {
    setBusyStartedAt(new Date(Date.now()));
    setBusy(step);
    setError(null);
    try {
      const result = await fn();
      if (step === "claimTargets") setClaimTargetsApproved(true);
      if (step === "approve" && estimate) {
        setApprovedEstimateSetId(estimate.estimate_set_id);
      }
      if (step === "compute") {
        if (forecast?.forecast_mode === "scenario_projection") {
          setProjectionSet(result as ProjectionSetResponse);
          setEstimate(null);
        } else {
          setEstimate(result as EstimateSetResponse);
          setProjectionSet(null);
        }
      }
      if (step === "resolve") setResolution(result as ResolveForecastResponse);
      await load();
      return true;
    } catch (err) {
      setError(formatForecastError(err));
      return false;
    } finally {
      setBusy(null);
      setBusyStartedAt(null);
    }
  }

  function runCommand(command: Command) {
    switch (command) {
      case "pack":
        return runStep("pack", () =>
          forecast?.forecast_mode === "scenario_projection"
            ? dispatchCurrentStatePack(forecastId, {
                idempotencyKey: idempotencyKeys.current.pack,
              })
            : dispatchDefaultResearchPacks(forecastId, {
                idempotencyKey: idempotencyKeys.current.pack,
              }),
        );
      case "manualPack":
        return Promise.resolve();
      case "evidence":
        return runStep("evidence", () =>
          extractEvidence(forecastId, {
            idempotencyKey: idempotencyKeys.current.evidence,
          }),
        );
      case "scenarios":
        if (forecast?.forecast_mode === "scenario_projection") {
          return runCommand("compute");
        }
        return runStep("scenarios", () =>
          generateScenarios(forecastId, {
            idempotencyKey: idempotencyKeys.current.scenarios,
          }),
        );
      case "claimTargets":
        if (forecast?.forecast_mode === "scenario_projection") {
          return runCommand("compute");
        }
        return runStep("claimTargets", () =>
          reviewForecast(
            forecastId,
            { action: "approve_claim_target_links" },
            { idempotencyKey: idempotencyKeys.current.claimTargets },
          ),
        );
      case "compute":
        if (forecast?.forecast_mode === "scenario_projection") {
          return runStep("compute", () =>
            computeProjection(forecastId, {
              idempotencyKey: idempotencyKeys.current.compute,
            }),
          );
        }
        return runStep("compute", () =>
          computeProbabilities(
            forecastId,
            { engine_version: probabilityEngine },
            {
              idempotencyKey: idempotencyKeys.current.compute,
            },
          ),
        );
      case "approve":
        if (forecast?.forecast_mode === "scenario_projection") {
          if (!projectionSet) return Promise.resolve();
          return runStep("approve", () =>
            approveProjection(
              forecastId,
              projectionSet.projection_set_id,
              { idempotencyKey: idempotencyKeys.current.approve },
            ),
          );
        }
        if (!estimate) return Promise.resolve();
        if (!estimate.estimate_set_id) return Promise.resolve();
        if (
          estimate.engine_version !== "phase_a_v1" &&
          estimate.engine_version !== "phase_b_v1"
        ) {
          return Promise.resolve();
        }
        return runStep("approve", () =>
          reviewForecast(
            forecastId,
            estimate.engine_version === "phase_b_v1"
              ? {
                  action: "approve_probability_publication",
                  estimate_set_id: estimate.estimate_set_id,
                }
              : {
                  action: "approve_phase_a_version",
                  estimate_set_id: estimate.estimate_set_id,
                },
            { idempotencyKey: idempotencyKeys.current.approve },
          ),
        );
      case "commit":
        if (forecast?.forecast_mode === "scenario_projection") {
          if (!projectionSet) return Promise.resolve();
          return runStep("commit", () =>
            commitForecastVersion(
              forecastId,
              {
                projection_set_id: projectionSet.projection_set_id,
                expected_input_snapshot_hash: projectionSet.input_snapshot_hash,
              },
              { idempotencyKey: idempotencyKeys.current.commit },
            ),
          );
        }
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

  async function loadManualPrompt() {
    if (manualPromptLoading) return;
    const requestId = manualPromptRequestId.current + 1;
    manualPromptRequestId.current = requestId;
    setManualPromptLoading(true);
    setError(null);
    try {
      const prompt = await getManualResearchPackPrompt(forecastId);
      if (manualPromptRequestId.current === requestId) {
        setManualPrompt(prompt);
        setManualPromptCopyStatus(null);
      }
    } catch (err) {
      if (manualPromptRequestId.current === requestId) {
        setError(formatForecastError(err));
      }
    } finally {
      if (manualPromptRequestId.current === requestId) {
        setManualPromptLoading(false);
      }
    }
  }

  function selectCollectionMode(mode: CollectionMode) {
    setCollectionMode(mode);
    if (mode === "manual" && !manualPrompt) {
      void loadManualPrompt();
    }
  }

  function openManualRecovery() {
    setManualRecoveryOpen(true);
    setManualPrompt(null);
    void loadManualPrompt();
  }

  async function copyManualPrompt() {
    if (!manualPrompt) return;
    const result = await copyTextToClipboard(manualPrompt.prompt);
    setManualPromptCopyStatus({
      message: result === "failed" ? MANUAL_PROMPT_COPY_FAILED : "コピーしました",
      failed: result === "failed",
    });
  }

  function selectManualPrompt() {
    manualPromptTextareaRef.current?.focus();
    manualPromptTextareaRef.current?.select();
  }

  function downloadManualPrompt() {
    if (!manualPrompt) return;
    const blob = new Blob([manualPrompt.prompt], { type: "text/markdown;charset=utf-8" });
    const url = URL.createObjectURL(blob);
    const anchor = document.createElement("a");
    anchor.href = url;
    anchor.download = `forecast-${forecastId}-deep-research-prompt.md`;
    anchor.click();
    URL.revokeObjectURL(url);
  }

  function ProjectionSummary({
    forecast,
    projectionSet,
  }: {
    forecast: ForecastDetailType;
    projectionSet: ProjectionSetResponse | null;
  }) {
    const dimensions = forecast.projection_dimensions;
    const scenarios = projectionSet?.scenarios ?? [];
    const composites = projectionSet?.composites ?? [];
    const sensitivities = projectionSet?.sensitivities ?? [];
    return (
      <section className="form-panel">
        <div className="forecast-panel-heading">
          <h2>2035年状態予測</h2>
        </div>
        {projectionSet ? (
          <>
            <div className="metric-grid">
              <MetricCard
                label="Projection set"
                value={projectionSet.status}
                unit={projectionSet.engine_version}
              />
              <MetricCard
                label="Scenarios"
                value={String(scenarios.length)}
                unit={projectionSet.approved ? "approved" : "draft"}
              />
              <MetricCard
                label="Snapshot"
                value={projectionSet.input_snapshot_hash.slice(0, 12)}
                unit="input hash"
              />
            </div>
            <div className="forecast-impact-scroll">
              <table className="forecast-impact-table">
                <thead>
                  <tr>
                    <th>Metric</th>
                    <th>Horizon</th>
                    <th>P10</th>
                    <th>P50</th>
                    <th>P90</th>
                  </tr>
                </thead>
                <tbody>
                  {composites.map((item) => (
                    <tr key={item.composite_id}>
                      <th>{item.metric_id}</th>
                      <td>{item.horizon_year}</td>
                      <td>{item.p10.toFixed(2)}</td>
                      <td>{item.p50.toFixed(2)}</td>
                      <td>{item.p90.toFixed(2)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
            <div className="result-list">
              {scenarios.map((scenario) => (
                <article className="run-card" key={scenario.projection_scenario_id}>
                  <p className="run-card-title">{scenario.label}</p>
                  <p>{scenario.narrative}</p>
                  <p className="run-card-meta">
                    {(scenario.probability * 100).toFixed(1)}% /{" "}
                    {scenario.coverage_role}
                  </p>
                </article>
              ))}
            </div>
            {sensitivities.length > 0 && (
              <div className="result-list">
                {sensitivities.slice(0, 4).map((item) => (
                  <article className="run-card" key={item.sensitivity_id}>
                    <p className="run-card-title">{item.sensitivity_kind}</p>
                    <p className="run-card-meta">
                      delta P50 {item.delta_p50.toFixed(2)} / delta probability{" "}
                      {item.delta_probability.toFixed(3)}
                    </p>
                  </article>
                ))}
              </div>
            )}
          </>
        ) : (
          <div className="result-list">
            {dimensions.map((dimension) => (
              <article className="run-card" key={dimension.dimension_id}>
                <p className="run-card-title">{dimension.label}</p>
                <p>
                  {dimension.baseline_value} {dimension.unit} in{" "}
                  {dimension.baseline_year}
                </p>
                <p className="run-card-meta">{dimension.horizons.join(", ")}</p>
              </article>
            ))}
          </div>
        )}
      </section>
    );
  }

  async function importManualPack() {
    if (!manualPrompt) return;
    const text = manualReportText.trim();
    const report =
      manualReportFile !== null
        ? { source: "file" as const, file: manualReportFile }
        : { source: "text" as const, text };
    const imported = await runStep("manualPack", () =>
      importManualResearchPack(
        forecastId,
        {
          promptSha256: manualPrompt.prompt_sha256,
          report,
        },
        { idempotencyKey: idempotencyKeys.current.manualPack },
      ),
    );
    if (imported) {
      setManualReportText("");
      setManualReportFile(null);
      if (manualReportFileInputRef.current) {
        manualReportFileInputRef.current.value = "";
      }
    }
  }

  function rerunPack(pack: ForecastCurrentResearchPack) {
    if (
      !window.confirm(
        "このパックを再実行しますか？Deep Researchを再度呼び出すため、追加のコストと時間が発生します。",
      )
    ) {
      return;
    }
    void runStep("pack", () =>
      rerunForecastResearchPack(
        forecastId,
        pack.pack_id,
        { expected_active_pack_id: pack.pack_id },
        { idempotencyKey: stableKey(forecastId, "pack") },
      ),
    );
  }

  const isProjectionForecast = forecast?.forecast_mode === "scenario_projection";
  const canDispatch = approvedFraming && status === "framing_approved";
  const canRecoverManualPack =
    !forecastProgress.phaseBStarted &&
    Boolean(currentResearchPack) &&
    (currentResearchPackStatus === "needs_human_review" ||
      currentResearchPackStatus === "failed" ||
      currentResearchPackStatus === "cancelled");
  const showManualPackPanel =
    (canDispatch && collectionMode === "manual") ||
    (canRecoverManualPack && manualRecoveryOpen);
  const manualReportReady = manualReportFile !== null || manualReportText.trim().length > 0;
  const canExtract = status === "pack_running" && researchPackCompleted;
  const canGenerate = !isProjectionForecast && status === "evidence_ready";
  const effectiveClaimTargetsApproved =
    claimTargetsApproved ||
    (forecast?.approved_claim_target_link_count ?? 0) > 0 ||
    statusAtLeast(status, "draft_ready");
  const isPhaseBEstimate = estimate?.engine_version === "phase_b_v1";
  const hasKnownEstimateEngine =
    estimate?.engine_version === "phase_a_v1" ||
    estimate?.engine_version === "phase_b_v1";
  const estimateApproved =
    statusAtLeast(status, "committed") ||
    Boolean(
      estimate &&
        (estimate.approved || approvedEstimateSetId === estimate.estimate_set_id),
    ) ||
    Boolean(projectionSet?.approved);
  const canApproveClaimTargets =
    !isProjectionForecast && status === "scenarios_ready" && !effectiveClaimTargetsApproved;
  const canCompute = isProjectionForecast
    ? status === "evidence_ready"
    : status === "scenarios_ready" && effectiveClaimTargetsApproved;
  const canRestoreDraft = status === "draft_ready" && !estimate && !projectionSet;
  const canApproveEstimate =
    status === "draft_ready" &&
    !estimateApproved &&
    (isProjectionForecast
      ? Boolean(projectionSet?.projection_set_id)
      : Boolean(estimate?.estimate_set_id) && hasKnownEstimateEngine);
  const canSubmitEstimateApproval = canApproveEstimate;
  const canCommit =
    status === "draft_ready" &&
    (isProjectionForecast ? Boolean(projectionSet) : Boolean(estimate)) &&
    estimateApproved;
  const canResolve =
    !isProjectionForecast && status === "committed" && Boolean(selectedOutcomeId);
  const flowNodes = forecastExecutionNodes({
    status,
    approvedFraming,
    researchPackCompleted,
    researchPackRunning,
    researchPackBlocked,
    researchPackSubmitting,
    researchPackSubmitWaiting,
    currentResearchPackStatus,
    phaseBStarted: forecastProgress.phaseBStarted,
    completedDefaultPackCount: forecastProgress.completedDefaultPackCount,
    missingDefaultPacks: forecastProgress.missingDefaultRoles.length,
    claimTargetsApproved: effectiveClaimTargetsApproved,
    hasEstimate: isProjectionForecast ? Boolean(projectionSet) : Boolean(estimate),
    estimateApproved,
    packSubmissionPending,
    busy,
    probabilityEngine,
    isPhaseBEstimate,
    isProjectionForecast,
  });
  const currentStep = deriveCurrentStep({
    status,
    currentResearchPackStatus,
    currentResearchPackPresent:
      Boolean(currentResearchPack) || forecastProgress.phaseBStarted,
    packSubmissionPending,
    packSubmissionIsSlow,
    researchPackSubmitting,
    researchPackSubmitWaiting,
    researchPackSubmitStalled,
    researchPackRunning,
    researchPackCompleted,
    researchPackBlocked,
    phaseBStarted: forecastProgress.phaseBStarted,
    missingDefaultPacks: forecastProgress.missingDefaultRoles.length,
    canDispatch,
    canExtract,
    canGenerate,
    canApproveClaimTargets,
    canCompute,
    canRestoreDraft,
    canApproveEstimate,
    canCommit,
    canResolve,
    estimatePresent: isProjectionForecast ? Boolean(projectionSet) : Boolean(estimate),
    probabilityEngine,
    isPhaseBEstimate,
    isProjectionForecast,
  });
  const researchPackUpdatedLabel = formatStartedAt(
    currentResearchPack?.research_run_updated_at ??
      currentResearchPack?.pack_updated_at ??
      undefined,
  );
  const forecastDisplayStatus = forecastStatusLabel(status);
  const currentResearchPackDisplayStatus = researchPackSubmitWaiting
    ? "Deep Research送信待ち"
    : packSubmissionPending
      ? "サーバーに登録中"
      : isProjectionForecast
        ? researchPackCompleted
          ? "current_state Pack完了"
          : localizePackStatus(currentResearchPackStatus)
      : forecastProgress.phaseBStarted
        ? forecastProgress.missingDefaultRoles.length > 0
          ? `必要Pack不足 (${forecastProgress.completedDefaultPackCount}/5完了)`
          : researchPackCompleted
            ? "5 Pack完了"
            : `${forecastProgress.completedDefaultPackCount}/5 Pack完了`
      : localizePackStatus(currentResearchPackStatus);
  const currentStepDetails = [
    { label: "Forecast本体状態", value: forecastDisplayStatus },
    {
      label: "公開情報パック状態",
      value: currentResearchPackDisplayStatus,
    },
    currentResearchPack?.research_run_id
      ? { label: "Research run ID", value: currentResearchPack.research_run_id }
      : null,
    currentResearchPack?.pack_id
      ? { label: "Pack ID", value: currentResearchPack.pack_id }
      : null,
    currentResearchPack?.research_run_status
      ? { label: "Research run状態", value: currentResearchPack.research_run_status }
      : null,
    researchPackStartedLabel
      ? { label: "開始時刻", value: researchPackStartedLabel }
      : null,
    researchPackSubmitWaiting && researchPackSubmitStartedLabel
      ? { label: "Run作成時刻", value: researchPackSubmitStartedLabel }
      : null,
    packSubmissionPending
      ? { label: "経過時間", value: formatElapsed(packSubmissionElapsed) }
      : researchPackSubmitWaiting
        ? { label: "経過時間", value: formatElapsed(researchPackSubmitElapsed) }
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
    currentResearchPack?.last_error
      ? { label: "エラー詳細", value: currentResearchPack.last_error }
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
          <span className={`status-pill status-pill--${forecastStatusTone(status)}`}>
            {forecastDisplayStatus}
          </span>
          {researchRunPath && (
            <Link to={researchRunPath} className="btn-secondary">
              {researchRunLinkLabel}
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
        <MetricCard label="Forecast本体" value={forecastDisplayStatus} />
        <MetricCard
          label="フレーミング"
          value={forecast?.approved_framing_version ? "承認済み" : "承認待ち"}
        />
        <MetricCard label="公開情報パック" value={currentResearchPackDisplayStatus} />
        <MetricCard
          label={isProjectionForecast ? "Projectionエンジン" : "確率エンジン"}
          value={
            isProjectionForecast
              ? projectionSet?.engine_version ?? "未作成"
              : estimate?.engine_version ?? "未計算"
          }
        />
      </div>

      <section
        className={`forecast-current-step forecast-current-step--${currentStep.tone}`}
        aria-labelledby="forecast-current-step-heading"
      >
        <div className="forecast-current-step__header">
          <div>
            <span className="forecast-current-step__kicker">現在のステップ</span>
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
            ) : currentStep.action === "researchRun" ? (
              <button type="button" className="btn-primary" disabled>
                Research run未登録
              </button>
            ) : (
              <button
                type="button"
                className="btn-primary"
                disabled={
                  (!!busy && currentStep.action !== "refresh") ||
                  (currentStep.action === "approve" && !canSubmitEstimateApproval)
                }
                onClick={() => handleCurrentStepAction(currentStep.action)}
              >
                {currentStep.actionLabel}
              </button>
            )}
          </div>
        )}
        {canRecoverManualPack && (
          <div className="forecast-current-step__action">
            <button
              type="button"
              className="btn-secondary"
              disabled={manualPromptLoading}
              onClick={openManualRecovery}
            >
              ChatGPT Deep Researchで手動収集に切り替え
            </button>
          </div>
        )}
        {canDispatch && (
          <div className="forecast-collection-choice">
            <div
              className="forecast-collection-choice__modes"
              role="group"
              aria-label="公開情報の収集方法"
            >
              <button
                type="button"
                className={collectionMode === "auto" ? "is-active" : ""}
                aria-pressed={collectionMode === "auto"}
                onClick={() => selectCollectionMode("auto")}
              >
                アプリで自動収集
                <span>おすすめ</span>
              </button>
              <button
                type="button"
                className={collectionMode === "manual" ? "is-active" : ""}
                aria-pressed={collectionMode === "manual"}
                onClick={() => selectCollectionMode("manual")}
              >
                ChatGPTで手動収集
                <span>APIが使えない時</span>
              </button>
            </div>
            {collectionMode === "auto" ? (
              <div className="forecast-collection-choice__action">
                <p>
                  {isProjectionForecast
                    ? "Deep Research APIでcurrent_state Packを収集し、完了後に証拠抽出へ進めます。"
                    : "Deep Research APIで既定5 Packを収集し、完了後に証拠抽出へ進めます。"}
                </p>
                <button
                  type="button"
                  className="btn-primary"
                  disabled={!!busy}
                  onClick={() => void runCommand("pack")}
                >
                  {isProjectionForecast
                    ? "current_state Packを開始"
                    : "公開情報の収集を開始"}
                </button>
              </div>
            ) : null}
          </div>
        )}
        {showManualPackPanel && (
          <div className="forecast-manual-pack">
            <div className="forecast-manual-pack__header">
              <div>
                <h3>ChatGPT Deep Researchへ渡すPrompt</h3>
                <p>
                  このPromptを手動で実行し、得られたレポートを貼り付けるか
                  md/txtでアップロードします。
                </p>
              </div>
              <button
                type="button"
                className="btn-secondary"
                disabled={manualPromptLoading}
                onClick={() => void loadManualPrompt()}
              >
                {manualPrompt ? "Promptを再取得" : "Promptを取得"}
              </button>
            </div>
            {manualPrompt?.recovering_existing_pack && (
              <p className="muted">
                既存の公開情報パックを手動レポートで復旧します。
              </p>
            )}
            {manualPromptLoading && <p className="muted">Promptを取得しています。</p>}
            {manualPrompt && (
              <>
                <textarea
                  className="forecast-manual-pack__prompt"
                  aria-label="ChatGPT Deep Researchへ渡すPrompt"
                  ref={manualPromptTextareaRef}
                  value={manualPrompt.prompt}
                  readOnly
                  rows={8}
                />
                <div className="forecast-manual-pack__tools">
                  <button
                    type="button"
                    className="btn-secondary"
                    onClick={() => void copyManualPrompt()}
                  >
                    Promptをコピー
                  </button>
                  {manualPromptCopyStatus?.failed && (
                    <button
                      type="button"
                      className="btn-secondary"
                      onClick={selectManualPrompt}
                    >
                      全文を選択
                    </button>
                  )}
                  <button
                    type="button"
                    className="btn-secondary"
                    onClick={downloadManualPrompt}
                  >
                    PromptをMarkdownでダウンロード
                  </button>
                </div>
                {manualPromptCopyStatus && (
                  <p
                    className={`char-counter${manualPromptCopyStatus.failed ? " char-counter--error" : ""}`}
                    aria-live="polite"
                  >
                    {manualPromptCopyStatus.message}
                  </p>
                )}
              </>
            )}
            <label className="field">
              <span>結果を貼り付け</span>
              <textarea
                value={manualReportText}
                onChange={(event) => {
                  setManualReportText(event.target.value);
                  if (event.target.value.length > 0) {
                    setManualReportFile(null);
                    if (manualReportFileInputRef.current) {
                      manualReportFileInputRef.current.value = "";
                    }
                  }
                }}
                rows={6}
                placeholder="ChatGPT Deep Researchの最終レポートを貼り付け"
              />
            </label>
            <label className="field">
              <span>md/txtをアップロード</span>
              <input
                ref={manualReportFileInputRef}
                type="file"
                accept=".md,.txt,text/markdown,text/plain"
                onChange={(event) => {
                  setManualReportFile(event.target.files?.[0] ?? null);
                  if (event.target.files?.[0]) setManualReportText("");
                }}
              />
            </label>
            {manualReportFile && (
              <p className="muted">選択中: {manualReportFile.name}</p>
            )}
            <div className="forecast-collection-choice__action">
              <p>
                手動レポートは未検証の公開情報として保存され、次の証拠抽出で
                Forecast用の主張とソースに分解します。
              </p>
              <button
                type="button"
                className="btn-primary"
                disabled={
                  !!busy ||
                  manualPromptLoading ||
                  !manualPrompt ||
                  !manualReportReady
                }
                onClick={() => void importManualPack()}
              >
                結果を取り込む
              </button>
            </div>
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

      {isProjectionForecast ? (
        <ProjectionSummary forecast={forecast} projectionSet={projectionSet} />
      ) : (
        <ForecastReport forecast={forecast} estimate={estimate} />
      )}

      <ForecastFlowProgress
        heading="全体フロー"
        summary="Forecastが解決までのどこにいるかを確認できます。操作は上の現在ステップから行います。"
        nodes={flowNodes}
        label="Forecast実行フロー"
        layout="wrapped"
        columns={4}
      />

      <PackCollectionPanel
        packs={forecastProgress.packs}
        busy={Boolean(busy)}
        onRerunPack={rerunPack}
        onDispatchDefaults={
          canDispatch && !isProjectionForecast
            ? () => void runCommand("pack")
            : undefined
        }
      />

      <EvidenceBoard forecast={forecast} />
      {!isProjectionForecast && (
        <>
          <ScenarioMap forecast={forecast} estimate={estimate} />
          <ProbabilityPanel forecast={forecast} estimate={estimate} />
        </>
      )}

      {!isProjectionForecast &&
        (forecast?.status === "committed" || forecast?.status === "resolved") && (
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
