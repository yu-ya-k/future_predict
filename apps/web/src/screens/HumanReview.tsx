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
import { navigate, routes } from "../router";
import {
  type HumanReviewAction,
  type HumanReviewPayload,
  type ResearchItem,
} from "../types";

const NO_PROGRESS_WARN_THRESHOLD = 2;
const MAX_COMMENT_CHARS = 10_000;

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

function safeManualRerunFilename(runId: string, rerunId: string): string {
  const safeRunId = runId.replace(/[^a-zA-Z0-9._-]+/g, "-").replace(/^-+|-+$/g, "");
  const safeRerunId = rerunId.replace(/[^a-zA-Z0-9._-]+/g, "-").replace(/^-+|-+$/g, "");
  return `${safeRunId || "research-run"}-${safeRerunId || "manual-rerun"}-prompt.md`;
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

export function HumanReview({ runId }: HumanReviewProps) {
  const [payload, setPayload] = useState<HumanReviewPayload | null>(null);
  const [loading, setLoading] = useState(true);
  const [loadError, setLoadError] = useState<string | null>(null);

  const [comment, setComment] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [submitError, setSubmitError] = useState<string | null>(null);
  const [manualResultText, setManualResultText] = useState("");
  const [manualResultFile, setManualResultFile] = useState<File | null>(null);
  const [manualUploadMode, setManualUploadMode] = useState<"text" | "file">("text");
  const [manualUploading, setManualUploading] = useState(false);
  const [manualUploadError, setManualUploadError] = useState<string | null>(null);
  const [copyStatus, setCopyStatus] = useState<string | null>(null);

  const abortRef = useRef<AbortController | null>(null);
  const requestGenerationRef = useRef(0);

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
    void fetchPayload();
    return () => {
      requestGenerationRef.current += 1;
      abortRef.current?.abort();
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [runId]);

  async function handleDecision(action: HumanReviewAction) {
    setSubmitting(true);
    setSubmitError(null);
    try {
      await resumeRun(runId, {
        action,
        comment: comment.trim() || null,
      });
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

  async function handleCopyManualPrompt() {
    const prompt = payload?.pending_manual_rerun?.prompt;
    if (!prompt) return;
    if (!navigator.clipboard?.writeText) {
      setCopyStatus("コピーできませんでした");
      return;
    }
    try {
      await navigator.clipboard.writeText(prompt);
      setCopyStatus("コピーしました");
    } catch {
      setCopyStatus("コピーできませんでした");
    }
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
  const unresolvedItems = payload.unresolved_items ?? [];
  const latestItemAssessments = latest_review?.item_assessments ?? [];

  const noProgressWarn =
    audit_summary.no_progress_count >= NO_PROGRESS_WARN_THRESHOLD;
  const latestReportIsEmpty = payload.latest_report.trim().length === 0;

  const isAllowed = (action: HumanReviewAction) => allowed_actions.includes(action);
  const canRetryReview = isAllowed("request_review");

  const targetedRerunGuardMessage = noProgressWarn
    ? `改善停滞が${audit_summary.no_progress_count}回続いています。同じitemへの再実行効果は限定的かもしれません。`
    : undefined;
  const fullRerunButton = (
    <DecisionButton
      action="request_full_rerun"
      label="全体再実行"
      consequence="Deep Researchを最初から再実行"
      tone="warning"
      costHint="追加コスト発生予定"
      disabled={!isAllowed("request_full_rerun") || submitting}
      block
      onClick={() => void handleDecision("request_full_rerun")}
    />
  );

  return (
    <div className="screen-review">
      <header className="screen-header">
        <BackLink to={routes().monitor(runId)} label="Runへ戻る" />
        <h1 className="screen-title">判断</h1>
      </header>

      {/* ── Stop-reason banner ────────────────────────── */}
      <div className="review-reason-banner" role="note">
        <span className="review-reason-label">停止理由</span>
        <p className="review-reason-text">{payload.reason}</p>
      </div>

      {/* ── Warnings ──────────────────────────────────── */}
      {payload.warnings.length > 0 && (
        <div className="review-warnings" role="alert">
          <ul>
            {payload.warnings.map((w, i) => (
              <li key={i}>{w}</li>
            ))}
          </ul>
        </div>
      )}

      {/* ── High-risk flags ───────────────────────────── */}
      {latest_review?.high_risk_flags && latest_review.high_risk_flags.length > 0 && (
        <div className="review-risk-flags" role="alert">
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
                {pendingManualRerun.rerun_id}
              </span>
              <span className="reviewer-confidence">
                Deep Research {pendingManualRerun.expected_run_no}回目
              </span>
            </div>
            <pre className="prompt-attempt-body">{pendingManualRerun.prompt}</pre>
            <div className="form-actions">
              <button
                type="button"
                className="btn-secondary"
                onClick={() => void handleCopyManualPrompt()}
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
              {copyStatus && (
                <span className="char-counter" aria-live="polite">
                  {copyStatus}
                </span>
              )}
            </div>
          </div>

          <form className="manual-rerun-upload" onSubmit={handleManualRerunUpload}>
            <fieldset className="source-fieldset">
              <legend className="form-label">Rerun結果</legend>
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
        <div className="decision-buttons">
          {latestReportIsEmpty && fullRerunButton}
          <DecisionButton
            action="approve"
            label="承認"
            consequence="現状で最終化"
            tone="success"
            disabled={!isAllowed("approve") || submitting}
            block
            onClick={() => void handleDecision("approve")}
          />
          <DecisionButton
            action="approve_with_limitation"
            label="制約付き承認"
            consequence="未確認点を明示して完了"
            tone="success"
            disabled={!isAllowed("approve_with_limitation") || submitting}
            block
            onClick={() => void handleDecision("approve_with_limitation")}
          />
          {canRetryReview && (
            <DecisionButton
              action="request_review"
              label="レビュー再実行"
              consequence="GPT-5.5で再レビュー"
              tone="neutral"
              disabled={submitting}
              block
              onClick={() => void handleDecision("request_review")}
            />
          )}
          <DecisionButton
            action="request_llm_patch"
            label="LLM patch"
            consequence="GPT-5.5で差分修正"
            tone="warning"
            disabled={!isAllowed("request_llm_patch") || submitting}
            block
            onClick={() => void handleDecision("request_llm_patch")}
          />
          <DecisionButton
            action="request_verification"
            label="検証"
            consequence="対象itemだけ検証"
            tone="neutral"
            disabled={!isAllowed("request_verification") || submitting}
            block
            onClick={() => void handleDecision("request_verification")}
          />
          <DecisionButton
            action="request_targeted_rerun"
            label="Targeted rerun"
            consequence="未解決itemだけ再実行"
            tone="neutral"
            costHint={`追加コスト発生予定`}
            guardMessage={targetedRerunGuardMessage}
            disabled={!isAllowed("request_targeted_rerun") || submitting}
            block
            onClick={() => void handleDecision("request_targeted_rerun")}
          />
          {!latestReportIsEmpty && fullRerunButton}
          <DecisionButton
            action="request_item_revision"
            label="Item revision"
            consequence="ResearchItemを見直す"
            tone="warning"
            disabled={!isAllowed("request_item_revision") || submitting}
            block
            onClick={() => void handleDecision("request_item_revision")}
          />
          <DecisionButton
            action="reject"
            label="却下"
            consequence="部分版で停止"
            tone="danger"
            disabled={!isAllowed("reject") || submitting}
            block
            onClick={() => void handleDecision("reject")}
          />
        </div>
      </section>
        </>
      )}
    </div>
  );
}
