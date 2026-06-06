from __future__ import annotations

import hashlib
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
    union = left | right
    if not union:
        return 0.0
    return len(left & right) / len(union)


def repeated_list_similarity(left: set[str], right: set[str], threshold: float = 0.6) -> bool:
    if not left or not right:
        return False
    return jaccard_similarity(left, right) >= threshold


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
    gaps_still_similar = repeated_list_similarity(
        normalize_text_list(previous.gaps),
        normalize_text_list(current_review.gaps),
    )
    factuality_still_similar = repeated_list_similarity(
        normalize_text_list(previous.factuality_concerns),
        normalize_text_list(current_review.factuality_concerns),
    )
    source_still_similar = repeated_list_similarity(
        normalize_text_list(previous.source_quality_concerns),
        normalize_text_list(current_review.source_quality_concerns),
    )
    same_report = (
        bool(previous.report_hash)
        and bool(current_review.report_hash)
        and previous.report_hash == current_review.report_hash
    )

    concerns_still_similar = factuality_still_similar or source_still_similar

    if (
        previous.verdict == current_review.verdict
        and score_delta < 5
        and (gaps_still_similar or concerns_still_similar or same_report)
    ):
        return current_no_progress_count + 1

    return 0


def report_hash(report: str | None) -> str:
    normalized = re.sub(r"\s+", " ", (report or "").strip())
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()
