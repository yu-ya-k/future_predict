import type { CostMeterProps } from "./types";

export function CostMeter({ estimated, max, compact = false }: CostMeterProps) {
  const pct = max > 0 ? Math.min(100, (estimated / max) * 100) : 0;
  const isCritical = pct >= 95;
  const isWarn = pct >= 80 && !isCritical;

  const fillClass = [
    "cost-meter__fill",
    isWarn ? "cost-meter__fill--warn" : "",
    isCritical ? "cost-meter__fill--critical" : "",
  ]
    .filter(Boolean)
    .join(" ");

  if (compact) {
    return (
      <span className="cost-meter cost-meter--compact" aria-label={`コスト $${estimated.toFixed(2)} / $${max.toFixed(2)}`}>
        <span className="cost-meter__track" role="presentation">
          <span
            className={fillClass}
            style={{ width: `${pct}%` }}
            data-warn={isWarn ? "true" : undefined}
            data-critical={isCritical ? "true" : undefined}
          />
        </span>
        <span className={`cost-meter__label${isCritical ? " cost-meter__label--critical" : isWarn ? " cost-meter__label--warn" : ""}`}>
          ${estimated.toFixed(2)}
        </span>
      </span>
    );
  }

  return (
    <div className="cost-meter" aria-label={`コスト $${estimated.toFixed(2)} / $${max.toFixed(2)}`}>
      <div className="cost-meter__header">
        <span className="cost-meter__title">予算使用状況</span>
        <span className={`cost-meter__label${isCritical ? " cost-meter__label--critical" : isWarn ? " cost-meter__label--warn" : ""}`}>
          ${estimated.toFixed(2)}
          <span className="cost-meter__max"> / ${max.toFixed(2)}</span>
        </span>
      </div>
      <div className="cost-meter__track" role="progressbar" aria-valuenow={Math.round(pct)} aria-valuemin={0} aria-valuemax={100}>
        <div
          className={fillClass}
          style={{ width: `${pct}%` }}
          data-warn={isWarn ? "true" : undefined}
          data-critical={isCritical ? "true" : undefined}
        />
      </div>
      {isWarn && (
        <p className="cost-meter__warning">
          {isCritical ? "予算上限に達しそうです" : `残り $${(max - estimated).toFixed(2)}`}
        </p>
      )}
    </div>
  );
}
