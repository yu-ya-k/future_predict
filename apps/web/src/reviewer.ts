/**
 * Reviewer identity management (ui_plan.md A2 GAP-4 / A8 Q-2).
 *
 * The API requires an `X-Reviewer-Id` header on the human-review endpoints
 * (router.py:35-44). The backend only checks that the header is non-empty — it
 * does not authenticate. As an interim measure (per Q-2) we persist a
 * reviewer id in localStorage and prompt for it on first use.
 */

const STORAGE_KEY = "dro.reviewerId";

export function getReviewerId(): string | null {
  try {
    const value = localStorage.getItem(STORAGE_KEY);
    return value && value.trim() ? value : null;
  } catch {
    return null;
  }
}

export function setReviewerId(reviewerId: string): void {
  const trimmed = reviewerId.trim();
  try {
    if (trimmed) {
      localStorage.setItem(STORAGE_KEY, trimmed);
    } else {
      localStorage.removeItem(STORAGE_KEY);
    }
  } catch {
    /* ignore storage failures (private mode etc.) */
  }
  notifyChange();
}

export function clearReviewerId(): void {
  try {
    localStorage.removeItem(STORAGE_KEY);
  } catch {
    /* ignore */
  }
  notifyChange();
}

// Lightweight subscription so React can re-render when the identity changes.
type Listener = () => void;
const listeners = new Set<Listener>();

function notifyChange(): void {
  for (const listener of listeners) listener();
}

export function subscribeReviewer(listener: Listener): () => void {
  listeners.add(listener);
  return () => listeners.delete(listener);
}
