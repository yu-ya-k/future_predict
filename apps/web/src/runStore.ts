/**
 * Locally tracked runs (ui_plan.md A2 GAP-1, A5).
 *
 * There is no `GET /research-runs` list endpoint, and the status response does
 * not carry the dashboard title. So when a run is created (SCR-1) we persist
 * the run id plus the request-time metadata the UI needs to localStorage.
 * The dashboard (SCR-2) reconstructs its "in progress / done" lists from here.
 */

import type { RunStatus } from "./types";

export interface TrackedRun {
  run_id: string;
  /** First line / summary of the user prompt, for list display. */
  title: string;
  /**
   * Iteration ceiling from the create request options
   * (`max_total_iterations`). The status response does not carry it, so the
   * PipelineStepper loop indicator (I-3) reads it from here.
   */
  max_total_iterations?: number;
  created_at: string;
  /** Last status observed by polling, for offline list rendering. */
  last_status?: RunStatus;
  /**
   * Last cost / score observed by polling. Persisted so terminal runs (which
   * are no longer polled) still show their final cost (I-5) and score on the
   * dashboard after the live status drops out.
   */
  last_estimated_cost_usd?: number;
  last_latest_score?: number | null;
}

/** Optional progress snapshot persisted alongside a status update. */
export interface TrackedProgressSnapshot {
  estimated_cost_usd?: number;
  latest_score?: number | null;
}

const STORAGE_KEY = "dro.trackedRuns";

type Listener = () => void;
const listeners = new Set<Listener>();

function read(): TrackedRun[] {
  try {
    const raw = localStorage.getItem(STORAGE_KEY);
    if (!raw) return [];
    const parsed = JSON.parse(raw) as TrackedRun[];
    return Array.isArray(parsed) ? parsed : [];
  } catch {
    return [];
  }
}

function write(runs: TrackedRun[]): void {
  try {
    localStorage.setItem(STORAGE_KEY, JSON.stringify(runs));
  } catch {
    /* ignore quota / private mode */
  }
  for (const listener of listeners) listener();
}

export function getTrackedRuns(): TrackedRun[] {
  return read().sort((a, b) => b.created_at.localeCompare(a.created_at));
}

export function trackRun(run: TrackedRun): void {
  const runs = read().filter((r) => r.run_id !== run.run_id);
  runs.push(run);
  write(runs);
}

export function updateTrackedStatus(
  runId: string,
  status: RunStatus,
  snapshot?: TrackedProgressSnapshot,
): void {
  const runs = read();
  const target = runs.find((r) => r.run_id === runId);
  if (!target) return;

  let changed = false;
  if (target.last_status !== status) {
    target.last_status = status;
    changed = true;
  }
  if (
    snapshot?.estimated_cost_usd !== undefined &&
    target.last_estimated_cost_usd !== snapshot.estimated_cost_usd
  ) {
    target.last_estimated_cost_usd = snapshot.estimated_cost_usd;
    changed = true;
  }
  if (
    snapshot?.latest_score !== undefined &&
    target.last_latest_score !== snapshot.latest_score
  ) {
    target.last_latest_score = snapshot.latest_score;
    changed = true;
  }

  if (changed) write(runs);
}

export function getTrackedRun(runId: string): TrackedRun | undefined {
  return read().find((r) => r.run_id === runId);
}

export function untrackRun(runId: string): void {
  write(read().filter((r) => r.run_id !== runId));
}

export function subscribeRuns(listener: Listener): () => void {
  listeners.add(listener);
  return () => listeners.delete(listener);
}
