/**
 * SCR-7: Settings — local default editor.
 *
 * No save API (整合注記: settings API not yet implemented).
 * Persists defaults to localStorage key "dro.defaults" so NewResearch can
 * read them. Numeric inputs are bounded by OPTION_BOUNDS.
 */

import { useEffect, useState } from "react";

import { BackLink } from "../components";
import {
  FACTORY_RESEARCH_DEFAULTS,
  loadResearchDefaults,
  normalizeResearchDefaultValue,
  normalizeResearchDefaults,
  saveResearchDefaults,
  type ResearchDefaults,
} from "../researchDefaults";
import {
  clearResearchApiKey,
  getResearchApiKey,
  saveResearchApiKey,
} from "../researchApiKey";
import { routes } from "../router";
import { OPTION_BOUNDS } from "../types";

export function Settings() {
  const [defaults, setDefaults] = useState<ResearchDefaults>(() => loadResearchDefaults());
  const [saved, setSaved] = useState(false);
  const [apiKeyDraft, setApiKeyDraft] = useState(() => getResearchApiKey() ?? "");
  const [apiKeySaved, setApiKeySaved] = useState(false);

  // Persist whenever defaults change (auto-save on blur/change)
  useEffect(() => {
    saveResearchDefaults(defaults);
  }, [defaults]);

  function handleChange(key: keyof ResearchDefaults, value: string) {
    setDefaults((prev) => ({
      ...prev,
      [key]: normalizeResearchDefaultValue(key, value),
    }));
    setSaved(false);
  }

  function handleSave() {
    const normalized = normalizeResearchDefaults(defaults);
    setDefaults(normalized);
    saveResearchDefaults(normalized);
    setSaved(true);
    setTimeout(() => setSaved(false), 2000);
  }

  function handleReset() {
    setDefaults({ ...FACTORY_RESEARCH_DEFAULTS });
    setSaved(false);
  }

  function handleApiKeySave() {
    saveResearchApiKey(apiKeyDraft);
    setApiKeyDraft(apiKeyDraft.trim());
    setApiKeySaved(true);
    setTimeout(() => setApiKeySaved(false), 2000);
  }

  function handleApiKeyClear() {
    clearResearchApiKey();
    setApiKeyDraft("");
    setApiKeySaved(false);
  }

  return (
    <div className="screen-settings">
      <header className="screen-header">
        <BackLink to={routes().dashboard} label="ダッシュボードへ戻る" />
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
            <label className="settings-label" htmlFor="s-max-targeted-rerun">
              最大Targeted rerun回数
            </label>
            <input
              id="s-max-targeted-rerun"
              type="number"
              className="settings-input"
              value={defaults.max_targeted_rerun_runs}
              min={OPTION_BOUNDS.max_targeted_rerun_runs.min}
              max={OPTION_BOUNDS.max_targeted_rerun_runs.max}
              onChange={(e) =>
                handleChange("max_targeted_rerun_runs", e.target.value)
              }
            />
            <span className="settings-range">
              {OPTION_BOUNDS.max_targeted_rerun_runs.min}–{OPTION_BOUNDS.max_targeted_rerun_runs.max}
            </span>
          </div>

          <div className="settings-field">
            <label className="settings-label" htmlFor="s-max-full-rerun">
              最大Full rerun回数
            </label>
            <input
              id="s-max-full-rerun"
              type="number"
              className="settings-input"
              value={defaults.max_full_rerun_runs}
              min={OPTION_BOUNDS.max_full_rerun_runs.min}
              max={OPTION_BOUNDS.max_full_rerun_runs.max}
              onChange={(e) => handleChange("max_full_rerun_runs", e.target.value)}
            />
            <span className="settings-range">
              {OPTION_BOUNDS.max_full_rerun_runs.min}–{OPTION_BOUNDS.max_full_rerun_runs.max}
            </span>
          </div>

          <div className="settings-field">
            <label className="settings-label" htmlFor="s-max-llm-patch">
              最大LLM patch回数
            </label>
            <input
              id="s-max-llm-patch"
              type="number"
              className="settings-input"
              value={defaults.max_llm_patch_runs}
              min={OPTION_BOUNDS.max_llm_patch_runs.min}
              max={OPTION_BOUNDS.max_llm_patch_runs.max}
              onChange={(e) => handleChange("max_llm_patch_runs", e.target.value)}
            />
            <span className="settings-range">
              {OPTION_BOUNDS.max_llm_patch_runs.min}–{OPTION_BOUNDS.max_llm_patch_runs.max}
            </span>
          </div>

          <div className="settings-field">
            <label className="settings-label" htmlFor="s-max-verification">
              最大Verification回数
            </label>
            <input
              id="s-max-verification"
              type="number"
              className="settings-input"
              value={defaults.max_verification_runs}
              min={OPTION_BOUNDS.max_verification_runs.min}
              max={OPTION_BOUNDS.max_verification_runs.max}
              onChange={(e) =>
                handleChange("max_verification_runs", e.target.value)
              }
            />
            <span className="settings-range">
              {OPTION_BOUNDS.max_verification_runs.min}–{OPTION_BOUNDS.max_verification_runs.max}
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
                handleChange("max_total_iterations", e.target.value)
              }
            />
            <span className="settings-range">
              {OPTION_BOUNDS.max_total_iterations.min}–{OPTION_BOUNDS.max_total_iterations.max}
            </span>
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
              min={OPTION_BOUNDS.max_total_tool_calls.min}
              max={OPTION_BOUNDS.max_total_tool_calls.max}
              step={10}
              onChange={(e) =>
                handleChange("max_total_tool_calls", e.target.value)
              }
            />
            <span className="settings-range">
              {OPTION_BOUNDS.max_total_tool_calls.min}–{OPTION_BOUNDS.max_total_tool_calls.max}
            </span>
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

      <section className="settings-section" aria-labelledby="api-connection-heading">
        <h2 id="api-connection-heading" className="section-title">API接続</h2>
        <div className="settings-field settings-secret-field">
          <label className="settings-label" htmlFor="s-research-api-key">
            Research API key
          </label>
          <input
            id="s-research-api-key"
            type="password"
            className="settings-input"
            value={apiKeyDraft}
            autoComplete="off"
            spellCheck={false}
            placeholder="RESEARCH_API_KEY"
            onChange={(e) => {
              setApiKeyDraft(e.target.value);
              setApiKeySaved(false);
            }}
          />
          <span className="settings-range">
            RESEARCH_API_KEY が設定されたAPIへ接続する場合に、このブラウザから送信します。
          </span>
        </div>

        <div className="settings-actions">
          <button type="button" className="btn-primary" onClick={handleApiKeySave}>
            {apiKeySaved ? "保存しました" : "API keyを保存"}
          </button>
          <button type="button" className="btn-secondary" onClick={handleApiKeyClear}>
            API keyを削除
          </button>
        </div>
      </section>

    </div>
  );
}
