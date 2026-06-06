import type { FlagChipProps } from "./types";

export function FlagChip({ active, label, tone = "neutral" }: FlagChipProps) {
  // When active, use the specified tone. When inactive, always neutral.
  const mod = active ? tone : "neutral";

  return (
    <span
      className={`flag-chip flag-chip--${mod}${active ? " flag-chip--active" : " flag-chip--inactive"}`}
      aria-label={`${label}: ${active ? "はい" : "いいえ"}`}
    >
      <span className="flag-chip__indicator" aria-hidden="true">
        {active ? "✓" : "–"}
      </span>
      {label}
    </span>
  );
}
