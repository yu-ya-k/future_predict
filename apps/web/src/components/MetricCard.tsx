import type { MetricCardProps } from "./types";

export function MetricCard({ label, value, unit, warn = false, icon }: MetricCardProps) {
  // icon prop is reserved for future Tabler icon rendering; currently unused
  void icon;
  return (
    <div className={`metric-card${warn ? " metric-card--warn" : ""}`}>
      <span className="metric-card__label">{label}</span>
      <span className="metric-card__value">
        {value}
        {unit && <span className="metric-card__unit">{unit}</span>}
      </span>
    </div>
  );
}
