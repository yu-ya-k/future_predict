/**
 * Generic polling hook with phase-dependent intervals and error backoff
 * (ui_plan.md A6). Used everywhere SSE would otherwise be needed (GAP-2).
 *
 *  - `interval(data)` returns the next delay in ms, or `null` to stop polling
 *    (e.g. terminal status reached).
 *  - On 3 consecutive failures the effective interval doubles, then grows by one
 *    base step per further failure (2x, 3x, 4x ...), capped at 120s, and
 *    `connectionUnstable` flips true so the UI can say "retrying" (I-2). The
 *    gentle linear climb keeps long jobs from looking frozen after a brief blip.
 */

import { useCallback, useEffect, useRef, useState } from "react";

const BACKOFF_THRESHOLD = 3;
const MAX_BACKOFF_MS = 120_000;

interface PollingState<T> {
  data: T | undefined;
  error: unknown;
  loading: boolean;
  /** true after BACKOFF_THRESHOLD consecutive errors (I-2 messaging). */
  connectionUnstable: boolean;
}

interface UsePollingOptions<T> {
  fetcher: (signal: AbortSignal) => Promise<T>;
  /** Next delay in ms given the latest data, or null to stop. */
  interval: (data: T | undefined) => number | null;
  enabled?: boolean;
  /** Called once on each successful fetch (e.g. notifications, store sync). */
  onData?: (data: T) => void;
}

export function usePolling<T>({
  fetcher,
  interval,
  enabled = true,
  onData,
}: UsePollingOptions<T>): PollingState<T> & { refetch: () => void } {
  const [state, setState] = useState<PollingState<T>>({
    data: undefined,
    error: undefined,
    loading: enabled,
    connectionUnstable: false,
  });

  const errorStreak = useRef(0);
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const cancelled = useRef(false);

  // Keep the latest callbacks without retriggering the polling loop.
  const fetcherRef = useRef(fetcher);
  const intervalRef = useRef(interval);
  const onDataRef = useRef(onData);
  fetcherRef.current = fetcher;
  intervalRef.current = interval;
  onDataRef.current = onData;

  const tick = useCallback(async () => {
    const controller = new AbortController();
    try {
      const data = await fetcherRef.current(controller.signal);
      if (cancelled.current) return;
      errorStreak.current = 0;
      setState({ data, error: undefined, loading: false, connectionUnstable: false });
      onDataRef.current?.(data);
      schedule(data);
    } catch (error) {
      if (cancelled.current || (error instanceof DOMException && error.name === "AbortError")) {
        return;
      }
      errorStreak.current += 1;
      setState((prev) => ({
        ...prev,
        error,
        loading: false,
        connectionUnstable: errorStreak.current >= BACKOFF_THRESHOLD,
      }));
      schedule(undefined, true);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const schedule = useCallback(
    (data: T | undefined, isError = false) => {
      const base = intervalRef.current(data);
      if (base === null) return; // stop polling (terminal)
      let delay = base;
      if (isError && errorStreak.current >= BACKOFF_THRESHOLD) {
        const factor = 2 + (errorStreak.current - BACKOFF_THRESHOLD); // streak 3→2x, 4→3x, 5→4x ...
        delay = Math.min(base * factor, MAX_BACKOFF_MS);
      }
      timer.current = setTimeout(tick, delay);
    },
    [tick],
  );

  const refetch = useCallback(() => {
    if (timer.current) clearTimeout(timer.current);
    void tick();
  }, [tick]);

  useEffect(() => {
    cancelled.current = false;
    if (!enabled) {
      setState((prev) => ({ ...prev, loading: false }));
      return;
    }
    void tick();
    return () => {
      cancelled.current = true;
      if (timer.current) clearTimeout(timer.current);
    };
  }, [enabled, tick]);

  return { ...state, refetch };
}
