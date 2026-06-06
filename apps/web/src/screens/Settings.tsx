/**
 * SCR-7: Settings — local default editor.
 *
 * No save API (整合注記: settings API not yet implemented).
 * Persists defaults to localStorage key "dro.defaults" so NewResearch can
 * read them. Numeric inputs are bounded by OPTION_BOUNDS.
 */

import { useEffect, useState } from "react";

import {
  FACTORY_RESEARCH_DEFAULTS,
  loadResearchDefaults,
  saveResearchDefaults,
  type ResearchDefaults,
} from "../researchDefaults";
import { OPTION_BOUNDS } from "../types";

export function Settings() {
  const [defaults, setDefaults] = useState<ResearchDefaults>(() => loadResearchDefaults());
  const [saved, setSaved] = useState(false);

  // Persist whenever defaults change (auto-save on blur/change)
  useEffect(() => {
    saveResearchDefaults(defaults);
  }, [defaults]);

  function handleChange(key: keyof ResearchDefaults, value: number) {
    setDefaults((prev) => ({ ...prev, [key]: value }));
    setSaved(false);
  }

  function handleSave() {
    saveResearchDefaults(defaults);
    setSaved(true);
    setTimeout(() => setSaved(false), 2000);
  }

  function handleReset() {
    setDefaults({ ...FACTORY_RESEARCH_DEFAULTS });
    setSaved(false);
  }

  return (
    <div className="screen-settings">
      <header className="screen-header">
        <h1 className="screen-title">設定</h1>
        <p className="screen-subtitle">
          新規リサーチのデフォルト値を設定します。変更はこのブラウザにのみ保存されます。
        </p>
        <p className="settings-api-note" role="note">
          注: 設定APIは未実装のため、サーバー側には保存されません（ローカルのみ）。
        </p>
      </header>

      {/* ── Default numeric options ──────────────────────────────────────── */}
      <section className="settings-section" aria-labelledby="defaults-heading">
        <h2 id="defaults-heading" className="section-title">デフォルトオプション</h2>
        <div className="settings-grid">
          <div className="settings-field">
            <label className="settings-label" htmlFor="s-max-deep-research">
              最大Deep Research回数
            </label>
            <input
              id="s-max-deep-research"
              type="number"
              className="settings-input"
              value={defaults.max_deep_research_runs}
              min={OPTION_BOUNDS.max_deep_research_runs.min}
              max={OPTION_BOUNDS.max_deep_research_runs.max}
              onChange={(e) =>
                handleChange("max_deep_research_runs", Number(e.target.value))
              }
            />
            <span className="settings-range">
              {OPTION_BOUNDS.max_deep_research_runs.min}–{OPTION_BOUNDS.max_deep_research_runs.max}
            </span>
          </div>

          <div className="settings-field">
            <label className="settings-label" htmlFor="s-max-llm-fix">
              最大LLM修正回数
            </label>
            <input
              id="s-max-llm-fix"
              type="number"
              className="settings-input"
              value={defaults.max_llm_fix_runs}
              min={OPTION_BOUNDS.max_llm_fix_runs.min}
              max={OPTION_BOUNDS.max_llm_fix_runs.max}
              onChange={(e) => handleChange("max_llm_fix_runs", Number(e.target.value))}
            />
            <span className="settings-range">
              {OPTION_BOUNDS.max_llm_fix_runs.min}–{OPTION_BOUNDS.max_llm_fix_runs.max}
            </span>
          </div>

          <div className="settings-field">
            <label className="settings-label" htmlFor="s-max-iterations">
              最大反復回数
            </label>
            <input
              id="s-max-iterations"
              type="number"
              className="settings-input"
              value={defaults.max_total_iterations}
              min={OPTION_BOUNDS.max_total_iterations.min}
              max={OPTION_BOUNDS.max_total_iterations.max}
              onChange={(e) =>
                handleChange("max_total_iterations", Number(e.target.value))
              }
            />
            <span className="settings-range">
              {OPTION_BOUNDS.max_total_iterations.min}–{OPTION_BOUNDS.max_total_iterations.max}
            </span>
          </div>

          <div className="settings-field">
            <label className="settings-label" htmlFor="s-max-no-progress">
              最大停滞許容回数
            </label>
            <input
              id="s-max-no-progress"
              type="number"
              className="settings-input"
              value={defaults.max_no_progress_rounds}
              min={OPTION_BOUNDS.max_no_progress_rounds.min}
              max={OPTION_BOUNDS.max_no_progress_rounds.max}
              onChange={(e) =>
                handleChange("max_no_progress_rounds", Number(e.target.value))
              }
            />
            <span className="settings-range">
              {OPTION_BOUNDS.max_no_progress_rounds.min}–{OPTION_BOUNDS.max_no_progress_rounds.max}
            </span>
          </div>

          <div className="settings-field">
            <label className="settings-label" htmlFor="s-max-cost">
              最大コスト (USD)
            </label>
            <input
              id="s-max-cost"
              type="number"
              className="settings-input"
              value={defaults.max_cost_usd}
              min={0.5}
              max={100}
              step={0.5}
              onChange={(e) => handleChange("max_cost_usd", Number(e.target.value))}
            />
            <span className="settings-range">$0.50–$100</span>
          </div>

          <div className="settings-field">
            <label className="settings-label" htmlFor="s-max-tool-calls">
              最大ツール呼び出し数
            </label>
            <input
              id="s-max-tool-calls"
              type="number"
              className="settings-input"
              value={defaults.max_total_tool_calls}
              min={10}
              max={1000}
              step={10}
              onChange={(e) =>
                handleChange("max_total_tool_calls", Number(e.target.value))
              }
            />
            <span className="settings-range">10–1000</span>
          </div>
        </div>

        <div className="settings-actions">
          <button type="button" className="btn-primary" onClick={handleSave}>
            {saved ? "保存しました" : "保存"}
          </button>
          <button type="button" className="btn-secondary" onClick={handleReset}>
            デフォルトに戻す
          </button>
        </div>
      </section>

    </div>
  );
}
