import type { VerdictBadgeProps } from "./types";
import type { Verdict } from "../types";

const VERDICT_MOD: Record<Verdict, string> = {
  pass: "pass",
  needs_llm_fix: "llm",
  needs_deep_research: "deep",
  human_review: "human",
};

export function VerdictBadge({ verdict }: VerdictBadgeProps) {
  const mod = VERDICT_MOD[verdict];

  return (
    <span className={`verdict-badge verdict-badge--${mod}`}>
      {verdict}
    </span>
  );
}
