from __future__ import annotations

import re

from api.research.schemas import ReviewRecord


def normalize_text_list(items: list[str]) -> set[str]:
    normalized: set[str] = set()
    for item in items:
        value = re.sub(r"\s+", " ", item.strip().lower())
        if value:
            normalized.add(value)
    return normalized


def jaccard_similarity(left: set[str], right: set[str]) -> float:
    if not left and not right:
        return 1.0
    union = left | right
    if not union:
        return 0.0
    return len(left & right) / len(union)


def compute_no_progress_count(
    *,
    previous_reviews: list[ReviewRecord],
    current_review: ReviewRecord,
    current_no_progress_count: int,
) -> int:
    if not previous_reviews:
        return 0

    previous = previous_reviews[-1]
    score_delta = current_review.score - previous.score
    gap_similarity = jaccard_similarity(
        normalize_text_list(previous.gaps),
        normalize_text_list(current_review.gaps),
    )

    if previous.verdict == current_review.verdict and score_delta < 5 and gap_similarity >= 0.6:
        return current_no_progress_count + 1

    return 0
