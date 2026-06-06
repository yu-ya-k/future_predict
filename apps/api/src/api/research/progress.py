from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass

from api.research.schemas import ItemStatus, ReviewRecord


@dataclass(frozen=True)
class ItemProgress:
    has_progress: bool
    newly_answered: int
    newly_unverifiable: int
    confidence_gain: int
    regressions: int
    no_progress_reason: str | None


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
    item_progress = _item_progress(previous, current_review)
    if item_progress is True:
        return 0
    if item_progress is False:
        return current_no_progress_count + 1

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


def _item_progress(previous: ReviewRecord, current: ReviewRecord) -> bool | None:
    if not previous.item_assessments or not current.item_assessments:
        return None

    previous_by_id = {item.item_id: item for item in previous.item_assessments}
    current_by_id = {item.item_id: item for item in current.item_assessments}
    shared_ids = set(previous_by_id) & set(current_by_id)
    if not shared_ids:
        return None

    progress_detected = False
    regression_detected = False

    for item_id in shared_ids:
        prev = previous_by_id[item_id]
        curr = current_by_id[item_id]
        if _is_status_regression(prev.status.value, curr.status.value):
            regression_detected = True
        if prev.status in {ItemStatus.UNANSWERED, ItemStatus.PARTIAL} and curr.status in {
            ItemStatus.ANSWERED,
            ItemStatus.UNVERIFIABLE,
        }:
            progress_detected = True
        if curr.failure_mode_confidence - prev.failure_mode_confidence >= 10:
            progress_detected = True

    if regression_detected:
        return False
    if progress_detected:
        return True

    same_failures = all(
        previous_by_id[item_id].status == current_by_id[item_id].status
        and previous_by_id[item_id].failure_mode == current_by_id[item_id].failure_mode
        for item_id in shared_ids
    )
    return False if same_failures else None


def report_hash(report: str | None) -> str:
    normalized = re.sub(r"\s+", " ", (report or "").strip())
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


def compute_item_progress(
    *,
    previous_items: list[dict[str, object]],
    current_items: list[dict[str, object]],
) -> ItemProgress:
    previous_by_id = {str(item.get("item_id")): item for item in previous_items}
    current_by_id = {str(item.get("item_id")): item for item in current_items}
    shared_ids = set(previous_by_id) & set(current_by_id)

    newly_answered = 0
    newly_unverifiable = 0
    confidence_gain = 0
    regressions = 0

    for item_id in shared_ids:
        prev = previous_by_id[item_id]
        curr = current_by_id[item_id]
        prev_status = str(prev.get("status"))
        curr_status = str(curr.get("status"))
        prev_conf = _int_value(prev.get("confidence"))
        curr_conf = _int_value(curr.get("confidence"))
        confidence_gain += max(curr_conf - prev_conf, 0)
        if prev_status in {"unanswered", "partial"} and curr_status == "answered":
            newly_answered += 1
        if prev_status in {"unanswered", "partial"} and curr_status == "unverifiable":
            newly_unverifiable += 1
        if _is_status_regression(prev_status, curr_status):
            regressions += 1

    has_progress = (
        newly_answered > 0 or newly_unverifiable > 0 or confidence_gain >= 10
    ) and regressions == 0
    return ItemProgress(
        has_progress=has_progress,
        newly_answered=newly_answered,
        newly_unverifiable=newly_unverifiable,
        confidence_gain=confidence_gain,
        regressions=regressions,
        no_progress_reason=None
        if has_progress
        else "same_item_status_without_confidence_gain",
    )


def _int_value(value: object) -> int:
    return int(value) if isinstance(value, int | float) else 0


def _is_status_regression(previous_status: str, current_status: str) -> bool:
    if previous_status in {"answered", "out_of_scope"} and current_status in {
        "partial",
        "unanswered",
        "unverifiable",
    }:
        return True
    return previous_status == "unverifiable" and current_status in {
        "partial",
        "unanswered",
    }
