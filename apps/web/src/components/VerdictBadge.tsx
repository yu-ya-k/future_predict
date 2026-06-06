import type { VerdictBadgeProps } from "./types";
import type { Verdict } from "../types";

const VERDICT_MOD: Record<Verdict, string> = {
  pass: "pass",
  needs_llm_patch: "llm",
  needs_verification: "deep",
  needs_targeted_rerun: "deep",
  needs_full_rerun: "deep",
  needs_item_revision: "human",
  finalize_with_limitation: "llm",
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
