import type {
  EstimateSetResponse,
  ForecastCurrentResearchPack,
  ForecastDetail,
  ForecastPackRole,
} from "../../types";

const DEFAULT_PACK_ROLES: ForecastPackRole[] = [
  "current_state",
  "base_rate",
  "drivers",
  "counter_evidence",
  "signals",
];

const PACK_LABELS: Record<ForecastPackRole, string> = {
  current_state: "Current State",
  base_rate: "Base Rate",
  drivers: "Drivers",
  counter_evidence: "Counter Evidence",
  signals: "Signals",
};

function percent(value: number | undefined): string {
  if (typeof value !== "number" || Number.isNaN(value)) return "-";
  return `${(value * 100).toFixed(1)}%`;
}

function packStatus(pack: ForecastCurrentResearchPack | undefined): string {
  if (!pack) return "未収集";
  return pack.effective_status || pack.pack_status;
}

function packTime(pack: ForecastCurrentResearchPack): number {
  const time = Date.parse(pack.pack_updated_at || pack.pack_created_at);
  return Number.isNaN(time) ? 0 : time;
}

function preferredPack(
  current: ForecastCurrentResearchPack | undefined,
  candidate: ForecastCurrentResearchPack,
): ForecastCurrentResearchPack {
  if (!current) return candidate;
  const currentActive = current.is_active !== false;
  const candidateActive = candidate.is_active !== false;
  if (candidateActive !== currentActive) return candidateActive ? candidate : current;
  const currentAttempt = current.attempt_no ?? 1;
  const candidateAttempt = candidate.attempt_no ?? 1;
  if (candidateAttempt !== currentAttempt) {
    return candidateAttempt > currentAttempt ? candidate : current;
  }
  return packTime(candidate) > packTime(current) ? candidate : current;
}

function activeDefaultPackByRole(
  packs: ForecastCurrentResearchPack[],
): Map<ForecastPackRole, ForecastCurrentResearchPack> {
  const byRole = new Map<ForecastPackRole, ForecastCurrentResearchPack>();
  for (const pack of packs) {
    if (!pack.pack_role || !DEFAULT_PACK_ROLES.includes(pack.pack_role)) continue;
    byRole.set(pack.pack_role, preferredPack(byRole.get(pack.pack_role), pack));
  }
  return byRole;
}

export function PackCollectionPanel({
  packs,
  onDispatchDefaults,
  onRerunPack,
  busy,
}: {
  packs: ForecastCurrentResearchPack[];
  onDispatchDefaults?: () => void;
  onRerunPack?: (pack: ForecastCurrentResearchPack) => void;
  busy?: boolean;
}) {
  const byRole = activeDefaultPackByRole(packs);
  const historyCounts = new Map<ForecastPackRole, number>();
  for (const pack of packs) {
    if (!pack.pack_role || !DEFAULT_PACK_ROLES.includes(pack.pack_role)) continue;
    historyCounts.set(pack.pack_role, (historyCounts.get(pack.pack_role) ?? 0) + 1);
  }
  const completed = DEFAULT_PACK_ROLES.filter(
    (role) => byRole.get(role)?.effective_status === "completed",
  );
  return (
    <section className="form-panel" aria-labelledby="forecast-pack-collection-heading">
      <div className="run-card-meta">
        <span>Phase B Packs</span>
        <span>{completed.length}/5</span>
      </div>
      <h2 id="forecast-pack-collection-heading">Pack Collection</h2>
      <div className="result-list">
        {DEFAULT_PACK_ROLES.map((role) => {
          const pack = byRole.get(role);
          const status = packStatus(pack);
          const rerunnable =
            Boolean(pack && onRerunPack) &&
            status !== "running" &&
            status !== "submitting";
          return (
            <article key={role} className="run-card">
              <p className="run-card-title">{PACK_LABELS[role]}</p>
              <p>{status}</p>
              <p className="run-card-meta">
                {pack
                  ? `${pack.tool_profile ?? "public"} / ${pack.data_classification ?? "public"} / attempt ${pack.attempt_no ?? 1}`
                  : "required default pack"}
              </p>
              {pack && (historyCounts.get(role) ?? 0) > 1 && (
                <p className="run-card-meta">
                  active {pack.is_active === false ? "no" : "yes"} / history {historyCounts.get(role)}
                </p>
              )}
              {pack && onRerunPack && (
                <button
                  type="button"
                  className="btn-secondary"
                  disabled={busy || !rerunnable}
                  onClick={() => onRerunPack(pack)}
                  aria-label={`${PACK_LABELS[role]} を再実行`}
                >
                  再実行
                </button>
              )}
            </article>
          );
        })}
      </div>
      {onDispatchDefaults && (
        <button
          type="button"
          className="btn-secondary"
          disabled={busy}
          onClick={onDispatchDefaults}
        >
          5 Packを開始
        </button>
      )}
    </section>
  );
}

export function EvidenceBoard({ forecast }: { forecast: ForecastDetail | null }) {
  const packs = forecast?.research_packs ?? [];
  const completed = packs.filter((pack) => pack.effective_status === "completed").length;
  return (
    <section className="form-panel" aria-labelledby="forecast-evidence-board-heading">
      <h2 id="forecast-evidence-board-heading">Evidence Board</h2>
      <div className="metric-grid">
        <div className="metric-card">
          <span className="metric-label">Active packs</span>
          <strong>{packs.length}</strong>
        </div>
        <div className="metric-card">
          <span className="metric-label">Completed</span>
          <strong>{completed}</strong>
        </div>
        <div className="metric-card">
          <span className="metric-label">Claim links</span>
          <strong>{forecast?.approved_claim_target_link_count ?? 0}</strong>
        </div>
      </div>
    </section>
  );
}

export function ScenarioMap({
  forecast,
  estimate,
}: {
  forecast: ForecastDetail | null;
  estimate: EstimateSetResponse | null;
}) {
  const scenarioEstimates = estimate?.estimates.filter((item) => item.target_kind === "scenario") ?? [];
  return (
    <section className="form-panel" aria-labelledby="forecast-scenario-map-heading">
      <h2 id="forecast-scenario-map-heading">Scenario Map</h2>
      <div className="result-list">
        {(forecast?.outcomes ?? []).map((outcome) => (
          <article key={outcome.outcome_id} className="run-card">
            <p className="run-card-title">{outcome.label}</p>
            <p>{outcome.definition}</p>
            <p className="run-card-meta">
              {scenarioEstimates.filter(
                (item) => item.components.derived_from_outcome_id === outcome.outcome_id,
              ).length} scenarios
            </p>
          </article>
        ))}
      </div>
    </section>
  );
}

export function ProbabilityPanel({ estimate }: { estimate: EstimateSetResponse | null }) {
  if (!estimate) return null;
  return (
    <section className="form-panel" id="forecast-estimate-panel">
      <div className="run-card-meta">
        <span>{estimate.engine_version}</span>
        <span>{estimate.input_snapshot_hash}</span>
      </div>
      <h2>Probability</h2>
      <div className="result-list">
        {estimate.estimates.map((item) => (
          <article key={item.estimate_id} className="run-card">
            <p className="run-card-title">{item.target_id}</p>
            <p>{percent(item.final_probability)}</p>
            <p className="run-card-meta">
              prior {percent(item.prior)} / evidence {item.evidence_update.toFixed(3)} /
              cross {item.cross_impact_adjustment.toFixed(3)}
            </p>
            <p className="run-card-meta">
              80% {item.uncertainty_range.lo80.toFixed(3)}-
              {item.uncertainty_range.hi80.toFixed(3)}
            </p>
          </article>
        ))}
      </div>
    </section>
  );
}

export function ForecastReport({
  forecast,
  estimate,
}: {
  forecast: ForecastDetail | null;
  estimate: EstimateSetResponse | null;
}) {
  const topOutcome = estimate
    ? [...estimate.estimates]
        .filter((item) => item.target_kind === "outcome")
        .sort((a, b) => b.final_probability - a.final_probability)[0]
    : undefined;
  return (
    <section className="form-panel" aria-labelledby="forecast-report-heading">
      <h2 id="forecast-report-heading">Forecast Report</h2>
      <p>{forecast ? `Status: ${forecast.status}` : "Status: loading"}</p>
      <div className="run-card-meta">
        <span>{forecast?.status ?? "loading"}</span>
        <span>{topOutcome ? `top ${percent(topOutcome.final_probability)}` : "not computed"}</span>
      </div>
    </section>
  );
}
