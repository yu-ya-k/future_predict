import { act, cleanup, renderHook, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { usePolling } from "./usePolling";

interface PollResult {
  id: string;
  terminal?: boolean;
}

interface Deferred<T> {
  promise: Promise<T>;
  resolve: (value: T) => void;
  reject: (error: unknown) => void;
}

function deferred<T>(): Deferred<T> {
  let resolve!: (value: T) => void;
  let reject!: (error: unknown) => void;
  const promise = new Promise<T>((resolvePromise, rejectPromise) => {
    resolve = resolvePromise;
    reject = rejectPromise;
  });
  return { promise, resolve, reject };
}

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

describe("usePolling", () => {
  it("resets and fetches immediately when the resource key changes after terminal stop", async () => {
    const requests: Array<Deferred<PollResult> & { id: string; signal: AbortSignal }> = [];
    const fetchRun = vi.fn((id: string, signal: AbortSignal) => {
      const request = deferred<PollResult>();
      requests.push({ id, signal, ...request });
      return request.promise;
    });

    const { result, rerender } = renderHook(
      ({ runId }: { runId: string }) =>
        usePolling<PollResult>({
          key: runId,
          fetcher: (signal) => fetchRun(runId, signal),
          interval: (data) => (data?.terminal ? null : 1_000),
        }),
      { initialProps: { runId: "run-a" } },
    );

    await waitFor(() => expect(fetchRun).toHaveBeenCalledTimes(1));

    await act(async () => {
      requests[0].resolve({ id: "run-a", terminal: true });
      await requests[0].promise;
    });

    expect(result.current.data).toEqual({ id: "run-a", terminal: true });
    expect(result.current.loading).toBe(false);

    rerender({ runId: "run-b" });

    await waitFor(() => expect(fetchRun).toHaveBeenCalledTimes(2));
    expect(result.current.data).toBeUndefined();
    expect(result.current.loading).toBe(true);
    expect(requests[1].id).toBe("run-b");

    await act(async () => {
      requests[1].resolve({ id: "run-b" });
      await requests[1].promise;
    });

    expect(result.current.data).toEqual({ id: "run-b" });
    expect(result.current.loading).toBe(false);
  });

  it("aborts and ignores an in-flight request when polling is disabled", async () => {
    const onData = vi.fn();
    const requests: Array<Deferred<PollResult> & { signal: AbortSignal }> = [];
    const fetchRun = vi.fn((signal: AbortSignal) => {
      const request = deferred<PollResult>();
      requests.push({ signal, ...request });
      return request.promise;
    });

    const { result, rerender } = renderHook(
      ({ enabled }: { enabled: boolean }) =>
        usePolling<PollResult>({
          key: "run-a",
          enabled,
          fetcher: fetchRun,
          interval: () => 1_000,
          onData,
        }),
      { initialProps: { enabled: true } },
    );

    await waitFor(() => expect(fetchRun).toHaveBeenCalledTimes(1));

    rerender({ enabled: false });

    await waitFor(() => expect(result.current.loading).toBe(false));
    expect(requests[0].signal.aborted).toBe(true);

    await act(async () => {
      requests[0].resolve({ id: "run-a" });
      await requests[0].promise;
    });

    expect(result.current.data).toBeUndefined();
    expect(onData).not.toHaveBeenCalled();

    act(() => {
      result.current.refetch();
    });
    expect(fetchRun).toHaveBeenCalledTimes(1);
  });

  it("aborts an in-flight request on refetch and ignores the stale result", async () => {
    const onData = vi.fn();
    const requests: Array<Deferred<PollResult> & { signal: AbortSignal }> = [];
    const fetchRun = vi.fn((signal: AbortSignal) => {
      const request = deferred<PollResult>();
      requests.push({ signal, ...request });
      return request.promise;
    });

    const { result } = renderHook(() =>
      usePolling<PollResult>({
        key: "run-a",
        fetcher: fetchRun,
        interval: () => 1_000,
        onData,
      }),
    );

    await waitFor(() => expect(fetchRun).toHaveBeenCalledTimes(1));

    act(() => {
      result.current.refetch();
    });

    await waitFor(() => expect(fetchRun).toHaveBeenCalledTimes(2));
    expect(requests[0].signal.aborted).toBe(true);

    await act(async () => {
      requests[0].resolve({ id: "stale" });
      await requests[0].promise;
    });

    expect(result.current.data).toBeUndefined();
    expect(onData).not.toHaveBeenCalled();

    await act(async () => {
      requests[1].resolve({ id: "fresh" });
      await requests[1].promise;
    });

    expect(result.current.data).toEqual({ id: "fresh" });
    expect(onData).toHaveBeenCalledTimes(1);
    expect(onData).toHaveBeenCalledWith({ id: "fresh" });
  });
});
