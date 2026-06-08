import { useMemo, useRef, useState } from "react";

import {
  createForecast,
  createForecastFramingDraft,
  getForecast,
  reviewForecast,
} from "../../api/forecast";
import { navigate, routes } from "../../router";
import type {
  ForecastCreateRequest,
  ForecastDetail,
  ForecastFramingDraftClarifyingQuestion,
  ForecastFramingDraftResponse,
} from "../../types";
import { formatForecastError } from "./errors";

type State =
  | "rough_input"
  | "drafting"
  | "questions"
  | "refining"
  | "needs_retry"
  | "final_edit"
  | "creating"
  | "preview_ready"
  | "approving"
  | "detail";

interface FinalFields {
  question: string;
  resolutionCriteria: string;
  resolutionSources: string;
  outcomes: string;
  targetPopulation: string;
  unitOfAnalysis: string;
  decisionContext: string;
}

function stableKey(action: string): string {
  return `forecast-new-${action}-${crypto.randomUUID()}`;
}

function splitLines(value: string): string[] {
  return value
    .split("\n")
    .map((item) => item.trim())
    .filter(Boolean);
}

function joinLines(values: string[] | undefined): string {
  return (values ?? []).join("\n");
}

function optionalValue(value: string): string | null {
  const trimmed = value.trim();
  return trimmed ? trimmed : null;
}

function finalFieldsFromDraft(response: ForecastFramingDraftResponse): FinalFields {
  const draft = response.draft;
  const payload = response.create_payload;
  return {
    question: payload?.question || draft.question || "",
    resolutionCriteria: payload?.resolution_criteria ?? draft.resolution_criteria ?? "",
    resolutionSources: joinLines(payload?.resolution_sources ?? draft.resolution_sources),
    outcomes: joinLines(payload?.outcomes ?? draft.outcomes),
    targetPopulation: payload?.target_population ?? draft.target_population ?? "",
    unitOfAnalysis: payload?.unit_of_analysis ?? draft.unit_of_analysis ?? "",
    decisionContext: payload?.decision_context ?? draft.decision_context ?? "",
  };
}

function finalPayloadFromFields(
  fields: FinalFields,
  basePayload: ForecastCreateRequest | null | undefined,
): ForecastCreateRequest {
  return {
    ...basePayload,
    question: fields.question.trim(),
    resolution_criteria: fields.resolutionCriteria.trim(),
    resolution_sources: splitLines(fields.resolutionSources),
    outcomes: splitLines(fields.outcomes),
    target_population: optionalValue(fields.targetPopulation),
    unit_of_analysis: optionalValue(fields.unitOfAnalysis),
    decision_context: optionalValue(fields.decisionContext),
    confidentiality_class: basePayload?.confidentiality_class ?? "public",
  };
}

function nextStateForDraft(response: ForecastFramingDraftResponse): State {
  if (response.ready_to_create) return "final_edit";
  if (response.draft.clarifying_questions.length > 0) return "questions";
  return "needs_retry";
}

function warningItems(response: ForecastFramingDraftResponse | null): string[] {
  return response?.warnings.filter((warning) => warning.trim()) ?? [];
}

function answerPayload(
  questions: ForecastFramingDraftClarifyingQuestion[],
  answers: Record<string, string>,
) {
  return questions
    .map((question) => ({
      question_id: question.question_id,
      answer: (answers[question.question_id] ?? "").trim(),
    }))
    .filter((answer) => answer.answer.length > 0);
}

export function NewForecast() {
  const [roughQuestion, setRoughQuestion] = useState("");
  const [draftResponse, setDraftResponse] = useState<ForecastFramingDraftResponse | null>(null);
  const [answers, setAnswers] = useState<Record<string, string>>({});
  const [finalFields, setFinalFields] = useState<FinalFields>({
    question: "",
    resolutionCriteria: "",
    resolutionSources: "",
    outcomes: "",
    targetPopulation: "",
    unitOfAnalysis: "",
    decisionContext: "",
  });
  const [forecastId, setForecastId] = useState<string | null>(null);
  const [preview, setPreview] = useState<ForecastDetail | null>(null);
  const [state, setState] = useState<State>("rough_input");
  const [error, setError] = useState<string | null>(null);
  const idempotencyKeys = useRef({
    draft: stableKey("framing-draft"),
    refine: stableKey("framing-draft-refine"),
    create: stableKey("create"),
    approve: stableKey("approve-framing"),
  });

  const warnings = warningItems(draftResponse);
  const clarifyingQuestions = draftResponse?.draft.clarifying_questions ?? [];
  const sourceLines = useMemo(
    () => splitLines(finalFields.resolutionSources),
    [finalFields.resolutionSources],
  );
  const outcomeLabels = useMemo(() => splitLines(finalFields.outcomes), [finalFields.outcomes]);
  const hasTooManySources = sourceLines.length > 20;
  const hasTooManyOutcomes = outcomeLabels.length > 8;
  const hasNoOutcome = outcomeLabels.length === 0;
  const hasNoQuestion = finalFields.question.trim().length === 0;
  const isCreatingForecast = state === "creating";
  const finalPayload = useMemo(
    () => finalPayloadFromFields(finalFields, draftResponse?.create_payload),
    [draftResponse?.create_payload, finalFields],
  );
  const canSubmitRough = roughQuestion.trim().length > 0 && state !== "drafting";
  const areAnswersReady =
    clarifyingQuestions.length > 0 &&
    clarifyingQuestions.every(
      (question) => !question.required || answers[question.question_id]?.trim(),
    );
  const isFinalValid = !hasNoQuestion && !hasNoOutcome && !hasTooManyOutcomes && !hasTooManySources;
  const canCreate =
    state === "final_edit" && Boolean(draftResponse?.ready_to_create) && isFinalValid;
  const canOpenManualEdit = Boolean(draftResponse) && isFinalValid;

  function applyDraftResponse(response: ForecastFramingDraftResponse) {
    setDraftResponse(response);
    setFinalFields(finalFieldsFromDraft(response));
    setState(nextStateForDraft(response));
  }

  async function onGenerateDraft() {
    setError(null);
    setState("drafting");
    setDraftResponse(null);
    setPreview(null);
    setForecastId(null);
    setAnswers({});
    idempotencyKeys.current.draft = stableKey("framing-draft");
    try {
      const response = await createForecastFramingDraft({
        rough_question: roughQuestion,
        locale: "ja",
      }, {
        idempotencyKey: idempotencyKeys.current.draft,
      });
      applyDraftResponse(response);
    } catch (err) {
      setError(formatForecastError(err));
      setState("rough_input");
    }
  }

  async function onRefineDraft() {
    if (!draftResponse) return;
    setError(null);
    setState("refining");
    idempotencyKeys.current.refine = stableKey("framing-draft-refine");
    try {
      const response = await createForecastFramingDraft({
        rough_question: roughQuestion,
        answers: answerPayload(clarifyingQuestions, answers),
        previous_draft: draftResponse.draft,
        locale: "ja",
      }, {
        idempotencyKey: idempotencyKeys.current.refine,
      });
      applyDraftResponse(response);
    } catch (err) {
      setError(formatForecastError(err));
      setState("questions");
    }
  }

  async function onCreate() {
    if (!canCreate) return;
    setError(null);
    setState("creating");
    try {
      const response = await createForecast(finalPayload, {
        idempotencyKey: idempotencyKeys.current.create,
      });
      setForecastId(response.forecast_id);
      setPreview(await getForecast(response.forecast_id));
      setState("preview_ready");
    } catch (err) {
      setError(formatForecastError(err));
      setState("final_edit");
    }
  }

  async function onApprove() {
    if (!forecastId) return;
    setError(null);
    setState("approving");
    try {
      await reviewForecast(
        forecastId,
        { action: "approve_framing" },
        { idempotencyKey: idempotencyKeys.current.approve },
      );
      setState("detail");
      navigate(routes().forecastDetail(forecastId));
    } catch (err) {
      setError(formatForecastError(err));
      setState(preview ? "preview_ready" : "final_edit");
    }
  }

  return (
    <section className="screen screen-forecast-new">
      <div className="screen-header forecast-new-header">
        <div>
          <h1>新規Forecast</h1>
          <p className="screen-subtitle">
            AIで問いを整え、公開情報で判定できる形にしてから作成します。
          </p>
        </div>
        <div className="forecast-mode-pills" aria-label="Forecast実行条件">
          <span className="status-pill">PhaseA</span>
          <span className="status-pill status-pill--info">public</span>
          <span className="status-pill">current_state pack</span>
        </div>
      </div>

      {error && (
        <div className="alert alert-error" role="alert" style={{ whiteSpace: "pre-wrap" }}>
          {error}
        </div>
      )}

      <div className="forecast-create-layout">
        <div className="forecast-create-main">
          {(state === "rough_input" || state === "drafting") && (
            <section className="form-panel forecast-form-panel" aria-labelledby="rough-title">
              <div className="forecast-panel-heading">
                <p className="forecast-step-label">Step 1</p>
                <h2 id="rough-title">まずはざっくり教えてください</h2>
              </div>

              <label className="forecast-field" htmlFor="forecast-rough-question">
                <span className="forecast-field-header">
                  <span className="forecast-field-label">予測したいこと</span>
                  <span className="forecast-required">必須</span>
                </span>
                <span className="forecast-field-help">
                  期限や判定条件が曖昧でも大丈夫です。AIがForecast用に整理します。
                </span>
                <textarea
                  id="forecast-rough-question"
                  className="forecast-textarea forecast-textarea--large"
                  value={roughQuestion}
                  onChange={(event) => setRoughQuestion(event.target.value)}
                  rows={8}
                  disabled={state === "drafting"}
                  placeholder="例: 来年度中に日本で特定のAI規制が施行されるかを予測したい"
                />
              </label>

              <div className="forecast-actions">
                <button
                  type="button"
                  className="btn-primary"
                  disabled={!canSubmitRough}
                  onClick={onGenerateDraft}
                >
                  {state === "drafting" ? "Forecast案を作成中" : "AIでForecast案を作成"}
                </button>
              </div>
            </section>
          )}

          {(state === "questions" || state === "refining") && draftResponse && (
            <section className="form-panel forecast-form-panel" aria-labelledby="questions-title">
              <div className="forecast-panel-heading">
                <p className="forecast-step-label">Step 2</p>
                <h2 id="questions-title">確認したいこと</h2>
                <p>回答するとAIがForecast案を更新します。</p>
              </div>

              <WarningsList warnings={warnings} />

              <div className="forecast-question-grid">
                {clarifyingQuestions.map((question, index) => (
                  <article className="forecast-question-card" key={question.question_id}>
                    <label className="forecast-field" htmlFor={`forecast-answer-${index}`}>
                      <span className="forecast-field-header">
                        <span className="forecast-field-label">{question.label}</span>
                        {question.required ? (
                          <span className="forecast-required">必須</span>
                        ) : (
                          <span className="forecast-optional">任意</span>
                        )}
                      </span>
                      <span className="forecast-field-help">{question.prompt}</span>
                      <span className="forecast-field-meta">{question.why_needed}</span>
                      <textarea
                        id={`forecast-answer-${index}`}
                        className="forecast-textarea forecast-textarea--answer"
                        aria-label={question.label}
                        value={answers[question.question_id] ?? ""}
                        onChange={(event) =>
                          setAnswers((current) => ({
                            ...current,
                            [question.question_id]: event.target.value,
                          }))
                        }
                        rows={3}
                        disabled={state === "refining"}
                        placeholder="回答を入力"
                      />
                    </label>
                  </article>
                ))}
              </div>

              <div className="forecast-actions">
                <button
                  type="button"
                  className="btn-secondary"
                  disabled={state === "refining"}
                  onClick={() => setState("rough_input")}
                >
                  大枠を編集
                </button>
                <button
                  type="button"
                  className="btn-primary"
                  disabled={!areAnswersReady || state === "refining"}
                  onClick={onRefineDraft}
                >
                  {state === "refining" ? "Forecast案を更新中" : "回答をAIに反映"}
                </button>
              </div>
            </section>
          )}

          {state === "needs_retry" && draftResponse && (
            <section className="form-panel forecast-form-panel" aria-labelledby="retry-title">
              <div className="forecast-panel-heading">
                <p className="forecast-step-label">Step 2</p>
                <h2 id="retry-title">大枠を調整</h2>
                <p>AIから追加質問が返らなかったため、大枠を編集して再試行できます。</p>
              </div>

              <WarningsList warnings={warnings} />

              <label className="forecast-field" htmlFor="forecast-rough-retry">
                <span className="forecast-field-label">予測したいこと</span>
                <textarea
                  id="forecast-rough-retry"
                  className="forecast-textarea forecast-textarea--large"
                  value={roughQuestion}
                  onChange={(event) => setRoughQuestion(event.target.value)}
                  rows={7}
                />
              </label>

              <div className="forecast-actions">
                {canOpenManualEdit && (
                  <button
                    type="button"
                    className="btn-secondary"
                    onClick={() => setState("final_edit")}
                  >
                    最終編集を開く
                  </button>
                )}
                <button
                  type="button"
                  className="btn-primary"
                  disabled={!canSubmitRough}
                  onClick={onGenerateDraft}
                >
                  AIでForecast案を再作成
                </button>
              </div>
            </section>
          )}

          {(state === "final_edit" || state === "creating" || state === "preview_ready" || state === "approving") &&
            draftResponse && (
              <section className="form-panel forecast-form-panel" aria-labelledby="final-title">
                <div className="forecast-panel-heading">
                  <p className="forecast-step-label">Step 3</p>
                  <h2 id="final-title">最終確認</h2>
                  <p>作成前に内容を編集できます。承認はForecast保存後に別操作で行います。</p>
                  <div className="forecast-draft-meta">
                    <span>{draftResponse.model}</span>
                    <span>confidence {draftResponse.draft.confidence.toFixed(2)}</span>
                  </div>
                </div>

                <WarningsList warnings={warnings} />

                <div className="forecast-field-stack">
                  <label className="forecast-field" htmlFor="forecast-final-question">
                    <span className="forecast-field-header">
                      <span className="forecast-field-label">問い</span>
                      <span className="forecast-required">必須</span>
                    </span>
                    <textarea
                      id="forecast-final-question"
                      className="forecast-textarea"
                      value={finalFields.question}
                      onChange={(event) =>
                        setFinalFields((current) => ({ ...current, question: event.target.value }))
                      }
                      rows={3}
                      disabled={state !== "final_edit"}
                    />
                  </label>

                  <label className="forecast-field" htmlFor="forecast-final-criteria">
                    <span className="forecast-field-label">判定条件</span>
                    <textarea
                      id="forecast-final-criteria"
                      className="forecast-textarea"
                      value={finalFields.resolutionCriteria}
                      onChange={(event) =>
                        setFinalFields((current) => ({
                          ...current,
                          resolutionCriteria: event.target.value,
                        }))
                      }
                      rows={5}
                      disabled={state !== "final_edit"}
                    />
                  </label>

                  <label className="forecast-field" htmlFor="forecast-final-sources">
                    <span className="forecast-field-header">
                      <span className="forecast-field-label">判定ソース</span>
                      <span className="forecast-optional">1行1ソース</span>
                    </span>
                    <textarea
                      id="forecast-final-sources"
                      className="forecast-textarea forecast-textarea--compact"
                      value={finalFields.resolutionSources}
                      onChange={(event) =>
                        setFinalFields((current) => ({
                          ...current,
                          resolutionSources: event.target.value,
                        }))
                      }
                      rows={4}
                      disabled={state !== "final_edit"}
                      aria-describedby="forecast-source-count"
                    />
                    <span
                      id="forecast-source-count"
                      className={`forecast-field-meta${hasTooManySources ? " forecast-field-meta--error" : ""}`}
                    >
                      {sourceLines.length}/20 件
                      {hasTooManySources ? "。判定ソースを20件以内にしてください。" : ""}
                    </span>
                  </label>

                  <label className="forecast-field" htmlFor="forecast-final-outcomes">
                    <span className="forecast-field-header">
                      <span className="forecast-field-label">結果候補</span>
                      <span className="forecast-required">1行1候補</span>
                    </span>
                    <textarea
                      id="forecast-final-outcomes"
                      className="forecast-textarea forecast-textarea--compact"
                      value={finalFields.outcomes}
                      onChange={(event) =>
                        setFinalFields((current) => ({ ...current, outcomes: event.target.value }))
                      }
                      rows={4}
                      disabled={state !== "final_edit"}
                      aria-describedby="forecast-outcome-count"
                    />
                    <span
                      id="forecast-outcome-count"
                      className={`forecast-field-meta${hasTooManyOutcomes || hasNoOutcome ? " forecast-field-meta--error" : ""}`}
                    >
                      {outcomeLabels.length}/8 件
                      {hasNoOutcome ? "。結果候補を1件以上入力してください。" : ""}
                      {hasTooManyOutcomes ? "。結果候補を8件以内にしてください。" : ""}
                    </span>
                  </label>

                  <div className="forecast-optional-grid">
                    <label className="forecast-field" htmlFor="forecast-final-target">
                      <span className="forecast-field-label">対象集団</span>
                      <input
                        id="forecast-final-target"
                        className="forecast-input"
                        value={finalFields.targetPopulation}
                        onChange={(event) =>
                          setFinalFields((current) => ({
                            ...current,
                            targetPopulation: event.target.value,
                          }))
                        }
                        disabled={state !== "final_edit"}
                      />
                    </label>
                    <label className="forecast-field" htmlFor="forecast-final-unit">
                      <span className="forecast-field-label">分析単位</span>
                      <input
                        id="forecast-final-unit"
                        className="forecast-input"
                        value={finalFields.unitOfAnalysis}
                        onChange={(event) =>
                          setFinalFields((current) => ({
                            ...current,
                            unitOfAnalysis: event.target.value,
                          }))
                        }
                        disabled={state !== "final_edit"}
                      />
                    </label>
                  </div>

                  <label className="forecast-field" htmlFor="forecast-final-context">
                    <span className="forecast-field-label">意思決定文脈</span>
                    <textarea
                      id="forecast-final-context"
                      className="forecast-textarea"
                      value={finalFields.decisionContext}
                      onChange={(event) =>
                        setFinalFields((current) => ({
                          ...current,
                          decisionContext: event.target.value,
                        }))
                      }
                      rows={3}
                      disabled={state !== "final_edit"}
                    />
                  </label>
                </div>

                {state === "final_edit" && !draftResponse.ready_to_create && (
                  <div className="forecast-ready-note" role="status">
                    AI判定ではまだ作成準備が完了していません。大枠を編集して再作成してください。
                  </div>
                )}

                <div className="forecast-actions">
                  {state === "final_edit" && (
                    <button
                      type="button"
                      className="btn-secondary"
                      onClick={() =>
                        setState(clarifyingQuestions.length > 0 ? "questions" : "needs_retry")
                      }
                    >
                      前のステップへ
                    </button>
                  )}
                  {draftResponse.ready_to_create && (state === "final_edit" || isCreatingForecast) && (
                    <button
                      type="button"
                      className="btn-primary"
                      disabled={!canCreate || isCreatingForecast}
                      onClick={onCreate}
                    >
                      {isCreatingForecast ? "Forecastを作成中" : "Forecastを作成"}
                    </button>
                  )}
                  {state === "preview_ready" && (
                    <button type="button" className="btn-primary" onClick={onApprove}>
                      この内容で承認
                    </button>
                  )}
                  {state === "approving" && (
                    <button type="button" className="btn-primary" disabled>
                      承認中
                    </button>
                  )}
                </div>
              </section>
            )}

          {preview && (
            <section className="form-panel forecast-preview-panel" aria-labelledby="saved-preview-title">
              <div className="forecast-panel-heading">
                <p className="forecast-step-label">Saved</p>
                <h2 id="saved-preview-title">保存済みプレビュー</h2>
                <p>承認すると、このフレーミングでcurrent_state packへ進めます。</p>
              </div>
              <div className="run-card-meta">
                <span>Version {preview.current_framing_version}</span>
                <span>{preview.confidentiality_class}</span>
              </div>
              <div className="forecast-preview-summary">
                <p className="run-card-title">{preview.question}</p>
                <p>{preview.resolution_criteria || "判定条件は未入力です。"}</p>
                {preview.resolution_sources.length > 0 && (
                  <ul className="forecast-source-list">
                    {preview.resolution_sources.map((source) => (
                      <li key={source}>{source}</li>
                    ))}
                  </ul>
                )}
              </div>
              <div className="result-list forecast-outcome-list">
                {preview.outcomes.map((outcome) => (
                  <article className="run-card forecast-outcome-card" key={outcome.outcome_id}>
                    <p className="run-card-title">{outcome.label}</p>
                    <p>{outcome.definition}</p>
                    <p className="run-card-meta">{outcome.normalization_group_id}</p>
                  </article>
                ))}
              </div>
            </section>
          )}
        </div>

        <GuidePanel />
      </div>
    </section>
  );
}

function WarningsList({ warnings }: { warnings: string[] }) {
  if (warnings.length === 0) return null;
  return (
    <div className="forecast-warning-list" role="status">
      {warnings.map((warning) => (
        <p key={warning}>{warning}</p>
      ))}
    </div>
  );
}

function GuidePanel() {
  return (
    <aside className="forecast-guidance-panel" aria-label="入力ガイド">
      <h2>作成の流れ</h2>
      <div className="forecast-guide-stack">
        <div>
          <strong>大枠だけ入力</strong>
          <span>期限、対象、判定条件が曖昧でも、まずは一文で始められます。</span>
        </div>
        <div>
          <strong>AIが下書き化</strong>
          <span>問い、判定条件、公開ソース、結果候補をForecast用に整えます。</span>
        </div>
        <div>
          <strong>不足点だけ確認</strong>
          <span>追加質問に答えると、作成前の最終確認で自由に編集できます。</span>
        </div>
        <div>
          <strong>保存後に承認</strong>
          <span>Forecast作成後、保存済みフレーミングを確認して承認します。</span>
        </div>
      </div>
      <div className="forecast-guidance-note">
        <span>現在の実行条件</span>
        <strong>AI draft / public / PhaseA</strong>
      </div>
    </aside>
  );
}
