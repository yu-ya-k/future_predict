/**
 * SCR-1: New Research — create a new research run.
 *
 * Integrations:
 *  - OPTION_BOUNDS: bounded numeric inputs.
 *  - requestNotificationPermission before submit.
 *  - createRun → trackRun → navigate to monitor.
 *
 * NOTE: The "auto brief preview/edit" feature requires a backend endpoint that
 * returns a structured brief before the run starts. This is a future backend
 * feature (SCR-1 整合注記); we go straight to the run after create.
 */

import { useEffect, useRef, useState } from "react";

import { BackLink } from "../components";
import { createRun } from "../api/research";
import { ApiError } from "../api/client";
import { requestNotificationPermission } from "../notifications";
import {
  FACTORY_RESEARCH_DEFAULTS,
  loadResearchDefaults,
  RESEARCH_DEFAULTS_STORAGE_KEY,
} from "../researchDefaults";
import { navigate, routes } from "../router";
import { trackRun } from "../runStore";
import { OPTION_BOUNDS, type ContextClassification } from "../types";

const MAX_PROMPT_CHARS = 50_000;

export function NewResearch() {
  const defaults = useRef(loadResearchDefaults());

  const [prompt, setPrompt] = useState("");
  const [showAdvanced, setShowAdvanced] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [contextClassification, setContextClassification] =
    useState<ContextClassification>("public");

  // Advanced options — initialised from localStorage defaults
  const [maxTargetedRerun, setMaxTargetedRerun] = useState(
    defaults.current.max_targeted_rerun_runs ??
      FACTORY_RESEARCH_DEFAULTS.max_targeted_rerun_runs,
  );
  const [maxFullRerun, setMaxFullRerun] = useState(
    defaults.current.max_full_rerun_runs ?? FACTORY_RESEARCH_DEFAULTS.max_full_rerun_runs,
  );
  const [maxLlmPatch, setMaxLlmPatch] = useState(
    defaults.current.max_llm_patch_runs ?? FACTORY_RESEARCH_DEFAULTS.max_llm_patch_runs,
  );
  const [maxVerification, setMaxVerification] = useState(
    defaults.current.max_verification_runs ??
      FACTORY_RESEARCH_DEFAULTS.max_verification_runs,
  );
  const [maxIterations, setMaxIterations] = useState(
    defaults.current.max_total_iterations ?? FACTORY_RESEARCH_DEFAULTS.max_total_iterations,
  );
  const [maxToolCalls, setMaxToolCalls] = useState(
    defaults.current.max_total_tool_calls ?? FACTORY_RESEARCH_DEFAULTS.max_total_tool_calls,
  );

  // Reload defaults when storage changes (e.g. from Settings tab)
  useEffect(() => {
    function onStorage(e: StorageEvent) {
      if (e.key !== RESEARCH_DEFAULTS_STORAGE_KEY) return;
      const d = loadResearchDefaults();
      setMaxTargetedRerun(d.max_targeted_rerun_runs);
      setMaxFullRerun(d.max_full_rerun_runs);
      setMaxLlmPatch(d.max_llm_patch_runs);
      setMaxVerification(d.max_verification_runs);
      setMaxIterations(d.max_total_iterations);
      setMaxToolCalls(d.max_total_tool_calls);
    }
    window.addEventListener("storage", onStorage);
    return () => window.removeEventListener("storage", onStorage);
  }, []);

  const promptTrimmed = prompt.trim();
  const canSubmit = promptTrimmed.length > 0 && promptTrimmed.length <= MAX_PROMPT_CHARS && !submitting;

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    if (!canSubmit) return;
    setSubmitting(true);
    setError(null);

    try {
      await requestNotificationPermission();

      const response = await createRun({
        user_prompt: promptTrimmed,
        context_classification: contextClassification,
        options: {
          max_targeted_rerun_runs: maxTargetedRerun,
          max_full_rerun_runs: maxFullRerun,
          max_llm_patch_runs: maxLlmPatch,
          max_verification_runs: maxVerification,
          max_total_iterations: maxIterations,
          max_total_tool_calls: maxToolCalls,
        },
      });

      // Derive title from first line of prompt
      const title = promptTrimmed.split("\n")[0].slice(0, 120);

      trackRun({
        run_id: response.run_id,
        title,
        max_total_iterations: maxIterations,
        created_at: response.created_at,
        last_status: response.status,
      });

      navigate(routes().monitor(response.run_id));
    } catch (err) {
      if (err instanceof ApiError) {
        setError(err.detail ?? err.message);
      } else if (err instanceof Error) {
        setError(err.message);
      } else {
        setError("予期しないエラーが発生しました");
      }
      setSubmitting(false);
    }
  }

  const remaining = MAX_PROMPT_CHARS - prompt.length;
  const overLimit = prompt.length > MAX_PROMPT_CHARS;
  const nonPublicContextSelected = contextClassification !== "public";

  return (
    <div className="screen-new">
      <header className="screen-header">
        <BackLink to={routes().dashboard} label="ダッシュボードへ戻る" />
        <h1 className="screen-title">新規リサーチを開始</h1>
        <p className="screen-subtitle">
          調査したい内容を入力してください。AIが段階的にリサーチを行い、品質レビューを経て最終レポートを生成します。
        </p>
      </header>

      <form className="new-research-form" onSubmit={handleSubmit} noValidate>
        {/* Prompt textarea */}
        <section className="form-section">
          <label className="form-label" htmlFor="prompt">
            リサーチ内容
            <span className="form-required" aria-hidden="true">*</span>
          </label>
          <textarea
            id="prompt"
            className={`prompt-textarea${overLimit ? " prompt-textarea--error" : ""}`}
            value={prompt}
            onChange={(e) => setPrompt(e.target.value)}
            placeholder="調査したいテーマや質問を入力してください..."
            rows={10}
            aria-describedby="prompt-count"
            disabled={submitting}
            required
          />
          <div className="prompt-meta">
            <span
              id="prompt-count"
              className={`char-counter${overLimit ? " char-counter--error" : ""}`}
              aria-live="polite"
            >
              {overLimit
                ? `${Math.abs(remaining).toLocaleString()} 文字オーバー`
                : `残り ${remaining.toLocaleString()} 文字`}
            </span>
          </div>
        </section>

        <section className="form-section">
          <label className="form-label" htmlFor="context-classification">
            コンテキスト分類
            <span className="form-required" aria-hidden="true">*</span>
          </label>
          <select
            id="context-classification"
            className="option-input"
            value={contextClassification}
            onChange={(e) =>
              setContextClassification(e.target.value as ContextClassification)
            }
            disabled={submitting}
            required
          >
            <option value="public">public</option>
            <option value="internal">internal</option>
            <option value="confidential">confidential</option>
            <option value="mixed">mixed</option>
          </select>
          {nonPublicContextSelected && (
            <div className="form-warning" role="alert">
              public web Deep Research は policy により送信されず、人間レビューで停止します。
            </div>
          )}
        </section>

        {/* Advanced options (collapsible) */}
        <section className="form-section">
          <button
            type="button"
            className="advanced-toggle"
            aria-expanded={showAdvanced}
            onClick={() => setShowAdvanced(!showAdvanced)}
          >
            <span className="advanced-toggle-icon" aria-hidden="true">
              {showAdvanced ? "▲" : "▼"}
            </span>
            詳細オプション
          </button>

          {showAdvanced && (
            <div className="advanced-options" role="group" aria-label="詳細オプション">
              <div className="options-grid">
                <div className="option-field">
                  <label className="option-label" htmlFor="max-targeted-rerun">
                    最大Targeted rerun回数
                  </label>
                  <input
                    id="max-targeted-rerun"
                    type="number"
                    className="option-input"
                    value={maxTargetedRerun}
                    min={OPTION_BOUNDS.max_targeted_rerun_runs.min}
                    max={OPTION_BOUNDS.max_targeted_rerun_runs.max}
                    onChange={(e) => setMaxTargetedRerun(Number(e.target.value))}
                    disabled={submitting}
                  />
                  <span className="option-range">
                    {OPTION_BOUNDS.max_targeted_rerun_runs.min}–{OPTION_BOUNDS.max_targeted_rerun_runs.max}
                  </span>
                </div>

                <div className="option-field">
                  <label className="option-label" htmlFor="max-full-rerun">
                    最大Full rerun回数
                  </label>
                  <input
                    id="max-full-rerun"
                    type="number"
                    className="option-input"
                    value={maxFullRerun}
                    min={OPTION_BOUNDS.max_full_rerun_runs.min}
                    max={OPTION_BOUNDS.max_full_rerun_runs.max}
                    onChange={(e) => setMaxFullRerun(Number(e.target.value))}
                    disabled={submitting}
                  />
                  <span className="option-range">
                    {OPTION_BOUNDS.max_full_rerun_runs.min}–{OPTION_BOUNDS.max_full_rerun_runs.max}
                  </span>
                </div>

                <div className="option-field">
                  <label className="option-label" htmlFor="max-llm-patch">
                    最大LLM patch回数
                  </label>
                  <input
                    id="max-llm-patch"
                    type="number"
                    className="option-input"
                    value={maxLlmPatch}
                    min={OPTION_BOUNDS.max_llm_patch_runs.min}
                    max={OPTION_BOUNDS.max_llm_patch_runs.max}
                    onChange={(e) => setMaxLlmPatch(Number(e.target.value))}
                    disabled={submitting}
                  />
                  <span className="option-range">
                    {OPTION_BOUNDS.max_llm_patch_runs.min}–{OPTION_BOUNDS.max_llm_patch_runs.max}
                  </span>
                </div>

                <div className="option-field">
                  <label className="option-label" htmlFor="max-verification">
                    最大Verification回数
                  </label>
                  <input
                    id="max-verification"
                    type="number"
                    className="option-input"
                    value={maxVerification}
                    min={OPTION_BOUNDS.max_verification_runs.min}
                    max={OPTION_BOUNDS.max_verification_runs.max}
                    onChange={(e) => setMaxVerification(Number(e.target.value))}
                    disabled={submitting}
                  />
                  <span className="option-range">
                    {OPTION_BOUNDS.max_verification_runs.min}–{OPTION_BOUNDS.max_verification_runs.max}
                  </span>
                </div>

                <div className="option-field">
                  <label className="option-label" htmlFor="max-iterations">
                    最大反復回数
                  </label>
                  <input
                    id="max-iterations"
                    type="number"
                    className="option-input"
                    value={maxIterations}
                    min={OPTION_BOUNDS.max_total_iterations.min}
                    max={OPTION_BOUNDS.max_total_iterations.max}
                    onChange={(e) => setMaxIterations(Number(e.target.value))}
                    disabled={submitting}
                  />
                  <span className="option-range">
                    {OPTION_BOUNDS.max_total_iterations.min}–{OPTION_BOUNDS.max_total_iterations.max}
                  </span>
                </div>

                <div className="option-field">
                  <label className="option-label" htmlFor="max-tool-calls">
                    最大ツール呼び出し数
                  </label>
                  <input
                    id="max-tool-calls"
                    type="number"
                    className="option-input"
                    value={maxToolCalls}
                    min={OPTION_BOUNDS.max_total_tool_calls.min}
                    max={OPTION_BOUNDS.max_total_tool_calls.max}
                    step={10}
                    onChange={(e) => setMaxToolCalls(Number(e.target.value))}
                    disabled={submitting}
                  />
                  <span className="option-range">
                    {OPTION_BOUNDS.max_total_tool_calls.min}–{OPTION_BOUNDS.max_total_tool_calls.max}
                  </span>
                </div>
              </div>
            </div>
          )}
        </section>

        {error && (
          <div className="form-error" role="alert">
            <strong>エラー:</strong> {error}
          </div>
        )}

        <div className="form-actions">
          <button
            type="submit"
            className="btn-primary btn-start"
            disabled={!canSubmit}
            aria-busy={submitting}
          >
            {submitting ? "開始中..." : "リサーチを開始"}
          </button>
        </div>
      </form>
    </div>
  );
}
