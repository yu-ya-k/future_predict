import { useCallback, useEffect, useRef, useState } from "react";

import {
  commitForecastVersion,
  computeProbabilities,
  dispatchCurrentStatePack,
  extractEvidence,
  generateScenarios,
  getForecast,
  getForecastEstimateSet,
  resolveForecast,
  reviewForecast,
} from "../../api/forecast";
import { Link, routes } from "../../router";
import type {
  EstimateSetResponse,
  ForecastDetail as ForecastDetailType,
  ForecastStatus,
  ResolveForecastResponse,
} from "../../types";
import { formatForecastError } from "./errors";

type Command =
  | "pack"
  | "evidence"
  | "scenarios"
  | "claimTargets"
  | "compute"
  | "approve"
  | "commit"
  | "resolve";

function stableKey(forecastId: string, action: Command): string {
  return `forecast-${forecastId}-${action}-${crypto.randomUUID()}`;
}

function isMutatingClosed(status: ForecastStatus | undefined): boolean {
  return status === "resolved";
}

function statusReason(status: ForecastStatus | undefined, needed: string): string {
  if (!status) return "Loading forecast state.";
  if (status === "resolved") return "This forecast is already resolved.";
  return needed;
}

function hasEstimateSet(status: ForecastStatus): boolean {
  return status === "draft_ready" || status === "committed" || status === "resolved";
}

export function ForecastDetail({ forecastId }: { forecastId: string }) {
  const [forecast, setForecast] = useState<ForecastDetailType | null>(null);
  const [estimate, setEstimate] = useState<EstimateSetResponse | null>(null);
  const [claimTargetsApproved, setClaimTargetsApproved] = useState(false);
  const [resolution, setResolution] = useState<ResolveForecastResponse | null>(null);
  const [selectedOutcomeId, setSelectedOutcomeId] = useState("");
  const [resolutionNotes, setResolutionNotes] = useState("");
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const idempotencyKeys = useRef<Record<Command, string>>({
    pack: stableKey(forecastId, "pack"),
    evidence: stableKey(forecastId, "evidence"),
    scenarios: stableKey(forecastId, "scenarios"),
    claimTargets: stableKey(forecastId, "claimTargets"),
    compute: stableKey(forecastId, "compute"),
    approve: stableKey(forecastId, "approve"),
    commit: stableKey(forecastId, "commit"),
    resolve: stableKey(forecastId, "resolve"),
  });

  const load = useCallback(async () => {
    const nextForecast = await getForecast(forecastId);
    setForecast(nextForecast);
    if (hasEstimateSet(nextForecast.status)) {
      setEstimate(await getForecastEstimateSet(forecastId));
    } else {
      setEstimate(null);
    }
  }, [forecastId]);

  useEffect(() => {
    void load().catch((err) => setError(formatForecastError(err)));
  }, [load]);

  useEffect(() => {
    if (forecast?.status !== "pack_running") return undefined;
    const interval = window.setInterval(() => {
      void load().catch((err) => setError(formatForecastError(err)));
    }, 3_000);
    return () => window.clearInterval(interval);
  }, [forecast?.status, load]);

  useEffect(() => {
    if (!selectedOutcomeId && forecast?.outcomes[0]) {
      setSelectedOutcomeId(forecast.outcomes[0].outcome_id);
    }
  }, [forecast, selectedOutcomeId]);

  useEffect(() => {
    if (forecast?.status && forecast.status !== "scenarios_ready") {
      setClaimTargetsApproved(false);
    }
  }, [forecast?.status]);

  async function runStep(step: Command, fn: () => Promise<unknown>) {
    setBusy(step);
    setError(null);
    try {
      const result = await fn();
      if (step === "claimTargets") setClaimTargetsApproved(true);
      if (step === "compute") setEstimate(result as EstimateSetResponse);
      if (step === "resolve") setResolution(result as ResolveForecastResponse);
      await load();
    } catch (err) {
      setError(formatForecastError(err));
    } finally {
      setBusy(null);
    }
  }

  const status = forecast?.status;
  const approvedFraming = Boolean(forecast?.approved_framing_version);
  const canDispatch = approvedFraming && status === "framing_approved";
  const canExtract = status === "pack_running";
  const canGenerate = status === "evidence_ready";
  const canApproveClaimTargets = status === "scenarios_ready" && !claimTargetsApproved;
  const canCompute = status === "scenarios_ready" && claimTargetsApproved;
  const canRestoreDraft = status === "draft_ready" && !estimate;
  const canApproveEstimate = status === "draft_ready" && Boolean(estimate);
  const canCommit = status === "draft_ready" && Boolean(estimate);
  const canResolve = status === "committed" && Boolean(selectedOutcomeId);
  const closed = isMutatingClosed(status);

  return (
    <section className="screen">
      <div className="screen-header">
        <div>
          <h1>Forecast</h1>
          <p className="screen-subtitle">{forecast?.question ?? forecastId}</p>
        </div>
        <Link to={routes().forecastAudit(forecastId)} className="btn-secondary">
          Audit
        </Link>
      </div>

      {error && (
        <div className="alert alert-error" role="alert" style={{ whiteSpace: "pre-wrap" }}>
          {error}
        </div>
      )}

      <div className="metric-grid">
        <div className="metric-card">
          <span className="metric-label">Status</span>
          <strong>{forecast?.status ?? "loading"}</strong>
        </div>
        <div className="metric-card">
          <span className="metric-label">Framing</span>
          <strong>{forecast?.approved_framing_version ? "approved" : "pending"}</strong>
        </div>
        <div className="metric-card">
          <span className="metric-label">Engine</span>
          <strong>{estimate?.engine_version ?? "not computed"}</strong>
        </div>
      </div>

      {forecast?.status === "pack_running" && (
        <p role="status">
          Polling current_state pack status. Evidence extraction is available once the pack
          has completed.
        </p>
      )}

      <div className="button-row">
        <button
          type="button"
          className="btn-secondary"
          disabled={!!busy || !canDispatch || closed}
          title={
            canDispatch
              ? undefined
              : statusReason(status, "Requires approved framing and no existing pack.")
          }
          onClick={() =>
            runStep("pack", () =>
              dispatchCurrentStatePack(forecastId, {
                idempotencyKey: idempotencyKeys.current.pack,
              }),
            )
          }
        >
          Dispatch pack
        </button>
        <button
          type="button"
          className="btn-secondary"
          disabled={!!busy || !canExtract || closed}
          title={
            canExtract
              ? "Requires completed current_state pack."
              : statusReason(status, "Requires a running or completed current_state pack.")
          }
          onClick={() =>
            runStep("evidence", () =>
              extractEvidence(forecastId, {
                idempotencyKey: idempotencyKeys.current.evidence,
              }),
            )
          }
        >
          Extract evidence
        </button>
        <button
          type="button"
          className="btn-secondary"
          disabled={!!busy || !canGenerate || closed}
          title={
            canGenerate ? undefined : statusReason(status, "Requires extracted evidence.")
          }
          onClick={() =>
            runStep("scenarios", () =>
              generateScenarios(forecastId, {
                idempotencyKey: idempotencyKeys.current.scenarios,
              }),
            )
          }
        >
          Generate scenarios
        </button>
        <button
          type="button"
          className="btn-secondary"
          disabled={!!busy || !canApproveClaimTargets || closed}
          title={
            canApproveClaimTargets
              ? undefined
              : claimTargetsApproved
                ? "Claim-target links are approved."
                : statusReason(status, "Requires generated scenarios.")
          }
          onClick={() =>
            runStep("claimTargets", () =>
              reviewForecast(
                forecastId,
                { action: "approve_claim_target_links" },
                { idempotencyKey: idempotencyKeys.current.claimTargets },
              ),
            )
          }
        >
          Approve claim links
        </button>
        <button
          type="button"
          className="btn-primary"
          disabled={!!busy || (!canCompute && !canRestoreDraft) || closed}
          title={
            canCompute || canRestoreDraft
              ? undefined
              : statusReason(status, "Requires approved claim-target links.")
          }
          onClick={() =>
            runStep("compute", () =>
              computeProbabilities(forecastId, {
                idempotencyKey: idempotencyKeys.current.compute,
              }),
            )
          }
        >
          {canRestoreDraft ? "Restore draft estimate" : "Compute"}
        </button>
      </div>

      <div className="form-panel">
        <h2>Next action</h2>
        <ul>
          <li>
            Dispatch pack:{" "}
            {canDispatch ? "available" : statusReason(status, "approve framing first")}
          </li>
          <li>
            Extract evidence:{" "}
            {canExtract
              ? "available after the research pack completes"
              : statusReason(status, "dispatch the current_state pack first")}
          </li>
          <li>
            Generate scenarios:{" "}
            {canGenerate ? "available" : statusReason(status, "extract evidence first")}
          </li>
          <li>
            Approve claim links:{" "}
            {canApproveClaimTargets
              ? "available"
              : claimTargetsApproved
                ? "approved"
                : statusReason(status, "generate scenarios first")}
          </li>
          <li>
            Compute:{" "}
            {canCompute
              ? "available"
              : canRestoreDraft
                ? "draft estimate exists; loading current estimate set"
                : statusReason(status, "approve claim links first")}
          </li>
        </ul>
      </div>

      {estimate && (
        <div className="form-panel">
          <div className="run-card-meta">
            <span>{estimate.engine_version}</span>
            <span>{estimate.input_snapshot_hash}</span>
          </div>
          <div className="result-list">
            {estimate.estimates.map((item) => (
              <article key={item.estimate_id} className="run-card">
                <p className="run-card-title">{item.target_id}</p>
                <p>{(item.final_probability * 100).toFixed(1)}%</p>
                <p className="run-card-meta">
                  ±0.10 range {item.uncertainty_range.lo80.toFixed(3)}-
                  {item.uncertainty_range.hi80.toFixed(3)}
                </p>
              </article>
            ))}
          </div>
          <div className="button-row">
            <button
              type="button"
              className="btn-secondary"
              disabled={!!busy || !canApproveEstimate}
              onClick={() =>
                runStep("approve", () =>
                  reviewForecast(
                    forecastId,
                    {
                      action: "approve_phase_a_version",
                      estimate_set_id: estimate.estimate_set_id,
                    },
                    { idempotencyKey: idempotencyKeys.current.approve },
                  ),
                )
              }
            >
              Approve PhaseA
            </button>
            <button
              type="button"
              className="btn-primary"
              disabled={!!busy || !canCommit}
              onClick={() =>
                runStep("commit", () =>
                  commitForecastVersion(
                    forecastId,
                    {
                      estimate_set_id: estimate.estimate_set_id,
                      expected_input_snapshot_hash: estimate.input_snapshot_hash,
                    },
                    { idempotencyKey: idempotencyKeys.current.commit },
                  ),
                )
              }
            >
              Commit
            </button>
          </div>
        </div>
      )}

      {(forecast?.status === "committed" || forecast?.status === "resolved") && (
        <div className="form-panel">
          <h2>Resolve</h2>
          {forecast.status === "resolved" ? (
            <p>This forecast has already been resolved.</p>
          ) : (
            <>
              <label className="field">
                <span>Actual outcome</span>
                <select
                  value={selectedOutcomeId}
                  onChange={(event) => setSelectedOutcomeId(event.target.value)}
                >
                  {forecast.outcomes.map((outcome) => (
                    <option key={outcome.outcome_id} value={outcome.outcome_id}>
                      {outcome.label}
                    </option>
                  ))}
                </select>
              </label>
              <label className="field">
                <span>Resolution notes</span>
                <textarea
                  value={resolutionNotes}
                  onChange={(event) => setResolutionNotes(event.target.value)}
                  rows={3}
                />
              </label>
              <button
                type="button"
                className="btn-primary"
                disabled={!!busy || !canResolve}
                onClick={() =>
                  runStep("resolve", () =>
                    resolveForecast(
                      forecastId,
                      {
                        outcome_id: selectedOutcomeId,
                        resolution_notes: resolutionNotes.trim() || null,
                      },
                      { idempotencyKey: idempotencyKeys.current.resolve },
                    ),
                  )
                }
              >
                Resolve
              </button>
            </>
          )}
          {resolution && (
            <div className="run-card-meta">
              <span>Brier {resolution.multiclass_brier.toFixed(4)}</span>
              <span>Log {resolution.log_score.toFixed(4)}</span>
              <span>{resolution.scorer_version}</span>
            </div>
          )}
        </div>
      )}
    </section>
  );
}
