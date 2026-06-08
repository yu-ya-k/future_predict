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
import { FRAMING_ROUGH_QUESTION_MAX_LENGTH } from "./constants";
import { formatForecastError } from "./errors";
import { ForecastFlowProgress } from "./ForecastFlowProgress";

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

interface AnswerHistoryEntry {
  questionId: string;
  questionKey: string;
  answer: string;
}

const FRAMING_ANSWER_API_LIMIT = 5;

const FRAMING_WARNING_LABELS: Record<string, string> = {
  required_clarifying_answers_missing:
    "Forecast作成に必要なメタデータがまだ不足しています。追加質問に回答するか、最終編集で必須項目を補ってください。",
};

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

function finalFieldsFromDraft(
  response: ForecastFramingDraftResponse,
): FinalFields {
  const draft = response.draft;
  const payload = response.create_payload;
  return {
    question: payload?.question || draft.question || "",
    resolutionCriteria:
      payload?.resolution_criteria ?? draft.resolution_criteria ?? "",
    resolutionSources: joinLines(
      payload?.resolution_sources ?? draft.resolution_sources,
    ),
    outcomes: joinLines(payload?.outcomes ?? draft.outcomes),
    targetPopulation:
      payload?.target_population ?? draft.target_population ?? "",
    unitOfAnalysis: payload?.unit_of_analysis ?? draft.unit_of_analysis ?? "",
    decisionContext: payload?.decision_context ?? draft.decision_context ?? "",
  };
}

function finalPayloadFromFields(
  fields: FinalFields,
  basePayload: ForecastCreateRequest | null | undefined,
  originalExecutionPrompt: string,
): ForecastCreateRequest {
  return {
    ...basePayload,
    question: fields.question.trim(),
    original_execution_prompt:
      optionalValue(originalExecutionPrompt) ??
      basePayload?.original_execution_prompt ??
      null,
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
  const seen = new Set<string>();
  const items: string[] = [];
  for (const rawWarning of response?.warnings ?? []) {
    const warning = rawWarning.trim();
    if (!warning) continue;
    const localized = FRAMING_WARNING_LABELS[warning] ?? warning;
    if (seen.has(localized)) continue;
    seen.add(localized);
    items.push(localized);
  }
  return items;
}

function aiChangeSummaryItems(
  response: ForecastFramingDraftResponse | null,
  fields: FinalFields,
  originalExecutionPrompt: string,
): string[] {
  if (!response) return [];
  const original = originalExecutionPrompt.trim();
  const items: string[] = [];
  if (fields.question.trim() && fields.question.trim() !== original) {
    items.push("元の依頼からForecast用の短い問いを抽出しました。");
  }
  if (fields.resolutionCriteria.trim()) {
    items.push("解決条件を確認対象として整理しました。");
  }
  if (splitLines(fields.resolutionSources).length > 0) {
    items.push("公開情報で確認する解決確認ソースを整理しました。");
  }
  if (splitLines(fields.outcomes).length > 0) {
    items.push("解決時の結果状態を確認対象として整理しました。");
  }
  if (
    fields.targetPopulation.trim() ||
    fields.unitOfAnalysis.trim() ||
    fields.decisionContext.trim()
  ) {
    items.push("対象、分析単位、意思決定文脈を確認対象として抽出しました。");
  }
  if (items.length === 0) {
    return [
      "元の依頼から大きな変更はありません。不足メタデータだけ確認します。",
    ];
  }
  return items.slice(0, 4);
}

function answerKey(question: ForecastFramingDraftClarifyingQuestion): string {
  return `${question.question_id}::${question.prompt}`;
}

function answerValue(
  question: ForecastFramingDraftClarifyingQuestion,
  answers: Record<string, string>,
  answerHistory: AnswerHistoryEntry[],
): string {
  const key = answerKey(question);
  const typedAnswer = answers[key];
  if (typedAnswer !== undefined) return typedAnswer;
  return (
    answerHistory.find((entry) => entry.questionKey === key)?.answer ?? ""
  );
}

function upsertAnswerHistory(
  history: AnswerHistoryEntry[],
  question: ForecastFramingDraftClarifyingQuestion,
  answer: string,
): AnswerHistoryEntry[] {
  const questionKey = answerKey(question);
  const next = history.filter((entry) => entry.questionKey !== questionKey);
  const trimmed = answer.trim();
  if (!trimmed) return next;
  return [
    {
      questionId: question.question_id,
      questionKey,
      answer,
    },
    ...next,
  ];
}

function answerPayload(
  questions: ForecastFramingDraftClarifyingQuestion[],
  answers: Record<string, string>,
  answerHistory: AnswerHistoryEntry[],
) {
  const currentQuestionKeys = new Set(questions.map(answerKey));
  const currentQuestionIds = new Set(
    questions.map((question) => question.question_id),
  );
  const candidates = [
    ...questions.map((question) => ({
      question_id: question.question_id,
      questionKey: answerKey(question),
      answer: (answers[answerKey(question)] ?? "").trim(),
    })),
    ...answerHistory
      .filter(
        (entry) =>
          !currentQuestionIds.has(entry.questionId) ||
          currentQuestionKeys.has(entry.questionKey),
      )
      .map((entry) => ({
        question_id: entry.questionId,
        questionKey: entry.questionKey,
        answer: entry.answer.trim(),
      })),
  ];
  const seenQuestionIds = new Set<string>();
  return candidates
    .filter((answer) => answer.answer.length > 0)
    .filter((answer) => {
      if (seenQuestionIds.has(answer.question_id)) return false;
      seenQuestionIds.add(answer.question_id);
      return true;
    })
    .slice(0, FRAMING_ANSWER_API_LIMIT)
    .map((question) => ({
      question_id: question.question_id,
      answer: question.answer,
    }));
}

function currentAnswerProgress(
  questions: ForecastFramingDraftClarifyingQuestion[],
  answers: Record<string, string>,
  answerHistory: AnswerHistoryEntry[],
) {
  return {
    answered: questions.filter((question) =>
      answerValue(question, answers, answerHistory).trim(),
    ).length,
    total: questions.length,
  };
}

export function NewForecast() {
  const [roughQuestion, setRoughQuestion] = useState("");
  const [draftResponse, setDraftResponse] =
    useState<ForecastFramingDraftResponse | null>(null);
  const [originalExecutionPrompt, setOriginalExecutionPrompt] = useState("");
  const [answers, setAnswers] = useState<Record<string, string>>({});
  const [answerHistory, setAnswerHistory] = useState<AnswerHistoryEntry[]>([]);
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
  const answerProgress = currentAnswerProgress(
    clarifyingQuestions,
    answers,
    answerHistory,
  );
  const sourceLines = useMemo(
    () => splitLines(finalFields.resolutionSources),
    [finalFields.resolutionSources],
  );
  const outcomeLabels = useMemo(
    () => splitLines(finalFields.outcomes),
    [finalFields.outcomes],
  );
  const hasTooManySources = sourceLines.length > 20;
  const hasTooManyOutcomes = outcomeLabels.length > 8;
  const hasNoOutcome = outcomeLabels.length === 0;
  const hasNoQuestion = finalFields.question.trim().length === 0;
  const hasNoResolutionCriteria =
    finalFields.resolutionCriteria.trim().length === 0;
  const isCreatingForecast = state === "creating";
  const finalPayload = useMemo(
    () =>
      finalPayloadFromFields(
        finalFields,
        draftResponse?.create_payload,
        originalExecutionPrompt,
      ),
    [draftResponse?.create_payload, finalFields, originalExecutionPrompt],
  );
  const aiChangeSummary = useMemo(
    () =>
      aiChangeSummaryItems(draftResponse, finalFields, originalExecutionPrompt),
    [draftResponse, finalFields, originalExecutionPrompt],
  );
  const roughQuestionLength = roughQuestion.length;
  const hasTooLongRoughQuestion =
    roughQuestionLength > FRAMING_ROUGH_QUESTION_MAX_LENGTH;
  const canSubmitRough =
    roughQuestion.trim().length > 0 &&
    !hasTooLongRoughQuestion &&
    state !== "drafting";
  const areAnswersReady =
    clarifyingQuestions.length > 0 &&
    clarifyingQuestions.every(
      (question) =>
        !question.required ||
        answerValue(question, answers, answerHistory).trim(),
    );
  const isFinalValid =
    !hasNoQuestion &&
    !hasNoResolutionCriteria &&
    !hasNoOutcome &&
    !hasTooManyOutcomes &&
    !hasTooManySources;
  const canCreate =
    state === "final_edit" &&
    isFinalValid;
  const canOpenManualEdit = Boolean(draftResponse);

  function applyDraftResponse(response: ForecastFramingDraftResponse) {
    setDraftResponse(response);
    setFinalFields(finalFieldsFromDraft(response));
    setState(nextStateForDraft(response));
  }

  async function onGenerateDraft() {
    const requestRoughQuestion = roughQuestion;
    const shouldCaptureOriginalPrompt =
      !draftResponse && !originalExecutionPrompt.trim();
    setError(null);
    setState("drafting");
    setDraftResponse(null);
    setPreview(null);
    setForecastId(null);
    setAnswers({});
    setAnswerHistory([]);
    idempotencyKeys.current.draft = stableKey("framing-draft");
    try {
      const response = await createForecastFramingDraft(
        {
          rough_question: requestRoughQuestion,
          locale: "ja",
        },
        {
          idempotencyKey: idempotencyKeys.current.draft,
        },
      );
      if (shouldCaptureOriginalPrompt) {
        setOriginalExecutionPrompt(requestRoughQuestion);
      }
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
      const response = await createForecastFramingDraft(
        {
          rough_question: roughQuestion,
          answers: answerPayload(clarifyingQuestions, answers, answerHistory),
          previous_draft: draftResponse.draft,
          locale: "ja",
        },
        {
          idempotencyKey: idempotencyKeys.current.refine,
        },
      );
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
            元の依頼を保持したまま、公開情報で判定するためのメタデータを確認します。
          </p>
        </div>
        <div className="forecast-mode-pills" aria-label="Forecast実行条件">
          <span className="status-pill">PhaseA</span>
          <span className="status-pill status-pill--info">public</span>
          <span className="status-pill">current_state pack</span>
        </div>
      </div>

      {error && (
        <div
          className="alert alert-error"
          role="alert"
          style={{ whiteSpace: "pre-wrap" }}
        >
          {error}
        </div>
      )}

      <div className="forecast-create-layout">
        <div className="forecast-create-main">
          {(state === "rough_input" || state === "drafting") && (
            <section
              className="form-panel forecast-form-panel"
              aria-labelledby="rough-title"
            >
              <div className="forecast-panel-heading">
                <p className="forecast-step-label">Step 1</p>
                <h2 id="rough-title">まずはざっくり教えてください</h2>
              </div>

              {state === "drafting" && (
                <ForecastFlowProgress
                  heading="AI応答待ち"
                  summary="元の依頼を保持したまま、Forecastメタデータを抽出しています。"
                  nodes={[
                    {
                      id: "preserve",
                      title: "元の依頼",
                      meta: "verbatimで保持",
                      status: "done",
                      tone: "brief",
                    },
                    {
                      id: "extract",
                      title: "AIメタデータ抽出",
                      meta: "問い / 解決条件 / 結果状態",
                      status: "active",
                      tone: "research",
                    },
                    {
                      id: "clarify",
                      title: "不足点確認",
                      meta: "必要な質問を生成",
                      status: "pending",
                      tone: "review",
                    },
                    {
                      id: "confirm",
                      title: "保存前確認",
                      meta: "編集して保存",
                      status: "pending",
                      tone: "finalize",
                    },
                  ]}
                />
              )}

              <label
                className="forecast-field"
                htmlFor="forecast-rough-question"
              >
                <span className="forecast-field-header">
                  <span className="forecast-field-label">予測したいこと</span>
                  <span className="forecast-required">必須</span>
                </span>
                <span className="forecast-field-help">
                  元の依頼はそのまま保存し、AIは不足しているForecastメタデータだけ確認します。
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
                <span
                  className={`forecast-field-meta${
                    hasTooLongRoughQuestion ? " forecast-field-meta--error" : ""
                  }`}
                >
                  {roughQuestionLength.toLocaleString("ja-JP")} /{" "}
                  {FRAMING_ROUGH_QUESTION_MAX_LENGTH.toLocaleString("ja-JP")}{" "}
                  文字
                  {hasTooLongRoughQuestion
                    ? "。長すぎるため、Forecastに必要な前提や解決条件に絞ってください。"
                    : ""}
                </span>
              </label>

              <div className="forecast-actions">
                <button
                  type="button"
                  className="btn-primary"
                  disabled={!canSubmitRough}
                  onClick={onGenerateDraft}
                >
                  {state === "drafting"
                    ? "Forecast案を作成中"
                    : "AIでForecast案を作成"}
                </button>
              </div>
            </section>
          )}

          {(state === "questions" || state === "refining") && draftResponse && (
            <section
              className="form-panel forecast-form-panel"
              aria-labelledby="questions-title"
            >
              <div className="forecast-panel-heading">
                <p className="forecast-step-label">Step 2</p>
                <h2 id="questions-title">Forecastメタデータの不足確認</h2>
                <p>
                  元の実行プロンプトは変更しません。ここでは公開情報で解決状態を判定するために不足している期限・対象・ソースなどだけを確認します。
                </p>
              </div>

              {state === "refining" && (
                <ForecastFlowProgress
                  heading="回答を反映中"
                  summary="入力済みの回答を使い、Forecastメタデータだけを更新しています。"
                  nodes={[
                    {
                      id: "preserve",
                      title: "元の依頼",
                      meta: "変更せず保持",
                      status: "done",
                      tone: "brief",
                    },
                    {
                      id: "answers",
                      title: "追加回答",
                      meta: `${answerProgress.answered}/${answerProgress.total}件`,
                      status: "done",
                      tone: "review",
                    },
                    {
                      id: "refine",
                      title: "AIメタデータ更新",
                      meta: "解決条件と結果状態を再整理",
                      status: "active",
                      tone: "research",
                    },
                    {
                      id: "confirm",
                      title: "保存前確認",
                      meta: "編集して保存",
                      status: "pending",
                      tone: "finalize",
                    },
                  ]}
                />
              )}

              <OriginalPromptDisclosure prompt={originalExecutionPrompt} />
              <AiChangeSummary items={aiChangeSummary} />
              <WarningsList warnings={warnings} />

              <div className="forecast-question-grid">
                {clarifyingQuestions.map((question, index) => {
                  const answerId = `forecast-answer-${index}`;
                  const promptId = `forecast-answer-${index}-prompt`;
                  const whyNeededId = `forecast-answer-${index}-why`;
                  return (
                    <article
                      className="forecast-question-card"
                      key={answerKey(question)}
                    >
                      <label className="forecast-field" htmlFor={answerId}>
                        <span className="forecast-field-header">
                          <span className="forecast-field-label">
                            {question.label}
                          </span>
                          {question.required ? (
                            <span className="forecast-required">必須</span>
                          ) : (
                            <span className="forecast-optional">任意</span>
                          )}
                        </span>
                        <span
                          id={promptId}
                          className="forecast-field-help"
                        >
                          {question.prompt}
                        </span>
                        <span
                          id={whyNeededId}
                          className="forecast-field-meta"
                        >
                          {question.why_needed}
                        </span>
                        <textarea
                          id={answerId}
                          className="forecast-textarea forecast-textarea--answer"
                          aria-label={question.label}
                          aria-describedby={`${promptId} ${whyNeededId}`}
                          value={answerValue(question, answers, answerHistory)}
                          onChange={(event) => {
                            const nextAnswer = event.target.value;
                            setAnswers((current) => ({
                              ...current,
                              [answerKey(question)]: nextAnswer,
                            }));
                            setAnswerHistory((current) =>
                              upsertAnswerHistory(
                                current,
                                question,
                                nextAnswer,
                              ),
                            );
                          }}
                          rows={3}
                          disabled={state === "refining"}
                          placeholder="回答を入力"
                        />
                      </label>
                    </article>
                  );
                })}
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
                  {state === "refining"
                    ? "Forecast案を更新中"
                    : "回答をメタデータ案に反映"}
                </button>
              </div>
            </section>
          )}

          {state === "needs_retry" && draftResponse && (
            <section
              className="form-panel forecast-form-panel"
              aria-labelledby="retry-title"
            >
              <div className="forecast-panel-heading">
                <p className="forecast-step-label">Step 2</p>
                <h2 id="retry-title">大枠を調整</h2>
                <p>
                  元の依頼は保持されます。作成に足りないForecastメタデータを補うため、
                  大枠を編集して再試行できます。
                </p>
              </div>

              <OriginalPromptDisclosure prompt={originalExecutionPrompt} />
              <AiChangeSummary items={aiChangeSummary} />
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
                <span
                  className={`forecast-field-meta${
                    hasTooLongRoughQuestion ? " forecast-field-meta--error" : ""
                  }`}
                >
                  {roughQuestionLength.toLocaleString("ja-JP")} /{" "}
                  {FRAMING_ROUGH_QUESTION_MAX_LENGTH.toLocaleString("ja-JP")}{" "}
                  文字
                  {hasTooLongRoughQuestion
                    ? "。長すぎるため、Forecastに必要な前提や解決条件に絞ってください。"
                    : ""}
                </span>
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

          {(state === "final_edit" ||
            state === "creating" ||
            state === "preview_ready" ||
            state === "approving") &&
            draftResponse && (
              <section
                className="form-panel forecast-form-panel"
                aria-labelledby="final-title"
              >
                <div className="forecast-panel-heading">
                  <p className="forecast-step-label">Step 3</p>
                  <h2 id="final-title">保存前のフレーミング確認</h2>
                  <p>
                    元の依頼は保存時に保持されます。ここでは作成に必要なForecastメタデータだけ編集します。
                  </p>
                  <div className="forecast-draft-meta">
                    <span>{draftResponse.model}</span>
                    <span>
                      confidence {draftResponse.draft.confidence.toFixed(2)}
                    </span>
                  </div>
                </div>

                <WarningsList warnings={warnings} />
                <OriginalPromptDisclosure prompt={originalExecutionPrompt} />
                <AiChangeSummary items={aiChangeSummary} />

                <div className="forecast-field-stack">
                  <div className="forecast-metadata-heading">
                    <h3>Forecastメタデータ</h3>
                    <p>AIが整えた項目です。必要な範囲だけ編集できます。</p>
                  </div>

                  <label
                    className="forecast-field"
                    htmlFor="forecast-final-question"
                  >
                    <span className="forecast-field-header">
                      <span className="forecast-field-label">
                        Forecast用の短い問い
                      </span>
                      <span className="forecast-required">必須</span>
                    </span>
                    <textarea
                      id="forecast-final-question"
                      className="forecast-textarea"
                      value={finalFields.question}
                      onChange={(event) =>
                        setFinalFields((current) => ({
                          ...current,
                          question: event.target.value,
                        }))
                      }
                      rows={3}
                      disabled={state !== "final_edit"}
                      aria-describedby="forecast-final-question-help"
                      aria-invalid={hasNoQuestion}
                    />
                    <span
                      id="forecast-final-question-help"
                      className={`forecast-field-meta${
                        hasNoQuestion ? " forecast-field-meta--error" : ""
                      }`}
                    >
                      {hasNoQuestion
                        ? "Forecast用の短い問いを入力してください。"
                        : "元の依頼を置き換える本文ではなく、解決対象を短く示すメタデータです。"}
                    </span>
                  </label>

                  <label
                    className="forecast-field"
                    htmlFor="forecast-final-criteria"
                  >
                    <span className="forecast-field-header">
                      <span className="forecast-field-label">解決条件</span>
                      <span className="forecast-required">必須</span>
                    </span>
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
                      aria-describedby="forecast-final-criteria-help"
                      aria-invalid={hasNoResolutionCriteria}
                    />
                    <span
                      id="forecast-final-criteria-help"
                      className={`forecast-field-meta${
                        hasNoResolutionCriteria
                          ? " forecast-field-meta--error"
                          : ""
                      }`}
                    >
                      {hasNoResolutionCriteria
                        ? "解決条件を入力してください。"
                        : "公開情報で解決状態を選べる条件を書きます。"}
                    </span>
                  </label>

                  <label
                    className="forecast-field"
                    htmlFor="forecast-final-sources"
                  >
                    <span className="forecast-field-header">
                      <span className="forecast-field-label">
                        解決確認ソース
                      </span>
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
                      {hasTooManySources
                        ? "。解決確認ソースを20件以内にしてください。"
                        : ""}
                    </span>
                  </label>

                  <label
                    className="forecast-field"
                    htmlFor="forecast-final-outcomes"
                  >
                    <span className="forecast-field-header">
                      <span className="forecast-field-label">
                        解決時の結果状態
                      </span>
                      <span className="forecast-required">1行1候補</span>
                    </span>
                    <span
                      id="forecast-outcomes-help"
                      className="forecast-field-help"
                    >
                      これは元の調査依頼の答えではなく、後で公開情報に照らして選ぶ解決・結果状態です。Yes/Noに限る必要はありません。
                    </span>
                    <textarea
                      id="forecast-final-outcomes"
                      className="forecast-textarea forecast-textarea--compact"
                      value={finalFields.outcomes}
                      onChange={(event) =>
                        setFinalFields((current) => ({
                          ...current,
                          outcomes: event.target.value,
                        }))
                      }
                      rows={4}
                      disabled={state !== "final_edit"}
                      aria-describedby="forecast-outcomes-help forecast-outcome-count"
                      aria-invalid={hasNoOutcome || hasTooManyOutcomes}
                    />
                    <span
                      id="forecast-outcome-count"
                      className={`forecast-field-meta${hasTooManyOutcomes || hasNoOutcome ? " forecast-field-meta--error" : ""}`}
                    >
                      {outcomeLabels.length}/8 件
                      {hasNoOutcome
                        ? "。解決時の結果状態を1件以上入力してください。"
                        : ""}
                      {hasTooManyOutcomes
                        ? "。解決時の結果状態を8件以内にしてください。"
                        : ""}
                    </span>
                  </label>

                  <div className="forecast-optional-grid">
                    <label
                      className="forecast-field"
                      htmlFor="forecast-final-target"
                    >
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
                    <label
                      className="forecast-field"
                      htmlFor="forecast-final-unit"
                    >
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

                  <label
                    className="forecast-field"
                    htmlFor="forecast-final-context"
                  >
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

                {state === "creating" && (
                  <ForecastFlowProgress
                    heading="Forecastを保存中"
                    summary="元の依頼とForecastメタデータを保存済みフレーミングにしています。"
                    nodes={[
                      {
                        id: "preserve",
                        title: "元の依頼",
                        meta: "保存対象",
                        status: "done",
                        tone: "brief",
                      },
                      {
                        id: "metadata",
                        title: "メタデータ",
                        meta: "編集内容を反映",
                        status: "done",
                        tone: "review",
                      },
                      {
                        id: "save",
                        title: "Forecast保存",
                        meta: "作成リクエスト中",
                        status: "active",
                        tone: "research",
                      },
                      {
                        id: "approval",
                        title: "承認",
                        meta: "保存後に実行",
                        status: "pending",
                        tone: "finalize",
                      },
                    ]}
                  />
                )}

                {state === "final_edit" && !draftResponse.ready_to_create && (
                  <div className="forecast-ready-note" role="status">
                    メタデータ抽出ではまだ作成に必要な項目がそろっていません。必須メタデータが入力済みなら、この画面の編集内容で作成できます。
                  </div>
                )}

                <div className="forecast-actions">
                  {state === "final_edit" && (
                    <button
                      type="button"
                      className="btn-secondary"
                      onClick={() =>
                        setState(
                          clarifyingQuestions.length > 0
                            ? "questions"
                            : "needs_retry",
                        )
                      }
                    >
                      前のステップへ
                    </button>
                  )}
                  {(state === "final_edit" || isCreatingForecast) && (
                    <button
                      type="button"
                      className="btn-primary"
                      disabled={!canCreate || isCreatingForecast}
                      onClick={onCreate}
                    >
                      {isCreatingForecast
                        ? "Forecastを作成中"
                        : "Forecastを作成"}
                    </button>
                  )}
                  {state === "preview_ready" && (
                    <button
                      type="button"
                      className="btn-primary"
                      onClick={onApprove}
                    >
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
            <section
              className="form-panel forecast-preview-panel"
              aria-labelledby="saved-preview-title"
            >
              <div className="forecast-panel-heading">
                <p className="forecast-step-label">Saved</p>
                <h2 id="saved-preview-title">保存済みプレビュー</h2>
                <p>
                  承認すると、このフレーミングでcurrent_state packへ進めます。
                </p>
              </div>
              <div className="run-card-meta">
                <span>Version {preview.current_framing_version}</span>
                <span>{preview.confidentiality_class}</span>
              </div>
              <div className="forecast-preview-summary">
                <p className="run-card-title">{preview.question}</p>
                <p>{preview.resolution_criteria || "解決条件は未入力です。"}</p>
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
                  <article
                    className="run-card forecast-outcome-card"
                    key={outcome.outcome_id}
                  >
                    <p className="run-card-title">{outcome.label}</p>
                    <p>{outcome.definition}</p>
                    <p className="run-card-meta">
                      {outcome.normalization_group_id}
                    </p>
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

function OriginalPromptDisclosure({ prompt }: { prompt: string }) {
  const preservedPrompt = prompt.trim();
  if (!preservedPrompt) return null;
  return (
    <details className="forecast-original-prompt" open>
      <summary>Step 1の元の依頼</summary>
      <p>
        元の依頼は保存時にそのまま保持します。ここでは不足しているForecastメタデータだけを確認します。
      </p>
      <pre>{preservedPrompt}</pre>
    </details>
  );
}

function AiChangeSummary({ items }: { items: string[] }) {
  if (items.length === 0) return null;
  return (
    <section
      className="forecast-ai-summary"
      aria-labelledby="forecast-ai-summary-title"
    >
      <h3 id="forecast-ai-summary-title">AIが抽出・整理した点</h3>
      <ul>
        {items.map((item) => (
          <li key={item}>{item}</li>
        ))}
      </ul>
    </section>
  );
}

function GuidePanel() {
  return (
    <aside className="forecast-guidance-panel" aria-label="入力ガイド">
      <h2>作成の流れ</h2>
      <div className="forecast-guide-stack">
        <div>
          <strong>大枠だけ入力</strong>
          <span>
            期限、対象、解決条件が曖昧でも、まずは一文で始められます。
          </span>
        </div>
        <div>
          <strong>AIがメタデータ抽出</strong>
          <span>
            Forecast用の短い問い、解決条件、解決確認ソース、解決時の結果状態を元の依頼から拾います。
          </span>
        </div>
        <div>
          <strong>不足点だけ確認</strong>
          <span>
            元の依頼は保持し、足りないForecastメタデータだけ確認します。
          </span>
        </div>
        <div>
          <strong>保存後に承認</strong>
          <span>
            Forecast作成後、保存済みフレーミングを確認して承認します。
          </span>
        </div>
      </div>
      <div className="forecast-guidance-note">
        <span>現在の実行条件</span>
        <strong>AI draft / public / PhaseA</strong>
      </div>
    </aside>
  );
}
