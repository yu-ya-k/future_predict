import type { CostMeterProps } from "./types";

export function CostMeter({ estimated, compact = false }: CostMeterProps) {
  if (compact) {
    return (
      <span className="cost-meter cost-meter--compact" aria-label={`推定コスト $${estimated.toFixed(2)}`}>
        <span className="cost-meter__label">
          ${estimated.toFixed(2)}
        </span>
      </span>
    );
  }

  return (
    <div className="cost-meter" aria-label={`推定コスト $${estimated.toFixed(2)}`}>
      <div className="cost-meter__header">
        <span className="cost-meter__title">推定コスト</span>
        <span className="cost-meter__label">${estimated.toFixed(2)}</span>
      </div>
    </div>
  );
}
