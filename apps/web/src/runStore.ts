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
const RUN_STATUSES: ReadonlySet<RunStatus> = new Set([
  "queued",
  "submitted",
  "waiting_deep_research",
  "collecting",
  "reviewing",
  "needs_action",
  "needs_human_review",
  "completed",
  "cancelled",
  "failed",
]);

type Listener = () => void;
const listeners = new Set<Listener>();

function read(): TrackedRun[] {
  try {
    const raw = localStorage.getItem(STORAGE_KEY);
    if (!raw) return [];
    const parsed = JSON.parse(raw) as TrackedRun[];
    if (!Array.isArray(parsed)) return [];
    return parsed.flatMap((entry) => {
      const run = toTrackedRun(entry);
      return run ? [run] : [];
    });
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
  const validRun = toTrackedRun(run);
  if (!validRun) return;

  const runs = read().filter((r) => r.run_id !== run.run_id);
  runs.push(validRun);
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

function toTrackedRun(value: unknown): TrackedRun | null {
  if (!isRecord(value)) return null;
  if (!isNonEmptyString(value.run_id)) return null;
  if (!isNonEmptyString(value.title)) return null;
  if (!isValidCreatedAt(value.created_at)) return null;

  const run: TrackedRun = {
    run_id: value.run_id,
    title: value.title,
    created_at: value.created_at,
  };

  if (isFiniteNumber(value.max_total_iterations)) {
    run.max_total_iterations = Math.max(1, Math.trunc(value.max_total_iterations));
  }
  if (value.last_status !== undefined) {
    if (!isRunStatus(value.last_status)) return null;
    run.last_status = value.last_status;
  }
  if (isFiniteNumber(value.last_estimated_cost_usd)) {
    run.last_estimated_cost_usd = value.last_estimated_cost_usd;
  }
  if (value.last_latest_score === null || isFiniteNumber(value.last_latest_score)) {
    run.last_latest_score = value.last_latest_score;
  }

  return run;
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function isNonEmptyString(value: unknown): value is string {
  return typeof value === "string" && value.trim().length > 0;
}

function isValidCreatedAt(value: unknown): value is string {
  return typeof value === "string" && Number.isFinite(Date.parse(value));
}

function isRunStatus(value: unknown): value is RunStatus {
  return typeof value === "string" && RUN_STATUSES.has(value as RunStatus);
}

function isFiniteNumber(value: unknown): value is number {
  return typeof value === "number" && Number.isFinite(value);
}
