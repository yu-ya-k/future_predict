/**
 * SCR-4: Human Review — reviewer-scoped decision screen.
 *
 * Invariant I-4: one-screen decision. All judgment material is presented
 * on a single screen. DecisionButtons map 1:1 to HumanReviewAction.
 *
 * Guard rules (A4 / A8 Q-4):
 *  - Any action not in payload.allowed_actions → disabled.
 *  - request_targeted_rerun shows a warning when audit_summary.no_progress_count >= 2
 *    so reviewers see the item-loop context before resuming.
 *  - request_full_rerun is used for empty-report recovery and full replacement.
 *  - 409 conflict → show detail + refetch payload.
 */

import { useEffect, useRef, useState } from "react";

import {
  BackLink,
  DecisionButton,
  FlagChip,
  Markdown,
  MetricCard,
  ScoreChip,
  Skeleton,
  VerdictBadge,
} from "../components";
import {
  getHumanReviewPayload,
  resumeRun,
  uploadManualRerunResult,
} from "../api/research";
import { ApiError } from "../api/client";
import { copyTextToClipboard } from "../lib/clipboard";
import { navigate, routes } from "../router";
import {
  type HumanReviewAction,
  type HumanReviewPayload,
  type ResearchItem,
} from "../types";

const NO_PROGRESS_WARN_THRESHOLD = 2;
const MAX_COMMENT_CHARS = 10_000;
const COPY_FAILED_MESSAGE =
  "コピーできませんでした。プロンプト欄を選択してコピーするか、.mdをダウンロードしてください。";

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

const BLOCKED_REASON_LABELS: Record<string, string> = {
  manual_chatgpt_rerun_pending: "ChatGPT手動rerunの結果アップロード待ちです。",
  missing_report_for_approval: "承認できるレポート本文がありません。",
  missing_report_for_review_retry: "再レビューできるレポート本文がありません。",
  missing_report_for_llm_patch: "差分修正できるレポート本文がありません。",
  missing_review_for_verification: "検証には先にLLMレビューが必要です。",
  missing_report_for_targeted_rerun_use_full_rerun:
    "対象itemだけ再実行できるレポート本文がありません。全面再実行を使ってください。",
  max_total_tool_calls_reached: "総ツール呼び出し上限に達しています。",
  max_total_iterations_reached: "総反復回数の上限に達しています。",
  max_no_progress_count_reached: "改善停滞が続いたため、この自動処理は停止しています。",
  max_targeted_rerun_runs_reached: "API Targeted rerun回数の上限に達しています。",
  max_full_rerun_runs_reached: "API Full rerun回数の上限に達しています。",
  max_llm_patch_runs_reached: "LLM patch回数の上限に達しています。",
  max_verification_runs_reached: "Verification回数の上限に達しています。",
  review_retry_available_only_after_review_error:
    "レビュー再実行はレビューエラー後だけ選択できます。",
};

function formatBlockedReason(reason?: string | null): string | undefined {
  if (!reason) return undefined;
  return BLOCKED_REASON_LABELS[reason] ?? `停止理由: ${reason}`;
}

/**
 * Actions that finalize the run irreversibly or spend additional budget. These
 * require an explicit inline confirmation step before resumeRun() fires, so a
 * single mis-click on the 2-column decision grid cannot commit them.
 */
const CONFIRM_REQUIRED_ACTIONS = new Set<HumanReviewAction>([
  "approve",
  "approve_with_limitation",
  "reject",
  "request_full_rerun",
  "request_targeted_rerun",
  "request_manual_full_rerun",
]);

function requiresConfirmation(action: HumanReviewAction): boolean {
  return CONFIRM_REQUIRED_ACTIONS.has(action);
}

function expectedOutputLabel(kind?: string | null, scope?: string | null): string {
  if (kind === "complete_replacement_report" || scope === "full_rerun") {
    return "完成版レポート全文";
  }
  return "差分セクション";
}

function stopReasonSummary(payload: HumanReviewPayload): string | null {
  const reason = payload.reason;
  const summary = payload.route_summary;
  const verdict = summary?.latest_verdict ?? payload.latest_review?.verdict ?? null;
  if (
    reason === "max_full_rerun_runs_reached" ||
    summary?.blocked_reason === "max_full_rerun_runs_reached"
  ) {
    return `LLMレビューは完了しています。判定は ${verdict ?? "needs_full_rerun"} で、API自動Full rerunは上限に達しました。ChatGPT手動Fullが有効なら、完成版レポート全文を手動で作成してアップロードできます。`;
  }
  if (
    reason === "max_targeted_rerun_runs_reached" ||
    summary?.blocked_reason === "max_targeted_rerun_runs_reached"
  ) {
    return `LLMレビューは完了しています。判定は ${verdict ?? "needs_targeted_rerun"} で、API自動Targeted rerunは上限に達しました。ChatGPT手動Targetedが有効なら、未解決item向けの差分を手動で作成してアップロードできます。`;
  }
  if (summary?.candidate_route || summary?.selected_route || summary?.blocked_reason) {
    return [
      summary.candidate_route ? `候補: ${summary.candidate_route}` : null,
      summary.selected_route ? `選択: ${summary.selected_route}` : null,
      summary.blocked_reason ? `ブロック: ${summary.blocked_reason}` : null,
    ]
      .filter(Boolean)
      .join(" / ");
  }
  return null;
}

function safeManualRerunFilename(runId: string, rerunId: string): string {
  const safeRunId = runId.replace(/[^a-zA-Z0-9._-]+/g, "-").replace(/^-+|-+$/g, "");
  const safeRerunId = rerunId.replace(/[^a-zA-Z0-9._-]+/g, "-").replace(/^-+|-+$/g, "");
  return `${safeRunId || "research-run"}-${safeRerunId || "manual-rerun"}-prompt.md`;
}

function safeSuggestedRerunFilename(runId: string, scope: string): string {
  const safeRunId = runId.replace(/[^a-zA-Z0-9._-]+/g, "-").replace(/^-+|-+$/g, "");
  const safeScope = scope.replace(/[^a-zA-Z0-9._-]+/g, "-").replace(/^-+|-+$/g, "");
  return `${safeRunId || "research-run"}-${safeScope || "rerun"}-suggested-prompt.md`;
}

function isManualRerunRequest(action: HumanReviewAction): boolean {
  return (
    action === "request_manual_targeted_rerun" ||
    action === "request_manual_full_rerun"
  );
}

function downloadMarkdown(filename: string, markdown: string) {
  const blob = new Blob([markdown], { type: "text/markdown;charset=utf-8" });
  const url = URL.createObjectURL(blob);
  const anchor = document.createElement("a");
  anchor.href = url;
  anchor.download = filename;
  anchor.rel = "noopener";
  anchor.style.display = "none";
  document.body.appendChild(anchor);
  anchor.click();
  anchor.remove();
  window.setTimeout(() => URL.revokeObjectURL(url), 1000);
}

interface HumanReviewProps {
  runId: string;
}

type PromptCopyTarget = "suggested" | "pending";

export function HumanReview({ runId }: HumanReviewProps) {
  const [payload, setPayload] = useState<HumanReviewPayload | null>(null);
  const [loading, setLoading] = useState(true);
  const [loadError, setLoadError] = useState<string | null>(null);

  const [comment, setComment] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [submitError, setSubmitError] = useState<string | null>(null);
  const [confirmingAction, setConfirmingAction] = useState<HumanReviewAction | null>(
    null,
  );
  const [manualResultText, setManualResultText] = useState("");
  const [manualResultFile, setManualResultFile] = useState<File | null>(null);
  const [manualUploadMode, setManualUploadMode] = useState<"text" | "file">("text");
  const [manualUploading, setManualUploading] = useState(false);
  const [manualUploadError, setManualUploadError] = useState<string | null>(null);
  const [copyStatus, setCopyStatus] = useState<{
    target: PromptCopyTarget;
    message: string;
    failed: boolean;
  } | null>(null);

  const abortRef = useRef<AbortController | null>(null);
  const requestGenerationRef = useRef(0);
  const suggestedPromptRef = useRef<HTMLTextAreaElement | null>(null);
  const pendingPromptRef = useRef<HTMLTextAreaElement | null>(null);
  const confirmButtonRef = useRef<HTMLButtonElement | null>(null);
  // Remembers which action's trigger button opened the confirm gate so focus
  // can return to it when the gate closes without navigating away.
  const confirmTriggerActionRef = useRef<HumanReviewAction | null>(null);

  // Open the inline confirm gate and record the triggering action so its
  // decision button can reclaim focus when the gate closes.
  function openConfirmGate(action: HumanReviewAction) {
    confirmTriggerActionRef.current = action;
    setConfirmingAction(action);
  }

  // Move focus to the confirm action when an inline confirmation opens so the
  // reviewer can complete or cancel it with the keyboard alone. When the gate
  // closes while staying on-page (cancel via やめる/Escape, or a failed submit),
  // restore focus to the triggering decision button so focus does not fall back
  // to <body> (WCAG 2.4.3).
  useEffect(() => {
    if (confirmingAction) {
      confirmButtonRef.current?.focus();
      return;
    }
    const triggerAction = confirmTriggerActionRef.current;
    if (!triggerAction) return;
    confirmTriggerActionRef.current = null;
    document
      .querySelector<HTMLButtonElement>(`[data-action="${triggerAction}"]`)
      ?.focus();
  }, [confirmingAction]);

  async function fetchPayload() {
    abortRef.current?.abort();
    const controller = new AbortController();
    const generation = ++requestGenerationRef.current;
    abortRef.current = controller;

    setLoading(true);
    setLoadError(null);

    const isCurrentRequest = () =>
      generation === requestGenerationRef.current &&
      abortRef.current === controller &&
      !controller.signal.aborted;

    try {
      const data = await getHumanReviewPayload(runId, controller.signal);
      if (!isCurrentRequest()) return;
      setPayload(data);
    } catch (err) {
      if (err instanceof DOMException && err.name === "AbortError") return;
      if (!isCurrentRequest()) return;
      if (err instanceof ApiError) {
        setLoadError(err.detail ?? err.message);
      } else if (err instanceof Error) {
        setLoadError(err.message);
      }
    } finally {
      if (isCurrentRequest()) {
        setLoading(false);
      }
    }
  }

  useEffect(() => {
    confirmTriggerActionRef.current = null;
    setConfirmingAction(null);
    void fetchPayload();
    return () => {
      requestGenerationRef.current += 1;
      abortRef.current?.abort();
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [runId]);

  async function handleDecision(action: HumanReviewAction) {
    setConfirmingAction(null);
    setSubmitting(true);
    setSubmitError(null);
    try {
      await resumeRun(runId, {
        action,
        comment: comment.trim() || null,
      });
      if (isManualRerunRequest(action)) {
        await fetchPayload();
        return;
      }
      navigate(routes().monitor(runId));
    } catch (err) {
      if (err instanceof ApiError && err.isConflict) {
        setSubmitError(
          `操作が競合しました: ${err.detail ?? "詳細不明"}。最新の状態を確認します。`,
        );
        // Refetch payload to reflect updated state
        void fetchPayload();
      } else if (err instanceof ApiError) {
        setSubmitError(err.detail ?? err.message);
      } else {
        setSubmitError("予期しないエラーが発生しました");
      }
    } finally {
      setSubmitting(false);
    }
  }

  async function handleCopyPrompt(
    target: PromptCopyTarget,
    prompt: string | null | undefined,
  ) {
    if (!prompt) return;
    const result = await copyTextToClipboard(prompt);
    setCopyStatus({
      target,
      message: result === "failed" ? COPY_FAILED_MESSAGE : "コピーしました",
      failed: result === "failed",
    });
  }

  function selectPromptText(target: PromptCopyTarget) {
    const element =
      target === "suggested" ? suggestedPromptRef.current : pendingPromptRef.current;
    if (!element) return;
    element.focus();
    element.select();
  }

  function handleManualUploadModeChange(mode: "text" | "file") {
    setManualUploadMode(mode);
    setManualUploadError(null);
  }

  function handleManualResultTextChange(value: string) {
    setManualResultText(value);
    setManualUploadError(null);
  }

  function handleManualResultFileChange(file: File | null) {
    setManualResultFile(file);
    setManualUploadError(null);
  }

  function handleDownloadManualPrompt() {
    const pending = payload?.pending_manual_rerun;
    if (!pending) return;
    downloadMarkdown(safeManualRerunFilename(runId, pending.rerun_id), pending.prompt);
  }

  function handleDownloadSuggestedPrompt() {
    const suggested = payload?.suggested_rerun;
    if (!suggested) return;
    downloadMarkdown(
      safeSuggestedRerunFilename(runId, suggested.scope),
      suggested.prompt,
    );
  }

  async function handleManualRerunUpload(e: React.FormEvent) {
    e.preventDefault();
    const pending = payload?.pending_manual_rerun;
    if (!pending) return;
    const text = manualResultText.trim();
    if (manualUploadMode === "text" && !text) {
      setManualUploadError("結果テキストを入力してください");
      return;
    }
    if (manualUploadMode === "file" && !manualResultFile) {
      setManualUploadError("結果ファイルを選択してください");
      return;
    }

    setManualUploading(true);
    setManualUploadError(null);
    try {
      await uploadManualRerunResult(runId, {
        rerun_id: pending.rerun_id,
        report:
          manualUploadMode === "file"
            ? { source: "file", file: manualResultFile as File }
            : { source: "text", text },
      });
      navigate(routes().monitor(runId));
    } catch (err) {
      if (err instanceof ApiError && err.isConflict) {
        setManualUploadError(
          `アップロードが競合しました: ${err.detail ?? "詳細不明"}。最新の状態を確認します。`,
        );
        void fetchPayload();
      } else if (err instanceof ApiError) {
        setManualUploadError(err.detail ?? err.message);
      } else {
        setManualUploadError("予期しないエラーが発生しました");
      }
    } finally {
      setManualUploading(false);
    }
  }

  // ── Loading state ─────────────────────────────────────────────────────────

  if (loading) {
    return (
      <div className="screen-review">
        <BackLink to={routes().monitor(runId)} label="Runへ戻る" />
        <div className="review-skeleton">
          <Skeleton width="70%" height="28px" />
          <Skeleton width="100%" height="100px" />
          <Skeleton width="100%" height="200px" />
        </div>
      </div>
    );
  }

  if (loadError) {
    return (
      <div className="screen-review">
        <BackLink to={routes().monitor(runId)} label="Runへ戻る" />
        <div className="review-load-error" role="alert">
          <p>ペイロードの取得に失敗しました: {loadError}</p>
          <button type="button" className="btn-secondary" onClick={fetchPayload}>
            再試行
          </button>
        </div>
      </div>
    );
  }

  if (!payload) return null;

  const { latest_review, audit_summary, allowed_actions } = payload;
  const pendingManualRerun = payload.pending_manual_rerun ?? null;
  const suggestedRerun = payload.suggested_rerun ?? null;
  const unresolvedItems = payload.unresolved_items ?? [];
  const latestItemAssessments = latest_review?.item_assessments ?? [];

  const noProgressWarn =
    audit_summary.no_progress_count >= NO_PROGRESS_WARN_THRESHOLD;
  const latestReportIsEmpty = payload.latest_report.trim().length === 0;

  const actionState = (action: HumanReviewAction) =>
    payload.action_states?.find((state) => state.action === action) ?? null;
  const isAllowed = (action: HumanReviewAction) =>
    actionState(action)?.allowed ?? allowed_actions.includes(action);
  const disabledReason = (action: HumanReviewAction) =>
    formatBlockedReason(actionState(action)?.blocked_reason);
  const reasonSummary = stopReasonSummary(payload);

  const targetedRerunGuardMessage = noProgressWarn
    ? `改善停滞が${audit_summary.no_progress_count}回続いています。同じitemへの再実行効果は限定的かもしれません。`
    : undefined;
  const fullRerunGuardMessage = noProgressWarn
    ? `改善停滞が${audit_summary.no_progress_count}回続いています。全面再実行でも同じ失敗を繰り返す可能性があります。`
    : undefined;
  const renderDecision = (
    action: HumanReviewAction,
    label: string,
    consequence: string,
    tone: "success" | "warning" | "danger" | "neutral",
    options: { costHint?: string; guardMessage?: string } = {},
  ) => {
    const gated = requiresConfirmation(action);
    const disabled = !isAllowed(action) || submitting;
    const confirming = gated && confirmingAction === action;
    const confirmConfirmClass = tone === "danger" ? "btn-danger" : "btn-primary";

    return (
      <div className="decision-action-cell">
        <DecisionButton
          action={action}
          label={label}
          consequence={consequence}
          tone={tone}
          costHint={options.costHint}
          guardMessage={confirming ? undefined : options.guardMessage}
          disabled={disabled}
          disabledReason={disabledReason(action)}
          block
          onClick={
            gated
              ? () => openConfirmGate(action)
              : () => void handleDecision(action)
          }
        />
        {confirming && (
          <div
            className="decision-confirm"
            role="group"
            aria-label={`${label}の確認`}
            onKeyDown={(event) => {
              if (event.key === "Escape") {
                event.stopPropagation();
                setConfirmingAction(null);
              }
            }}
          >
            <p className="decision-button__consequence">
              本当に「{label}」を実行しますか？{consequence}
            </p>
            {options.costHint && (
              <p className="decision-button__cost-hint">{options.costHint}</p>
            )}
            {options.guardMessage && (
              <p className="decision-button__guard" role="note">
                {options.guardMessage}
              </p>
            )}
            <div className="form-actions">
              <button
                type="button"
                ref={confirmButtonRef}
                className={confirmConfirmClass}
                disabled={submitting}
                onClick={() => void handleDecision(action)}
              >
                実行する
              </button>
              <button
                type="button"
                className="btn-secondary"
                disabled={submitting}
                onClick={() => setConfirmingAction(null)}
              >
                やめる
              </button>
            </div>
          </div>
        )}
      </div>
    );
  };

  return (
    <div className="screen-review">
      <header className="screen-header">
        <BackLink to={routes().monitor(runId)} label="Runへ戻る" />
        <h1 className="screen-title">判断</h1>
      </header>

      {/* ── Stop-reason banner ────────────────────────── */}
      <div className="review-reason-banner" role="note">
        <span className="review-reason-label">停止理由</span>
        <p className="review-reason-text">
          {BLOCKED_REASON_LABELS[payload.reason] ?? payload.reason}
        </p>
        {reasonSummary && (
          <p className="review-reason-summary">{reasonSummary}</p>
        )}
      </div>

      {/* ── Warnings ──────────────────────────────────── */}
      {payload.warnings.length > 0 && (
        <div className="review-warnings" role="note">
          <ul>
            {payload.warnings.map((w, i) => (
              <li key={i}>{w}</li>
            ))}
          </ul>
        </div>
      )}

      {/* ── High-risk flags ───────────────────────────── */}
      {latest_review?.high_risk_flags && latest_review.high_risk_flags.length > 0 && (
        <div className="review-risk-flags" role="note">
          <strong>リスクフラグ:</strong>
          <ul>
            {latest_review.high_risk_flags.map((flag, i) => (
              <li key={i}>{flag}</li>
            ))}
          </ul>
        </div>
      )}

      {/* ── Investment metrics ────────────────────────── */}
      <section className="review-investment" aria-labelledby="investment-heading">
        <h2 id="investment-heading" className="section-title">これまでの投資</h2>
        <div className="metrics-row">
          <MetricCard
            label="Targeted rerun"
            value={audit_summary.targeted_rerun_runs}
            icon="ti-search"
          />
          <MetricCard
            label="Full rerun"
            value={audit_summary.full_rerun_runs}
            icon="ti-refresh"
          />
          <MetricCard
            label="LLM patch"
            value={audit_summary.llm_patch_runs}
            icon="ti-pencil"
          />
          <MetricCard
            label="Verification"
            value={audit_summary.verification_runs}
            icon="ti-shield-check"
          />
          <MetricCard
            label="推定コスト"
            value={`$${audit_summary.estimated_cost_usd.toFixed(3)}`}
            icon="ti-currency-dollar"
            warn={audit_summary.no_progress_count >= NO_PROGRESS_WARN_THRESHOLD}
          />
        </div>
      </section>

      {/* ── Latest review card ────────────────────────── */}
      {latest_review && (
        <section className="review-latest" aria-labelledby="latest-review-heading">
          <h2 id="latest-review-heading" className="section-title">直近のレビュー所見</h2>
          <div className="latest-review-card">
            <div className="latest-review-header">
              <VerdictBadge verdict={latest_review.verdict} />
              <ScoreChip score={latest_review.score} />
              <span className="reviewer-confidence">
                確信度: {latest_review.reviewer_confidence}%
              </span>
            </div>

            <p className="latest-review-rationale">{latest_review.rationale}</p>
            {latest_review.route_rationale && (
              <p className="latest-review-route">
                Route: {latest_review.route_rationale}
              </p>
            )}

            <div className="latest-review-flags">
              <FlagChip
                active={latest_review.verdict === "needs_llm_patch"}
                label="LLM patch"
                tone={latest_review.verdict === "needs_llm_patch" ? "pass" : "neutral"}
              />
              <FlagChip
                active={latest_review.verdict === "needs_targeted_rerun"}
                label="Targeted rerun"
                tone={latest_review.verdict === "needs_targeted_rerun" ? "deep" : "neutral"}
              />
            </div>

            {latestItemAssessments.length > 0 && (
              <div className="review-gaps">
                <h3 className="gaps-title">Item assessments</h3>
                <ul className="gaps-list">
                  {latestItemAssessments.map((item) => (
                    <li key={item.item_id}>
                      {item.item_id}: {item.status} / {item.failure_mode} /{" "}
                      {item.recommended_action}
                    </li>
                  ))}
                </ul>
              </div>
            )}

            {latest_review.gaps.length > 0 && (
              <div className="review-gaps">
                <h3 className="gaps-title">未解決のギャップ</h3>
                <ul className="gaps-list">
                  {latest_review.gaps.map((gap, i) => (
                    <li key={i}>{gap}</li>
                  ))}
                </ul>
              </div>
            )}
          </div>
        </section>
      )}

      {suggestedRerun && !pendingManualRerun && (
        <section className="review-manual-rerun" aria-labelledby="suggested-rerun-heading">
          <h2 id="suggested-rerun-heading" className="section-title">
            Rerun向けプロンプト
          </h2>
          <div className="latest-review-card">
            <div className="latest-review-header">
              <span className="reviewer-confidence">
                {suggestedRerun.scope === "full_rerun" ? "Full rerun" : "Targeted rerun"}
              </span>
              <span className="reviewer-confidence">
                出力: {expectedOutputLabel(
                  suggestedRerun.expected_output_kind,
                  suggestedRerun.scope,
                )}
              </span>
              <span className="reviewer-confidence">
                Deep Research {suggestedRerun.expected_run_no}回目
              </span>
              {suggestedRerun.target_item_ids.length > 0 && (
                <span className="reviewer-confidence">
                  {suggestedRerun.target_item_ids.join(", ")}
                </span>
              )}
            </div>
            {suggestedRerun.query_policy.status !== "allowed" && (
              <p className="manual-rerun-note" role="note">
                Query policy:{" "}
                {suggestedRerun.query_policy.blocked_reason ??
                  suggestedRerun.query_policy.status}
              </p>
            )}
            <textarea
              aria-label="Rerun向けプロンプト本文"
              className="prompt-attempt-body"
              ref={suggestedPromptRef}
              readOnly
              rows={12}
              value={suggestedRerun.prompt}
            />
            <div className="form-actions">
              <button
                type="button"
                className="btn-secondary"
                onClick={() => void handleCopyPrompt("suggested", suggestedRerun.prompt)}
              >
                コピー
              </button>
              <button
                type="button"
                className="btn-secondary"
                onClick={handleDownloadSuggestedPrompt}
              >
                .md ダウンロード
              </button>
              {copyStatus?.target === "suggested" && copyStatus.failed && (
                <button
                  type="button"
                  className="btn-secondary"
                  onClick={() => selectPromptText("suggested")}
                >
                  全文を選択
                </button>
              )}
              {copyStatus?.target === "suggested" && (
                <span
                  className={`char-counter${copyStatus.failed ? " char-counter--error" : ""}`}
                  aria-live="polite"
                >
                  {copyStatus.message}
                </span>
              )}
            </div>
          </div>
        </section>
      )}

      {/* ── Unresolved items ─────────────────────────── */}
      {unresolvedItems.length > 0 && (
        <section className="review-unresolved-items" aria-labelledby="unresolved-items-heading">
          <h2 id="unresolved-items-heading" className="section-title">
            未解決ResearchItems
          </h2>
          <div className="item-table-wrap">
            <table className="item-table">
              <thead>
                <tr>
                  <th scope="col">Item</th>
                  <th scope="col">Severity</th>
                  <th scope="col">Status</th>
                  <th scope="col">Failure mode</th>
                  <th scope="col">Unresolved reason</th>
                </tr>
              </thead>
              <tbody>
                {unresolvedItems.map((item) => (
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
                    <td>{item.unresolved_reason ?? item.question}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </section>
      )}

      {/* ── Report preview ────────────────────────────── */}
      <section className="review-report" aria-labelledby="report-preview-heading">
        <h2 id="report-preview-heading" className="section-title">最新レポートプレビュー</h2>
        <div className="report-preview-scroll">
          <Markdown source={payload.latest_report} />
        </div>
      </section>

      {pendingManualRerun && (
        <section className="review-manual-rerun" aria-labelledby="manual-rerun-heading">
          <h2 id="manual-rerun-heading" className="section-title">
            ChatGPT手動rerun
          </h2>
          <div className="latest-review-card">
            <div className="latest-review-header">
              <span className="reviewer-confidence">
                {pendingManualRerun.scope === "full_rerun" ? "Full rerun" : "Targeted rerun"}
              </span>
              <span className="reviewer-confidence">
                出力: {expectedOutputLabel(
                  pendingManualRerun.expected_output_kind,
                  pendingManualRerun.scope,
                )}
              </span>
              <span className="reviewer-confidence">
                {pendingManualRerun.rerun_id}
              </span>
              <span className="reviewer-confidence">
                Deep Research {pendingManualRerun.expected_run_no}回目
              </span>
            </div>
            <ol className="manual-rerun-steps">
              <li>プロンプトをコピーまたはダウンロードする</li>
              <li>ChatGPTのDeep Researchで実行する</li>
              <li>
                {pendingManualRerun.scope === "full_rerun"
                  ? "完成版レポート全文をアップロードする"
                  : "既存レポートへ追加する差分セクションをアップロードする"}
              </li>
            </ol>
            <textarea
              aria-label="ChatGPT手動rerunプロンプト本文"
              className="prompt-attempt-body"
              ref={pendingPromptRef}
              readOnly
              rows={12}
              value={pendingManualRerun.prompt}
            />
            <div className="form-actions">
              <button
                type="button"
                className="btn-secondary"
                onClick={() => void handleCopyPrompt("pending", pendingManualRerun.prompt)}
              >
                コピー
              </button>
              <button
                type="button"
                className="btn-secondary"
                onClick={handleDownloadManualPrompt}
              >
                .md ダウンロード
              </button>
              {copyStatus?.target === "pending" && copyStatus.failed && (
                <button
                  type="button"
                  className="btn-secondary"
                  onClick={() => selectPromptText("pending")}
                >
                  全文を選択
                </button>
              )}
              {copyStatus?.target === "pending" && (
                <span
                  className={`char-counter${copyStatus.failed ? " char-counter--error" : ""}`}
                  aria-live="polite"
                >
                  {copyStatus.message}
                </span>
              )}
            </div>
          </div>

          <form className="manual-rerun-upload" onSubmit={handleManualRerunUpload}>
            <fieldset className="source-fieldset">
              <legend className="form-label">
                {pendingManualRerun.scope === "full_rerun"
                  ? "完成版レポート全文"
                  : "差分セクション"}
              </legend>
              <div className="source-switch">
                <label>
                  <input
                    type="radio"
                    name="manual-rerun-result-source"
                    checked={manualUploadMode === "text"}
                    onChange={() => handleManualUploadModeChange("text")}
                    disabled={manualUploading}
                  />
                  テキスト
                </label>
                <label>
                  <input
                    type="radio"
                    name="manual-rerun-result-source"
                    checked={manualUploadMode === "file"}
                    onChange={() => handleManualUploadModeChange("file")}
                    disabled={manualUploading}
                  />
                  ファイル
                </label>
              </div>
            </fieldset>
            {manualUploadMode === "text" ? (
              <textarea
                className="comment-textarea"
                value={manualResultText}
                onChange={(event) => handleManualResultTextChange(event.target.value)}
                rows={10}
                aria-label="Rerun結果テキスト"
                disabled={manualUploading}
              />
            ) : (
              <div className="file-input-row">
                <input
                  type="file"
                  accept=".md,.txt,text/markdown,text/plain"
                  aria-label="Rerun結果ファイル"
                  onChange={(event) =>
                    handleManualResultFileChange(event.target.files?.[0] ?? null)
                  }
                  disabled={manualUploading}
                />
                {manualResultFile && (
                  <span className="file-input-meta">{manualResultFile.name}</span>
                )}
              </div>
            )}
            {manualUploadError && (
              <div className="review-submit-error" role="alert">
                {manualUploadError}
              </div>
            )}
            <div className="form-actions">
              <button
                type="submit"
                className="btn-primary"
                disabled={manualUploading}
                aria-busy={manualUploading}
              >
                {manualUploading ? "アップロード中..." : "結果をアップロード"}
              </button>
            </div>
          </form>
        </section>
      )}

      {!pendingManualRerun && (
        <>
      {/* ── Comment ───────────────────────────────────── */}
      <section className="review-comment">
        <label className="form-label" htmlFor="review-comment">
          コメント（任意）
        </label>
        <textarea
          id="review-comment"
          className="comment-textarea"
          value={comment}
          onChange={(e) => setComment(e.target.value)}
          maxLength={MAX_COMMENT_CHARS}
          rows={4}
          placeholder="判断の理由や補足を入力..."
          disabled={submitting}
          aria-describedby="comment-count"
        />
        <span id="comment-count" className="char-counter">
          {comment.length}/{MAX_COMMENT_CHARS}
        </span>
      </section>

      {submitError && (
        <div className="review-submit-error" role="alert">
          {submitError}
        </div>
      )}

      {/* ── Decision buttons (I-4) ────────────────────── */}
      <section className="review-decisions" aria-labelledby="decision-heading">
        <h2 id="decision-heading" className="section-title">判断を選択してください</h2>
        <div className="decision-groups">
          <div className="decision-group">
            <h3 className="decision-group-title">完了する</h3>
            <div className="decision-buttons">
              {renderDecision("approve", "承認", "現状で最終化", "success")}
              {renderDecision(
                "approve_with_limitation",
                "制約付き承認",
                "未確認点を明示して完了",
                "success",
              )}
              {renderDecision("reject", "却下", "部分版で停止", "danger")}
            </div>
          </div>

          <div className="decision-group">
            <h3 className="decision-group-title">APIで自動改善</h3>
            <div className="decision-buttons">
              {renderDecision(
                "request_review",
                "レビュー再実行",
                "GPT-5.5で再レビュー",
                "neutral",
              )}
              {renderDecision(
                "request_llm_patch",
                "LLM patch",
                "GPT-5.5で差分修正",
                "warning",
              )}
              {renderDecision(
                "request_verification",
                "検証",
                "対象itemだけ検証",
                "neutral",
              )}
              {renderDecision(
                "request_targeted_rerun",
                "APIで部分再調査 (Targeted)",
                "未解決itemだけDeep Research再実行",
                "neutral",
                {
                  costHint: "追加コスト発生予定",
                  guardMessage: targetedRerunGuardMessage,
                },
              )}
              {renderDecision(
                "request_full_rerun",
                latestReportIsEmpty ? "APIで空レポート復旧 (Full)" : "APIで全面再調査 (Full)",
                "Deep Researchを最初から再実行",
                "warning",
                {
                  costHint: "追加コスト発生予定",
                  guardMessage: fullRerunGuardMessage,
                },
              )}
            </div>
          </div>

          <div className="decision-group">
            <h3 className="decision-group-title">ChatGPTで手動実行</h3>
            <div className="decision-buttons">
              {renderDecision(
                "request_manual_targeted_rerun",
                "ChatGPTで部分補強 (Targeted)",
                "未解決itemだけの差分を作成してアップロード",
                "neutral",
                { guardMessage: targetedRerunGuardMessage },
              )}
              {renderDecision(
                "request_manual_full_rerun",
                "ChatGPTで全面作り直し (Full)",
                "完成版レポート全文を作成してアップロード",
                "warning",
                { guardMessage: fullRerunGuardMessage },
              )}
            </div>
          </div>

          <div className="decision-group">
            <h3 className="decision-group-title">構造を見直す</h3>
            <div className="decision-buttons">
              {renderDecision(
                "request_item_revision",
                "ResearchItem見直し",
                "評価項目や分解を見直す",
                "warning",
              )}
            </div>
          </div>
        </div>
      </section>
        </>
      )}
    </div>
  );
}
