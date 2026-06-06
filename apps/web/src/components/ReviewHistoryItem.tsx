import type { ReviewHistoryItemProps } from "./types";
import type { Verdict } from "../types";
import { VerdictBadge } from "./VerdictBadge";
import { ScoreChip } from "./ScoreChip";

const VERDICT_LINE_COLOR: Record<Verdict, string> = {
  pass: "var(--pass)",
  needs_llm_patch: "var(--llm)",
  needs_verification: "var(--deep)",
  needs_targeted_rerun: "var(--deep)",
  needs_full_rerun: "var(--deep)",
  needs_item_revision: "var(--human)",
  finalize_with_limitation: "var(--llm)",
  human_review: "var(--human)",
};

export function ReviewHistoryItem({
  review,
  showTrend = false,
  previousScore,
}: ReviewHistoryItemProps) {
  const rationale =
    review.rationale.length > 80
      ? review.rationale.slice(0, 80) + "…"
      : review.rationale;

  const lineColor = VERDICT_LINE_COLOR[review.verdict];

  let trendEl: React.ReactNode = null;
  if (showTrend && previousScore !== undefined) {
    const diff = review.score - previousScore;
    if (diff > 0) {
      trendEl = (
        <span className="review-history-item__trend review-history-item__trend--up" aria-label={`スコア +${diff} 改善`}>
          ↑+{diff}
        </span>
      );
    } else if (diff < 0) {
      trendEl = (
        <span className="review-history-item__trend review-history-item__trend--down" aria-label={`スコア ${diff} 低下`}>
          ↓{diff}
        </span>
      );
    }
  }

  return (
    <div
      className="review-history-item"
      style={{ "--verdict-line": lineColor } as React.CSSProperties}
    >
      <span className="review-history-item__num" aria-label={`レビュー番号 ${review.review_no}`}>
        #{review.review_no}
      </span>
      <VerdictBadge verdict={review.verdict} />
      <span className="review-history-item__rationale" title={review.rationale}>
        {rationale}
      </span>
      <div className="review-history-item__score-wrap">
        <ScoreChip score={review.score} />
        {trendEl}
      </div>
    </div>
  );
}
