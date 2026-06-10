import type { CSSProperties } from "react";

import { MetricCard } from "../../components";
import type {
  EstimateSetResponse,
  ForecastCurrentResearchPack,
  ForecastDetail,
  ForecastPackRole,
  ProbabilityEstimate,
} from "../../types";
import { localizePackStatus } from "./forecastStatus";

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

/** Format an additive adjustment as signed percentage points (e.g. "+3.0pt"). */
function points(value: number | undefined): string {
  if (typeof value !== "number" || Number.isNaN(value)) return "-";
  const pt = value * 100;
  // Branch on the rounded value so a tiny negative that rounds to zero
  // renders as "±0.0pt" (the ± path) instead of leaking a "-0.0pt".
  const rounded = Number(pt.toFixed(1));
  if (rounded > 0) return `+${rounded.toFixed(1)}pt`;
  if (rounded < 0) return `-${Math.abs(rounded).toFixed(1)}pt`;
  return `±${Math.abs(rounded).toFixed(1)}pt`;
}

function clampPercent(value: number): number {
  if (!Number.isFinite(value)) return 0;
  return Math.min(100, Math.max(0, value));
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
        <span>公開情報パック</span>
        <span>{completed.length}/5</span>
      </div>
      <h2 id="forecast-pack-collection-heading">公開情報の収集状況</h2>
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
              <p>{pack ? localizePackStatus(status) : "未収集"}</p>
              <p className="run-card-meta">
                {pack
                  ? `${pack.tool_profile ?? "public"} / ${pack.data_classification ?? "public"} / attempt ${pack.attempt_no ?? 1}`
                  : "必須の既定パック"}
              </p>
              {pack && (historyCounts.get(role) ?? 0) > 1 && (
                <p className="run-card-meta">
                  有効 {pack.is_active === false ? "いいえ" : "はい"} / 履歴 {historyCounts.get(role)}件
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
      <h2 id="forecast-evidence-board-heading">証拠ボード</h2>
      <div className="metric-grid">
        <MetricCard label="有効パック数" value={packs.length} unit="件" />
        <MetricCard label="収集完了" value={completed} unit="件" />
        <MetricCard
          label="承認済みの主張対応"
          value={forecast?.approved_claim_target_link_count ?? 0}
          unit="件"
        />
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
      <h2 id="forecast-scenario-map-heading">シナリオマップ</h2>
      <div className="result-list">
        {(forecast?.outcomes ?? []).map((outcome) => (
          <article key={outcome.outcome_id} className="run-card">
            <p className="run-card-title">{outcome.label}</p>
            <p>{outcome.definition}</p>
            <p className="run-card-meta">
              シナリオ{" "}
              {scenarioEstimates.filter(
                (item) => item.components.derived_from_outcome_id === outcome.outcome_id,
              ).length}
              件
            </p>
          </article>
        ))}
      </div>
    </section>
  );
}

/** Resolve a probability estimate's target_id to a human-readable outcome label. */
function outcomeLabel(
  forecast: ForecastDetail | null,
  estimate: ProbabilityEstimate,
): string {
  if (estimate.target_kind === "outcome") {
    const outcome = forecast?.outcomes.find(
      (item) => item.outcome_id === estimate.target_id,
    );
    if (outcome) return outcome.label;
    // Never leak a raw UUID for an unresolved outcome.
    return "(不明なアウトカム)";
  }
  return estimate.target_id;
}

function ProbabilityRow({
  label,
  estimate,
  isTop,
}: {
  label: string;
  estimate: ProbabilityEstimate;
  isTop: boolean;
}) {
  const probPct = clampPercent(estimate.final_probability * 100);
  // Render sorted bounds so an inverted lo80 > hi80 never prints backwards.
  const lo80 = Math.min(estimate.uncertainty_range.lo80, estimate.uncertainty_range.hi80);
  const hi80 = Math.max(estimate.uncertainty_range.lo80, estimate.uncertainty_range.hi80);
  const lo = clampPercent(lo80 * 100);
  const hi = clampPercent(hi80 * 100);
  const intervalWidth = Math.max(0, hi - lo);
  const intervalText = `80%予測区間: ${percent(lo80)}〜${percent(hi80)}`;
  const ariaLabel = `${label}: 確率 ${percent(estimate.final_probability)}、${intervalText}`;
  return (
    <article className={`forecast-prob-row${isTop ? " forecast-prob-row--top" : ""}`}>
      <div className="forecast-prob-row__head">
        <span className="forecast-prob-row__label">
          {label}
          {isTop && <span className="forecast-prob-row__lead-tag">最有力</span>}
        </span>
        <span className="forecast-prob-row__value">{percent(estimate.final_probability)}</span>
      </div>
      <div
        className="forecast-prob-bar"
        role="img"
        aria-label={ariaLabel}
        style={
          {
            "--prob-pct": `${probPct}%`,
            "--interval-lo": `${lo}%`,
            "--interval-width": `${intervalWidth}%`,
          } as CSSProperties
        }
      >
        <div className="forecast-prob-bar__fill" />
        {intervalWidth > 0 && <div className="forecast-prob-bar__interval" />}
      </div>
      <p className="forecast-prob-row__interval-text">{intervalText}</p>
      <dl className="forecast-prob-row__breakdown">
        <div>
          <dt>事前確率</dt>
          <dd>{percent(estimate.prior)}</dd>
        </div>
        <div>
          <dt>証拠による更新</dt>
          <dd>{points(estimate.evidence_update)}</dd>
        </div>
        <div>
          <dt>交差影響</dt>
          <dd>{points(estimate.cross_impact_adjustment)}</dd>
        </div>
      </dl>
    </article>
  );
}

export function ProbabilityPanel({
  forecast,
  estimate,
}: {
  forecast?: ForecastDetail | null;
  estimate: EstimateSetResponse | null;
}) {
  if (!estimate) return null;
  const sorted = estimate.estimates
    .filter((item) => item.target_kind === "outcome")
    .sort((a, b) => b.final_probability - a.final_probability);
  const topId = sorted[0]?.estimate_id;
  return (
    <section className="form-panel" id="forecast-estimate-panel" aria-labelledby="forecast-probability-heading">
      <h2 id="forecast-probability-heading">確率の内訳</h2>
      <div className="forecast-prob-list">
        {sorted.map((item) => (
          <ProbabilityRow
            key={item.estimate_id}
            label={outcomeLabel(forecast ?? null, item)}
            estimate={item}
            isTop={item.estimate_id === topId}
          />
        ))}
      </div>
      <details className="forecast-debug">
        <summary>計算エンジン情報</summary>
        <dl className="forecast-debug__grid">
          <div>
            <dt>エンジン</dt>
            <dd>{estimate.engine_version}</dd>
          </div>
          <div>
            <dt>入力スナップショット</dt>
            <dd>{estimate.input_snapshot_hash}</dd>
          </div>
        </dl>
      </details>
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
  if (!topOutcome) return null;
  const label = outcomeLabel(forecast, topOutcome);
  // Render sorted bounds so an inverted lo80 > hi80 never prints backwards.
  const lo80 = Math.min(topOutcome.uncertainty_range.lo80, topOutcome.uncertainty_range.hi80);
  const hi80 = Math.max(topOutcome.uncertainty_range.lo80, topOutcome.uncertainty_range.hi80);
  const intervalText = `${percent(lo80)}〜${percent(hi80)}`;
  return (
    <section className="form-panel forecast-conclusion" aria-labelledby="forecast-report-heading">
      <h2 id="forecast-report-heading">予測サマリ</h2>
      <div className="forecast-conclusion__headline">
        <span className="forecast-conclusion__label">最有力アウトカム</span>
        <span className="forecast-conclusion__outcome">{label}</span>
        <span className="forecast-conclusion__probability">
          {percent(topOutcome.final_probability)}
        </span>
      </div>
      <p className="forecast-conclusion__interval">80%予測区間: {intervalText}</p>
    </section>
  );
}
