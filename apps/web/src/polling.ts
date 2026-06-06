/**
 * Phase-dependent polling intervals (ui_plan.md A6 table).
 * Returns the next delay in ms for a given run status, or null to stop
 * (terminal statuses). Centralised so SCR-2 and SCR-3 stay consistent.
 */

import { isTerminal, type RunStatus } from "./types";

const SECONDS = 1000;

export function statusPollInterval(status: RunStatus | undefined): number | null {
  if (status && isTerminal(status)) return null;
  switch (status) {
    case "waiting_deep_research":
    case "collecting":
      return 15 * SECONDS; // long phases
    case "needs_human_review":
      return 30 * SECONDS; // awaiting a human; just confirm resume happened
    case "reviewing":
    case "submitted":
    case "queued":
    case "needs_action":
      return 5 * SECONDS;
    default:
      return 10 * SECONDS;
  }
}

/** Dashboard queue + tracked-run polling intervals (A6 SCR-2). */
export const HUMAN_REVIEW_QUEUE_INTERVAL = 20 * SECONDS;
export const TRACKED_RUN_INTERVAL = 30 * SECONDS;
