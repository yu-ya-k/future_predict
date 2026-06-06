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
import { OPTION_BOUNDS } from "../types";

const MAX_PROMPT_CHARS = 50_000;

export function NewResearch() {
  const defaults = useRef(loadResearchDefaults());

  const [prompt, setPrompt] = useState("");
  const [showAdvanced, setShowAdvanced] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  // Advanced options — initialised from localStorage defaults
  const [maxDeepResearch, setMaxDeepResearch] = useState(
    defaults.current.max_deep_research_runs ?? FACTORY_RESEARCH_DEFAULTS.max_deep_research_runs,
  );
  const [maxLlmFix, setMaxLlmFix] = useState(
    defaults.current.max_llm_fix_runs ?? FACTORY_RESEARCH_DEFAULTS.max_llm_fix_runs,
  );
  const [maxIterations, setMaxIterations] = useState(
    defaults.current.max_total_iterations ?? FACTORY_RESEARCH_DEFAULTS.max_total_iterations,
  );
  const [maxNoProgress, setMaxNoProgress] = useState(
    defaults.current.max_no_progress_rounds ?? FACTORY_RESEARCH_DEFAULTS.max_no_progress_rounds,
  );
  const [maxCost, setMaxCost] = useState(
    defaults.current.max_cost_usd ?? FACTORY_RESEARCH_DEFAULTS.max_cost_usd,
  );
  const [maxToolCalls, setMaxToolCalls] = useState(
    defaults.current.max_total_tool_calls ?? FACTORY_RESEARCH_DEFAULTS.max_total_tool_calls,
  );

  // Reload defaults when storage changes (e.g. from Settings tab)
  useEffect(() => {
    function onStorage(e: StorageEvent) {
      if (e.key !== RESEARCH_DEFAULTS_STORAGE_KEY) return;
      const d = loadResearchDefaults();
      setMaxDeepResearch(d.max_deep_research_runs);
      setMaxLlmFix(d.max_llm_fix_runs);
      setMaxIterations(d.max_total_iterations);
      setMaxNoProgress(d.max_no_progress_rounds);
      setMaxCost(d.max_cost_usd);
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
        options: {
          max_deep_research_runs: maxDeepResearch,
          max_llm_fix_runs: maxLlmFix,
          max_total_iterations: maxIterations,
          max_no_progress_rounds: maxNoProgress,
          max_cost_usd: maxCost,
          max_total_tool_calls: maxToolCalls,
        },
      });

      // Derive title from first line of prompt
      const title = promptTrimmed.split("\n")[0].slice(0, 120);

      trackRun({
        run_id: response.run_id,
        title,
        max_cost_usd: maxCost,
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

  return (
    <div className="screen-new">
      <header className="screen-header">
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
                  <label className="option-label" htmlFor="max-deep-research">
                    最大Deep Research回数
                  </label>
                  <input
                    id="max-deep-research"
                    type="number"
                    className="option-input"
                    value={maxDeepResearch}
                    min={OPTION_BOUNDS.max_deep_research_runs.min}
                    max={OPTION_BOUNDS.max_deep_research_runs.max}
                    onChange={(e) => setMaxDeepResearch(Number(e.target.value))}
                    disabled={submitting}
                  />
                  <span className="option-range">
                    {OPTION_BOUNDS.max_deep_research_runs.min}–{OPTION_BOUNDS.max_deep_research_runs.max}
                  </span>
                </div>

                <div className="option-field">
                  <label className="option-label" htmlFor="max-llm-fix">
                    最大LLM修正回数
                  </label>
                  <input
                    id="max-llm-fix"
                    type="number"
                    className="option-input"
                    value={maxLlmFix}
                    min={OPTION_BOUNDS.max_llm_fix_runs.min}
                    max={OPTION_BOUNDS.max_llm_fix_runs.max}
                    onChange={(e) => setMaxLlmFix(Number(e.target.value))}
                    disabled={submitting}
                  />
                  <span className="option-range">
                    {OPTION_BOUNDS.max_llm_fix_runs.min}–{OPTION_BOUNDS.max_llm_fix_runs.max}
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
                  <label className="option-label" htmlFor="max-no-progress">
                    最大停滞許容回数
                  </label>
                  <input
                    id="max-no-progress"
                    type="number"
                    className="option-input"
                    value={maxNoProgress}
                    min={OPTION_BOUNDS.max_no_progress_rounds.min}
                    max={OPTION_BOUNDS.max_no_progress_rounds.max}
                    onChange={(e) => setMaxNoProgress(Number(e.target.value))}
                    disabled={submitting}
                  />
                  <span className="option-range">
                    {OPTION_BOUNDS.max_no_progress_rounds.min}–{OPTION_BOUNDS.max_no_progress_rounds.max}
                  </span>
                </div>

                <div className="option-field">
                  <label className="option-label" htmlFor="max-cost">
                    最大コスト (USD)
                  </label>
                  <input
                    id="max-cost"
                    type="number"
                    className="option-input"
                    value={maxCost}
                    min={0.5}
                    max={100}
                    step={0.5}
                    onChange={(e) => setMaxCost(Number(e.target.value))}
                    disabled={submitting}
                  />
                  <span className="option-range">$0.50–$100</span>
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
                    min={10}
                    max={1000}
                    step={10}
                    onChange={(e) => setMaxToolCalls(Number(e.target.value))}
                    disabled={submitting}
                  />
                  <span className="option-range">10–1000</span>
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
