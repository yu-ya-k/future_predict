import { useRef, useState } from "react";

import { createForecast, getForecast, reviewForecast } from "../../api/forecast";
import { navigate, routes } from "../../router";
import type { ForecastDetail } from "../../types";
import { formatForecastError } from "./errors";

type State =
  | "draft_input"
  | "creating"
  | "framing_pending"
  | "preview_ready"
  | "approving"
  | "detail";

function stableKey(action: string): string {
  return `forecast-new-${action}-${crypto.randomUUID()}`;
}

export function NewForecast() {
  const [question, setQuestion] = useState("");
  const [criteria, setCriteria] = useState("");
  const [outcomes, setOutcomes] = useState("Yes\nNo");
  const [forecastId, setForecastId] = useState<string | null>(null);
  const [preview, setPreview] = useState<ForecastDetail | null>(null);
  const [state, setState] = useState<State>("draft_input");
  const [error, setError] = useState<string | null>(null);
  const idempotencyKeys = useRef({
    create: stableKey("create"),
    approve: stableKey("approve-framing"),
  });
  const outcomeLabels = outcomes
    .split("\n")
    .map((item) => item.trim())
    .filter(Boolean);
  const isEditable = state === "draft_input";
  const isCreating = state === "creating" || state === "framing_pending";
  const hasTooManyOutcomes = outcomeLabels.length > 8;
  const canCreate = question.trim().length > 0 && !hasTooManyOutcomes && isEditable;

  async function onCreate() {
    setError(null);
    setState("creating");
    try {
      const response = await createForecast({
        question,
        resolution_criteria: criteria,
        outcomes: outcomeLabels,
      }, {
        idempotencyKey: idempotencyKeys.current.create,
      });
      setForecastId(response.forecast_id);
      setState("framing_pending");
      setPreview(await getForecast(response.forecast_id));
      setState("preview_ready");
    } catch (err) {
      setError(formatForecastError(err));
      setState("draft_input");
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
      setState(preview ? "preview_ready" : "framing_pending");
    }
  }

  return (
    <section className="screen screen-forecast-new">
      <div className="screen-header forecast-new-header">
        <div>
          <h1>新規Forecast</h1>
          <p className="screen-subtitle">
            公開情報で判定できる予測問いを作り、PhaseAのフレーミングを確認します。
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
          <div className="form-panel forecast-form-panel">
            <div className="forecast-panel-heading">
              <p className="forecast-step-label">Step 1</p>
              <h2>Forecastの前提を入力</h2>
              <p>
                後から公開情報で判定できる形にそろえると、証拠抽出と確率計算が安定します。
              </p>
            </div>

            <div className="forecast-field-stack">
              <label className="forecast-field" htmlFor="forecast-question">
                <span className="forecast-field-header">
                  <span className="forecast-field-label">予測したい問い</span>
                  <span className="forecast-required">必須</span>
                </span>
                <span className="forecast-field-help" id="forecast-question-help">
                  期限、対象、判定する事実を1つの問いにします。
                </span>
                <textarea
                  id="forecast-question"
                  className="forecast-textarea forecast-textarea--large"
                  value={question}
                  onChange={(event) => setQuestion(event.target.value)}
                  rows={5}
                  disabled={!isEditable}
                  aria-describedby="forecast-question-help"
                  placeholder="例: 2027年3月31日までに、対象プロダクトは正式ローンチされるか？"
                />
              </label>

              <label className="forecast-field" htmlFor="forecast-resolution-criteria">
                <span className="forecast-field-header">
                  <span className="forecast-field-label">判定条件</span>
                  <span className="forecast-optional">任意</span>
                </span>
                <span className="forecast-field-help" id="forecast-criteria-help">
                  どの公開情報でYes/Noを決めるか、曖昧なケースをどう扱うかを書きます。
                </span>
                <textarea
                  id="forecast-resolution-criteria"
                  className="forecast-textarea"
                  value={criteria}
                  onChange={(event) => setCriteria(event.target.value)}
                  rows={4}
                  disabled={!isEditable}
                  aria-describedby="forecast-criteria-help"
                  placeholder="例: 公式発表、規制当局の公開資料、主要報道のいずれかで確認する。ベータ版のみの場合はNo。"
                />
              </label>

              <label className="forecast-field" htmlFor="forecast-outcomes">
                <span className="forecast-field-header">
                  <span className="forecast-field-label">結果候補</span>
                  <span className="forecast-optional">1行1候補</span>
                </span>
                <span className="forecast-field-help" id="forecast-outcomes-help">
                  最大8件まで。二択なら既定のYes / Noのままで進められます。
                </span>
                <textarea
                  id="forecast-outcomes"
                  className="forecast-textarea forecast-textarea--compact"
                  value={outcomes}
                  onChange={(event) => setOutcomes(event.target.value)}
                  rows={3}
                  disabled={!isEditable}
                  aria-describedby="forecast-outcomes-help forecast-outcome-count"
                />
                <span
                  id="forecast-outcome-count"
                  className={`forecast-field-meta${hasTooManyOutcomes ? " forecast-field-meta--error" : ""}`}
                >
                  {outcomeLabels.length || 0}/8 件
                  {hasTooManyOutcomes ? "。結果候補を8件以内にしてください。" : ""}
                </span>
              </label>
            </div>

            <div className="forecast-actions">
              <button
                type="button"
                className="btn-primary"
                disabled={!canCreate}
                onClick={onCreate}
              >
                {isCreating ? "フレーミング作成中" : "フレーミングを作成"}
              </button>
              <button
                type="button"
                className="btn-secondary"
                disabled={!forecastId || state !== "preview_ready"}
                onClick={onApprove}
              >
                {state === "approving" ? "承認中" : "この内容で承認"}
              </button>
            </div>
          </div>

          {preview && (
            <section className="form-panel forecast-preview-panel" aria-labelledby="framing-preview-title">
              <div className="forecast-panel-heading">
                <p className="forecast-step-label">Step 2</p>
                <h2 id="framing-preview-title">フレーミングプレビュー</h2>
                <p>承認すると、この問いと結果候補を使ってcurrent_state packへ進みます。</p>
              </div>
              <div className="run-card-meta">
                <span>Version {preview.current_framing_version}</span>
                <span>{preview.confidentiality_class}</span>
              </div>
              <div className="forecast-preview-summary">
                <p className="run-card-title">{preview.question}</p>
                <p>{preview.resolution_criteria || "判定条件は未入力です。"}</p>
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

        <div className="forecast-guidance-panel" aria-label="入力ガイド">
          <h2>入力の目安</h2>
          <ul>
            <li>問いには「いつまでに」「何が」「どうなったら」を入れる。</li>
            <li>判定条件には、採用する公開ソースと例外扱いを書く。</li>
            <li>結果候補は互いに重ならない名前にする。</li>
          </ul>
          <div className="forecast-guidance-note">
            <span>現在の設定</span>
            <strong>public / current_state / PhaseA</strong>
          </div>
        </div>
      </div>
    </section>
  );
}
