import type { ScoreChipProps } from "./types";

function getTier(score: number): string {
  if (score >= 85) return "pass";
  if (score >= 70) return "llm";
  if (score >= 40) return "deep";
  return "human";
}

export function ScoreChip({ score, animate }: ScoreChipProps) {
  // animate is reserved for future count-up animation; currently unused
  void animate;
  const tier = getTier(score);
  const clamped = Math.max(0, Math.min(100, Math.round(score)));

  return (
    <span
      className="score-chip"
      data-tier={tier}
      aria-label={`スコア ${clamped}`}
    >
      {clamped}
    </span>
  );
}
