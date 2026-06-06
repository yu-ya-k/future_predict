/**
 * SCR-7: Settings — local default editor.
 *
 * No save API (整合注記: settings API not yet implemented).
 * Persists defaults to localStorage key "dro.defaults" so NewResearch can
 * read them. Displays web-search policy table (read-only, ContextBadge + WebSearchBadge).
 * Numeric inputs are bounded by OPTION_BOUNDS.
 */

import { useEffect, useState } from "react";

import { ContextBadge, WebSearchBadge } from "../components";
import {
  FACTORY_RESEARCH_DEFAULTS,
  loadResearchDefaults,
  saveResearchDefaults,
  type ResearchDefaults,
} from "../researchDefaults";
import { OPTION_BOUNDS, type ContextClassification } from "../types";

const WEB_SEARCH_POLICY: { context: ContextClassification; allowed: boolean; note: string }[] = [
  { context: "public", allowed: true, note: "Web検索有効" },
  { context: "internal", allowed: false, note: "機密区分により無効" },
  { context: "confidential", allowed: false, note: "機密区分により無効" },
  { context: "mixed", allowed: false, note: "公開主張の範囲のみ（実質無効）" },
];

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

      {/* ── Web search policy (read-only) ─────────────────────────────────── */}
      <section className="settings-section" aria-labelledby="policy-heading">
        <h2 id="policy-heading" className="section-title">Web検索ポリシー</h2>
        <p className="settings-policy-note">
          機密区分によってWeb検索の可否が自動的に決まります（I-1）。
        </p>
        <table className="policy-table" aria-label="機密区分とWeb検索の対応表">
          <thead>
            <tr>
              <th>機密区分</th>
              <th>Web検索</th>
              <th>備考</th>
            </tr>
          </thead>
          <tbody>
            {WEB_SEARCH_POLICY.map((row) => (
              <tr key={row.context}>
                <td>
                  <ContextBadge context={row.context} showLock />
                </td>
                <td>
                  <WebSearchBadge
                    webSearchAllowed={row.allowed}
                    context={row.context}
                    showReason
                  />
                </td>
                <td>{row.note}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </section>
    </div>
  );
}
