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

  async function onCreate() {
    setError(null);
    setState("creating");
    try {
      const response = await createForecast({
        question,
        resolution_criteria: criteria,
        outcomes: outcomes
          .split("\n")
          .map((item) => item.trim())
          .filter(Boolean),
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
    <section className="screen">
      <div className="screen-header">
        <div>
          <h1>新規Forecast</h1>
          <p className="screen-subtitle">PhaseA public current_state pack</p>
        </div>
      </div>

      {error && (
        <div className="alert alert-error" role="alert" style={{ whiteSpace: "pre-wrap" }}>
          {error}
        </div>
      )}

      <div className="form-panel">
        <label className="field">
          <span>Question</span>
          <textarea
            value={question}
            onChange={(event) => setQuestion(event.target.value)}
            rows={4}
            disabled={state !== "draft_input" && state !== "creating"}
          />
        </label>
        <label className="field">
          <span>Resolution criteria</span>
          <textarea
            value={criteria}
            onChange={(event) => setCriteria(event.target.value)}
            rows={3}
            disabled={state !== "draft_input" && state !== "creating"}
          />
        </label>
        <label className="field">
          <span>Outcomes</span>
          <textarea
            value={outcomes}
            onChange={(event) => setOutcomes(event.target.value)}
            rows={3}
            disabled={state !== "draft_input" && state !== "creating"}
          />
        </label>
        {preview && (
          <section aria-labelledby="framing-preview-title">
            <h2 id="framing-preview-title">Framing preview</h2>
            <div className="run-card-meta">
              <span>Version {preview.current_framing_version}</span>
              <span>{preview.confidentiality_class}</span>
            </div>
            <p className="run-card-title">{preview.question}</p>
            <p>{preview.resolution_criteria || "No resolution criteria provided."}</p>
            <div className="result-list">
              {preview.outcomes.map((outcome) => (
                <article className="run-card" key={outcome.outcome_id}>
                  <p className="run-card-title">{outcome.label}</p>
                  <p>{outcome.definition}</p>
                  <p className="run-card-meta">{outcome.normalization_group_id}</p>
                </article>
              ))}
            </div>
          </section>
        )}
        <div className="button-row">
          <button
            type="button"
            className="btn-primary"
            disabled={!question.trim() || state !== "draft_input"}
            onClick={onCreate}
          >
            Create framing
          </button>
          <button
            type="button"
            className="btn-secondary"
            disabled={!forecastId || state !== "preview_ready"}
            onClick={onApprove}
          >
            Approve framing
          </button>
        </div>
      </div>
    </section>
  );
}
