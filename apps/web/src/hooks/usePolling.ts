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
  /** Resource or fetcher identity. Changing this resets state and starts a fresh request. */
  key?: unknown;
  /** Called once on each successful fetch (e.g. notifications, store sync). */
  onData?: (data: T) => void;
}

function initialState<T>(loading: boolean): PollingState<T> {
  return {
    data: undefined,
    error: undefined,
    loading,
    connectionUnstable: false,
  };
}

function isAbortError(error: unknown) {
  return error instanceof DOMException && error.name === "AbortError";
}

export function usePolling<T>({
  fetcher,
  interval,
  enabled = true,
  key = null,
  onData,
}: UsePollingOptions<T>): PollingState<T> & { refetch: () => void } {
  const [state, setState] = useState<PollingState<T>>(() => initialState(enabled));

  const errorStreak = useRef(0);
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const controller = useRef<AbortController | null>(null);
  const sessionGeneration = useRef(0);
  const requestGeneration = useRef(0);
  const mounted = useRef(false);
  const previousKey = useRef(key);
  const tickRef = useRef<(sessionId: number) => void>(() => undefined);

  // Keep the latest callbacks without retriggering the polling loop.
  const fetcherRef = useRef(fetcher);
  const intervalRef = useRef(interval);
  const onDataRef = useRef(onData);
  const enabledRef = useRef(enabled);
  fetcherRef.current = fetcher;
  intervalRef.current = interval;
  onDataRef.current = onData;
  enabledRef.current = enabled;

  const clearTimer = useCallback(() => {
    if (timer.current) {
      clearTimeout(timer.current);
      timer.current = null;
    }
  }, []);

  const abortCurrent = useCallback(() => {
    if (controller.current) {
      controller.current.abort();
      controller.current = null;
    }
  }, []);

  const schedule = useCallback(
    (sessionId: number, data: T | undefined, isError = false) => {
      if (sessionId !== sessionGeneration.current || !enabledRef.current) return;

      const base = intervalRef.current(data);
      if (base === null) return; // stop polling (terminal)

      let delay = base;
      if (isError && errorStreak.current >= BACKOFF_THRESHOLD) {
        const factor = 2 + (errorStreak.current - BACKOFF_THRESHOLD); // streak 3→2x, 4→3x, 5→4x ...
        delay = Math.min(base * factor, MAX_BACKOFF_MS);
      }

      clearTimer();
      timer.current = setTimeout(() => tickRef.current(sessionId), delay);
    },
    [clearTimer],
  );

  const tick = useCallback(
    async (sessionId: number) => {
      if (sessionId !== sessionGeneration.current || !enabledRef.current) return;

      clearTimer();
      abortCurrent();
      const generation = ++requestGeneration.current;
      const currentController = new AbortController();
      controller.current = currentController;
      try {
        const data = await fetcherRef.current(currentController.signal);
        if (controller.current === currentController) {
          controller.current = null;
        }
        if (
          sessionId !== sessionGeneration.current ||
          generation !== requestGeneration.current ||
          !enabledRef.current
        ) {
          return;
        }
        errorStreak.current = 0;
        setState({ data, error: undefined, loading: false, connectionUnstable: false });
        onDataRef.current?.(data);
        schedule(sessionId, data);
      } catch (error) {
        if (controller.current === currentController) {
          controller.current = null;
        }
        if (
          sessionId !== sessionGeneration.current ||
          generation !== requestGeneration.current ||
          !enabledRef.current ||
          isAbortError(error)
        ) {
          return;
        }
        errorStreak.current += 1;
        setState((prev) => ({
          ...prev,
          error,
          loading: false,
          connectionUnstable: errorStreak.current >= BACKOFF_THRESHOLD,
        }));
        schedule(sessionId, undefined, true);
      }
    },
    [abortCurrent, clearTimer, schedule],
  );
  tickRef.current = tick;

  const refetch = useCallback(() => {
    if (!enabledRef.current) return;
    clearTimer();
    setState((prev) => ({ ...prev, loading: true }));
    void tick(sessionGeneration.current);
  }, [clearTimer, tick]);

  useEffect(() => {
    const keyChanged = mounted.current && !Object.is(previousKey.current, key);
    mounted.current = true;
    previousKey.current = key;

    sessionGeneration.current += 1;
    requestGeneration.current += 1;
    const sessionId = sessionGeneration.current;
    errorStreak.current = 0;
    clearTimer();
    abortCurrent();

    if (!enabled) {
      setState((prev) => (keyChanged ? initialState(false) : { ...prev, loading: false }));
    } else {
      setState(initialState<T>(true));
      tickRef.current(sessionId);
    }

    return () => {
      if (sessionGeneration.current === sessionId) {
        sessionGeneration.current += 1;
        requestGeneration.current += 1;
      }
      clearTimer();
      abortCurrent();
    };
  }, [abortCurrent, clearTimer, enabled, key]);

  return { ...state, refetch };
}
