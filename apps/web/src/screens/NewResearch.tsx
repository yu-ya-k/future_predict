/**
 * SCR-1: New Research — create a new research run or import a manual report.
 */

import { useEffect, useRef, useState } from "react";

import { BackLink } from "../components";
import { createManualImportRun, createRun } from "../api/research";
import { ApiError } from "../api/client";
import { requestNotificationPermission } from "../notifications";
import {
  FACTORY_RESEARCH_DEFAULTS,
  loadResearchDefaults,
  normalizeResearchDefaultValue,
  normalizeResearchDefaults,
  RESEARCH_DEFAULTS_STORAGE_KEY,
  type ResearchDefaults,
} from "../researchDefaults";
import { navigate, routes } from "../router";
import { trackRun } from "../runStore";
import { OPTION_BOUNDS } from "../types";

const MAX_PROMPT_CHARS = 50_000;
const MAX_REPORT_CHARS = 50_000;
const MAX_MANUAL_FILE_BYTES = 1_048_576;

type ResearchMode = "api" | "manual";
type ManualSource = "text" | "file";

function fileError(file: File | null): string | null {
  if (!file) return null;
  if (!/\.(md|txt)$/i.test(file.name)) return ".md または .txt ファイルを選択してください";
  if (file.size > MAX_MANUAL_FILE_BYTES) return "ファイルサイズは1MB以下にしてください";
  return null;
}

function textError(value: string, maxChars: number): string | null {
  if (!value.trim()) return "入力してください";
  if (value.length > maxChars) return `${maxChars.toLocaleString()}文字以内で入力してください`;
  return null;
}

function activeManualError(
  source: ManualSource,
  text: string,
  file: File | null,
  maxChars: number,
): string | null {
  if (source === "text") return textError(text, maxChars);
  if (!file) return "ファイルを選択してください";
  return fileError(file);
}

export function NewResearch() {
  const defaults = useRef(loadResearchDefaults());

  const [mode, setMode] = useState<ResearchMode>("api");
  const [apiPrompt, setApiPrompt] = useState("");
  const [manualPromptSource, setManualPromptSource] = useState<ManualSource>("text");
  const [manualPromptText, setManualPromptText] = useState("");
  const [manualPromptFile, setManualPromptFile] = useState<File | null>(null);
  const [manualReportSource, setManualReportSource] = useState<ManualSource>("text");
  const [manualReportText, setManualReportText] = useState("");
  const [manualReportFile, setManualReportFile] = useState<File | null>(null);
  const [manualPromptValidationVisible, setManualPromptValidationVisible] = useState(false);
  const [manualReportValidationVisible, setManualReportValidationVisible] = useState(false);
  const [allowRemoteReview, setAllowRemoteReview] = useState(false);
  const [allowApiReruns, setAllowApiReruns] = useState(false);
  const [showAdvanced, setShowAdvanced] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);

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

  const apiPromptTrimmed = apiPrompt.trim();
  const apiOverLimit = apiPrompt.length > MAX_PROMPT_CHARS;
  const manualPromptError = activeManualError(
    manualPromptSource,
    manualPromptText,
    manualPromptFile,
    MAX_PROMPT_CHARS,
  );
  const manualReportError = activeManualError(
    manualReportSource,
    manualReportText,
    manualReportFile,
    MAX_REPORT_CHARS,
  );
  const manualPromptBlockingError =
    manualPromptSource === "text"
      ? manualPromptText.length > MAX_PROMPT_CHARS
      : fileError(manualPromptFile);
  const manualReportBlockingError =
    manualReportSource === "text"
      ? manualReportText.length > MAX_REPORT_CHARS
      : fileError(manualReportFile);
  const manualPromptVisibleError = manualPromptValidationVisible ? manualPromptError : null;
  const manualReportVisibleError = manualReportValidationVisible ? manualReportError : null;
  const canSubmit =
    mode === "api"
      ? apiPromptTrimmed.length > 0 && !apiOverLimit && !submitting
      : !manualPromptBlockingError && !manualReportBlockingError && !submitting;
  const rerunOptionsDisabled = mode === "manual" && !allowApiReruns;

  function handleOptionChange(key: keyof ResearchDefaults, value: string) {
    const normalized = normalizeResearchDefaultValue(key, value);
    switch (key) {
      case "max_targeted_rerun_runs":
        setMaxTargetedRerun(normalized);
        break;
      case "max_full_rerun_runs":
        setMaxFullRerun(normalized);
        break;
      case "max_llm_patch_runs":
        setMaxLlmPatch(normalized);
        break;
      case "max_verification_runs":
        setMaxVerification(normalized);
        break;
      case "max_total_iterations":
        setMaxIterations(normalized);
        break;
      case "max_total_tool_calls":
        setMaxToolCalls(normalized);
        break;
    }
  }

  function options() {
    return normalizeResearchDefaults({
      max_targeted_rerun_runs: maxTargetedRerun,
      max_full_rerun_runs: maxFullRerun,
      max_llm_patch_runs: maxLlmPatch,
      max_verification_runs: maxVerification,
      max_total_iterations: maxIterations,
      max_total_tool_calls: maxToolCalls,
    });
  }

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    if (!canSubmit) return;
    setSubmitting(true);
    setError(null);

    try {
      const normalizedOptions = options();
      const requestOptions =
        mode === "manual" && !allowApiReruns
          ? {
              ...normalizedOptions,
              max_targeted_rerun_runs: 0,
              max_full_rerun_runs: 0,
            }
          : normalizedOptions;
      if (mode === "manual" && (manualPromptError || manualReportError)) {
        setManualPromptValidationVisible(true);
        setManualReportValidationVisible(true);
        setSubmitting(false);
        return;
      }
      await requestNotificationPermission();
      const response =
        mode === "api"
          ? await createRun({
              user_prompt: apiPromptTrimmed,
              options: requestOptions,
            })
          : await createManualImportRun({
              input_prompt:
                manualPromptSource === "file"
                  ? { source: "file", file: manualPromptFile as File }
                  : { source: "text", text: manualPromptText.trim() },
              report:
                manualReportSource === "file"
                  ? { source: "file", file: manualReportFile as File }
                  : { source: "text", text: manualReportText.trim() },
              options: requestOptions,
              allow_remote_review: allowRemoteReview,
              allow_api_reruns: allowApiReruns,
            });

      const title =
        mode === "api"
          ? apiPromptTrimmed.split("\n")[0].slice(0, 120)
          : manualPromptSource === "file"
            ? `手動取り込み: ${manualPromptFile?.name ?? response.run_id}`
            : manualPromptText.trim().split("\n")[0].slice(0, 120);

      trackRun({
        run_id: response.run_id,
        title: title || "手動取り込み",
        max_total_iterations: requestOptions.max_total_iterations,
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

  const apiRemaining = MAX_PROMPT_CHARS - apiPrompt.length;
  const manualPromptRemaining = MAX_PROMPT_CHARS - manualPromptText.length;
  const manualReportRemaining = MAX_REPORT_CHARS - manualReportText.length;

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
        <fieldset className="mode-fieldset">
          <legend className="form-label">開始方法</legend>
          <div className="segmented-control">
            <label className={`segmented-option${mode === "api" ? " segmented-option--active" : ""}`}>
              <input
                type="radio"
                name="research-mode"
                value="api"
                checked={mode === "api"}
                onChange={() => {
                  setMode("api");
                  setError(null);
                  setManualPromptValidationVisible(false);
                  setManualReportValidationVisible(false);
                }}
                disabled={submitting}
              />
              APIで開始
            </label>
            <label className={`segmented-option${mode === "manual" ? " segmented-option--active" : ""}`}>
              <input
                type="radio"
                name="research-mode"
                value="manual"
                checked={mode === "manual"}
                onChange={() => {
                  setMode("manual");
                  setError(null);
                  setManualPromptValidationVisible(false);
                  setManualReportValidationVisible(false);
                }}
                disabled={submitting}
              />
              ChatGPT結果を取り込み
            </label>
          </div>
        </fieldset>

        {mode === "api" ? (
          <section className="form-section">
            <label className="form-label" htmlFor="prompt">
              リサーチ内容
              <span className="form-required" aria-hidden="true">*</span>
            </label>
            <textarea
              id="prompt"
              className={`prompt-textarea${apiOverLimit ? " prompt-textarea--error" : ""}`}
              value={apiPrompt}
              onChange={(e) => setApiPrompt(e.target.value)}
              placeholder="調査したいテーマや質問を入力してください..."
              rows={10}
              aria-describedby="prompt-count"
              disabled={submitting}
              required
            />
            <div className="prompt-meta">
              <span
                id="prompt-count"
                className={`char-counter${apiOverLimit ? " char-counter--error" : ""}`}
                aria-live="polite"
              >
                {apiOverLimit
                  ? `${Math.abs(apiRemaining).toLocaleString()} 文字オーバー`
                  : `残り ${apiRemaining.toLocaleString()} 文字`}
              </span>
            </div>
          </section>
        ) : (
          <>
            <ManualImportField
              idPrefix="manual-prompt"
              label="入力プロンプト"
              source={manualPromptSource}
              text={manualPromptText}
              file={manualPromptFile}
              textRows={7}
              remaining={manualPromptRemaining}
              error={manualPromptVisibleError}
              disabled={submitting}
              onSourceChange={(source) => {
                setManualPromptSource(source);
                setManualPromptValidationVisible(false);
                setError(null);
              }}
              onTextChange={(value) => {
                setManualPromptText(value);
                setManualPromptValidationVisible(true);
              }}
              onFileChange={(file) => {
                setManualPromptFile(file);
                setManualPromptValidationVisible(true);
              }}
            />
            <ManualImportField
              idPrefix="manual-report"
              label="出力レポート"
              source={manualReportSource}
              text={manualReportText}
              file={manualReportFile}
              textRows={12}
              remaining={manualReportRemaining}
              error={manualReportVisibleError}
              disabled={submitting}
              onSourceChange={(source) => {
                setManualReportSource(source);
                setManualReportValidationVisible(false);
                setError(null);
              }}
              onTextChange={(value) => {
                setManualReportText(value);
                setManualReportValidationVisible(true);
              }}
              onFileChange={(file) => {
                setManualReportFile(file);
                setManualReportValidationVisible(true);
              }}
            />
            <fieldset className="manual-gates">
              <legend className="form-label">実行許可</legend>
              <label className="checkbox-row">
                <input
                  type="checkbox"
                  checked={allowRemoteReview}
                  onChange={(e) => setAllowRemoteReview(e.target.checked)}
                  disabled={submitting}
                />
                LLMレビューを許可する
              </label>
              <label className="checkbox-row">
                <input
                  type="checkbox"
                  checked={allowApiReruns}
                  onChange={(e) => setAllowApiReruns(e.target.checked)}
                  disabled={submitting}
                />
                API rerunを許可する
              </label>
              {!allowRemoteReview && (
                <p className="manual-gate-note">
                  LLMレビューを許可しない場合、取り込み後は人間レビュー待ちになります。
                </p>
              )}
            </fieldset>
          </>
        )}

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
              {rerunOptionsDisabled && (
                <p className="option-note">
                  API rerun未許可のため、Targeted rerun / Full rerun は0回として送信されます。
                </p>
              )}
              <div className="options-grid">
                <OptionField
                  id="max-targeted-rerun"
                  label="最大Targeted rerun回数"
                  value={maxTargetedRerun}
                  optionKey="max_targeted_rerun_runs"
                  disabled={submitting || rerunOptionsDisabled}
                  onChange={handleOptionChange}
                />
                <OptionField
                  id="max-full-rerun"
                  label="最大Full rerun回数"
                  value={maxFullRerun}
                  optionKey="max_full_rerun_runs"
                  disabled={submitting || rerunOptionsDisabled}
                  onChange={handleOptionChange}
                />
                <OptionField
                  id="max-llm-patch"
                  label="最大LLM patch回数"
                  value={maxLlmPatch}
                  optionKey="max_llm_patch_runs"
                  disabled={submitting}
                  onChange={handleOptionChange}
                />
                <OptionField
                  id="max-verification"
                  label="最大Verification回数"
                  value={maxVerification}
                  optionKey="max_verification_runs"
                  disabled={submitting}
                  onChange={handleOptionChange}
                />
                <OptionField
                  id="max-iterations"
                  label="最大反復回数"
                  value={maxIterations}
                  optionKey="max_total_iterations"
                  disabled={submitting}
                  onChange={handleOptionChange}
                />
                <OptionField
                  id="max-tool-calls"
                  label="最大ツール呼び出し数"
                  value={maxToolCalls}
                  optionKey="max_total_tool_calls"
                  disabled={submitting}
                  step={10}
                  onChange={handleOptionChange}
                />
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
            {submitting
              ? mode === "api"
                ? "開始中..."
                : "取り込み中..."
              : mode === "api"
                ? "リサーチを開始"
                : allowRemoteReview
                  ? "取り込んでレビューを開始"
                  : "手動結果を取り込む"}
          </button>
        </div>
      </form>
    </div>
  );
}

interface ManualImportFieldProps {
  idPrefix: string;
  label: string;
  source: ManualSource;
  text: string;
  file: File | null;
  textRows: number;
  remaining: number;
  error: string | null;
  disabled: boolean;
  onSourceChange: (source: ManualSource) => void;
  onTextChange: (value: string) => void;
  onFileChange: (file: File | null) => void;
}

function ManualImportField({
  idPrefix,
  label,
  source,
  text,
  file,
  textRows,
  remaining,
  error,
  disabled,
  onSourceChange,
  onTextChange,
  onFileChange,
}: ManualImportFieldProps) {
  const overLimit = remaining < 0;
  return (
    <section className="form-section manual-import-field">
      <fieldset className="source-fieldset">
        <legend className="form-label">
          {label}
          <span className="form-required" aria-hidden="true">*</span>
        </legend>
        <div className="source-switch">
          <label>
            <input
              type="radio"
              name={`${idPrefix}-source`}
              checked={source === "text"}
              onChange={() => onSourceChange("text")}
              disabled={disabled}
            />
            テキスト
          </label>
          <label>
            <input
              type="radio"
              name={`${idPrefix}-source`}
              checked={source === "file"}
              onChange={() => onSourceChange("file")}
              disabled={disabled}
            />
            ファイル
          </label>
        </div>
      </fieldset>

      {source === "text" ? (
        <>
          <textarea
            id={`${idPrefix}-text`}
            className={`prompt-textarea${overLimit ? " prompt-textarea--error" : ""}`}
            value={text}
            onChange={(e) => onTextChange(e.target.value)}
            rows={textRows}
            disabled={disabled}
            aria-label={label}
            aria-describedby={`${idPrefix}-count`}
          />
          <div className="prompt-meta">
            <span
              id={`${idPrefix}-count`}
              className={`char-counter${overLimit ? " char-counter--error" : ""}`}
              aria-live="polite"
            >
              {overLimit
                ? `${Math.abs(remaining).toLocaleString()} 文字オーバー`
                : `残り ${remaining.toLocaleString()} 文字`}
            </span>
          </div>
        </>
      ) : (
        <div className="file-input-row">
          <input
            id={`${idPrefix}-file`}
            type="file"
            accept=".md,.txt,text/markdown,text/plain"
            onChange={(e) => onFileChange(e.target.files?.[0] ?? null)}
            disabled={disabled}
            aria-label={`${label}ファイル`}
          />
          {file && <span className="file-input-meta">{file.name}</span>}
        </div>
      )}
      {error && <p className="field-error">{error}</p>}
    </section>
  );
}

interface OptionFieldProps {
  id: string;
  label: string;
  value: number;
  optionKey: keyof ResearchDefaults;
  disabled: boolean;
  step?: number;
  onChange: (key: keyof ResearchDefaults, value: string) => void;
}

function OptionField({
  id,
  label,
  value,
  optionKey,
  disabled,
  step,
  onChange,
}: OptionFieldProps) {
  const bounds = OPTION_BOUNDS[optionKey];
  return (
    <div className="option-field">
      <label className="option-label" htmlFor={id}>
        {label}
      </label>
      <input
        id={id}
        type="number"
        className="option-input"
        value={value}
        min={bounds.min}
        max={bounds.max}
        step={step}
        onChange={(e) => onChange(optionKey, e.target.value)}
        disabled={disabled}
      />
      <span className="option-range">
        {bounds.min}–{bounds.max}
      </span>
    </div>
  );
}
