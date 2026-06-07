/**
 * App-level integration tests.
 *
 * Covers:
 *  1. App renders the dashboard with empty state when no tracked runs.
 *  2. NewResearch: start button disabled until prompt is entered; enabled after.
 *  3. HumanReview: actions not in allowed_actions are disabled; allowed actions enabled.
 *  4. App shell renders nav links.
 *  5. Settings page renders the default option editor.
 *
 * Strategy:
 *  - Stub VITE_API_BASE_URL via vi.stubEnv.
 *  - Mock fetch with vi.fn() returning resolved promises.
 *  - Stub window.scrollTo to silence jsdom's "not implemented".
 *  - Navigate by setting window.location.hash directly before rendering.
 *  - Use real timers — no fake timers (avoids async/timeout interaction).
 *  - Clean up with cleanup() + localStorage.clear() in afterEach.
 */

import {
  act,
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
  within,
} from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { App } from "./App";
import { Markdown } from "./components";
import type {
  AuditResponse,
  HumanReviewPayload,
  ItemAssessment,
  ResearchAttempt,
  ResearchCheckpoint,
  ResearchRunStatusResponse,
  ReviewRecord,
  RunStatus,
} from "./types";

// ── Global jsdom stubs ────────────────────────────────────────────────────────

// jsdom doesn't implement window.scrollTo
Object.defineProperty(window, "scrollTo", { value: vi.fn(), writable: true });

// Notification API stub
if (!("Notification" in window)) {
  Object.defineProperty(window, "Notification", {
    value: class MockNotification {
      static permission: NotificationPermission = "default";
      static async requestPermission() {
        return "default" as NotificationPermission;
      }
      // eslint-disable-next-line @typescript-eslint/no-unused-vars
      constructor(title: string, options?: NotificationOptions) {}
    },
    writable: true,
  });
}

// ── Helpers ───────────────────────────────────────────────────────────────────

function clearStorage() {
  localStorage.clear();
}

function staleSavedFactoryDefaults() {
  return {
    max_targeted_rerun_runs: 3,
    max_full_rerun_runs: 1,
    max_llm_patch_runs: 3,
    max_verification_runs: 3,
    max_total_iterations: 10,
    max_total_tool_calls: 200,
  };
}

function makeItemAssessment(overrides: Partial<ItemAssessment> = {}): ItemAssessment {
  return {
    item_id: "RI-001",
    status: "partial",
    severity: "major",
    failure_mode: "needs_deeper_search",
    failure_mode_confidence: 82,
    recommended_action: "targeted_rerun",
    evidence_summary: "追加調査が必要です",
    missing_evidence: ["一次情報"],
    rationale: "ResearchItem の根拠が不足しています",
    ...overrides,
  };
}

function makeReviewRecord(overrides: Partial<ReviewRecord> = {}): ReviewRecord {
  return {
    review_no: 1,
    verdict: "pass",
    recommended_route: "pass",
    goal_achieved: true,
    score: 86,
    rationale: "復旧したレビュー履歴です",
    route_rationale: null,
    item_assessments: [makeItemAssessment({ status: "answered", recommended_action: "none" })],
    gaps: [],
    factuality_concerns: [],
    source_quality_concerns: [],
    freshness_concerns: [],
    security_concerns: [],
    next_instructions: null,
    reviewer_confidence: 90,
    high_risk_flags: [],
    public_web_search_used: false,
    reviewer_response_id: "resp_review_1",
    report_hash: "hash-review-1",
    ...overrides,
  };
}

function makeAuditResponse(overrides: Partial<AuditResponse> = {}): AuditResponse {
  return {
    run_id: "run-audit-fixture",
    attempts: [],
    reviews: [],
    llm_calls: [],
    citations: [],
    tool_calls: [],
    cost_events: [],
    human_decisions: [],
    history: [],
    ...overrides,
  };
}

function makeCheckpoint(overrides: Partial<ResearchCheckpoint> = {}): ResearchCheckpoint {
  return {
    checkpoint_id: "chk-research-1",
    run_id: "run-checkpoint-fixture",
    checkpoint_no: 1,
    kind: "deep_research_collected",
    node_anchor: "research-1",
    forkable: true,
    dedupe_key: "deep-research-1",
    source_attempt_no: 1,
    source_review_no: null,
    source_response_id: "resp_research_1",
    report_hash: "report-hash-1",
    snapshot_json: {},
    created_at: "2026-06-06T03:10:00.000Z",
    child_forks: [],
    ...overrides,
  };
}

function readBlobText(blob: Blob): Promise<string> {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onerror = () => reject(reader.error ?? new Error("Failed to read blob"));
    reader.onload = () => resolve(String(reader.result ?? ""));
    reader.readAsText(blob);
  });
}

function jsonResponse(data: unknown, status = 200): Response {
  return {
    ok: status >= 200 && status < 300,
    status,
    json: () => Promise.resolve(data),
  } as Response;
}

function deferred<T>() {
  let resolve!: (value: T | PromiseLike<T>) => void;
  let reject!: (reason?: unknown) => void;
  const promise = new Promise<T>((promiseResolve, promiseReject) => {
    resolve = promiseResolve;
    reject = promiseReject;
  });
  return { promise, resolve, reject };
}

// ── Setup / teardown ──────────────────────────────────────────────────────────

beforeEach(() => {
  vi.stubEnv("VITE_API_BASE_URL", "http://localhost:8000");
  clearStorage();
  window.location.hash = "#/";
});

afterEach(() => {
  vi.useRealTimers();
  vi.unstubAllEnvs();
  vi.restoreAllMocks();
  cleanup();
  clearStorage();
});

// ── Tests ─────────────────────────────────────────────────────────────────────

describe("App shell", () => {
  it("renders nav links for dashboard, new research and settings", () => {
    globalThis.fetch = vi.fn().mockResolvedValue({
      ok: true,
      status: 200,
      json: () => Promise.resolve([]),
    } as Response);

    render(<App />);

    expect(screen.getByRole("link", { name: /ダッシュボード/i })).toBeInTheDocument();
    expect(screen.getByRole("link", { name: /新規リサーチ/i })).toBeInTheDocument();
    expect(screen.getByRole("link", { name: /設定/i })).toBeInTheDocument();
  });

  it("renders skip link pointing to main-content", () => {
    globalThis.fetch = vi.fn().mockResolvedValue({
      ok: true,
      status: 200,
      json: () => Promise.resolve([]),
    } as Response);

    render(<App />);

    const skip = screen.getByText(/メインコンテンツへスキップ/i);
    expect(skip).toBeInTheDocument();
    expect(skip).toHaveAttribute("href", "#main-content");
  });

  it("renders main element with id main-content", () => {
    globalThis.fetch = vi.fn().mockResolvedValue({
      ok: true,
      status: 200,
      json: () => Promise.resolve([]),
    } as Response);

    render(<App />);
    expect(document.getElementById("main-content")).toBeInTheDocument();
  });
});

describe("Markdown", () => {
  it("renders citations as plain text when no click handler is provided", () => {
    render(<Markdown source="根拠があります [1]" />);

    expect(screen.getByText("根拠があります [1]")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "引用 1 へジャンプ" })).not.toBeInTheDocument();
  });
});

describe("Dashboard (SCR-2)", () => {
  it("shows empty state for active runs when localStorage is empty", () => {
    globalThis.fetch = vi.fn().mockResolvedValue({
      ok: true,
      status: 200,
      json: () => Promise.resolve([]),
    } as Response);

    render(<App />);

    expect(screen.getByText(/進行中のrunなし/i)).toBeInTheDocument();
  });

  it("fetches the human-review queue without a reviewer id", async () => {
    const fetchMock = vi.fn().mockResolvedValue({
      ok: true,
      status: 200,
      json: () => Promise.resolve([]),
    } as Response);
    globalThis.fetch = fetchMock;

    render(<App />);

    expect(await screen.findByText("要対応なし")).toBeInTheDocument();
    expect(fetchMock).toHaveBeenCalledWith(
      "http://localhost:8000/research-runs/human-reviews",
      expect.objectContaining({
        headers: {},
      }),
    );
    expect(screen.queryByText(/レビュアーID/i)).not.toBeInTheDocument();
  });

  it("shows a retryable error instead of an empty queue when human-review fetch fails", async () => {
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(jsonResponse({ detail: "Service unavailable" }, 503))
      .mockResolvedValue(jsonResponse([]));
    globalThis.fetch = fetchMock;

    render(<App />);

    expect(await screen.findByRole("alert")).toHaveTextContent(
      "要対応の取得に失敗しました",
    );
    expect(screen.queryByText("要対応なし")).not.toBeInTheDocument();

    await userEvent.click(screen.getByRole("button", { name: "再試行" }));

    expect(await screen.findByText("要対応なし")).toBeInTheDocument();
    expect(fetchMock).toHaveBeenCalledTimes(2);
  });

  it("ignores stale reviewer id storage and does not render user identity UI", async () => {
    localStorage.setItem("dro.reviewerId", "Yuya");
    globalThis.fetch = vi.fn().mockResolvedValue({
      ok: true,
      status: 200,
      json: () => Promise.resolve([]),
    } as Response);

    render(<App />);

    expect(await screen.findByText("要対応なし")).toBeInTheDocument();
    expect(screen.queryByText("Yuya")).not.toBeInTheDocument();
    expect(screen.queryByText(/レビュアーID/i)).not.toBeInTheDocument();
  });

  it("drops malformed tracked-run entries without crashing the dashboard", async () => {
    localStorage.setItem(
      "dro.trackedRuns",
      JSON.stringify([
        null,
        { run_id: "missing-title", created_at: new Date().toISOString() },
        {
          run_id: "bad-status",
          title: "壊れたstatus",
          created_at: new Date().toISOString(),
          last_status: "not-a-real-status",
        },
        {
          run_id: "valid-terminal",
          title: "正常なリサーチ",
          max_total_iterations: 5,
          created_at: new Date().toISOString(),
          last_status: "completed",
        },
      ]),
    );
    globalThis.fetch = vi.fn().mockResolvedValue(jsonResponse([]));

    render(<App />);

    expect(await screen.findByText("正常なリサーチ")).toBeInTheDocument();
    expect(screen.queryByText("壊れたstatus")).not.toBeInTheDocument();
  });

  it("renders the 要対応 section heading", () => {
    globalThis.fetch = vi.fn().mockResolvedValue({
      ok: true,
      status: 200,
      json: () => Promise.resolve([]),
    } as Response);

    render(<App />);

    expect(screen.getByText("要対応")).toBeInTheDocument();
  });

  it("uses a compact empty state for the human-review queue", async () => {
    globalThis.fetch = vi.fn().mockResolvedValue({
      ok: true,
      status: 200,
      json: () => Promise.resolve([]),
    } as Response);

    render(<App />);

    expect(await screen.findByText("要対応なし")).toBeInTheDocument();
    expect(screen.getByText("レビュー待ちはありません。")).toBeInTheDocument();
    expect(
      screen.queryByText("現在、人間によるレビューが必要なrunはありません。"),
    ).not.toBeInTheDocument();
  });

  it("removes a tracked run from the dashboard", async () => {
    const confirmSpy = vi.spyOn(window, "confirm").mockReturnValue(true);
    localStorage.setItem(
      "dro.trackedRuns",
      JSON.stringify([
        {
          run_id: "run-remove-test",
          title: "削除対象のリサーチ",
          max_total_iterations: 5,
          created_at: new Date().toISOString(),
          last_status: "waiting_deep_research",
        },
      ]),
    );
    globalThis.fetch = vi.fn().mockImplementation((input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      if (url.endsWith("/research-runs/human-reviews")) {
        return Promise.resolve({
          ok: true,
          status: 200,
          json: () => Promise.resolve([]),
        } as Response);
      }
      if (url.endsWith("/research-runs/run-remove-test") && init?.method === "DELETE") {
        return Promise.resolve({
          ok: true,
          status: 204,
        } as Response);
      }
      return Promise.resolve({
        ok: true,
        status: 200,
        json: () =>
          Promise.resolve({
            run_id: "run-remove-test",
            status: "waiting_deep_research",
            done_reason: null,
            needs_human_review: false,
            progress: {
              targeted_rerun_runs: 1,
              llm_patch_runs: 0,
              total_reviews: 0,
              latest_verdict: null,
              latest_score: null,
              total_tool_calls: 0,
              estimated_cost_usd: 0,
            },
          }),
      } as Response);
    });

    render(<App />);

    expect(screen.getByText("削除対象のリサーチ")).toBeInTheDocument();

    await userEvent.click(
      screen.getByRole("button", {
        name: "停止して削除: 削除対象のリサーチ",
      }),
    );

    expect(confirmSpy).toHaveBeenCalledWith(expect.stringContaining("run-remove-test"));
    await waitFor(() =>
      expect(screen.queryByText("削除対象のリサーチ")).not.toBeInTheDocument(),
    );
    expect(globalThis.fetch).toHaveBeenCalledWith(
      "http://localhost:8000/research-runs/run-remove-test",
      expect.objectContaining({ method: "DELETE" }),
    );
    expect(JSON.parse(localStorage.getItem("dro.trackedRuns") ?? "[]")).toEqual([]);
  });

  it("removes a tracked run locally when backend delete returns 404", async () => {
    const confirmSpy = vi.spyOn(window, "confirm").mockReturnValue(true);
    localStorage.setItem(
      "dro.trackedRuns",
      JSON.stringify([
        {
          run_id: "run-already-gone",
          title: "削除済みのリサーチ",
          max_total_iterations: 5,
          created_at: new Date().toISOString(),
          last_status: "completed",
        },
      ]),
    );
    globalThis.fetch = vi.fn().mockImplementation((input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      if (url.endsWith("/research-runs/human-reviews")) {
        return Promise.resolve({
          ok: true,
          status: 200,
          json: () => Promise.resolve([]),
        } as Response);
      }
      if (url.endsWith("/research-runs/run-already-gone") && init?.method === "DELETE") {
        return Promise.resolve({
          ok: false,
          status: 404,
          json: () => Promise.resolve({ detail: "Run not found." }),
        } as Response);
      }
      return Promise.resolve({
        ok: true,
        status: 200,
        json: () => Promise.resolve({}),
      } as Response);
    });

    render(<App />);

    expect(screen.getByText("削除済みのリサーチ")).toBeInTheDocument();

    await userEvent.click(
      screen.getByRole("button", {
        name: "削除: 削除済みのリサーチ",
      }),
    );

    expect(confirmSpy).toHaveBeenCalledWith(expect.stringContaining("run-already-gone"));
    await waitFor(() =>
      expect(screen.queryByText("削除済みのリサーチ")).not.toBeInTheDocument(),
    );
    expect(JSON.parse(localStorage.getItem("dro.trackedRuns") ?? "[]")).toEqual([]);
  });

  it("does not delete an active tracked run when confirmation is cancelled", async () => {
    vi.spyOn(window, "confirm").mockReturnValue(false);
    localStorage.setItem(
      "dro.trackedRuns",
      JSON.stringify([
        {
          run_id: "run-cancel-active-delete",
          title: "削除キャンセル対象",
          max_total_iterations: 5,
          created_at: new Date().toISOString(),
          last_status: "waiting_deep_research",
        },
      ]),
    );
    const fetchMock = vi.fn().mockImplementation((input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      if (url.endsWith("/research-runs/human-reviews")) {
        return Promise.resolve({
          ok: true,
          status: 200,
          json: () => Promise.resolve([]),
        } as Response);
      }
      if (url.endsWith("/research-runs/run-cancel-active-delete") && init?.method !== "DELETE") {
        return Promise.resolve({
          ok: true,
          status: 200,
          json: () =>
            Promise.resolve({
              run_id: "run-cancel-active-delete",
              status: "waiting_deep_research",
              done_reason: null,
              needs_human_review: false,
              progress: {
                targeted_rerun_runs: 1,
                llm_patch_runs: 0,
                total_reviews: 0,
                latest_verdict: null,
                latest_score: null,
                total_tool_calls: 0,
                estimated_cost_usd: 0,
              },
            }),
        } as Response);
      }
      return Promise.resolve({
        ok: true,
        status: 204,
        json: () => Promise.resolve({}),
      } as Response);
    });
    globalThis.fetch = fetchMock;

    render(<App />);

    await userEvent.click(
      screen.getByRole("button", {
        name: "停止して削除: 削除キャンセル対象",
      }),
    );

    expect(screen.getByText("削除キャンセル対象")).toBeInTheDocument();
    expect(fetchMock).not.toHaveBeenCalledWith(
      "http://localhost:8000/research-runs/run-cancel-active-delete",
      expect.objectContaining({ method: "DELETE" }),
    );
    expect(JSON.parse(localStorage.getItem("dro.trackedRuns") ?? "[]")).toHaveLength(1);
  });

  it("does not delete a terminal tracked run when confirmation is cancelled", async () => {
    vi.spyOn(window, "confirm").mockReturnValue(false);
    localStorage.setItem(
      "dro.trackedRuns",
      JSON.stringify([
        {
          run_id: "run-cancel-terminal-delete",
          title: "完了run削除キャンセル対象",
          max_total_iterations: 5,
          created_at: new Date().toISOString(),
          last_status: "completed",
        },
      ]),
    );
    const fetchMock = vi.fn().mockResolvedValue({
      ok: true,
      status: 200,
      json: () => Promise.resolve([]),
    } as Response);
    globalThis.fetch = fetchMock;

    render(<App />);

    await userEvent.click(
      screen.getByRole("button", {
        name: "削除: 完了run削除キャンセル対象",
      }),
    );

    expect(screen.getByText("完了run削除キャンセル対象")).toBeInTheDocument();
    expect(fetchMock).not.toHaveBeenCalledWith(
      "http://localhost:8000/research-runs/run-cancel-terminal-delete",
      expect.objectContaining({ method: "DELETE" }),
    );
    expect(JSON.parse(localStorage.getItem("dro.trackedRuns") ?? "[]")).toHaveLength(1);
  });

  it("shows a visible error when tracked run deletion fails", async () => {
    vi.spyOn(window, "confirm").mockReturnValue(true);
    localStorage.setItem(
      "dro.trackedRuns",
      JSON.stringify([
        {
          run_id: "run-delete-fails",
          title: "削除失敗対象",
          max_total_iterations: 5,
          created_at: new Date().toISOString(),
          last_status: "completed",
        },
      ]),
    );
    globalThis.fetch = vi.fn().mockImplementation((input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      if (url.endsWith("/research-runs/human-reviews")) {
        return Promise.resolve({
          ok: true,
          status: 200,
          json: () => Promise.resolve([]),
        } as Response);
      }
      if (url.endsWith("/research-runs/run-delete-fails") && init?.method === "DELETE") {
        return Promise.resolve({
          ok: false,
          status: 500,
          json: () => Promise.resolve({ detail: "Delete service unavailable" }),
        } as Response);
      }
      return Promise.resolve({
        ok: true,
        status: 200,
        json: () => Promise.resolve([]),
      } as Response);
    });

    render(<App />);

    await userEvent.click(
      screen.getByRole("button", {
        name: "削除: 削除失敗対象",
      }),
    );

    expect(await screen.findByRole("alert")).toHaveTextContent(
      "run run-delete-fails の削除に失敗しました。Delete service unavailable",
    );
    expect(screen.getByText("削除失敗対象")).toBeInTheDocument();
    expect(JSON.parse(localStorage.getItem("dro.trackedRuns") ?? "[]")).toHaveLength(1);
  });

  it("removes a stale tracked run when dashboard polling returns 404", async () => {
    localStorage.setItem(
      "dro.trackedRuns",
      JSON.stringify([
        {
          run_id: "run-stale-poll",
          title: "存在しないリサーチ",
          max_total_iterations: 5,
          created_at: new Date().toISOString(),
          last_status: "waiting_deep_research",
        },
      ]),
    );
    globalThis.fetch = vi.fn().mockImplementation((input: RequestInfo | URL) => {
      const url = String(input);
      if (url.endsWith("/research-runs/human-reviews")) {
        return Promise.resolve({
          ok: true,
          status: 200,
          json: () => Promise.resolve([]),
        } as Response);
      }
      if (url.endsWith("/research-runs/run-stale-poll")) {
        return Promise.resolve({
          ok: false,
          status: 404,
          json: () => Promise.resolve({ detail: "Run not found." }),
        } as Response);
      }
      return Promise.resolve({
        ok: true,
        status: 200,
        json: () => Promise.resolve({}),
      } as Response);
    });

    render(<App />);

    expect(screen.getByText("存在しないリサーチ")).toBeInTheDocument();

    await waitFor(() =>
      expect(screen.queryByText("存在しないリサーチ")).not.toBeInTheDocument(),
    );
    expect(JSON.parse(localStorage.getItem("dro.trackedRuns") ?? "[]")).toEqual([]);
  });

  it("does not duplicate a queued human-review run in the active tracked list", async () => {
    const runId = "queued-review-run";
    localStorage.setItem(
      "dro.trackedRuns",
      JSON.stringify([
        {
          run_id: runId,
          title: "要対応と重複するリサーチ",
          max_total_iterations: 5,
          created_at: new Date().toISOString(),
          last_status: "needs_human_review",
        },
      ]),
    );
    globalThis.fetch = vi.fn().mockImplementation((input: RequestInfo | URL) => {
      const url = String(input);
      if (url.endsWith("/research-runs/human-reviews")) {
        return Promise.resolve({
          ok: true,
          status: 200,
          json: () =>
            Promise.resolve([
              {
                run_id: runId,
                status: "needs_human_review",
                done_reason: "deep_research_timeout",
                latest_verdict: "human_review",
                latest_score: 72,
                latest_rationale: "判断が必要です",
                audit_summary: {
                  targeted_rerun_runs: 1,
                  llm_patch_runs: 0,
                  total_reviews: 1,
                  no_progress_count: 0,
                  total_tool_calls: 0,
                  estimated_cost_usd: 0,
                },
                created_at: new Date().toISOString(),
                updated_at: new Date().toISOString(),
              },
            ]),
        } as Response);
      }
      if (url.endsWith(`/research-runs/${runId}`)) {
        return Promise.resolve({
          ok: true,
          status: 200,
          json: () =>
            Promise.resolve({
              run_id: runId,
              status: "needs_human_review",
              done_reason: "deep_research_timeout",
              needs_human_review: true,
              progress: {
                targeted_rerun_runs: 1,
                llm_patch_runs: 0,
                total_reviews: 1,
                latest_verdict: "human_review",
                latest_score: 72,
                total_tool_calls: 0,
                estimated_cost_usd: 0,
              },
            }),
        } as Response);
      }
      return Promise.resolve({
        ok: true,
        status: 200,
        json: () => Promise.resolve({}),
      } as Response);
    });

    render(<App />);

    await waitFor(() => expect(globalThis.fetch).toHaveBeenCalled());
    expect(await screen.findByText(runId)).toBeInTheDocument();

    const activeSection = screen.getByRole("heading", { name: "進行中" }).closest("section");
    expect(activeSection).not.toBeNull();
    expect(
      within(activeSection as HTMLElement).queryByText("要対応と重複するリサーチ"),
    ).not.toBeInTheDocument();
    expect(within(activeSection as HTMLElement).getByText("進行中のrunなし")).toBeInTheDocument();
  });

  it("deletes a human-review queue item from the backend queue", async () => {
    const confirmSpy = vi.spyOn(window, "confirm").mockReturnValue(true);
    const runId = "queue-delete-run";
    globalThis.fetch = vi.fn().mockImplementation((input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      if (url.endsWith(`/research-runs/${runId}`) && init?.method === "DELETE") {
        return Promise.resolve({
          ok: true,
          status: 204,
        } as Response);
      }
      if (url.endsWith("/research-runs/human-reviews")) {
        return Promise.resolve({
          ok: true,
          status: 200,
          json: () =>
            Promise.resolve([
              {
                run_id: runId,
                status: "needs_human_review",
                done_reason: "deep_research_timeout",
                latest_verdict: "human_review",
                latest_score: 72,
                latest_rationale: "判断が必要です",
                audit_summary: {
                  targeted_rerun_runs: 1,
                  llm_patch_runs: 0,
                  total_reviews: 1,
                  no_progress_count: 0,
                  total_tool_calls: 0,
                  estimated_cost_usd: 0,
                },
                created_at: new Date().toISOString(),
                updated_at: new Date().toISOString(),
              },
            ]),
        } as Response);
      }
      return Promise.resolve({
        ok: true,
        status: 200,
        json: () => Promise.resolve({}),
      } as Response);
    });

    render(<App />);

    expect(await screen.findByText(runId)).toBeInTheDocument();

    await userEvent.click(
      screen.getByRole("button", {
        name: `削除: ${runId}`,
      }),
    );

    expect(confirmSpy).toHaveBeenCalledWith(expect.stringContaining(runId));
    await waitFor(() => expect(screen.queryByText(runId)).not.toBeInTheDocument());
    expect(globalThis.fetch).toHaveBeenCalledWith(
      `http://localhost:8000/research-runs/${runId}`,
      expect.objectContaining({ method: "DELETE" }),
    );
  });
});

describe("NewResearch (SCR-1)", () => {
  beforeEach(() => {
    window.location.hash = "#/new";
  });

  it("start button is disabled when prompt is empty", () => {
    render(<App />);

    const button = screen.getByRole("button", { name: /リサーチを開始/i });
    expect(button).toBeDisabled();
  });

  it("returns to the dashboard from the new research screen", async () => {
    render(<App />);

    await userEvent.click(
      screen.getByRole("link", { name: "ダッシュボードへ戻る" }),
    );

    expect(window.location.hash).toBe("#/");
  });

  it("start button becomes enabled after entering a prompt", async () => {
    render(<App />);

    const textarea = screen.getByRole("textbox", { name: /リサーチ内容/i });
    await userEvent.type(textarea, "日本のAI政策について調査してください");

    const button = screen.getByRole("button", { name: /リサーチを開始/i });
    expect(button).not.toBeDisabled();
  });

  it("shows character counter", () => {
    render(<App />);

    const counter = screen.getByText(/残り.*文字/i);
    expect(counter).toBeInTheDocument();
  });

  it("renders the prompt and active guardrail controls", async () => {
    render(<App />);

    expect(screen.getByRole("textbox", { name: /リサーチ内容/i })).toBeInTheDocument();

    await userEvent.click(screen.getByRole("button", { name: /詳細オプション/i }));
    const advancedOptions = screen.getByRole("group", { name: /詳細オプション/i });
    expect(within(advancedOptions).getAllByRole("spinbutton")).toHaveLength(6);
  });

  it("submits a run and navigates to monitor on success", async () => {
    const runId = "run-test-abc-123";
    globalThis.fetch = vi.fn().mockResolvedValue({
      ok: true,
      status: 202,
      json: () =>
        Promise.resolve({
          run_id: runId,
          thread_id: "thread-1",
          status: "queued",
          created_at: new Date().toISOString(),
        }),
    } as Response);

    render(<App />);

    const textarea = screen.getByRole("textbox", { name: /リサーチ内容/i });
    await userEvent.type(textarea, "テスト用プロンプト");

    const button = screen.getByRole("button", { name: /リサーチを開始/i });
    await userEvent.click(button);

    await waitFor(() => {
      expect(window.location.hash).toContain(runId);
    });
  });

  it("sends the saved Research API key when submitting a run", async () => {
    localStorage.setItem("dro.researchApiKey", "browser-secret");
    const fetchMock = vi.fn().mockResolvedValue({
      ok: true,
      status: 202,
      json: () =>
        Promise.resolve({
          run_id: "run-api-key-test",
          thread_id: "thread-1",
          status: "queued",
          created_at: new Date().toISOString(),
        }),
    } as Response);
    globalThis.fetch = fetchMock;

    render(<App />);

    await userEvent.type(screen.getByRole("textbox", { name: /リサーチ内容/i }), "テスト");
    await userEvent.click(screen.getByRole("button", { name: /リサーチを開始/i }));

    await waitFor(() => {
      expect(fetchMock).toHaveBeenCalled();
    });
    const init = fetchMock.mock.calls[0][1] as RequestInit;

    expect(init.headers).toMatchObject({
      "Content-Type": "application/json",
      "X-API-Key": "browser-secret",
    });
  });

  it("submits only active API-aligned factory default guardrails", async () => {
    const fetchMock = vi.fn().mockResolvedValue({
      ok: true,
      status: 202,
      json: () =>
        Promise.resolve({
          run_id: "run-defaults-test",
          thread_id: "thread-1",
          status: "queued",
          created_at: new Date().toISOString(),
        }),
    } as Response);
    globalThis.fetch = fetchMock;

    render(<App />);

    const textarea = screen.getByRole("textbox", { name: /リサーチ内容/i });
    await userEvent.type(textarea, "テスト用プロンプト");
    await userEvent.click(screen.getByRole("button", { name: /リサーチを開始/i }));

    await waitFor(() => {
      expect(fetchMock).toHaveBeenCalled();
    });
    const init = fetchMock.mock.calls[0][1] as RequestInit;
    const body = JSON.parse(String(init.body)) as {
      user_prompt: string;
      options: Record<string, unknown>;
    };

    expect(Object.keys(body).sort()).toEqual(["options", "user_prompt"]);
    expect(body.user_prompt).toBe("テスト用プロンプト");
    expect(body.options).toEqual({
      max_targeted_rerun_runs: 2,
      max_full_rerun_runs: 1,
      max_llm_patch_runs: 3,
      max_verification_runs: 3,
      max_total_iterations: 5,
      max_total_tool_calls: 120,
    });
  });

  it("normalizes stale saved factory defaults before submitting", async () => {
    localStorage.setItem("dro.defaults", JSON.stringify(staleSavedFactoryDefaults()));
    const fetchMock = vi.fn().mockResolvedValue({
      ok: true,
      status: 202,
      json: () =>
        Promise.resolve({
          run_id: "run-stale-defaults-test",
          thread_id: "thread-1",
          status: "queued",
          created_at: new Date().toISOString(),
        }),
    } as Response);
    globalThis.fetch = fetchMock;

    render(<App />);

    await userEvent.type(screen.getByRole("textbox", { name: /リサーチ内容/i }), "テスト");
    await userEvent.click(screen.getByRole("button", { name: /リサーチを開始/i }));

    await waitFor(() => {
      expect(fetchMock).toHaveBeenCalled();
    });
    const init = fetchMock.mock.calls[0][1] as RequestInit;
    const body = JSON.parse(String(init.body)) as {
      options: Record<string, unknown>;
    };

    expect(body.options.max_targeted_rerun_runs).toBe(2);
    expect(body.options.max_total_iterations).toBe(5);
    expect(body.options.max_total_tool_calls).toBe(120);
  });

  it("sanitizes malformed and out-of-range saved defaults before submitting", async () => {
    localStorage.setItem(
      "dro.defaults",
      JSON.stringify({
        max_targeted_rerun_runs: 999,
        max_full_rerun_runs: "bad",
        max_llm_patch_runs: -4,
        max_verification_runs: "2.8",
        max_total_iterations: 0,
        max_total_tool_calls: null,
      }),
    );
    const fetchMock = vi.fn().mockResolvedValue({
      ok: true,
      status: 202,
      json: () =>
        Promise.resolve({
          run_id: "run-sanitized-defaults-test",
          thread_id: "thread-1",
          status: "queued",
          created_at: new Date().toISOString(),
        }),
    } as Response);
    globalThis.fetch = fetchMock;

    render(<App />);

    await userEvent.type(screen.getByRole("textbox", { name: /リサーチ内容/i }), "テスト");
    await userEvent.click(screen.getByRole("button", { name: /リサーチを開始/i }));

    await waitFor(() => {
      expect(fetchMock).toHaveBeenCalled();
    });
    const init = fetchMock.mock.calls[0][1] as RequestInit;
    const body = JSON.parse(String(init.body)) as {
      options: Record<string, unknown>;
    };

    expect(body.options).toEqual({
      max_targeted_rerun_runs: 5,
      max_full_rerun_runs: 1,
      max_llm_patch_runs: 0,
      max_verification_runs: 2,
      max_total_iterations: 1,
      max_total_tool_calls: 120,
    });
  });

  it("shows error when API fails", async () => {
    globalThis.fetch = vi.fn().mockResolvedValue({
      ok: false,
      status: 500,
      json: () => Promise.resolve({ detail: "Internal server error" }),
    } as Response);

    render(<App />);

    const textarea = screen.getByRole("textbox", { name: /リサーチ内容/i });
    await userEvent.type(textarea, "テスト用プロンプト");

    const button = screen.getByRole("button", { name: /リサーチを開始/i });
    await userEvent.click(button);

    await waitFor(() => {
      expect(screen.getByRole("alert")).toBeInTheDocument();
    });
  });

  it("submits manual import as FormData without setting Content-Type and tracks the monitor run", async () => {
    localStorage.setItem("dro.researchApiKey", "browser-secret");
    const runId = "run-manual-import-test";
    const createdAt = new Date().toISOString();
    const fetchMock = vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
      void init;
      const url = String(input);
      if (url.endsWith("/research-runs/manual-import")) {
        return Promise.resolve({
          ok: true,
          status: 202,
          json: () =>
            Promise.resolve({
              run_id: runId,
              thread_id: "thread-1",
              status: "reviewing",
              created_at: createdAt,
            }),
        } as Response);
      }
      if (url.endsWith(`/research-runs/${runId}/audit`)) {
        return Promise.resolve({
          ok: true,
          status: 200,
          json: () => Promise.resolve(makeAuditResponse({ run_id: runId })),
        } as Response);
      }
      if (url.endsWith(`/research-runs/${runId}/attempts`)) {
        return Promise.resolve({
          ok: true,
          status: 200,
          json: () => Promise.resolve([]),
        } as Response);
      }
      if (url.endsWith(`/research-runs/${runId}/items`)) {
        return Promise.resolve({
          ok: true,
          status: 200,
          json: () => Promise.resolve({ run_id: runId, items: [] }),
        } as Response);
      }
      if (url.endsWith(`/research-runs/${runId}/checkpoints?include_forks=true`)) {
        return Promise.resolve({
          ok: true,
          status: 200,
          json: () => Promise.resolve({ run_id: runId, checkpoints: [] }),
        } as Response);
      }
      if (url.endsWith(`/research-runs/${runId}/lineage`)) {
        return Promise.resolve({
          ok: true,
          status: 200,
          json: () => Promise.resolve({ run_id: runId, lineage: null }),
        } as Response);
      }
      return Promise.resolve({
        ok: true,
        status: 200,
        json: () =>
          Promise.resolve({
            run_id: runId,
            status: "reviewing",
            done_reason: null,
            needs_human_review: false,
            progress: null,
          }),
      } as Response);
    });
    globalThis.fetch = fetchMock;

    render(<App />);

    await userEvent.click(
      screen.getByRole("radio", { name: "ChatGPT結果を取り込み" }),
    );
    await userEvent.type(
      screen.getByRole("textbox", { name: "入力プロンプト" }),
      "手動実行したプロンプト",
    );
    await userEvent.type(
      screen.getByRole("textbox", { name: "出力レポート" }),
      "手動実行したレポート",
    );
    await userEvent.click(
      screen.getByRole("button", { name: /取り込んでレビューを開始/i }),
    );

    await waitFor(() => expect(fetchMock).toHaveBeenCalled());
    const manualImportCall = fetchMock.mock.calls.find(([input]) =>
      String(input).endsWith("/research-runs/manual-import"),
    );
    expect(manualImportCall).toBeDefined();
    const init = manualImportCall?.[1] as RequestInit;
    const headers = init.headers as Record<string, string>;
    const body = init.body as FormData;

    expect(manualImportCall?.[0]).toBe(
      "http://localhost:8000/research-runs/manual-import",
    );
    expect(headers).toEqual({ "X-API-Key": "browser-secret" });
    expect(body).toBeInstanceOf(FormData);
    expect(body.get("input_prompt_text")).toBe("手動実行したプロンプト");
    expect(body.get("report_text")).toBe("手動実行したレポート");
    expect(body.get("allow_remote_review")).toBe("true");
    expect(body.get("allow_api_reruns")).toBe("true");
    expect(body.has("input_prompt_file")).toBe(false);
    expect(body.has("report_file")).toBe(false);
    await waitFor(() => expect(window.location.hash).toBe(`#/runs/${runId}`));
    const trackedRuns = JSON.parse(localStorage.getItem("dro.trackedRuns") ?? "[]") as Array<{
      run_id: string;
      last_status: string;
    }>;
    expect(trackedRuns).toEqual(
      expect.arrayContaining([
        expect.objectContaining({ run_id: runId, last_status: "reviewing" }),
      ]),
    );
  });

  it("always enables remote review and API rerun permissions for manual imports", async () => {
    render(<App />);

    await userEvent.click(
      screen.getByRole("radio", { name: "ChatGPT結果を取り込み" }),
    );

    expect(
      screen.getByRole("button", { name: /取り込んでレビューを開始/i }),
    ).toBeInTheDocument();
    expect(screen.queryByText("実行許可")).not.toBeInTheDocument();
    expect(screen.queryByLabelText("LLMレビューを許可する")).not.toBeInTheDocument();
    expect(screen.queryByLabelText("API rerunを許可する")).not.toBeInTheDocument();
  });

  it("shows manual required field errors only after touch or submit and clears them on source switch", async () => {
    render(<App />);

    await userEvent.click(
      screen.getByRole("radio", { name: "ChatGPT結果を取り込み" }),
    );

    expect(screen.queryByText("入力してください")).not.toBeInTheDocument();

    await userEvent.click(
      screen.getByRole("button", { name: /取り込んでレビューを開始/i }),
    );

    expect(screen.getAllByText("入力してください")).toHaveLength(2);

    const promptSection = screen.getByText("入力プロンプト").closest("section");
    expect(promptSection).not.toBeNull();
    await userEvent.click(
      within(promptSection as HTMLElement).getByRole("radio", { name: "ファイル" }),
    );

    expect(
      within(promptSection as HTMLElement).queryByText("ファイルを選択してください"),
    ).not.toBeInTheDocument();
  });

  it("keeps manual rerun controls enabled and sends rerun limits", async () => {
    const fetchMock = vi.fn().mockResolvedValue({
      ok: true,
      status: 202,
      json: () =>
        Promise.resolve({
          run_id: "run-manual-rerun-controls-test",
          thread_id: "thread-1",
          status: "needs_human_review",
          created_at: new Date().toISOString(),
        }),
    } as Response);
    globalThis.fetch = fetchMock;

    render(<App />);

    await userEvent.click(
      screen.getByRole("radio", { name: "ChatGPT結果を取り込み" }),
    );
    await userEvent.click(screen.getByRole("button", { name: /詳細オプション/i }));

    const targetedInput = screen.getByLabelText(/最大Targeted rerun回数/i);
    const fullInput = screen.getByLabelText(/最大Full rerun回数/i);
    expect(targetedInput).not.toBeDisabled();
    expect(fullInput).not.toBeDisabled();
    expect(
      screen.queryByText(/API rerun未許可のため、Targeted rerun \/ Full rerun は0回として送信されます。/),
    ).not.toBeInTheDocument();

    await userEvent.type(
      screen.getByRole("textbox", { name: "入力プロンプト" }),
      "手動プロンプト",
    );
    await userEvent.type(
      screen.getByRole("textbox", { name: "出力レポート" }),
      "手動レポート",
    );
    await userEvent.click(
      screen.getByRole("button", { name: /取り込んでレビューを開始/i }),
    );

    await waitFor(() => expect(fetchMock).toHaveBeenCalled());
    const body = fetchMock.mock.calls[0][1]?.body as FormData;
    const options = JSON.parse(String(body.get("options_json"))) as Record<string, number>;
    expect(body.get("allow_remote_review")).toBe("true");
    expect(body.get("allow_api_reruns")).toBe("true");
    expect(options.max_targeted_rerun_runs).toBe(2);
    expect(options.max_full_rerun_runs).toBe(1);
  });

  it("sends manual ChatGPT rerun mode without enabling API reruns", async () => {
    const fetchMock = vi.fn().mockResolvedValue({
      ok: true,
      status: 202,
      json: () =>
        Promise.resolve({
          run_id: "run-manual-chatgpt-rerun-test",
          thread_id: "thread-1",
          status: "needs_human_review",
          created_at: new Date().toISOString(),
        }),
    } as Response);
    globalThis.fetch = fetchMock;

    render(<App />);

    await userEvent.click(
      screen.getByRole("radio", { name: "ChatGPT結果を取り込み" }),
    );
    await userEvent.click(screen.getByRole("radio", { name: "ChatGPT手動" }));
    await userEvent.type(
      screen.getByRole("textbox", { name: "入力プロンプト" }),
      "手動プロンプト",
    );
    await userEvent.type(
      screen.getByRole("textbox", { name: "出力レポート" }),
      "手動レポート",
    );
    await userEvent.click(
      screen.getByRole("button", { name: /取り込んでレビューを開始/i }),
    );

    await waitFor(() => expect(fetchMock).toHaveBeenCalled());
    const body = fetchMock.mock.calls[0][1]?.body as FormData;
    expect(body.get("allow_api_reruns")).toBe("false");
    expect(body.get("rerun_execution_mode")).toBe("manual_chatgpt");
  });

  it("omits inactive manual text and sends the selected file source", async () => {
    const fetchMock = vi.fn().mockResolvedValue({
      ok: true,
      status: 202,
      json: () =>
        Promise.resolve({
          run_id: "run-manual-file-test",
          thread_id: "thread-1",
          status: "needs_human_review",
          created_at: new Date().toISOString(),
        }),
    } as Response);
    globalThis.fetch = fetchMock;

    render(<App />);

    await userEvent.click(
      screen.getByRole("radio", { name: "ChatGPT結果を取り込み" }),
    );
    await userEvent.type(
      screen.getByRole("textbox", { name: "入力プロンプト" }),
      "送られない古いテキスト",
    );
    const promptSection = screen.getByText("入力プロンプト").closest("section");
    expect(promptSection).not.toBeNull();
    await userEvent.click(
      within(promptSection as HTMLElement).getByRole("radio", { name: "ファイル" }),
    );
    await userEvent.upload(
      screen.getByLabelText("入力プロンプトファイル"),
      new File(["file prompt"], "prompt.md", { type: "text/markdown" }),
    );
    await userEvent.type(
      screen.getByRole("textbox", { name: "出力レポート" }),
      "手動レポート",
    );
    await userEvent.click(
      screen.getByRole("button", { name: /取り込んでレビューを開始/i }),
    );

    await waitFor(() => expect(fetchMock).toHaveBeenCalled());
    const body = fetchMock.mock.calls[0][1]?.body as FormData;

    expect(body.has("input_prompt_text")).toBe(false);
    expect(body.get("input_prompt_file")).toBeInstanceOf(File);
    expect((body.get("input_prompt_file") as File).name).toBe("prompt.md");
    expect(body.get("report_text")).toBe("手動レポート");
    expect(body.has("report_file")).toBe(false);
    const options = JSON.parse(String(body.get("options_json"))) as Record<string, number>;
    expect(body.get("allow_remote_review")).toBe("true");
    expect(body.get("allow_api_reruns")).toBe("true");
    expect(options.max_targeted_rerun_runs).toBe(2);
    expect(options.max_full_rerun_runs).toBe(1);
  });

  it("blocks oversized manual files before submit", async () => {
    const fetchMock = vi.fn();
    globalThis.fetch = fetchMock;

    render(<App />);

    await userEvent.click(
      screen.getByRole("radio", { name: "ChatGPT結果を取り込み" }),
    );
    const promptSection = screen.getByText("入力プロンプト").closest("section");
    expect(promptSection).not.toBeNull();
    await userEvent.click(
      within(promptSection as HTMLElement).getByRole("radio", { name: "ファイル" }),
    );
    await userEvent.upload(
      screen.getByLabelText("入力プロンプトファイル"),
      new File([new Uint8Array(1_048_577)], "too-large.md", {
        type: "text/markdown",
      }),
    );
    await userEvent.type(
      screen.getByRole("textbox", { name: "出力レポート" }),
      "手動レポート",
    );

    expect(screen.getByText("ファイルサイズは1MB以下にしてください")).toBeInTheDocument();
    expect(
      screen.getByRole("button", { name: /取り込んでレビューを開始/i }),
    ).toBeDisabled();
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("shows manual import server errors", async () => {
    globalThis.fetch = vi.fn().mockResolvedValue({
      ok: false,
      status: 409,
      json: () => Promise.resolve({ detail: "Idempotency conflict" }),
    } as Response);

    render(<App />);

    await userEvent.click(
      screen.getByRole("radio", { name: "ChatGPT結果を取り込み" }),
    );
    await userEvent.type(
      screen.getByRole("textbox", { name: "入力プロンプト" }),
      "手動プロンプト",
    );
    await userEvent.type(
      screen.getByRole("textbox", { name: "出力レポート" }),
      "手動レポート",
    );
    await userEvent.click(
      screen.getByRole("button", { name: /取り込んでレビューを開始/i }),
    );

    expect(await screen.findByRole("alert")).toHaveTextContent(
      "Idempotency conflict",
    );
  });
});

describe("RunMonitor (SCR-3)", () => {
  const runId = "run-prompt-panel-test";

  beforeEach(() => {
    window.location.hash = `#/runs/${runId}`;
    localStorage.setItem(
      "dro.trackedRuns",
      JSON.stringify([
        {
          run_id: runId,
          title: "プロンプト確認テスト",
          max_total_iterations: 5,
          created_at: new Date().toISOString(),
          last_status: "waiting_deep_research",
        },
      ]),
    );
  });

  function makeResearchAttempt(
    runNo: number,
    overrides: Partial<ResearchAttempt> = {},
  ): ResearchAttempt {
    return {
      run_no: runNo,
      response_id: `resp_research_${runNo}`,
      status: "completed",
      model: "o3-deep-research",
      prompt: `# 指示 ${runNo}`,
      report: `# Deep Research ${runNo}`,
      citations: [],
      tool_calls_summary: [],
      error: null,
      ...overrides,
    };
  }

  function makeRunProgress(
    overrides: Partial<ResearchRunStatusResponse["progress"]> = {},
  ): ResearchRunStatusResponse["progress"] {
    return {
      deep_research_runs: 1,
      items_total: 0,
      items_answered: 0,
      items_partial: 0,
      items_unanswered: 0,
      items_unverifiable: 0,
      blockers_unresolved: 0,
      targeted_rerun_runs: 0,
      full_rerun_runs: 0,
      llm_patch_runs: 0,
      verification_runs: 0,
      total_reviews: 0,
      latest_verdict: null,
      latest_score: null,
      total_tool_calls: 0,
      estimated_cost_usd: 0,
      ...overrides,
    };
  }

  function mockRunMonitorFetch({
    status = "completed",
    doneReason = null,
    needsHumanReview = false,
    attempts = [makeResearchAttempt(1)],
    reviews = [],
    history = [],
    progress = makeRunProgress({ deep_research_runs: attempts.length }),
  }: {
    status?: RunStatus;
    doneReason?: string | null;
    needsHumanReview?: boolean;
    attempts?: ResearchAttempt[];
    reviews?: ReviewRecord[];
    history?: AuditResponse["history"];
    progress?: ResearchRunStatusResponse["progress"];
  }) {
    globalThis.fetch = vi.fn((input: RequestInfo | URL) => {
      const url = String(input);
      if (url.endsWith(`/research-runs/${runId}/audit`)) {
        return Promise.resolve({
          ok: true,
          status: 200,
          json: () =>
            Promise.resolve(makeAuditResponse({ run_id: runId, reviews, history })),
        } as Response);
      }
      if (url.endsWith(`/research-runs/${runId}/attempts`)) {
        return Promise.resolve({
          ok: true,
          status: 200,
          json: () => Promise.resolve(attempts),
        } as Response);
      }
      if (url.endsWith(`/research-runs/${runId}/items`)) {
        return Promise.resolve({
          ok: true,
          status: 200,
          json: () => Promise.resolve({ run_id: runId, items: [] }),
        } as Response);
      }
      if (url.endsWith(`/research-runs/${runId}/checkpoints?include_forks=true`)) {
        return Promise.resolve({
          ok: true,
          status: 200,
          json: () => Promise.resolve({ run_id: runId, checkpoints: [] }),
        } as Response);
      }
      if (url.endsWith(`/research-runs/${runId}/lineage`)) {
        return Promise.resolve({
          ok: true,
          status: 200,
          json: () => Promise.resolve({ run_id: runId, lineage: null }),
        } as Response);
      }
      return Promise.resolve({
        ok: true,
        status: 200,
        json: () =>
          Promise.resolve({
            run_id: runId,
            status,
            done_reason: doneReason,
            needs_human_review: needsHumanReview,
            progress,
          }),
      } as Response);
    });
  }

  it("unwraps ResearchItem API wrapper responses in the monitor", async () => {
    globalThis.fetch = vi.fn((input: RequestInfo | URL) => {
      const url = String(input);
      if (url.endsWith(`/research-runs/${runId}/audit`)) {
        return Promise.resolve({
          ok: true,
          status: 200,
          json: () => Promise.resolve(makeAuditResponse({ run_id: runId, reviews: [] })),
        } as Response);
      }
      if (url.endsWith(`/research-runs/${runId}/items`)) {
        return Promise.resolve({
          ok: true,
          status: 200,
          json: () =>
            Promise.resolve({
              run_id: runId,
              items: [
                {
                  item_id: "RI-001",
                  criterion_id: "AC-001",
                  question: "回答済みの項目",
                  expected_answer_type: "fact",
                  status: "answered",
                  severity: "major",
                  confidence: 90,
                  evidence_summary: "根拠あり",
                  citation_ids: [],
                  failure_mode: null,
                  failure_mode_confidence: null,
                  unresolved_reason: null,
                  tried_queries: [],
                  tried_source_types: [],
                  last_attempt_no: 1,
                  last_review_no: 1,
                },
                {
                  item_id: "RI-002",
                  criterion_id: "AC-002",
                  question: "未解決の項目",
                  expected_answer_type: "comparison",
                  status: "partial",
                  severity: "blocker",
                  confidence: 45,
                  evidence_summary: null,
                  citation_ids: [],
                  failure_mode: "needs_deeper_search",
                  failure_mode_confidence: 70,
                  unresolved_reason: "追加情報が必要",
                  tried_queries: [],
                  tried_source_types: [],
                  last_attempt_no: 1,
                  last_review_no: 1,
                },
              ],
            }),
        } as Response);
      }
      return Promise.resolve({
        ok: true,
        status: 200,
        json: () =>
          Promise.resolve({
            run_id: runId,
            status: "reviewing",
            done_reason: null,
            needs_human_review: false,
            progress: {
              items_total: 0,
              items_answered: 0,
              items_partial: 0,
              items_unanswered: 0,
              items_unverifiable: 0,
              blockers_unresolved: 0,
              targeted_rerun_runs: 1,
              full_rerun_runs: 0,
              llm_patch_runs: 0,
              verification_runs: 0,
              total_reviews: 1,
              latest_verdict: null,
              latest_score: 86,
              total_tool_calls: 12,
              estimated_cost_usd: 0.42,
            },
          }),
      } as Response);
    });

    render(<App />);

    expect(await screen.findByText("1/2 answered")).toBeInTheDocument();
    expect(screen.getByText("RI-002")).toBeInTheDocument();
    expect(screen.getByText("needs_deeper_search (70%)")).toBeInTheDocument();
    expect(screen.queryByLabelText("スコア 86")).not.toBeInTheDocument();
  });

  it("separates total elapsed time from the current Deep Research attempt", async () => {
    const submittedAt = "2026-06-06T04:30:00.000Z";
    const expectedStartedLabel = new Intl.DateTimeFormat("ja-JP", {
      hour: "2-digit",
      minute: "2-digit",
    }).format(new Date(submittedAt));

    vi.spyOn(Date, "now").mockReturnValue(
      new Date("2026-06-06T05:00:00.000Z").getTime(),
    );
    localStorage.setItem(
      "dro.trackedRuns",
      JSON.stringify([
        {
          run_id: runId,
          title: "再実行時刻テスト",
          max_total_iterations: 5,
          created_at: "2026-06-06T03:00:00.000Z",
          last_status: "waiting_deep_research",
        },
      ]),
    );
    globalThis.fetch = vi.fn((input: RequestInfo | URL) => {
      const url = String(input);
      if (url.endsWith(`/research-runs/${runId}/audit`)) {
        return Promise.resolve({
          ok: true,
          status: 200,
          json: () =>
            Promise.resolve(
              makeAuditResponse({ run_id: runId, reviews: [makeReviewRecord()] }),
            ),
        } as Response);
      }
      return Promise.resolve({
        ok: true,
        status: 200,
        json: () =>
          Promise.resolve({
            run_id: runId,
            status: "waiting_deep_research",
            done_reason: null,
            needs_human_review: false,
            deep_research_submitted_at: submittedAt,
            progress: {
              targeted_rerun_runs: 2,
              llm_patch_runs: 0,
              total_reviews: 1,
              latest_verdict: null,
              latest_score: null,
              total_tool_calls: 78,
              estimated_cost_usd: 5.54,
            },
          }),
      } as Response);
    });

    render(<App />);

    expect(await screen.findByText("トータル経過時間")).toBeInTheDocument();
    expect(screen.getByText("120:00")).toBeInTheDocument();
    expect(screen.getByText(/今回の経過時間: 30分/)).toBeInTheDocument();
    expect(screen.getByText(new RegExp(`開始時刻: ${expectedStartedLabel}`))).toBeInTheDocument();
  });

  it("shows Deep Research instructions from attempts", async () => {
    const fetchMock = vi.fn((input: RequestInfo | URL) => {
      const url = String(input);
      if (url.endsWith(`/research-runs/${runId}/attempts`)) {
        return Promise.resolve({
          ok: true,
          status: 200,
          json: () =>
            Promise.resolve([
              {
                run_no: 1,
                response_id: "resp_prompt_test",
                status: "queued",
                model: "o3-deep-research",
                prompt: "# Research Objective\n実際のDeep Research指示",
                report: "",
                citations: [],
                tool_calls_summary: [],
                error: null,
              },
              {
                run_no: 1,
                response_id: "resp_prompt_test",
                status: "completed",
                model: "o3-deep-research",
                prompt: "# Research Objective\n重複したcollect側の指示",
                report: "# Deep Research出力",
                citations: [],
                tool_calls_summary: [],
                error: null,
              },
            ]),
        } as Response);
      }
      if (url.endsWith(`/research-runs/${runId}/audit`)) {
        return Promise.resolve({
          ok: true,
          status: 200,
          json: () => Promise.resolve(makeAuditResponse({ run_id: runId, reviews: [] })),
        } as Response);
      }
      return Promise.resolve({
        ok: true,
        status: 200,
        json: () =>
          Promise.resolve({
            run_id: runId,
            status: "waiting_deep_research",
            done_reason: null,
            needs_human_review: false,
            progress: {
              targeted_rerun_runs: 1,
              llm_patch_runs: 0,
              total_reviews: 0,
              latest_verdict: null,
              latest_score: null,
              total_tool_calls: 0,
              estimated_cost_usd: 0,
            },
          }),
      } as Response);
    });
    globalThis.fetch = fetchMock;

    render(<App />);

    await userEvent.click(await screen.findByRole("button", { name: "指示内容" }));

    const promptPanel = await screen.findByRole("region", {
      name: "Deep Researchへの指示内容",
    });
    expect(promptPanel).toBeInTheDocument();
    expect(within(promptPanel).getByText(/実際のDeep Research指示/i)).toBeInTheDocument();
    expect(screen.queryByText(/重複したcollect側の指示/i)).not.toBeInTheDocument();
    expect(within(promptPanel).getAllByText("Deep Research 1回目")).toHaveLength(1);
    expect(within(promptPanel).getByText("completed")).toBeInTheDocument();
    expect(fetchMock).toHaveBeenCalledWith(
      expect.stringContaining(`/research-runs/${runId}/attempts`),
      expect.any(Object),
    );
  });

  it("labels manual upload attempts in the monitor prompt panel", async () => {
    mockRunMonitorFetch({
      attempts: [
        makeResearchAttempt(1, {
          response_id: null,
          model: "chatgpt-deep-research-manual",
          source: "manual_upload",
        }),
      ],
    });

    render(<App />);

    await userEvent.click(await screen.findByRole("button", { name: "指示内容" }));

    const promptPanel = await screen.findByRole("region", {
      name: "Deep Researchへの指示内容",
    });
    expect(
      within(promptPanel).getByText("Deep Research 1回目 手動取り込み"),
    ).toBeInTheDocument();
    expect(within(promptPanel).getByText("手動取り込み")).toBeInTheDocument();
  });

  it("labels manual ChatGPT rerun attempts in the monitor prompt panel", async () => {
    mockRunMonitorFetch({
      attempts: [
        makeResearchAttempt(1, {
          response_id: null,
          model: "chatgpt-deep-research-manual",
          source: "manual_upload",
        }),
        makeResearchAttempt(2, {
          response_id: null,
          model: "chatgpt-deep-research-manual",
          source: "manual_chatgpt_rerun",
          prompt: "# 手動rerun指示",
        }),
      ],
    });

    render(<App />);

    await userEvent.click(await screen.findByRole("button", { name: "指示内容" }));

    const promptPanel = await screen.findByRole("region", {
      name: "Deep Researchへの指示内容",
    });
    expect(
      within(promptPanel).getByText("Deep Research 2回目 ChatGPT手動rerun"),
    ).toBeInTheDocument();
    expect(within(promptPanel).getByText("ChatGPT手動rerun")).toBeInTheDocument();
  });

  it("returns to the dashboard from the run monitor", async () => {
    globalThis.fetch = vi.fn((input: RequestInfo | URL) => {
      const url = String(input);
      if (url.endsWith("/research-runs/human-reviews")) {
        return Promise.resolve({
          ok: true,
          status: 200,
          json: () => Promise.resolve([]),
        } as Response);
      }
      return Promise.resolve({
        ok: true,
        status: 200,
        json: () =>
          Promise.resolve({
            run_id: runId,
            status: "waiting_deep_research",
            done_reason: null,
            needs_human_review: false,
            progress: {
              targeted_rerun_runs: 1,
              llm_patch_runs: 0,
              total_reviews: 0,
              latest_verdict: null,
              latest_score: null,
              total_tool_calls: 0,
              estimated_cost_usd: 0,
            },
          }),
      } as Response);
    });

    render(<App />);

    await userEvent.click(
      await screen.findByRole("link", { name: "ダッシュボードへ戻る" }),
    );

    expect(window.location.hash).toBe("#/");
  });

  it("links to report history without the removed review report view", async () => {
    globalThis.fetch = vi.fn((input: RequestInfo | URL) => {
      const url = String(input);
      if (url.endsWith(`/research-runs/${runId}/citations`)) {
        return Promise.resolve({
          ok: true,
          status: 200,
          json: () => Promise.resolve([]),
        } as Response);
      }
      if (url.endsWith(`/research-runs/${runId}/audit`)) {
        return Promise.resolve({
          ok: true,
          status: 200,
          json: () =>
            Promise.resolve(
              makeAuditResponse({ run_id: runId, reviews: [makeReviewRecord()] }),
            ),
        } as Response);
      }
      if (url.endsWith(`/research-runs/${runId}/attempts`)) {
        return Promise.resolve({
          ok: true,
          status: 200,
          json: () => Promise.resolve([]),
        } as Response);
      }
      if (url.endsWith(`/research-runs/${runId}/report`)) {
        return Promise.resolve({
          ok: true,
          status: 200,
          json: () =>
            Promise.resolve({
              run_id: runId,
              status: "reviewing",
              final_report: null,
              report: "# 候補レポート",
              warnings: [],
            }),
        } as Response);
      }
      return Promise.resolve({
        ok: true,
        status: 200,
        json: () =>
          Promise.resolve({
            run_id: runId,
            status: "reviewing",
            done_reason: null,
            needs_human_review: false,
            progress: {
              targeted_rerun_runs: 1,
              llm_patch_runs: 0,
              total_reviews: 0,
              latest_verdict: null,
              latest_score: null,
              total_tool_calls: 0,
              estimated_cost_usd: 0,
            },
          }),
      } as Response);
    });

    render(<App />);

    expect(await screen.findByRole("link", { name: "レポート履歴" }))
      .toHaveAttribute("href", `#/runs/${runId}/report`);
    expect(screen.queryByRole("link", { name: "レビュー内容" })).not.toBeInTheDocument();
    expect(await screen.findByRole("heading", { name: "レビュー履歴" })).toBeInTheDocument();
    expect(screen.getByText("復旧したレビュー履歴です")).toBeInTheDocument();
  });

  it("selects execution DAG nodes and exposes result links in the inspector", async () => {
    globalThis.fetch = vi.fn((input: RequestInfo | URL) => {
      const url = String(input);
      if (url.endsWith(`/research-runs/${runId}/audit`)) {
        return Promise.resolve({
          ok: true,
          status: 200,
          json: () =>
            Promise.resolve(
              makeAuditResponse({
                run_id: runId,
                reviews: [
                  makeReviewRecord({
                    review_no: 1,
                    verdict: "needs_llm_patch",
                    recommended_route: "needs_llm_patch",
                    score: 72,
                    rationale: "LLM patch が必要です",
                    item_assessments: [
                      makeItemAssessment({ recommended_action: "llm_patch" }),
                    ],
                  }),
                  makeReviewRecord({
                    review_no: 2,
                    score: 90,
                    rationale: "最終レビューは合格です",
                    reviewer_response_id: "resp_review_2",
                  }),
                ],
                history: [
                  {
                    step: "review_recorded",
                    review_no: 1,
                    verdict: "needs_llm_patch",
                    score: 72,
                  },
                  {
                    step: "route_after_review",
                    route: "llm_patch",
                    total_reviews: 1,
                  },
                  {
                    step: "llm_patch",
                    run_no: 1,
                    response_id: "resp_llm_patch_1",
                  },
                  {
                    step: "review_recorded",
                    review_no: 2,
                    verdict: "pass",
                    score: 90,
                  },
                  {
                    step: "route_after_review",
                    route: "finalize",
                    total_reviews: 2,
                  },
                ],
              }),
            ),
        } as Response);
      }
      if (url.endsWith(`/research-runs/${runId}/attempts`)) {
        return Promise.resolve({
          ok: true,
          status: 200,
          json: () =>
            Promise.resolve([
              {
                run_no: 1,
                response_id: "resp_research_1",
                status: "completed",
                model: "o3-deep-research",
                prompt: "# 指示 1",
                report: "# Deep Research 1",
                citations: [],
                tool_calls_summary: [],
                error: null,
              },
            ]),
        } as Response);
      }
      if (url.includes(`/research-runs/${runId}/checkpoints`)) {
        return Promise.resolve({
          ok: true,
          status: 200,
          json: () =>
            Promise.resolve({
              run_id: runId,
              checkpoints: [
                makeCheckpoint({
                  run_id: runId,
                  checkpoint_id: "chk-research-1",
                  node_anchor: "research-1",
                  source_attempt_no: 1,
                  child_forks: [
                    {
                      run_id: "child-run-1",
                      status: "waiting_deep_research",
                      created_at: "2026-06-06T04:10:00.000Z",
                    },
                  ],
                }),
              ],
            }),
        } as Response);
      }
      if (url.endsWith(`/research-runs/${runId}/lineage`)) {
        return Promise.resolve({
          ok: true,
          status: 200,
          json: () => Promise.resolve({ run_id: runId, lineage: null }),
        } as Response);
      }
      if (url.endsWith(`/research-runs/${runId}/items`)) {
        return Promise.resolve({
          ok: true,
          status: 200,
          json: () => Promise.resolve({ run_id: runId, items: [] }),
        } as Response);
      }
      return Promise.resolve({
        ok: true,
        status: 200,
        json: () =>
          Promise.resolve({
            run_id: runId,
            status: "completed",
            done_reason: "passed_review",
            needs_human_review: false,
            progress: {
              deep_research_runs: 1,
              targeted_rerun_runs: 0,
              full_rerun_runs: 0,
              llm_patch_runs: 1,
              verification_runs: 0,
              total_reviews: 2,
              latest_verdict: "pass",
              latest_score: 90,
              total_tool_calls: 0,
              estimated_cost_usd: 1.25,
            },
          }),
      } as Response);
    });

    render(<App />);

    const researchNode = await screen.findByRole("button", {
      name: "Deep Research 1回目を選択",
    });
    await userEvent.click(researchNode);
    expect(screen.getByRole("link", { name: "結果を開く" }))
      .toHaveAttribute("href", `#/runs/${runId}/report?tab=research&attempt=1`);
    const inspector = screen.getByRole("complementary", { name: "選択checkpoint詳細" });
    expect(
      within(inspector).getByText(
        (_content, element) => element?.textContent === "#1 / Deep Research収集後",
      ),
    ).toBeInTheDocument();
    expect(within(inspector).getByRole("button", { name: "ここからフォーク" }))
      .toBeEnabled();
    expect(within(inspector).getByRole("link", { name: "child-run-1" }))
      .toHaveAttribute("href", "#/runs/child-run-1");

    await userEvent.click(screen.getByRole("button", { name: "LLMレビュー 1回目を選択" }));
    expect(screen.getByRole("link", { name: "監査ログを開く" }))
      .toHaveAttribute("href", `#/runs/${runId}/audit?tab=reviews&review=1`);

    await userEvent.click(screen.getByRole("button", { name: "LLMパッチ 1回目を選択" }));
    expect(screen.getByRole("link", { name: "監査ログを開く" }))
      .toHaveAttribute("href", `#/runs/${runId}/audit?tab=reviews&review=1`);

    await userEvent.click(screen.getByRole("button", { name: "最終レポートを選択" }));
    expect(screen.getByRole("link", { name: "結果を開く" }))
      .toHaveAttribute("href", `#/runs/${runId}/report`);
    expect(screen.getByRole("link", { name: "最終レポートを開く" }))
      .toHaveAttribute("href", `#/runs/${runId}/report`);
  });

  it("shows checkpoint details and child forks in the inspector", async () => {
    const checkpoint = makeCheckpoint({
      run_id: runId,
      child_forks: [
        {
          run_id: "child-run-1",
          status: "waiting_deep_research",
          created_at: "2026-06-06T03:20:00.000Z",
        },
      ],
    });
    globalThis.fetch = vi.fn((input: RequestInfo | URL) => {
      const url = String(input);
      if (url.endsWith(`/research-runs/${runId}/checkpoints?include_forks=true`)) {
        return Promise.resolve({
          ok: true,
          status: 200,
          json: () => Promise.resolve({ run_id: runId, checkpoints: [checkpoint] }),
        } as Response);
      }
      if (url.endsWith(`/research-runs/${runId}/lineage`)) {
        return Promise.resolve({
          ok: true,
          status: 200,
          json: () => Promise.resolve({ run_id: runId, lineage: null }),
        } as Response);
      }
      if (url.endsWith(`/research-runs/${runId}/audit`)) {
        return Promise.resolve({
          ok: true,
          status: 200,
          json: () => Promise.resolve(makeAuditResponse({ run_id: runId, reviews: [] })),
        } as Response);
      }
      if (url.endsWith(`/research-runs/${runId}/attempts`)) {
        return Promise.resolve({
          ok: true,
          status: 200,
          json: () =>
            Promise.resolve([
              {
                run_no: 1,
                response_id: "resp_research_1",
                status: "completed",
                model: "o3-deep-research",
                prompt: "# 指示 1",
                report: "# Deep Research 1",
                citations: [],
                tool_calls_summary: [],
                error: null,
              },
            ]),
        } as Response);
      }
      if (url.endsWith(`/research-runs/${runId}/items`)) {
        return Promise.resolve({
          ok: true,
          status: 200,
          json: () => Promise.resolve({ run_id: runId, items: [] }),
        } as Response);
      }
      return Promise.resolve({
        ok: true,
        status: 200,
        json: () =>
          Promise.resolve({
            run_id: runId,
            status: "completed",
            done_reason: "passed_review",
            needs_human_review: false,
            progress: {
              deep_research_runs: 1,
              targeted_rerun_runs: 0,
              full_rerun_runs: 0,
              llm_patch_runs: 0,
              verification_runs: 0,
              total_reviews: 0,
              latest_verdict: null,
              latest_score: null,
              total_tool_calls: 0,
              estimated_cost_usd: 1,
            },
          }),
      } as Response);
    });

    render(<App />);

    const researchNode = await screen.findByRole("button", {
      name: "Deep Research 1回目を選択",
    });
    expect(within(researchNode).getByText("保存済み")).toBeInTheDocument();
    expect(within(researchNode).getByText("分岐可")).toBeInTheDocument();
    expect(within(researchNode).getByText("派生 1")).toBeInTheDocument();
    expect(screen.getByText("#1 / Deep Research収集後")).toBeInTheDocument();
    expect(screen.getByText(/report-hash-/)).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "child-run-1" }))
      .toHaveAttribute("href", "#/runs/child-run-1");
    expect(screen.getByRole("button", { name: "ここからフォーク" })).toBeEnabled();
  });

  it("requires fork preview before submit and navigates to the child run", async () => {
    const checkpoint = makeCheckpoint({ run_id: runId });
    const fetchMock = vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      if (url.endsWith(`/research-runs/${runId}/checkpoints/chk-research-1/fork-preview`)) {
        expect(init?.method).toBe("POST");
        expect(JSON.parse(String(init?.body))).toEqual({
          additional_prompt: "追加で競合比較を深掘りしてください。",
        });
        return Promise.resolve({
          ok: true,
          status: 200,
          json: () =>
            Promise.resolve({
              composed_prompt: "合成されたフォーク指示",
              query_policy: { status: "allowed", safe_queries: [] },
              policy_decision: { status: "allowed", safe_queries: [] },
              source_prompt_excerpt: "元の指示抜粋",
              source_report_excerpt: "元レポート抜粋",
              warnings: ["新しいDeep Researchとして課金されます"],
              preview_hash: "preview-hash-1",
            }),
        } as Response);
      }
      if (url.endsWith(`/research-runs/${runId}/checkpoints/chk-research-1/forks`)) {
        expect(init?.method).toBe("POST");
        expect(JSON.parse(String(init?.body))).toEqual({
          additional_prompt: "追加で競合比較を深掘りしてください。",
          idempotency_key: expect.any(String),
          confirmed_preview_hash: "preview-hash-1",
        });
        return Promise.resolve({
          ok: true,
          status: 200,
          json: () =>
            Promise.resolve({
              run_id: "child-run-created",
              parent_run_id: runId,
              forked_from_checkpoint_id: "chk-research-1",
              child_run_id: "child-run-created",
              status: "waiting_deep_research",
              done_reason: null,
              needs_human_review: false,
              source_snapshot_json: {},
              lineage: {
                run_id: "child-run-created",
                root_run_id: runId,
                parent_run_id: runId,
                forked_from_checkpoint_id: "chk-research-1",
                fork_mode: "checkpoint",
                additional_prompt: "追加で競合比較を深掘りしてください。",
                confirmed_preview_hash: "preview-hash-1",
                idempotency_key: "test-idempotency-key",
                source_snapshot_json: {},
                created_at: "2026-06-06T04:20:00.000Z",
              },
            }),
        } as Response);
      }
      if (url.endsWith(`/research-runs/${runId}/checkpoints?include_forks=true`)) {
        return Promise.resolve({
          ok: true,
          status: 200,
          json: () => Promise.resolve({ run_id: runId, checkpoints: [checkpoint] }),
        } as Response);
      }
      if (url.endsWith(`/research-runs/${runId}/lineage`)) {
        return Promise.resolve({
          ok: true,
          status: 200,
          json: () => Promise.resolve({ run_id: runId, lineage: null }),
        } as Response);
      }
      if (url.endsWith(`/research-runs/${runId}/audit`)) {
        return Promise.resolve({
          ok: true,
          status: 200,
          json: () => Promise.resolve(makeAuditResponse({ run_id: runId, reviews: [] })),
        } as Response);
      }
      if (url.endsWith(`/research-runs/${runId}/attempts`)) {
        return Promise.resolve({
          ok: true,
          status: 200,
          json: () =>
            Promise.resolve([
              {
                run_no: 1,
                response_id: "resp_research_1",
                status: "completed",
                model: "o3-deep-research",
                prompt: "# 指示 1",
                report: "# Deep Research 1",
                citations: [],
                tool_calls_summary: [],
                error: null,
              },
            ]),
        } as Response);
      }
      if (url.endsWith(`/research-runs/${runId}/items`)) {
        return Promise.resolve({
          ok: true,
          status: 200,
          json: () => Promise.resolve({ run_id: runId, items: [] }),
        } as Response);
      }
      return Promise.resolve({
        ok: true,
        status: 200,
        json: () =>
          Promise.resolve({
            run_id: url.includes("child-run-created") ? "child-run-created" : runId,
            status: "completed",
            done_reason: null,
            needs_human_review: false,
            progress: {
              deep_research_runs: 1,
              targeted_rerun_runs: 0,
              full_rerun_runs: 0,
              llm_patch_runs: 0,
              verification_runs: 0,
              total_reviews: 0,
              latest_verdict: null,
              latest_score: null,
              total_tool_calls: 0,
              estimated_cost_usd: 0,
            },
          }),
      } as Response);
    });
    globalThis.fetch = fetchMock;

    render(<App />);

    await userEvent.click(await screen.findByRole("button", { name: "ここからフォーク" }));
    const submitButton = screen.getByRole("button", {
      name: "child runで新しいDeep Researchを開始",
    });
    expect(submitButton).toBeDisabled();

    await userEvent.type(
      screen.getByRole("textbox", { name: "追加指示" }),
      "追加で競合比較を深掘りしてください。",
    );
    expect(submitButton).toBeDisabled();

    await userEvent.click(screen.getByRole("button", { name: "フォーク内容をプレビュー" }));
    expect(await screen.findByText("合成されたフォーク指示")).toBeInTheDocument();
    expect(submitButton).toBeEnabled();

    await userEvent.click(submitButton);
    await waitFor(() => {
      expect(window.location.hash).toBe("#/runs/child-run-created");
    });
  });

  it("invalidates fork preview when the additional prompt changes", async () => {
    const checkpoint = makeCheckpoint({ run_id: runId });
    globalThis.fetch = vi.fn((input: RequestInfo | URL) => {
      const url = String(input);
      if (url.endsWith(`/research-runs/${runId}/checkpoints/chk-research-1/fork-preview`)) {
        return Promise.resolve({
          ok: true,
          status: 200,
          json: () =>
            Promise.resolve({
              composed_prompt: "合成されたフォーク指示",
              query_policy: { status: "allowed", safe_queries: [] },
              policy_decision: { status: "allowed", safe_queries: [] },
              source_prompt_excerpt: "元の指示抜粋",
              source_report_excerpt: "元レポート抜粋",
              warnings: [],
              preview_hash: "preview-hash-1",
            }),
        } as Response);
      }
      if (url.endsWith(`/research-runs/${runId}/checkpoints?include_forks=true`)) {
        return Promise.resolve({
          ok: true,
          status: 200,
          json: () => Promise.resolve({ run_id: runId, checkpoints: [checkpoint] }),
        } as Response);
      }
      if (url.endsWith(`/research-runs/${runId}/lineage`)) {
        return Promise.resolve({
          ok: true,
          status: 200,
          json: () => Promise.resolve({ run_id: runId, lineage: null }),
        } as Response);
      }
      if (url.endsWith(`/research-runs/${runId}/audit`)) {
        return Promise.resolve({
          ok: true,
          status: 200,
          json: () => Promise.resolve(makeAuditResponse({ run_id: runId, reviews: [] })),
        } as Response);
      }
      if (url.endsWith(`/research-runs/${runId}/attempts`)) {
        return Promise.resolve({
          ok: true,
          status: 200,
          json: () => Promise.resolve([]),
        } as Response);
      }
      if (url.endsWith(`/research-runs/${runId}/items`)) {
        return Promise.resolve({
          ok: true,
          status: 200,
          json: () => Promise.resolve({ run_id: runId, items: [] }),
        } as Response);
      }
      return Promise.resolve({
        ok: true,
        status: 200,
        json: () =>
          Promise.resolve({
            run_id: runId,
            status: "completed",
            done_reason: null,
            needs_human_review: false,
            progress: {
              deep_research_runs: 1,
              targeted_rerun_runs: 0,
              full_rerun_runs: 0,
              llm_patch_runs: 0,
              verification_runs: 0,
              total_reviews: 0,
              latest_verdict: null,
              latest_score: null,
              total_tool_calls: 0,
              estimated_cost_usd: 0,
            },
          }),
      } as Response);
    });

    render(<App />);

    await userEvent.click(await screen.findByRole("button", { name: "ここからフォーク" }));
    const textarea = screen.getByRole("textbox", { name: "追加指示" });
    const submitButton = screen.getByRole("button", {
      name: "child runで新しいDeep Researchを開始",
    });
    await userEvent.type(textarea, "追加調査");
    await userEvent.click(screen.getByRole("button", { name: "フォーク内容をプレビュー" }));
    await screen.findByText("合成されたフォーク指示");
    expect(submitButton).toBeEnabled();

    await userEvent.type(textarea, "を変更");
    expect(submitButton).toBeDisabled();
    expect(screen.getByText(/再プレビューが必要/)).toBeInTheDocument();
  });

  it("does not adopt an in-flight stale fork preview response", async () => {
    const checkpoint = makeCheckpoint({ run_id: runId });
    let resolveFirstPreview: (response: Response) => void = () => {
      throw new Error("First preview request was not started.");
    };
    globalThis.fetch = vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      if (url.endsWith(`/research-runs/${runId}/checkpoints/chk-research-1/fork-preview`)) {
        const body = JSON.parse(String(init?.body)) as { additional_prompt: string };
        if (body.additional_prompt === "古い指示") {
          return new Promise<Response>((resolve) => {
            resolveFirstPreview = resolve;
          });
        }
        return Promise.resolve({
          ok: true,
          status: 200,
          json: () =>
            Promise.resolve({
              composed_prompt: "現在のフォーク指示",
              query_policy: { status: "allowed", safe_queries: [] },
              policy_decision: { status: "allowed", safe_queries: [] },
              source_prompt_excerpt: "元の指示抜粋",
              source_report_excerpt: "元レポート抜粋",
              warnings: [],
              preview_hash: "preview-hash-current",
            }),
        } as Response);
      }
      if (url.endsWith(`/research-runs/${runId}/checkpoints?include_forks=true`)) {
        return Promise.resolve({
          ok: true,
          status: 200,
          json: () => Promise.resolve({ run_id: runId, checkpoints: [checkpoint] }),
        } as Response);
      }
      if (url.endsWith(`/research-runs/${runId}/lineage`)) {
        return Promise.resolve({
          ok: true,
          status: 200,
          json: () => Promise.resolve({ run_id: runId, lineage: null }),
        } as Response);
      }
      if (url.endsWith(`/research-runs/${runId}/audit`)) {
        return Promise.resolve({
          ok: true,
          status: 200,
          json: () => Promise.resolve(makeAuditResponse({ run_id: runId, reviews: [] })),
        } as Response);
      }
      if (url.endsWith(`/research-runs/${runId}/attempts`)) {
        return Promise.resolve({
          ok: true,
          status: 200,
          json: () => Promise.resolve([]),
        } as Response);
      }
      if (url.endsWith(`/research-runs/${runId}/items`)) {
        return Promise.resolve({
          ok: true,
          status: 200,
          json: () => Promise.resolve({ run_id: runId, items: [] }),
        } as Response);
      }
      return Promise.resolve({
        ok: true,
        status: 200,
        json: () =>
          Promise.resolve({
            run_id: runId,
            status: "completed",
            done_reason: null,
            needs_human_review: false,
            progress: {
              deep_research_runs: 1,
              targeted_rerun_runs: 0,
              full_rerun_runs: 0,
              llm_patch_runs: 0,
              verification_runs: 0,
              total_reviews: 0,
              latest_verdict: null,
              latest_score: null,
              total_tool_calls: 0,
              estimated_cost_usd: 0,
            },
          }),
      } as Response);
    });

    render(<App />);

    await userEvent.click(await screen.findByRole("button", { name: "ここからフォーク" }));
    const textarea = screen.getByRole("textbox", { name: "追加指示" });
    const previewButton = screen.getByRole("button", { name: "フォーク内容をプレビュー" });
    const submitButton = screen.getByRole("button", {
      name: "child runで新しいDeep Researchを開始",
    });

    await userEvent.type(textarea, "古い指示");
    await userEvent.click(previewButton);
    await userEvent.type(textarea, " updated");
    expect(submitButton).toBeDisabled();

    resolveFirstPreview({
      ok: true,
      status: 200,
      json: () =>
        Promise.resolve({
          composed_prompt: "古いフォーク指示",
          query_policy: { status: "allowed", safe_queries: [] },
          policy_decision: { status: "allowed", safe_queries: [] },
          source_prompt_excerpt: "元の指示抜粋",
          source_report_excerpt: "元レポート抜粋",
          warnings: [],
          preview_hash: "preview-hash-stale",
        }),
    } as Response);

    await waitFor(() => expect(previewButton).toBeEnabled());
    expect(screen.queryByText("古いフォーク指示")).not.toBeInTheDocument();
    expect(submitButton).toBeDisabled();

    await userEvent.click(previewButton);
    expect(await screen.findByText("現在のフォーク指示")).toBeInTheDocument();
    expect(submitButton).toBeEnabled();
  });

  it("shows fork submit 409 errors in the modal", async () => {
    const checkpoint = makeCheckpoint({ run_id: runId });
    globalThis.fetch = vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      if (url.endsWith(`/research-runs/${runId}/checkpoints/chk-research-1/fork-preview`)) {
        return Promise.resolve({
          ok: true,
          status: 200,
          json: () =>
            Promise.resolve({
              composed_prompt: "合成されたフォーク指示",
              query_policy: { status: "allowed", safe_queries: [] },
              policy_decision: { status: "allowed", safe_queries: [] },
              source_prompt_excerpt: "元の指示抜粋",
              source_report_excerpt: "元レポート抜粋",
              warnings: [],
              preview_hash: "preview-hash-1",
            }),
        } as Response);
      }
      if (url.endsWith(`/research-runs/${runId}/checkpoints/chk-research-1/forks`)) {
        expect(init?.method).toBe("POST");
        return Promise.resolve({
          ok: false,
          status: 409,
          json: () => Promise.resolve({ detail: "preview hash mismatch" }),
        } as Response);
      }
      if (url.endsWith(`/research-runs/${runId}/checkpoints?include_forks=true`)) {
        return Promise.resolve({
          ok: true,
          status: 200,
          json: () => Promise.resolve({ run_id: runId, checkpoints: [checkpoint] }),
        } as Response);
      }
      if (url.endsWith(`/research-runs/${runId}/lineage`)) {
        return Promise.resolve({
          ok: true,
          status: 200,
          json: () => Promise.resolve({ run_id: runId, lineage: null }),
        } as Response);
      }
      if (url.endsWith(`/research-runs/${runId}/audit`)) {
        return Promise.resolve({
          ok: true,
          status: 200,
          json: () => Promise.resolve(makeAuditResponse({ run_id: runId, reviews: [] })),
        } as Response);
      }
      if (url.endsWith(`/research-runs/${runId}/attempts`)) {
        return Promise.resolve({
          ok: true,
          status: 200,
          json: () => Promise.resolve([]),
        } as Response);
      }
      if (url.endsWith(`/research-runs/${runId}/items`)) {
        return Promise.resolve({
          ok: true,
          status: 200,
          json: () => Promise.resolve({ run_id: runId, items: [] }),
        } as Response);
      }
      return Promise.resolve({
        ok: true,
        status: 200,
        json: () =>
          Promise.resolve({
            run_id: runId,
            status: "completed",
            done_reason: null,
            needs_human_review: false,
            progress: {
              deep_research_runs: 1,
              targeted_rerun_runs: 0,
              full_rerun_runs: 0,
              llm_patch_runs: 0,
              verification_runs: 0,
              total_reviews: 0,
              latest_verdict: null,
              latest_score: null,
              total_tool_calls: 0,
              estimated_cost_usd: 0,
            },
          }),
      } as Response);
    });

    render(<App />);

    await userEvent.click(await screen.findByRole("button", { name: "ここからフォーク" }));
    await userEvent.type(screen.getByRole("textbox", { name: "追加指示" }), "追加調査");
    await userEvent.click(screen.getByRole("button", { name: "フォーク内容をプレビュー" }));
    await screen.findByText("合成されたフォーク指示");
    await userEvent.click(
      screen.getByRole("button", { name: "child runで新しいDeep Researchを開始" }),
    );

    expect(await screen.findByRole("alert")).toHaveTextContent(
      "プレビューが古くなっています。preview hash mismatch",
    );
  });

  it("does not render a third verification node when only two verification runs executed", async () => {
    globalThis.fetch = vi.fn((input: RequestInfo | URL) => {
      const url = String(input);
      if (url.endsWith(`/research-runs/${runId}/audit`)) {
        return Promise.resolve({
          ok: true,
          status: 200,
          json: () =>
            Promise.resolve(
              makeAuditResponse({
                run_id: runId,
                reviews: [
                  makeReviewRecord({
                    review_no: 1,
                    verdict: "needs_verification",
                    recommended_route: "needs_verification",
                    score: 62,
                    item_assessments: [
                      makeItemAssessment({ recommended_action: "verify" }),
                    ],
                  }),
                  makeReviewRecord({
                    review_no: 2,
                    verdict: "needs_verification",
                    recommended_route: "needs_verification",
                    score: 66,
                    item_assessments: [
                      makeItemAssessment({ recommended_action: "verify" }),
                    ],
                  }),
                  makeReviewRecord({
                    review_no: 3,
                    verdict: "needs_verification",
                    recommended_route: "needs_verification",
                    score: 68,
                    rationale: "検証上限により人間判断が必要です",
                    item_assessments: [
                      makeItemAssessment({ recommended_action: "verify" }),
                    ],
                  }),
                ],
                history: [
                  {
                    step: "route_after_review",
                    route: "verify_items",
                    total_reviews: 1,
                  },
                  {
                    step: "verification_completed",
                    response_id: "resp_verify_1",
                  },
                  {
                    step: "route_after_review",
                    route: "verify_items",
                    total_reviews: 2,
                  },
                  {
                    step: "verification_completed",
                    response_id: "resp_verify_2",
                  },
                  {
                    step: "route_after_review",
                    route: "human_review",
                    total_reviews: 3,
                  },
                  {
                    step: "human_review_required",
                    latest_review_no: 3,
                    reason: "max_verification_runs_reached",
                  },
                ],
              }),
            ),
        } as Response);
      }
      if (url.endsWith(`/research-runs/${runId}/attempts`)) {
        return Promise.resolve({
          ok: true,
          status: 200,
          json: () =>
            Promise.resolve([
              {
                run_no: 1,
                response_id: "resp_research_1",
                status: "completed",
                model: "o3-deep-research",
                prompt: "# 指示 1",
                report: "# Deep Research 1",
                citations: [],
                tool_calls_summary: [],
                error: null,
              },
            ]),
        } as Response);
      }
      if (url.endsWith(`/research-runs/${runId}/items`)) {
        return Promise.resolve({
          ok: true,
          status: 200,
          json: () => Promise.resolve({ run_id: runId, items: [] }),
        } as Response);
      }
      return Promise.resolve({
        ok: true,
        status: 200,
        json: () =>
          Promise.resolve({
            run_id: runId,
            status: "needs_human_review",
            done_reason: "max_verification_runs_reached",
            needs_human_review: true,
            progress: {
              deep_research_runs: 1,
              targeted_rerun_runs: 0,
              full_rerun_runs: 0,
              llm_patch_runs: 0,
              verification_runs: 2,
              total_reviews: 3,
              latest_verdict: "needs_verification",
              latest_score: 68,
              total_tool_calls: 30,
              estimated_cost_usd: 1.8,
            },
          }),
      } as Response);
    });

    render(<App />);

    expect(await screen.findByText("検証 1回目")).toBeInTheDocument();
    expect(screen.getByText("検証 2回目")).toBeInTheDocument();
    expect(screen.queryByText("検証 3回目")).not.toBeInTheDocument();

    const dag = screen.getByRole("region", { name: "具体的な実行フロー" });
    const nodeTitles = within(dag)
      .getAllByRole("heading", { level: 3 })
      .map((heading) => heading.textContent);
    expect(nodeTitles).toEqual(
      expect.arrayContaining(["LLMレビュー 3回目", "人間判断"]),
    );
    expect(nodeTitles.indexOf("人間判断")).toBe(
      nodeTitles.indexOf("LLMレビュー 3回目") + 1,
    );
  });

  it("does not render a targeted rerun node from a recommendation without an executed rerun", async () => {
    globalThis.fetch = vi.fn((input: RequestInfo | URL) => {
      const url = String(input);
      if (url.endsWith(`/research-runs/${runId}/audit`)) {
        return Promise.resolve({
          ok: true,
          status: 200,
          json: () =>
            Promise.resolve(
              makeAuditResponse({
                run_id: runId,
                reviews: [
                  makeReviewRecord({
                    review_no: 1,
                    verdict: "needs_targeted_rerun",
                    recommended_route: "needs_targeted_rerun",
                    score: 64,
                    rationale: "追加調査推奨だが自動実行されません",
                    item_assessments: [
                      makeItemAssessment({ recommended_action: "targeted_rerun" }),
                    ],
                  }),
                ],
                history: [
                  {
                    step: "route_after_review",
                    route: "human_review",
                    total_reviews: 1,
                  },
                  {
                    step: "human_review_required",
                    latest_review_no: 1,
                    reason: "max_targeted_rerun_runs_reached",
                  },
                ],
              }),
            ),
        } as Response);
      }
      if (url.endsWith(`/research-runs/${runId}/attempts`)) {
        return Promise.resolve({
          ok: true,
          status: 200,
          json: () =>
            Promise.resolve([
              {
                run_no: 1,
                response_id: "resp_research_1",
                status: "completed",
                model: "o3-deep-research",
                prompt: "# 指示 1",
                report: "# Deep Research 1",
                citations: [],
                tool_calls_summary: [],
                error: null,
              },
            ]),
        } as Response);
      }
      if (url.endsWith(`/research-runs/${runId}/items`)) {
        return Promise.resolve({
          ok: true,
          status: 200,
          json: () => Promise.resolve({ run_id: runId, items: [] }),
        } as Response);
      }
      return Promise.resolve({
        ok: true,
        status: 200,
        json: () =>
          Promise.resolve({
            run_id: runId,
            status: "needs_human_review",
            done_reason: "max_targeted_rerun_runs_reached",
            needs_human_review: true,
            progress: {
              deep_research_runs: 1,
              targeted_rerun_runs: 0,
              full_rerun_runs: 0,
              llm_patch_runs: 0,
              verification_runs: 0,
              total_reviews: 1,
              latest_verdict: "needs_targeted_rerun",
              latest_score: 64,
              total_tool_calls: 12,
              estimated_cost_usd: 0.9,
            },
          }),
      } as Response);
    });

    render(<App />);

    expect(await screen.findByText("LLMレビュー 1回目")).toBeInTheDocument();
    expect(screen.queryByText("Targeted rerun 1")).not.toBeInTheDocument();

    const dag = screen.getByRole("region", { name: "具体的な実行フロー" });
    const nodeTitles = within(dag)
      .getAllByRole("heading", { level: 3 })
      .map((heading) => heading.textContent);
    expect(nodeTitles.indexOf("人間判断")).toBe(
      nodeTitles.indexOf("LLMレビュー 1回目") + 1,
    );
  });

  it.each([
    {
      route: "verify_items",
      verdict: "needs_verification" as const,
      progressKey: "verification_runs" as const,
      title: "検証 1回目",
      action: "verify" as const,
    },
    {
      route: "llm_patch",
      verdict: "needs_llm_patch" as const,
      progressKey: "llm_patch_runs" as const,
      title: "LLMパッチ 1回目",
      action: "llm_patch" as const,
    },
  ])(
    "keeps an in-flight $route follow-up active instead of showing the next review",
    async ({ route, verdict, progressKey, title, action }) => {
      mockRunMonitorFetch({
        status: "reviewing",
        attempts: [makeResearchAttempt(1)],
        reviews: [
          makeReviewRecord({
            review_no: 1,
            verdict,
            recommended_route: verdict,
            score: 67,
            item_assessments: [makeItemAssessment({ recommended_action: action })],
          }),
        ],
        history: [
          {
            step: "route_after_review",
            route,
            total_reviews: 1,
          },
        ],
        progress: makeRunProgress({
          deep_research_runs: 1,
          total_reviews: 1,
          latest_verdict: verdict,
          latest_score: 67,
          [progressKey]: 0,
        }),
      });

      render(<App />);

      const followUpNode = await screen.findByRole("button", {
        name: `${title}を選択`,
      });
      expect(within(followUpNode).getByText("実行中")).toBeInTheDocument();
      expect(
        screen.queryByRole("button", { name: "LLMレビュー 2回目を選択" }),
      ).not.toBeInTheDocument();
    },
  );

  it("renders an executed targeted rerun between review and Deep Research 2 when progress lags", async () => {
    mockRunMonitorFetch({
      status: "reviewing",
      attempts: [
        makeResearchAttempt(1),
        makeResearchAttempt(2, {
          response_id: "resp_targeted_rerun_1",
          prompt: "# Targeted rerun brief",
          report: "# Targeted rerun delta",
        }),
      ],
      reviews: [
        makeReviewRecord({
          review_no: 1,
          verdict: "needs_targeted_rerun",
          recommended_route: "needs_targeted_rerun",
          score: 64,
          item_assessments: [
            makeItemAssessment({ recommended_action: "targeted_rerun" }),
          ],
        }),
      ],
      history: [
        {
          step: "route_after_review",
          route: "build_targeted_rerun_plan",
          total_reviews: 1,
        },
      ],
      progress: makeRunProgress({
        deep_research_runs: 1,
        targeted_rerun_runs: 0,
        total_reviews: 1,
        latest_verdict: "needs_targeted_rerun",
        latest_score: 64,
      }),
    });

    render(<App />);

    const dag = await screen.findByRole("region", { name: "具体的な実行フロー" });
    const nodeTitles = within(dag)
      .getAllByRole("heading", { level: 3 })
      .map((heading) => heading.textContent);
    expect(nodeTitles).toEqual(
      expect.arrayContaining([
        "LLMレビュー 1回目",
        "Targeted rerun 1",
        "Deep Research 2回目",
      ]),
    );
    expect(nodeTitles.indexOf("Targeted rerun 1")).toBe(
      nodeTitles.indexOf("LLMレビュー 1回目") + 1,
    );
    expect(nodeTitles.indexOf("Deep Research 2回目")).toBe(
      nodeTitles.indexOf("Targeted rerun 1") + 1,
    );
  });

  it("does not render a rerun follow-up from a plan without a submitted attempt", async () => {
    mockRunMonitorFetch({
      status: "needs_human_review",
      doneReason: "deep_research_blocked_by_query_policy",
      needsHumanReview: true,
      attempts: [makeResearchAttempt(1)],
      reviews: [
        makeReviewRecord({
          review_no: 1,
          verdict: "needs_targeted_rerun",
          recommended_route: "needs_targeted_rerun",
          score: 64,
          item_assessments: [
            makeItemAssessment({ recommended_action: "targeted_rerun" }),
          ],
        }),
      ],
      history: [
        {
          step: "route_after_review",
          route: "build_targeted_rerun_plan",
          total_reviews: 1,
        },
        {
          step: "rerun_plan_created",
          rerun_id: "rerun_1",
          scope: "targeted_gap_closure",
        },
        {
          step: "deep_research_submit_blocked",
          reason: "query_policy_blocked",
        },
      ],
      progress: makeRunProgress({
        deep_research_runs: 1,
        targeted_rerun_runs: 0,
        total_reviews: 1,
        latest_verdict: "needs_targeted_rerun",
        latest_score: 64,
      }),
    });

    render(<App />);

    const dag = await screen.findByRole("region", { name: "具体的な実行フロー" });
    const nodeTitles = within(dag)
      .getAllByRole("heading", { level: 3 })
      .map((heading) => heading.textContent);
    expect(nodeTitles).toEqual(expect.arrayContaining(["LLMレビュー 1回目", "人間判断"]));
    expect(nodeTitles).not.toContain("Targeted rerun 1");
    expect(nodeTitles).not.toContain("Deep Research 2回目");
  });

  it("does not mark a progress-only synthetic Deep Research node done", async () => {
    mockRunMonitorFetch({
      status: "completed",
      doneReason: "passed_review",
      attempts: [makeResearchAttempt(1)],
      reviews: [
        makeReviewRecord({
          review_no: 1,
          verdict: "needs_targeted_rerun",
          recommended_route: "needs_targeted_rerun",
          score: 65,
          item_assessments: [
            makeItemAssessment({ recommended_action: "targeted_rerun" }),
          ],
        }),
      ],
      history: [
        {
          step: "route_after_review",
          route: "build_targeted_rerun_plan",
          total_reviews: 1,
        },
      ],
      progress: makeRunProgress({
        deep_research_runs: 2,
        targeted_rerun_runs: 1,
        total_reviews: 1,
        latest_verdict: "needs_targeted_rerun",
        latest_score: 65,
      }),
    });

    render(<App />);

    const syntheticResearchNode = await screen.findByRole("button", {
      name: "Deep Research 2回目を選択",
    });
    expect(within(syntheticResearchNode).getByText("待機")).toBeInTheDocument();
    expect(within(syntheticResearchNode).queryByText("完了")).not.toBeInTheDocument();
  });

  it("links the active human decision DAG node to the review screen", async () => {
    globalThis.fetch = vi.fn((input: RequestInfo | URL) => {
      const url = String(input);
      if (url.endsWith(`/research-runs/${runId}/audit`)) {
        return Promise.resolve({
          ok: true,
          status: 200,
          json: () =>
            Promise.resolve(
              makeAuditResponse({
                run_id: runId,
                reviews: [
                  makeReviewRecord({
                    verdict: "human_review",
                    recommended_route: "human_review",
                    score: 61,
                    rationale: "人間判断が必要です",
                    item_assessments: [
                      makeItemAssessment({ recommended_action: "human_review" }),
                    ],
                  }),
                ],
              }),
            ),
        } as Response);
      }
      if (url.endsWith(`/research-runs/${runId}/attempts`)) {
        return Promise.resolve({
          ok: true,
          status: 200,
          json: () =>
            Promise.resolve([
              {
                run_no: 1,
                response_id: "resp_research_1",
                status: "completed",
                model: "o3-deep-research",
                prompt: "# 指示 1",
                report: "# Deep Research 1",
                citations: [],
                tool_calls_summary: [],
                error: null,
              },
            ]),
        } as Response);
      }
      if (url.endsWith(`/research-runs/${runId}/items`)) {
        return Promise.resolve({
          ok: true,
          status: 200,
          json: () => Promise.resolve({ run_id: runId, items: [] }),
        } as Response);
      }
      return Promise.resolve({
        ok: true,
        status: 200,
        json: () =>
          Promise.resolve({
            run_id: runId,
            status: "needs_human_review",
            done_reason: "human_review",
            needs_human_review: true,
            progress: {
              deep_research_runs: 1,
              targeted_rerun_runs: 0,
              full_rerun_runs: 0,
              llm_patch_runs: 0,
              verification_runs: 0,
              total_reviews: 1,
              latest_verdict: "human_review",
              latest_score: 61,
              total_tool_calls: 10,
              estimated_cost_usd: 0.9,
            },
          }),
      } as Response);
    });

    render(<App />);

    const decisionNode = await screen.findByRole("button", { name: "人間判断を選択" });
    await userEvent.click(decisionNode);
    await waitFor(() =>
      expect(screen.getByRole("button", { name: "人間判断を選択" }))
        .toHaveAttribute("aria-pressed", "true"),
    );
    expect(within(decisionNode).getByText("人間判断")).toBeInTheDocument();
    expect(within(decisionNode).getByText("実行中")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "結果を開く" }))
      .toHaveAttribute("href", `#/runs/${runId}/review`);
  });

  it("uses progress Deep Research count for the active DAG node when attempts lag", async () => {
    globalThis.fetch = vi.fn((input: RequestInfo | URL) => {
      const url = String(input);
      if (url.endsWith(`/research-runs/${runId}/audit`)) {
        return Promise.resolve({
          ok: true,
          status: 200,
          json: () => Promise.resolve(makeAuditResponse({ run_id: runId, reviews: [] })),
        } as Response);
      }
      if (url.endsWith(`/research-runs/${runId}/attempts`)) {
        return Promise.resolve({
          ok: true,
          status: 200,
          json: () =>
            Promise.resolve([
              {
                run_no: 1,
                response_id: "resp_research_1",
                status: "completed",
                model: "o3-deep-research",
                prompt: "# 指示 1",
                report: "# Deep Research 1",
                citations: [],
                tool_calls_summary: [],
                error: null,
              },
            ]),
        } as Response);
      }
      if (url.endsWith(`/research-runs/${runId}/items`)) {
        return Promise.resolve({
          ok: true,
          status: 200,
          json: () => Promise.resolve({ run_id: runId, items: [] }),
        } as Response);
      }
      return Promise.resolve({
        ok: true,
        status: 200,
        json: () =>
          Promise.resolve({
            run_id: runId,
            status: "collecting",
            done_reason: null,
            needs_human_review: false,
            progress: {
              deep_research_runs: 2,
              targeted_rerun_runs: 1,
              full_rerun_runs: 0,
              llm_patch_runs: 0,
              verification_runs: 0,
              total_reviews: 1,
              latest_verdict: "needs_targeted_rerun",
              latest_score: 70,
              total_tool_calls: 42,
              estimated_cost_usd: 2.4,
            },
          }),
      } as Response);
    });

    render(<App />);

    const activeResearchNode = await screen.findByRole("button", {
      name: "Deep Research 2回目を選択",
    });
    await userEvent.click(activeResearchNode);
    await waitFor(() =>
      expect(screen.getByRole("button", { name: "Deep Research 2回目を選択" }))
        .toHaveAttribute("aria-pressed", "true"),
    );
    expect(within(activeResearchNode).getByText("実行中")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "結果を開く" }))
      .toHaveAttribute("href", `#/runs/${runId}/report?tab=research&attempt=2`);
  });

  it("shows child run lineage and starts the DAG with a fork source node", async () => {
    globalThis.fetch = vi.fn((input: RequestInfo | URL) => {
      const url = String(input);
      if (url.endsWith(`/research-runs/${runId}/lineage`)) {
        return Promise.resolve({
          ok: true,
          status: 200,
          json: () =>
            Promise.resolve({
              run_id: runId,
              lineage: {
                run_id: runId,
                root_run_id: "root-run",
                parent_run_id: "parent-run",
                forked_from_checkpoint_id: "chk-parent-1",
                fork_mode: "checkpoint",
                additional_prompt: "未調査の国内事例を追加してください。",
                source_snapshot_json: {
                  source_attempt_no: 1,
                  source_review_no: 2,
                },
                created_at: "2026-06-06T04:00:00.000Z",
              },
            }),
        } as Response);
      }
      if (url.endsWith(`/research-runs/${runId}/checkpoints?include_forks=true`)) {
        return Promise.resolve({
          ok: true,
          status: 200,
          json: () => Promise.resolve({ run_id: runId, checkpoints: [] }),
        } as Response);
      }
      if (url.endsWith(`/research-runs/${runId}/audit`)) {
        return Promise.resolve({
          ok: true,
          status: 200,
          json: () => Promise.resolve(makeAuditResponse({ run_id: runId, reviews: [] })),
        } as Response);
      }
      if (url.endsWith(`/research-runs/${runId}/attempts`)) {
        return Promise.resolve({
          ok: true,
          status: 200,
          json: () =>
            Promise.resolve([
              {
                run_no: 1,
                response_id: "resp_child_1",
                status: "running",
                model: "o3-deep-research",
                prompt: "# child 指示",
                report: "",
                citations: [],
                tool_calls_summary: [],
                error: null,
              },
            ]),
        } as Response);
      }
      if (url.endsWith(`/research-runs/${runId}/items`)) {
        return Promise.resolve({
          ok: true,
          status: 200,
          json: () => Promise.resolve({ run_id: runId, items: [] }),
        } as Response);
      }
      return Promise.resolve({
        ok: true,
        status: 200,
        json: () =>
          Promise.resolve({
            run_id: runId,
            status: "collecting",
            done_reason: null,
            needs_human_review: false,
            progress: {
              deep_research_runs: 1,
              targeted_rerun_runs: 0,
              full_rerun_runs: 0,
              llm_patch_runs: 0,
              verification_runs: 0,
              total_reviews: 0,
              latest_verdict: null,
              latest_score: null,
              total_tool_calls: 2,
              estimated_cost_usd: 0.2,
            },
          }),
      } as Response);
    });

    render(<App />);

    expect(await screen.findByText("checkpointからフォークされたrunです")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "parent-run" }))
      .toHaveAttribute("href", "#/runs/parent-run");
    expect(screen.getByText("未調査の国内事例を追加してください。")).toBeInTheDocument();
    expect(screen.getByText("Attempt 1 Review 2")).toBeInTheDocument();

    const dag = screen.getByRole("region", { name: "具体的な実行フロー" });
    const nodeTitles = within(dag)
      .getAllByRole("heading", { level: 3 })
      .map((heading) => heading.textContent);
    expect(nodeTitles.slice(0, 2)).toEqual(["フォーク元", "Deep Research 1回目"]);
  });

  it("resets DAG node selection when the run id changes", async () => {
    const nextRunId = "run-reset-target";
    localStorage.setItem(
      "dro.trackedRuns",
      JSON.stringify([
        {
          run_id: runId,
          title: "選択リセット元",
          created_at: new Date().toISOString(),
          last_status: "completed",
        },
        {
          run_id: nextRunId,
          title: "選択リセット先",
          created_at: new Date().toISOString(),
          last_status: "completed",
        },
      ]),
    );
    globalThis.fetch = vi.fn((input: RequestInfo | URL) => {
      const url = String(input);
      const currentRunId = url.includes(`/research-runs/${nextRunId}`) ? nextRunId : runId;
      if (url.endsWith(`/research-runs/${currentRunId}/checkpoints?include_forks=true`)) {
        return Promise.resolve({
          ok: true,
          status: 200,
          json: () =>
            Promise.resolve({
              run_id: currentRunId,
              checkpoints:
                currentRunId === nextRunId
                  ? [makeCheckpoint({ run_id: nextRunId })]
                  : [],
            }),
        } as Response);
      }
      if (url.endsWith(`/research-runs/${currentRunId}/lineage`)) {
        return Promise.resolve({
          ok: true,
          status: 200,
          json: () => Promise.resolve({ run_id: currentRunId, lineage: null }),
        } as Response);
      }
      if (url.endsWith(`/research-runs/${currentRunId}/audit`)) {
        return Promise.resolve({
          ok: true,
          status: 200,
          json: () => Promise.resolve(makeAuditResponse({ run_id: currentRunId, reviews: [] })),
        } as Response);
      }
      if (url.endsWith(`/research-runs/${currentRunId}/attempts`)) {
        return Promise.resolve({
          ok: true,
          status: 200,
          json: () => Promise.resolve([]),
        } as Response);
      }
      if (url.endsWith(`/research-runs/${currentRunId}/items`)) {
        return Promise.resolve({
          ok: true,
          status: 200,
          json: () => Promise.resolve({ run_id: currentRunId, items: [] }),
        } as Response);
      }
      return Promise.resolve({
        ok: true,
        status: 200,
        json: () =>
          Promise.resolve({
            run_id: currentRunId,
            status: "completed",
            done_reason: "passed_review",
            needs_human_review: false,
            progress: {
              deep_research_runs: 1,
              targeted_rerun_runs: 0,
              full_rerun_runs: 0,
              llm_patch_runs: 0,
              verification_runs: 0,
              total_reviews: 0,
              latest_verdict: null,
              latest_score: null,
              total_tool_calls: 0,
              estimated_cost_usd: 0,
            },
          }),
      } as Response);
    });

    render(<App />);

    const finalNode = await screen.findByRole("button", { name: "最終レポートを選択" });
    await userEvent.click(finalNode);
    expect(finalNode).toHaveAttribute("aria-pressed", "true");

    act(() => {
      window.location.hash = `#/runs/${nextRunId}`;
      window.dispatchEvent(new Event("hashchange"));
    });

    expect(await screen.findByText(nextRunId)).toBeInTheDocument();
    expect(await screen.findByRole("button", { name: "ここからフォーク" })).toBeEnabled();
  });

  it("matches follow-up checkpoints by exact anchor before guarded fallback", async () => {
    const exactFollowUpCheckpoint = makeCheckpoint({
      checkpoint_id: "chk-followup-exact",
      run_id: runId,
      checkpoint_no: 2,
      kind: "llm_patch_applied",
      node_anchor: "followup-2",
      source_attempt_no: null,
      source_review_no: 1,
    });
    const legacyFollowUpCheckpoint = makeCheckpoint({
      checkpoint_id: "chk-followup-legacy",
      run_id: runId,
      checkpoint_no: 3,
      kind: "llm_patch_applied",
      node_anchor: "legacy-patch",
      source_attempt_no: null,
      source_review_no: null,
    });
    globalThis.fetch = vi.fn((input: RequestInfo | URL) => {
      const url = String(input);
      if (url.endsWith(`/research-runs/${runId}/checkpoints?include_forks=true`)) {
        return Promise.resolve({
          ok: true,
          status: 200,
          json: () =>
            Promise.resolve({
              run_id: runId,
              checkpoints: [legacyFollowUpCheckpoint, exactFollowUpCheckpoint],
            }),
        } as Response);
      }
      if (url.endsWith(`/research-runs/${runId}/lineage`)) {
        return Promise.resolve({
          ok: true,
          status: 200,
          json: () => Promise.resolve({ run_id: runId, lineage: null }),
        } as Response);
      }
      if (url.endsWith(`/research-runs/${runId}/audit`)) {
        return Promise.resolve({
          ok: true,
          status: 200,
          json: () =>
            Promise.resolve(
              makeAuditResponse({
                run_id: runId,
                reviews: [
                  makeReviewRecord({
                    review_no: 1,
                    verdict: "needs_llm_patch",
                    recommended_route: "needs_llm_patch",
                  }),
                  makeReviewRecord({
                    review_no: 2,
                    verdict: "needs_llm_patch",
                    recommended_route: "needs_llm_patch",
                    reviewer_response_id: "resp_review_2",
                  }),
                ],
                history: [
                  { step: "route_after_review", total_reviews: 1, route: "llm_patch" },
                  { step: "llm_patch" },
                  { step: "route_after_review", total_reviews: 2, route: "llm_patch" },
                  { step: "llm_patch" },
                ],
              }),
            ),
        } as Response);
      }
      if (url.endsWith(`/research-runs/${runId}/attempts`)) {
        return Promise.resolve({
          ok: true,
          status: 200,
          json: () => Promise.resolve([]),
        } as Response);
      }
      if (url.endsWith(`/research-runs/${runId}/items`)) {
        return Promise.resolve({
          ok: true,
          status: 200,
          json: () => Promise.resolve({ run_id: runId, items: [] }),
        } as Response);
      }
      return Promise.resolve({
        ok: true,
        status: 200,
        json: () =>
          Promise.resolve({
            run_id: runId,
            status: "completed",
            done_reason: "passed_review",
            needs_human_review: false,
            progress: {
              deep_research_runs: 1,
              targeted_rerun_runs: 0,
              full_rerun_runs: 0,
              llm_patch_runs: 2,
              verification_runs: 0,
              total_reviews: 2,
              latest_verdict: "needs_llm_patch",
              latest_score: 70,
              total_tool_calls: 0,
              estimated_cost_usd: 0,
            },
          }),
      } as Response);
    });

    render(<App />);

    const firstPatch = await screen.findByRole("button", {
      name: "LLMパッチ 1回目を選択",
    });
    const secondPatch = await screen.findByRole("button", {
      name: "LLMパッチ 2回目を選択",
    });
    expect(within(firstPatch).queryByText("保存済み")).not.toBeInTheDocument();
    expect(within(secondPatch).getByText("保存済み")).toBeInTheDocument();
  });
});

describe("ReportViewer (SCR-5)", () => {
  const runId = "run-report-back-test";

  function makeAttempt(
    runNo: number,
    report: string,
    status = "completed",
    overrides: Partial<ResearchAttempt> = {},
  ) {
    return {
      run_no: runNo,
      response_id: `resp_collect_${runNo}`,
      status,
      model: "o3-deep-research",
      prompt: `# 指示 ${runNo}`,
      report,
      citations: [],
      tool_calls_summary: [],
      error: null,
      ...overrides,
    };
  }

  function capturePollingTimers() {
    const realSetTimeout = globalThis.setTimeout;
    const pending: Array<() => void> = [];
    vi.spyOn(globalThis, "setTimeout").mockImplementation(
      ((handler: TimerHandler, timeout?: number, ...args: unknown[]) => {
        if (timeout === 30_000) {
          pending.push(() => {
            if (typeof handler === "function") {
              handler(...args);
            }
          });
          return 0 as unknown as ReturnType<typeof setTimeout>;
        }
        return realSetTimeout(handler, timeout, ...args);
      }) as typeof setTimeout,
    );

    return async function flushPollingTimers() {
      const callbacks = pending.splice(0);
      await act(async () => {
        callbacks.forEach((callback) => callback());
        await Promise.resolve();
      });
    };
  }

  beforeEach(() => {
    window.location.hash = `#/runs/${runId}/report`;
  });

  it("returns to the run monitor from the report screen", async () => {
    globalThis.fetch = vi.fn((input: RequestInfo | URL) => {
      const url = String(input);
      if (url.endsWith(`/research-runs/${runId}/citations`)) {
        return Promise.resolve({
          ok: true,
          status: 200,
          json: () => Promise.resolve([]),
        } as Response);
      }
      if (url.endsWith(`/research-runs/${runId}/attempts`)) {
        return Promise.resolve({
          ok: true,
          status: 200,
          json: () => Promise.resolve([]),
        } as Response);
      }
      return Promise.resolve({
        ok: true,
        status: 200,
        json: () =>
          Promise.resolve({
            run_id: runId,
            status: "waiting_deep_research",
            final_report: null,
            report: null,
            warnings: [],
          }),
      } as Response);
    });

    render(<App />);

    await userEvent.click(await screen.findByRole("link", { name: "Runへ戻る" }));

    expect(window.location.hash).toBe(`#/runs/${runId}`);
  });

  it("labels manual upload attempts in the report history", async () => {
    globalThis.fetch = vi.fn((input: RequestInfo | URL) => {
      const url = String(input);
      if (url.endsWith(`/research-runs/${runId}/citations`)) {
        return Promise.resolve({
          ok: true,
          status: 200,
          json: () => Promise.resolve([]),
        } as Response);
      }
      if (url.endsWith(`/research-runs/${runId}/attempts`)) {
        return Promise.resolve({
          ok: true,
          status: 200,
          json: () =>
            Promise.resolve([
              makeAttempt(1, "# Imported report", "completed", {
                response_id: null,
                model: "chatgpt-deep-research-manual",
                source: "manual_upload",
              }),
            ]),
        } as Response);
      }
      return Promise.resolve({
        ok: true,
        status: 200,
        json: () =>
          Promise.resolve({
            run_id: runId,
            status: "completed",
            final_report: null,
            report: null,
            warnings: [],
          }),
      } as Response);
    });

    render(<App />);

    await userEvent.click(
      await screen.findByRole("button", { name: /1回目 手動取り込み/ }),
    );

    expect(screen.getByRole("button", { name: /1回目 手動取り込み/ })).toBeInTheDocument();
    expect(screen.getByText("Source")).toBeInTheDocument();
    expect(screen.getByText("手動取り込み")).toBeInTheDocument();
  });

  it("labels manual ChatGPT rerun attempts in the report history", async () => {
    globalThis.fetch = vi.fn((input: RequestInfo | URL) => {
      const url = String(input);
      if (url.endsWith(`/research-runs/${runId}/citations`)) {
        return Promise.resolve({
          ok: true,
          status: 200,
          json: () => Promise.resolve([]),
        } as Response);
      }
      if (url.endsWith(`/research-runs/${runId}/attempts`)) {
        return Promise.resolve({
          ok: true,
          status: 200,
          json: () =>
            Promise.resolve([
              makeAttempt(2, "# Manual rerun delta", "completed", {
                response_id: null,
                model: "chatgpt-deep-research-manual",
                source: "manual_chatgpt_rerun",
              }),
            ]),
        } as Response);
      }
      return Promise.resolve({
        ok: true,
        status: 200,
        json: () =>
          Promise.resolve({
            run_id: runId,
            status: "completed",
            final_report: null,
            report: null,
            warnings: [],
          }),
      } as Response);
    });
    window.location.hash = `#/runs/${runId}/report?attempt=2`;

    render(<App />);

    expect(await screen.findByRole("button", { name: /2回目 ChatGPT手動rerun/ }))
      .toBeInTheDocument();
    expect(screen.getByText("Source")).toBeInTheDocument();
    expect(screen.getByText("ChatGPT手動rerun")).toBeInTheDocument();
  });

  it("defaults to the final report without mixed tabs or PDF printing", async () => {
    globalThis.fetch = vi.fn((input: RequestInfo | URL) => {
      const url = String(input);
      if (url.endsWith(`/research-runs/${runId}/citations`)) {
        return Promise.resolve({
          ok: true,
          status: 200,
          json: () => Promise.resolve([]),
        } as Response);
      }
      if (url.endsWith(`/research-runs/${runId}/attempts`)) {
        return Promise.resolve({
          ok: true,
          status: 200,
          json: () =>
            Promise.resolve([
              {
                run_no: 1,
                response_id: "resp_collect_1",
                status: "completed",
                model: "o3-deep-research",
                prompt: "# 指示 1",
                report: "# 1回目のDeep Research出力",
                citations: [],
                tool_calls_summary: [],
                error: null,
              },
              {
                run_no: 2,
                response_id: "resp_collect_2",
                status: "completed",
                model: "o3-deep-research",
                prompt: "# 指示 2",
                report: "# 2回目のDeep Research出力",
                citations: [],
                tool_calls_summary: [],
                error: null,
              },
            ]),
        } as Response);
      }
      return Promise.resolve({
        ok: true,
        status: 200,
        json: () =>
          Promise.resolve({
            run_id: runId,
            status: "completed",
            final_report: "# 最新版レポート",
            report: "# 下書き",
            warnings: [],
          }),
      } as Response);
    });

    render(<App />);

    expect(await screen.findByRole("heading", { name: "レポート履歴" })).toBeInTheDocument();
    expect(await screen.findByText("最新版レポート")).toBeInTheDocument();
    expect(screen.queryByText("下書き")).not.toBeInTheDocument();
    expect(screen.queryByText("2回目のDeep Research出力")).not.toBeInTheDocument();
    expect(screen.queryByRole("tab", { name: "最新版" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "PDF 印刷" })).not.toBeInTheDocument();
    expect(screen.queryByText("レビュー側の内容です")).not.toBeInTheDocument();
    expect(screen.queryByLabelText("品質スコア")).not.toBeInTheDocument();
  });

  it("renders only http and https citation URLs as links", async () => {
    globalThis.fetch = vi.fn((input: RequestInfo | URL) => {
      const url = String(input);
      if (url.endsWith(`/research-runs/${runId}/citations`)) {
        return Promise.resolve({
          ok: true,
          status: 200,
          json: () =>
            Promise.resolve([
              {
                title: "Allowed source",
                url: "https://example.com/source",
                source_type: "web",
              },
              {
                title: "Script source",
                url: "javascript:alert(1)",
                source_type: "web",
              },
              {
                title: "Data source",
                url: "data:text/html,hello",
                source_type: "web",
              },
              {
                title: "Broken source",
                url: "not a url",
                source_type: "web",
              },
            ]),
        } as Response);
      }
      if (url.endsWith(`/research-runs/${runId}/attempts`)) {
        return Promise.resolve({
          ok: true,
          status: 200,
          json: () => Promise.resolve([]),
        } as Response);
      }
      return Promise.resolve({
        ok: true,
        status: 200,
        json: () =>
          Promise.resolve({
            run_id: runId,
            status: "completed",
            final_report: "# 引用リンク確認",
            report: null,
            warnings: [],
          }),
      } as Response);
    });

    render(<App />);

    expect(await screen.findByText("引用リンク確認")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Allowed source を開く" }))
      .toHaveAttribute("href", "https://example.com/source");
    expect(screen.getByText("javascript:alert(1)")).toBeInTheDocument();
    expect(screen.getByText("data:text/html,hello")).toBeInTheDocument();
    expect(screen.getByText("not a url")).toBeInTheDocument();
    expect(screen.queryByRole("link", { name: "Script source を開く" }))
      .not.toBeInTheDocument();
    expect(screen.queryByRole("link", { name: "Data source を開く" }))
      .not.toBeInTheDocument();
    expect(screen.queryByRole("link", { name: "Broken source を開く" }))
      .not.toBeInTheDocument();
  });

  it("scrolls to the original citation index while the source list is filtered", async () => {
    const scrollIntoView = vi.fn();
    Object.defineProperty(HTMLElement.prototype, "scrollIntoView", {
      configurable: true,
      value: scrollIntoView,
    });

    globalThis.fetch = vi.fn((input: RequestInfo | URL) => {
      const url = String(input);
      if (url.endsWith(`/research-runs/${runId}/citations`)) {
        return Promise.resolve({
          ok: true,
          status: 200,
          json: () =>
            Promise.resolve([
              {
                title: "First source",
                url: "https://example.com/first",
                source_type: "blog",
              },
              {
                title: "Second source",
                url: "https://example.com/second",
                source_type: "news",
              },
            ]),
        } as Response);
      }
      if (url.endsWith(`/research-runs/${runId}/attempts`)) {
        return Promise.resolve({
          ok: true,
          status: 200,
          json: () => Promise.resolve([]),
        } as Response);
      }
      return Promise.resolve({
        ok: true,
        status: 200,
        json: () =>
          Promise.resolve({
            run_id: runId,
            status: "completed",
            final_report: "2番目の根拠です [2]",
            report: null,
            warnings: [],
          }),
      } as Response);
    });

    render(<App />);

    const citationButton = await screen.findByRole("button", {
      name: "引用 2 へジャンプ",
    });
    await userEvent.click(screen.getByRole("tab", { name: "news (1)" }));

    const sources = screen.getByRole("complementary", {
      name: "最終レポートの引用ソース",
    });
    expect(within(sources).getByLabelText("引用 2")).toHaveTextContent("[2]");
    expect(within(sources).queryByLabelText("引用 1")).not.toBeInTheDocument();

    await userEvent.click(citationButton);

    expect(scrollIntoView).toHaveBeenCalledWith({ behavior: "smooth", block: "start" });
  });

  it("downloads the fallback report as markdown by default", async () => {
    const createObjectURL = vi.fn((blob: Blob) => {
      void blob;
      return "blob:report-markdown";
    });
    const revokeObjectURL = vi.fn();
    Object.defineProperty(URL, "createObjectURL", {
      configurable: true,
      value: createObjectURL,
    });
    Object.defineProperty(URL, "revokeObjectURL", {
      configurable: true,
      value: revokeObjectURL,
    });
    let clickedHref: string | null = null;
    let clickedDownload: string | null = null;
    vi.spyOn(HTMLAnchorElement.prototype, "click").mockImplementation(function (
      this: HTMLAnchorElement,
    ) {
      clickedHref = this.href;
      clickedDownload = this.download;
    });

    globalThis.fetch = vi.fn((input: RequestInfo | URL) => {
      const url = String(input);
      if (url.endsWith(`/research-runs/${runId}/citations`)) {
        return Promise.resolve({
          ok: true,
          status: 200,
          json: () => Promise.resolve([]),
        } as Response);
      }
      if (url.endsWith(`/research-runs/${runId}/attempts`)) {
        return Promise.resolve({
          ok: true,
          status: 200,
          json: () => Promise.resolve([makeAttempt(1, "# ダウンロード対象")]),
        } as Response);
      }
      return Promise.resolve({
        ok: true,
        status: 200,
        json: () =>
          Promise.resolve({
            run_id: runId,
            status: "completed",
            final_report: null,
            report: "# 下書き",
            warnings: [],
          }),
      } as Response);
    });

    render(<App />);

    await screen.findByText("下書き");
    await userEvent.click(screen.getByRole("button", { name: "MD ダウンロード" }));

    expect(createObjectURL).toHaveBeenCalledTimes(1);
    const downloadedBlob = createObjectURL.mock.calls[0]?.[0];
    expect(downloadedBlob).toBeInstanceOf(Blob);
    await expect(readBlobText(downloadedBlob)).resolves.toBe("# 下書き");
    expect(clickedHref).toBe("blob:report-markdown");
    expect(clickedDownload).toBe(`${runId}-report.md`);
  });

  it("opens the requested Deep Research attempt from the URL", async () => {
    window.location.hash = `#/runs/${runId}/report?tab=research&attempt=1`;
    globalThis.fetch = vi.fn((input: RequestInfo | URL) => {
      const url = String(input);
      if (url.endsWith(`/research-runs/${runId}/citations`)) {
        return Promise.resolve({
          ok: true,
          status: 200,
          json: () => Promise.resolve([]),
        } as Response);
      }
      if (url.endsWith(`/research-runs/${runId}/attempts`)) {
        return Promise.resolve({
          ok: true,
          status: 200,
          json: () =>
            Promise.resolve([
              makeAttempt(1, "# 1回目のDeep Research出力"),
              makeAttempt(2, "# 2回目のDeep Research出力"),
            ]),
        } as Response);
      }
      return Promise.resolve({
        ok: true,
        status: 200,
        json: () =>
          Promise.resolve({
            run_id: runId,
            status: "completed",
            final_report: "# 最新版レポート",
            report: "# 下書き",
            warnings: [],
          }),
      } as Response);
    });

    render(<App />);

    expect(await screen.findByText("1回目のDeep Research出力")).toBeInTheDocument();
    expect(screen.queryByText("2回目のDeep Research出力")).not.toBeInTheDocument();
  });

  it("downloads the requested Deep Research attempt as markdown", async () => {
    window.location.hash = `#/runs/${runId}/report?tab=research&attempt=1`;
    const createObjectURL = vi.fn((blob: Blob) => {
      void blob;
      return "blob:report-markdown";
    });
    Object.defineProperty(URL, "createObjectURL", {
      configurable: true,
      value: createObjectURL,
    });
    Object.defineProperty(URL, "revokeObjectURL", {
      configurable: true,
      value: vi.fn(),
    });
    let clickedHref: string | null = null;
    let clickedDownload: string | null = null;
    vi.spyOn(HTMLAnchorElement.prototype, "click").mockImplementation(function (
      this: HTMLAnchorElement,
    ) {
      clickedHref = this.href;
      clickedDownload = this.download;
    });

    globalThis.fetch = vi.fn((input: RequestInfo | URL) => {
      const url = String(input);
      if (url.endsWith(`/research-runs/${runId}/citations`)) {
        return Promise.resolve({
          ok: true,
          status: 200,
          json: () => Promise.resolve([]),
        } as Response);
      }
      if (url.endsWith(`/research-runs/${runId}/attempts`)) {
        return Promise.resolve({
          ok: true,
          status: 200,
          json: () => Promise.resolve([makeAttempt(1, "# ダウンロード対象")]),
        } as Response);
      }
      return Promise.resolve({
        ok: true,
        status: 200,
        json: () =>
          Promise.resolve({
            run_id: runId,
            status: "completed",
            final_report: "# 最新版レポート",
            report: "# 下書き",
            warnings: [],
          }),
      } as Response);
    });

    render(<App />);

    await screen.findByText("ダウンロード対象");
    await userEvent.click(screen.getByRole("button", { name: "MD ダウンロード" }));

    expect(createObjectURL).toHaveBeenCalledTimes(1);
    const downloadedBlob = createObjectURL.mock.calls[0]?.[0];
    expect(downloadedBlob).toBeInstanceOf(Blob);
    await expect(readBlobText(downloadedBlob)).resolves.toBe("# ダウンロード対象");
    expect(clickedHref).toBe("blob:report-markdown");
    expect(clickedDownload).toBe(`${runId}-deep-research-1.md`);
  });

  it("keeps the report tab URL on the final report until an attempt is selected", async () => {
    window.location.hash = `#/runs/${runId}/report?tab=research`;
    globalThis.fetch = vi.fn((input: RequestInfo | URL) => {
      const url = String(input);
      if (url.endsWith(`/research-runs/${runId}/citations`)) {
        return Promise.resolve({
          ok: true,
          status: 200,
          json: () => Promise.resolve([]),
        } as Response);
      }
      if (url.endsWith(`/research-runs/${runId}/attempts`)) {
        return Promise.resolve({
          ok: true,
          status: 200,
          json: () =>
            Promise.resolve([
              {
                run_no: 1,
                response_id: "resp_submit_1",
                status: "queued",
                model: "o3-deep-research",
                prompt: "# 指示 1",
                report: "",
                citations: [],
                tool_calls_summary: [],
                error: null,
              },
              {
                run_no: 1,
                response_id: "resp_collect_1",
                status: "completed",
                model: "o3-deep-research",
                prompt: "# 指示 1",
                report: "# 1回目のDeep Research出力",
                citations: [],
                tool_calls_summary: [],
                error: null,
              },
              {
                run_no: 2,
                response_id: "resp_collect_2",
                status: "completed",
                model: "o3-deep-research",
                prompt: "# 改善指示 2",
                report: "# 2回目のDeep Research出力",
                citations: [],
                tool_calls_summary: [],
                error: null,
              },
            ]),
        } as Response);
      }
      return Promise.resolve({
        ok: true,
        status: 200,
        json: () =>
          Promise.resolve({
            run_id: runId,
            status: "completed",
            final_report: "# 最新版",
            report: "# 最新版",
            warnings: [],
          }),
      } as Response);
    });

    render(<App />);

    expect(await screen.findByRole("heading", { name: "レポート履歴" })).toBeInTheDocument();
    expect(await screen.findByText("最新版")).toBeInTheDocument();
    expect(screen.queryByText("2回目のDeep Research出力")).not.toBeInTheDocument();

    await userEvent.click(screen.getByRole("button", { name: /1回目/ }));

    expect(window.location.hash).toBe(`#/runs/${runId}/report?tab=research&attempt=1`);
    expect(await screen.findByText("1回目のDeep Research出力")).toBeInTheDocument();
  });

  it("opens audit reviews from the removed review tab URL", async () => {
    window.location.hash = `#/runs/${runId}/report?tab=reviews`;
    globalThis.fetch = vi.fn((input: RequestInfo | URL) => {
      const url = String(input);
      if (url.endsWith(`/research-runs/${runId}/audit`)) {
        return Promise.resolve({
          ok: true,
          status: 200,
          json: () =>
            Promise.resolve(
              makeAuditResponse({
                run_id: runId,
                reviews: [
                  makeReviewRecord({
                    verdict: "needs_targeted_rerun",
                    recommended_route: "needs_targeted_rerun",
                    goal_achieved: false,
                    score: 58,
                    rationale: "目的達成度が不足しています",
                    gaps: ["想定出力の比較表が不足"],
                    factuality_concerns: ["数値の検証が不足"],
                    source_quality_concerns: ["一次情報が不足"],
                    item_assessments: [
                      makeItemAssessment({
                        missing_evidence: ["想定出力の比較表"],
                        rationale: "比較表が不足しています",
                      }),
                    ],
                    next_instructions: "次回は公式資料を優先して比較表を作る",
                    reviewer_confidence: 82,
                    high_risk_flags: ["判断不能な数値"],
                  }),
                ],
              }),
            ),
        } as Response);
      }
      return Promise.resolve({
        ok: true,
        status: 200,
        json: () => Promise.resolve({}),
      } as Response);
    });

    render(<App />);

    expect(await screen.findByRole("heading", { name: "監査ログ" })).toBeInTheDocument();
    expect(await screen.findByRole("tab", { name: "レビュー" }))
      .toHaveAttribute("aria-selected", "true");
    expect(screen.getByText("目的達成度が不足しています")).toBeInTheDocument();
    await waitFor(() =>
      expect(window.location.hash).toBe(`#/runs/${runId}/audit?tab=reviews`),
    );
  });

  it("does not fall back to Deep Research attempts on the default report route", async () => {
    globalThis.fetch = vi.fn((input: RequestInfo | URL) => {
      const url = String(input);
      if (url.endsWith(`/research-runs/${runId}/citations`)) {
        return Promise.resolve({
          ok: true,
          status: 200,
          json: () => Promise.resolve([]),
        } as Response);
      }
      if (url.endsWith(`/research-runs/${runId}/attempts`)) {
        return Promise.resolve({
          ok: true,
          status: 200,
          json: () => Promise.resolve([makeAttempt(1, "# 1回目のDeep Research出力")]),
        } as Response);
      }
      return Promise.resolve({
        ok: true,
        status: 200,
        json: () =>
          Promise.resolve({
            run_id: runId,
            status: "collecting",
            final_report: null,
            report: null,
            warnings: [],
          }),
      } as Response);
    });

    render(<App />);

    expect(await screen.findByText("レポートなし")).toBeInTheDocument();
    expect(screen.queryByText("1回目のDeep Research出力")).not.toBeInTheDocument();
  });

  it("does not substitute a different attempt when the requested Deep Research output is not synced yet", async () => {
    window.location.hash = `#/runs/${runId}/report?tab=research&attempt=2`;
    globalThis.fetch = vi.fn((input: RequestInfo | URL) => {
      const url = String(input);
      if (url.endsWith(`/research-runs/${runId}/citations`)) {
        return Promise.resolve({
          ok: true,
          status: 200,
          json: () => Promise.resolve([]),
        } as Response);
      }
      if (url.endsWith(`/research-runs/${runId}/attempts`)) {
        return Promise.resolve({
          ok: true,
          status: 200,
          json: () => Promise.resolve([makeAttempt(1, "# 1回目のDeep Research出力")]),
        } as Response);
      }
      return Promise.resolve({
        ok: true,
        status: 200,
        json: () =>
          Promise.resolve({
            run_id: runId,
            status: "collecting",
            final_report: "# 最終レポート",
            report: "# 下書き",
            warnings: [],
          }),
      } as Response);
    });

    render(<App />);

    expect(await screen.findByText("Deep Research 2回目の出力なし")).toBeInTheDocument();
    expect(screen.getByText("この試行はまだ取得中か、履歴がまだ同期されていません。"))
      .toBeInTheDocument();
    expect(screen.queryByText("1回目のDeep Research出力")).not.toBeInTheDocument();
    expect(screen.queryByText("最終レポート")).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "MD ダウンロード" })).toBeDisabled();
  });

  it("keeps an explicitly selected older Deep Research attempt while polling adds newer output", async () => {
    window.location.hash = `#/runs/${runId}/report?tab=research&attempt=1`;
    const flushPollingTimers = capturePollingTimers();
    let attemptsCalls = 0;
    globalThis.fetch = vi.fn((input: RequestInfo | URL) => {
      const url = String(input);
      if (url.endsWith(`/research-runs/${runId}/citations`)) {
        return Promise.resolve({
          ok: true,
          status: 200,
          json: () => Promise.resolve([]),
        } as Response);
      }
      if (url.endsWith(`/research-runs/${runId}/attempts`)) {
        attemptsCalls += 1;
        return Promise.resolve({
          ok: true,
          status: 200,
          json: () =>
            Promise.resolve(
              attemptsCalls === 1
                ? [
                    makeAttempt(1, "# 1回目のDeep Research出力"),
                    makeAttempt(2, "# 2回目のDeep Research出力"),
                  ]
                : [
                    makeAttempt(1, "# 1回目のDeep Research出力"),
                    makeAttempt(2, "# 2回目のDeep Research出力"),
                    makeAttempt(3, "# 3回目のDeep Research出力"),
                  ],
            ),
        } as Response);
      }
      return Promise.resolve({
        ok: true,
        status: 200,
        json: () =>
          Promise.resolve({
            run_id: runId,
            status: "collecting",
            final_report: null,
            report: null,
            warnings: [],
          }),
      } as Response);
    });

    render(<App />);

    expect(await screen.findByText("1回目のDeep Research出力")).toBeInTheDocument();
    expect(screen.queryByText("2回目のDeep Research出力")).not.toBeInTheDocument();

    await flushPollingTimers();

    expect(screen.getByText("1回目のDeep Research出力")).toBeInTheDocument();
    expect(screen.queryByText("3回目のDeep Research出力")).not.toBeInTheDocument();
  });

});

describe("AuditLog (SCR-6)", () => {
  const runId = "run-audit-test";

  it("labels manual upload attempts in the audit attempts table", async () => {
    window.location.hash = `#/runs/${runId}/audit`;
    globalThis.fetch = vi.fn().mockResolvedValue({
      ok: true,
      status: 200,
      json: () =>
        Promise.resolve(
          makeAuditResponse({
            run_id: runId,
            attempts: [
              {
                run_no: 1,
                response_id: null,
                status: "completed",
                model: "chatgpt-deep-research-manual",
                prompt: "# Manual prompt",
                report: "# Manual report",
                source: "manual_upload",
                citations: [],
                tool_calls_summary: [],
                error: null,
                created_at: "2026-06-06T01:02:03.000Z",
              },
            ],
          }),
        ),
    } as Response);

    render(<App />);

    expect(await screen.findByRole("tab", { name: "調査試行" }))
      .toHaveAttribute("aria-selected", "true");
    expect(screen.getByText("手動取り込み")).toBeInTheDocument();
    expect(screen.getByText("chatgpt-deep-research-manual")).toBeInTheDocument();
  });

  it("labels manual ChatGPT rerun attempts in the audit attempts table", async () => {
    window.location.hash = `#/runs/${runId}/audit`;
    globalThis.fetch = vi.fn().mockResolvedValue({
      ok: true,
      status: 200,
      json: () =>
        Promise.resolve(
          makeAuditResponse({
            run_id: runId,
            attempts: [
              {
                run_no: 2,
                response_id: null,
                status: "completed",
                model: "chatgpt-deep-research-manual",
                prompt: "# Manual rerun prompt",
                report: "# Manual rerun result",
                source: "manual_chatgpt_rerun",
                citations: [],
                tool_calls_summary: [],
                error: null,
                created_at: "2026-06-06T01:02:03.000Z",
              },
            ],
          }),
        ),
    } as Response);

    render(<App />);

    expect(await screen.findByRole("tab", { name: "調査試行" }))
      .toHaveAttribute("aria-selected", "true");
    expect(screen.getByText("ChatGPT手動rerun")).toBeInTheDocument();
    expect(screen.getByText("chatgpt-deep-research-manual")).toBeInTheDocument();
  });

  it("renders only http and https citation URLs as links in the audit log", async () => {
    window.location.hash = `#/runs/${runId}/audit?tab=citations`;
    globalThis.fetch = vi.fn().mockResolvedValue({
      ok: true,
      status: 200,
      json: () =>
        Promise.resolve(
          makeAuditResponse({
            run_id: runId,
            citations: [
              {
                title: "Safe audit source",
                url: "http://example.com/audit",
                source_type: "web",
              },
              {
                title: "Unsafe audit source",
                url: "javascript:alert(1)",
                source_type: "web",
              },
            ],
          }),
        ),
    } as Response);

    render(<App />);

    expect(await screen.findByRole("tab", { name: "引用" }))
      .toHaveAttribute("aria-selected", "true");
    expect(screen.getByRole("link", { name: "http://example.com/audit" }))
      .toHaveAttribute("href", "http://example.com/audit");
    expect(screen.getByText("javascript:alert(1)")).toBeInTheDocument();
    expect(screen.queryByRole("link", { name: "javascript:alert(1)" }))
      .not.toBeInTheDocument();
  });

  it("shows LLM calls in the audit log", async () => {
    window.location.hash = `#/runs/${runId}/audit?tab=llm-calls`;
    globalThis.fetch = vi.fn().mockResolvedValue({
      ok: true,
      status: 200,
      json: () =>
        Promise.resolve(
          makeAuditResponse({
            run_id: runId,
            llm_calls: [
              {
                step: "review",
                model: "reviewer-model",
                response_id: "resp_review_1",
                input_tokens: 1200,
                output_tokens: 300,
                tool_calls: 0,
                estimated_cost_usd: 0.02,
                created_at: "2026-06-06T01:02:03.000Z",
              },
              {
                step: "llm_finalize",
                model: "reviewer-model",
                response_id: "resp_llm_fix_1",
                input_tokens: 1400,
                output_tokens: 500,
                tool_calls: 1,
                estimated_cost_usd: 0.04,
                created_at: "2026-06-06T01:03:04.000Z",
              },
            ],
          }),
        ),
    } as Response);

    render(<App />);

    expect(await screen.findByRole("tab", { name: "LLMコール" }))
      .toHaveAttribute("aria-selected", "true");
    expect(screen.getByText("LLM Review")).toBeInTheDocument();
    expect(screen.getByText("LLM patch")).toBeInTheDocument();
    expect(screen.getByText("resp_llm_fix_1")).toBeInTheDocument();

    await userEvent.click(screen.getByRole("tab", { name: "引用" }));
    await waitFor(() =>
      expect(window.location.hash).toBe(`#/runs/${runId}/audit?tab=citations`),
    );
    expect(screen.getByRole("tab", { name: "引用" }))
      .toHaveAttribute("aria-selected", "true");

    await userEvent.click(screen.getByRole("tab", { name: "調査試行" }));
    await waitFor(() => expect(window.location.hash).toBe(`#/runs/${runId}/audit`));
    expect(screen.getByRole("tab", { name: "調査試行" }))
      .toHaveAttribute("aria-selected", "true");
  });

  it("opens a requested review from the audit URL", async () => {
    window.location.hash = `#/runs/${runId}/audit?tab=reviews&review=2`;
    globalThis.fetch = vi.fn().mockResolvedValue({
      ok: true,
      status: 200,
      json: () =>
        Promise.resolve(
          makeAuditResponse({
            run_id: runId,
            reviews: [
              makeReviewRecord({ review_no: 1, rationale: "1回目レビュー" }),
              makeReviewRecord({
                review_no: 2,
                rationale: "2回目レビューを開きます",
                reviewer_response_id: "resp_review_2",
              }),
            ],
          }),
        ),
    } as Response);

    render(<App />);

    expect(await screen.findByRole("tab", { name: "レビュー" }))
      .toHaveAttribute("aria-selected", "true");
    expect(screen.getByText("2回目レビューを開きます").closest(".audit-review-item"))
      .toHaveClass("audit-review-item--focused");
  });
});

describe("HumanReview (SCR-4)", () => {
  const runId = "run-hr-test";

  function makePendingManualRerun(
    overrides: Partial<NonNullable<HumanReviewPayload["pending_manual_rerun"]>> = {},
  ): NonNullable<HumanReviewPayload["pending_manual_rerun"]> {
    return {
      rerun_id: "RR-manual-1",
      scope: "targeted_gap_closure",
      expected_run_no: 2,
      prompt: "# Manual rerun prompt\nFind better official sources.",
      prompt_artifact_path: "prompts/rerun_prompt_002.txt",
      target_item_ids: ["RI-001"],
      expected_output_kind: "targeted_delta_sections",
      query_policy: { status: "allowed", safe_queries: [], blocked_reason: null },
      base_report_hash: "hash-base",
      created_at: "2026-06-06T01:02:03.000Z",
      ...overrides,
    };
  }

  function makePayload(
    allowedActions: string[],
    overrides: Partial<{
      latest_report: string;
      latest_review: null;
      reason: string;
      pending_manual_rerun: NonNullable<HumanReviewPayload["pending_manual_rerun"]>;
      suggested_rerun: NonNullable<HumanReviewPayload["suggested_rerun"]>;
      allowed_actions: string[];
      action_states: HumanReviewPayload["action_states"];
      route_summary: HumanReviewPayload["route_summary"];
    }> = {},
  ) {
    return {
      run_id: runId,
      reason: overrides.reason ?? "スコアが閾値以下です",
      latest_report: overrides.latest_report ?? "# テストレポート\n\nサンプル内容",
      latest_review: overrides.latest_review === null ? null : {
        review_no: 1,
        verdict: "human_review",
        recommended_route: "human_review",
        goal_achieved: false,
        score: 42,
        rationale: "要改善",
        gaps: ["データ不足"],
        factuality_concerns: [],
        source_quality_concerns: [],
        item_assessments: [
          makeItemAssessment({
            recommended_action: "human_review",
            rationale: "人間の判断が必要です",
          }),
        ],
        next_instructions: null,
        reviewer_confidence: 80,
        high_risk_flags: [],
        public_web_search_used: false,
      },
      allowed_actions: overrides.allowed_actions ?? allowedActions,
      action_states: overrides.action_states,
      route_summary: overrides.route_summary ?? null,
      audit_summary: {
        deep_research_runs: 1,
        targeted_rerun_runs: 2,
        full_rerun_runs: 1,
        llm_patch_runs: 1,
        verification_runs: 1,
        total_reviews: 3,
        no_progress_count: 1,
        total_tool_calls: 45,
        estimated_cost_usd: 1.23,
      },
      warnings: [],
      pending_manual_rerun: overrides.pending_manual_rerun ?? null,
      suggested_rerun: overrides.suggested_rerun ?? null,
    };
  }

  beforeEach(() => {
    window.location.hash = `#/runs/${runId}/review`;
  });

  it("shows the stop-reason banner", async () => {
    globalThis.fetch = vi.fn().mockResolvedValue({
      ok: true,
      status: 200,
      json: () => Promise.resolve(makePayload(["approve", "reject"])),
    } as Response);

    render(<App />);

    expect(await screen.findByText("スコアが閾値以下です")).toBeInTheDocument();
  });

  it("shows a suggested rerun prompt during human review", async () => {
    globalThis.fetch = vi.fn().mockResolvedValue({
      ok: true,
      status: 200,
      json: () =>
        Promise.resolve(
          makePayload(["request_full_rerun", "reject"], {
            reason: "review_route_needs_full_rerun",
            suggested_rerun: {
              scope: "full_rerun",
              expected_output_kind: "complete_replacement_report",
              expected_run_no: 2,
              prompt: "# Suggested rerun prompt\nRebuild the full report.",
              target_item_ids: ["RI-001", "RI-002"],
              query_policy: { status: "allowed", safe_queries: [], blocked_reason: null },
              base_report_hash: "hash-base",
            },
          }),
        ),
    } as Response);

    render(<App />);

    expect(await screen.findByText("Rerun向けプロンプト")).toBeInTheDocument();
    expect(screen.getByText("Deep Research 2回目")).toBeInTheDocument();
    expect(screen.getByText("RI-001, RI-002")).toBeInTheDocument();
    expect(screen.getByText(/Rebuild the full report/i)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "コピー" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: ".md ダウンロード" })).toBeInTheDocument();
  });

  it.each([
    {
      action: "request_manual_targeted_rerun",
      buttonName: /ChatGPTで部分補強/i,
      scope: "targeted_gap_closure",
      expectedOutputKind: "targeted_delta_sections",
      reason: "review_route_needs_targeted_rerun",
      uploadStep: "既存レポートへ追加する差分セクションをアップロードする",
    },
    {
      action: "request_manual_full_rerun",
      buttonName: /ChatGPTで全面作り直し/i,
      scope: "full_rerun",
      expectedOutputKind: "complete_replacement_report",
      reason: "review_route_needs_full_rerun",
      uploadStep: "完成版レポート全文をアップロードする",
    },
  ] as const)(
    "refetches HumanReview after $action so the pending manual rerun flow appears",
    async ({
      action,
      buttonName,
      scope,
      expectedOutputKind,
      reason,
      uploadStep,
    }) => {
      let payloadFetches = 0;
      const fetchMock = vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
        const url = String(input);
        if (url.endsWith(`/research-runs/${runId}/resume`)) {
          expect(init?.method).toBe("POST");
          expect(JSON.parse(String(init?.body))).toEqual({
            action,
            comment: null,
          });
          return Promise.resolve(
            jsonResponse({
              run_id: runId,
              status: "needs_human_review",
              done_reason: "manual_chatgpt_rerun_pending",
              needs_human_review: true,
            }),
          );
        }
        payloadFetches += 1;
        if (payloadFetches > 1) {
          return Promise.resolve(
            jsonResponse(
              makePayload([], {
                reason: "manual_chatgpt_rerun_pending",
                pending_manual_rerun: makePendingManualRerun({
                  rerun_id: "RR-manual-after-request",
                  scope,
                  expected_output_kind: expectedOutputKind,
                  prompt: "# Pending manual rerun prompt\nRun this in ChatGPT.",
                }),
                allowed_actions: [],
              }),
            ),
          );
        }
        return Promise.resolve(
          jsonResponse(
            makePayload([action, "reject"], {
              reason,
              suggested_rerun: {
                scope,
                expected_output_kind: expectedOutputKind,
                expected_run_no: 2,
                prompt: "# Suggested rerun prompt\nRebuild the full report.",
                target_item_ids: ["RI-001"],
                query_policy: { status: "allowed", safe_queries: [], blocked_reason: null },
                base_report_hash: "hash-base",
              },
            }),
          ),
        );
      });
      globalThis.fetch = fetchMock;

      render(<App />);

      await userEvent.click(
        await screen.findByRole("button", { name: buttonName }),
      );

      await waitFor(() =>
        expect(fetchMock).toHaveBeenCalledWith(
          `http://localhost:8000/research-runs/${runId}/resume`,
          expect.objectContaining({ method: "POST" }),
        ),
      );
      await waitFor(() => expect(payloadFetches).toBe(2));
      expect(window.location.hash).toBe(`#/runs/${runId}/review`);
      expect(await screen.findByText("ChatGPT手動rerun")).toBeInTheDocument();
      expect(screen.getByText("プロンプトをコピーまたはダウンロードする")).toBeInTheDocument();
      expect(screen.getByText("ChatGPTのDeep Researchで実行する")).toBeInTheDocument();
      expect(screen.getByText(uploadStep)).toBeInTheDocument();
      expect(screen.getByText(/Run this in ChatGPT/i)).toBeInTheDocument();
    },
  );

  it("disables actions not in allowed_actions", async () => {
    globalThis.fetch = vi.fn().mockResolvedValue({
      ok: true,
      status: 200,
      json: () => Promise.resolve(makePayload(["approve", "reject"])),
    } as Response);

    render(<App />);

    // Wait for payload to load (buttons are rendered after fetch)
    const llmPatchBtn = await screen.findByRole("button", { name: /LLM patch/i });
    const targetedRerunBtn = screen.getByRole("button", { name: /APIで部分再調査/i });
    const fullRerunBtn = screen.getByRole("button", { name: /APIで全面再調査/i });

    expect(llmPatchBtn).toBeDisabled();
    expect(targetedRerunBtn).toBeDisabled();
    expect(fullRerunBtn).toBeDisabled();
  });

  it("shows action state disabled reasons and route stop context", async () => {
    globalThis.fetch = vi.fn().mockResolvedValue({
      ok: true,
      status: 200,
      json: () =>
        Promise.resolve(
          makePayload(["request_manual_full_rerun", "reject"], {
            reason: "max_full_rerun_runs_reached",
            latest_review: null,
            action_states: [
              {
                action: "request_full_rerun",
                allowed: false,
                blocked_reason: "max_full_rerun_runs_reached",
              },
              {
                action: "request_manual_full_rerun",
                allowed: true,
                blocked_reason: null,
              },
            ],
            route_summary: {
              candidate_route: "full_rerun_submit",
              selected_route: "human_review",
              blocked_reason: "max_full_rerun_runs_reached",
              dominant_actions: ["full_rerun"],
              latest_review_no: 2,
              latest_verdict: "needs_full_rerun",
            },
          }),
        ),
    } as Response);

    render(<App />);

    expect(await screen.findByText("max_full_rerun_runs_reached")).toBeInTheDocument();
    expect(screen.getByText(/LLMレビューは完了しています/)).toBeInTheDocument();
    expect(screen.getByText(/API自動Full rerunは上限/)).toBeInTheDocument();
    expect(screen.getByText("API Full rerun回数の上限に達しています。")).toBeInTheDocument();
    const fullRerunButton = screen.getByRole("button", { name: /APIで全面再調査/i });
    expect(fullRerunButton).toBeDisabled();
    const fullDescriptionIds = fullRerunButton
      .getAttribute("aria-describedby")
      ?.split(/\s+/);
    expect(fullDescriptionIds?.some((id) =>
      document.getElementById(id)?.textContent ===
        "API Full rerun回数の上限に達しています。",
    )).toBe(true);
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
    expect(
      screen.getByRole("button", { name: /ChatGPTで全面作り直し/i }),
    ).not.toBeDisabled();
  });

  it("shows max targeted rerun as a disabled API action while manual targeted remains available", async () => {
    globalThis.fetch = vi.fn().mockResolvedValue({
      ok: true,
      status: 200,
      json: () =>
        Promise.resolve(
          makePayload(["request_manual_targeted_rerun", "reject"], {
            reason: "max_targeted_rerun_runs_reached",
            action_states: [
              {
                action: "request_targeted_rerun",
                allowed: false,
                blocked_reason: "max_targeted_rerun_runs_reached",
              },
              {
                action: "request_manual_targeted_rerun",
                allowed: true,
                blocked_reason: null,
              },
            ],
            route_summary: {
              candidate_route: "targeted_rerun_submit",
              selected_route: "human_review",
              blocked_reason: "max_targeted_rerun_runs_reached",
              dominant_actions: ["targeted_rerun"],
              latest_review_no: 2,
              latest_verdict: "needs_targeted_rerun",
            },
          }),
        ),
    } as Response);

    render(<App />);

    expect(await screen.findByText("max_targeted_rerun_runs_reached")).toBeInTheDocument();
    expect(screen.getByText(/API自動Targeted rerunは上限/)).toBeInTheDocument();
    const targetedRerunButton = screen.getByRole("button", {
      name: /APIで部分再調査/i,
    });
    expect(targetedRerunButton).toBeDisabled();
    const targetedDescriptionIds = targetedRerunButton
      .getAttribute("aria-describedby")
      ?.split(/\s+/);
    expect(targetedDescriptionIds?.some((id) =>
      document.getElementById(id)?.textContent ===
        "API Targeted rerun回数の上限に達しています。",
    )).toBe(true);
    expect(
      screen.getByRole("button", { name: /ChatGPTで部分補強/i }),
    ).not.toBeDisabled();
  });

  it("shows no-progress warnings even when rerun actions are enabled", async () => {
    globalThis.fetch = vi.fn().mockResolvedValue({
      ok: true,
      status: 200,
      json: () =>
        Promise.resolve({
          ...makePayload([
            "request_targeted_rerun",
            "request_manual_targeted_rerun",
            "reject",
          ]),
          audit_summary: {
            ...makePayload([]).audit_summary,
            no_progress_count: 2,
          },
        }),
    } as Response);

    render(<App />);

    const targetedButton = await screen.findByRole("button", {
      name: /APIで部分再調査/i,
    });
    expect(targetedButton).not.toBeDisabled();
    expect(screen.getAllByText(/改善停滞が2回続いています/).length).toBeGreaterThan(0);
  });

  it("shows full rerun as the empty-report recovery action", async () => {
    globalThis.fetch = vi.fn().mockResolvedValue({
      ok: true,
      status: 200,
      json: () =>
        Promise.resolve(
          makePayload(["request_full_rerun", "reject"], {
            reason: "deep_research_incomplete",
            latest_report: "",
            latest_review: null,
          }),
        ),
    } as Response);

    render(<App />);

    const approveBtn = await screen.findByRole("button", {
      name: /現状で最終化/i,
    });
    const targetedRerunBtn = await screen.findByRole("button", {
      name: /APIで部分再調査/i,
    });
    const fullRerunBtn = screen.getByRole("button", { name: /APIで空レポート復旧/i });

    expect(approveBtn).toBeDisabled();
    expect(targetedRerunBtn).toBeDisabled();
    expect(fullRerunBtn).not.toBeDisabled();
  });

  it("shows pending manual rerun prompt and preserves typed result after 409 refetch", async () => {
    const pending = makePendingManualRerun();
    let payloadFetches = 0;
    const fetchMock = vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      if (url.endsWith(`/research-runs/${runId}/manual-rerun-result`)) {
        const body = init?.body as FormData;
        expect(body.get("rerun_id")).toBe("RR-manual-1");
        expect(body.get("report_text")).toBe("uploaded manual result");
        return Promise.resolve({
          ok: false,
          status: 409,
          json: () => Promise.resolve({ detail: "stale rerun id" }),
        } as Response);
      }
      payloadFetches += 1;
      return Promise.resolve({
        ok: true,
        status: 200,
        json: () =>
          Promise.resolve(
            makePayload([], {
              reason: "manual_chatgpt_rerun_pending",
              pending_manual_rerun: pending,
              allowed_actions: [],
            }),
          ),
      } as Response);
    });
    globalThis.fetch = fetchMock;

    render(<App />);

    expect(await screen.findByText("ChatGPT手動rerun")).toBeInTheDocument();
    expect(screen.getByText(/Find better official sources/i)).toBeInTheDocument();
    expect(screen.getByText("プロンプトをコピーまたはダウンロードする")).toBeInTheDocument();
    expect(screen.getByText("ChatGPTのDeep Researchで実行する")).toBeInTheDocument();
    expect(
      screen.getByText("既存レポートへ追加する差分セクションをアップロードする"),
    ).toBeInTheDocument();
    expect(screen.getByText("差分セクション")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "承認" })).not.toBeInTheDocument();

    const resultBox = screen.getByRole("textbox", { name: "Rerun結果テキスト" });
    await userEvent.type(resultBox, "uploaded manual result");
    await userEvent.click(screen.getByRole("button", { name: "結果をアップロード" }));

    expect(await screen.findByRole("alert")).toHaveTextContent("stale rerun id");
    await waitFor(() => expect(payloadFetches).toBeGreaterThanOrEqual(2));
    expect(screen.getByRole("textbox", { name: "Rerun結果テキスト" }))
      .toHaveValue("uploaded manual result");
  });

  it("uploads pending manual rerun text and navigates to monitor on success", async () => {
    const pending = makePendingManualRerun();
    const fetchMock = vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      if (url.endsWith(`/research-runs/${runId}/manual-rerun-result`)) {
        const body = init?.body as FormData;
        expect(body.get("rerun_id")).toBe("RR-manual-1");
        expect(body.get("report_text")).toBe("accepted manual result");
        expect(body.get("report_file")).toBeNull();
        return Promise.resolve(
          jsonResponse({
            run_id: runId,
            status: "reviewing",
            done_reason: null,
            needs_human_review: false,
            progress: {
              deep_research_runs: 2,
              items_total: 0,
              items_answered: 0,
              items_partial: 0,
              items_unanswered: 0,
              items_unverifiable: 0,
              blockers_unresolved: 0,
              targeted_rerun_runs: 1,
              full_rerun_runs: 0,
              llm_patch_runs: 0,
              verification_runs: 0,
              total_reviews: 1,
              latest_verdict: null,
              latest_score: null,
              total_tool_calls: 0,
              estimated_cost_usd: 0,
            },
          }),
        );
      }
      if (url.endsWith(`/research-runs/${runId}/human-review`)) {
        return Promise.resolve(
          jsonResponse(
            makePayload([], {
              reason: "manual_chatgpt_rerun_pending",
              pending_manual_rerun: pending,
              allowed_actions: [],
            }),
          ),
        );
      }
      return Promise.resolve(
        jsonResponse({
          run_id: runId,
          status: "reviewing",
          done_reason: null,
          needs_human_review: false,
          progress: {
            deep_research_runs: 2,
            items_total: 0,
            items_answered: 0,
            items_partial: 0,
            items_unanswered: 0,
            items_unverifiable: 0,
            blockers_unresolved: 0,
            targeted_rerun_runs: 1,
            full_rerun_runs: 0,
            llm_patch_runs: 0,
            verification_runs: 0,
            total_reviews: 1,
            latest_verdict: null,
            latest_score: null,
            total_tool_calls: 0,
            estimated_cost_usd: 0,
          },
        }),
      );
    });
    globalThis.fetch = fetchMock;

    render(<App />);

    await userEvent.type(
      await screen.findByRole("textbox", { name: "Rerun結果テキスト" }),
      "accepted manual result",
    );
    await userEvent.click(screen.getByRole("button", { name: "結果をアップロード" }));

    await waitFor(() => {
      expect(window.location.hash).toBe(`#/runs/${runId}`);
    });
  });

  it("uploads pending manual rerun result as a file", async () => {
    const pending = makePendingManualRerun();
    const reportFile = new File(["# Uploaded rerun"], "rerun-result.md", {
      type: "text/markdown",
    });
    const fetchMock = vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      if (url.endsWith(`/research-runs/${runId}/manual-rerun-result`)) {
        const body = init?.body as FormData;
        expect(body.get("rerun_id")).toBe("RR-manual-1");
        expect(body.get("report_text")).toBeNull();
        expect(body.get("report_file")).toBe(reportFile);
        return Promise.resolve(jsonResponse({ run_id: runId, status: "reviewing" }));
      }
      if (url.endsWith(`/research-runs/${runId}/human-review`)) {
        return Promise.resolve(
          jsonResponse(
            makePayload([], {
              reason: "manual_chatgpt_rerun_pending",
              pending_manual_rerun: pending,
              allowed_actions: [],
            }),
          ),
        );
      }
      return Promise.resolve(jsonResponse({ run_id: runId, status: "reviewing" }));
    });
    globalThis.fetch = fetchMock;

    render(<App />);

    await userEvent.click(await screen.findByLabelText("ファイル"));
    await userEvent.upload(screen.getByLabelText("Rerun結果ファイル"), reportFile);
    expect(screen.getByText("rerun-result.md")).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "結果をアップロード" }));

    await waitFor(() =>
      expect(fetchMock).toHaveBeenCalledWith(
        `http://localhost:8000/research-runs/${runId}/manual-rerun-result`,
        expect.objectContaining({ method: "POST" }),
      ),
    );
  });

  it("copies pending manual rerun prompt and reports clipboard failures", async () => {
    const pending = makePendingManualRerun({ prompt: "# Copy me" });
    const originalClipboard = navigator.clipboard;
    const writeText = vi.fn().mockResolvedValue(undefined);
    Object.defineProperty(navigator, "clipboard", {
      configurable: true,
      value: { writeText },
    });
    globalThis.fetch = vi.fn().mockResolvedValue(
      jsonResponse(
        makePayload([], {
          reason: "manual_chatgpt_rerun_pending",
          pending_manual_rerun: pending,
          allowed_actions: [],
        }),
      ),
    );

    render(<App />);

    await userEvent.click(await screen.findByRole("button", { name: "コピー" }));
    expect(writeText).toHaveBeenCalledWith("# Copy me");
    const copyStatus = await screen.findByText("コピーしました");
    expect(copyStatus).toHaveAttribute("aria-live", "polite");

    Object.defineProperty(navigator, "clipboard", {
      configurable: true,
      value: undefined,
    });
    await userEvent.click(screen.getByRole("button", { name: "コピー" }));
    expect(await screen.findByText("コピーできませんでした")).toBeInTheDocument();

    Object.defineProperty(navigator, "clipboard", {
      configurable: true,
      value: originalClipboard,
    });
  });

  it("downloads pending manual rerun prompt as markdown", async () => {
    const pending = makePendingManualRerun({ prompt: "# Download prompt" });
    const createObjectURL = vi.fn((blob: Blob) => {
      void blob;
      return "blob:manual-rerun-prompt";
    });
    Object.defineProperty(URL, "createObjectURL", {
      configurable: true,
      value: createObjectURL,
    });
    Object.defineProperty(URL, "revokeObjectURL", {
      configurable: true,
      value: vi.fn(),
    });
    let clickedHref: string | null = null;
    let clickedDownload: string | null = null;
    vi.spyOn(HTMLAnchorElement.prototype, "click").mockImplementation(function (
      this: HTMLAnchorElement,
    ) {
      clickedHref = this.href;
      clickedDownload = this.download;
    });
    globalThis.fetch = vi.fn().mockResolvedValue(
      jsonResponse(
        makePayload([], {
          reason: "manual_chatgpt_rerun_pending",
          pending_manual_rerun: pending,
          allowed_actions: [],
        }),
      ),
    );

    render(<App />);

    await userEvent.click(await screen.findByRole("button", { name: ".md ダウンロード" }));

    expect(createObjectURL).toHaveBeenCalledTimes(1);
    const downloadedBlob = createObjectURL.mock.calls[0]?.[0];
    expect(downloadedBlob).toBeInstanceOf(Blob);
    await expect(readBlobText(downloadedBlob)).resolves.toBe("# Download prompt");
    expect(clickedHref).toBe("blob:manual-rerun-prompt");
    expect(clickedDownload).toBe(`${runId}-RR-manual-1-prompt.md`);
  });

  it("clears manual rerun upload validation errors when the input changes", async () => {
    globalThis.fetch = vi.fn().mockResolvedValue(
      jsonResponse(
        makePayload([], {
          reason: "manual_chatgpt_rerun_pending",
          pending_manual_rerun: makePendingManualRerun(),
          allowed_actions: [],
        }),
      ),
    );

    render(<App />);

    await userEvent.click(await screen.findByRole("button", { name: "結果をアップロード" }));
    expect(await screen.findByRole("alert")).toHaveTextContent(
      "結果テキストを入力してください",
    );

    await userEvent.type(screen.getByRole("textbox", { name: "Rerun結果テキスト" }), "x");
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();

    await userEvent.clear(screen.getByRole("textbox", { name: "Rerun結果テキスト" }));
    await userEvent.click(screen.getByRole("button", { name: "結果をアップロード" }));
    expect(await screen.findByRole("alert")).toHaveTextContent(
      "結果テキストを入力してください",
    );

    await userEvent.click(screen.getByLabelText("ファイル"));
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
  });

  it("enables actions that are in allowed_actions", async () => {
    globalThis.fetch = vi.fn().mockResolvedValue({
      ok: true,
      status: 200,
      json: () => Promise.resolve(makePayload(["approve", "reject"])),
    } as Response);

    render(<App />);

    await screen.findByText("承認");
    const approveBtn = document.querySelector<HTMLButtonElement>(
      'button[data-action="approve"]',
    );
    const rejectBtn = screen.getByRole("button", { name: /却下/i });

    expect(approveBtn).not.toBeNull();
    expect(approveBtn).not.toBeDisabled();
    expect(rejectBtn).not.toBeDisabled();
  });

  it("enables review retry when the backend allows it", async () => {
    globalThis.fetch = vi.fn().mockResolvedValue({
      ok: true,
      status: 200,
      json: () =>
        Promise.resolve({
          ...makePayload(["approve", "request_review", "reject"]),
          reason: "review_timeout",
          latest_review: null,
        }),
    } as Response);

    render(<App />);

    expect(
      await screen.findByRole("button", { name: /レビュー再実行/i }),
    ).toBeInTheDocument();
  });

  it("renders blocked review retry as disabled with its backend reason", async () => {
    globalThis.fetch = vi.fn().mockResolvedValue({
      ok: true,
      status: 200,
      json: () =>
        Promise.resolve(
          makePayload(["approve", "reject"], {
            action_states: [
              {
                action: "request_review",
                allowed: false,
                blocked_reason: "review_retry_available_only_after_review_error",
              },
            ],
          }),
        ),
    } as Response);

    render(<App />);

    const retryButton = await screen.findByRole("button", {
      name: /レビュー再実行/i,
    });
    expect(retryButton).toBeDisabled();
    const descriptionIds = retryButton.getAttribute("aria-describedby")?.split(/\s+/);
    expect(descriptionIds?.some((id) =>
      document.getElementById(id)?.textContent ===
        "レビュー再実行はレビューエラー後だけ選択できます。",
    )).toBe(true);
  });

  it("ignores stale payload responses after the review run id changes", async () => {
    const oldPayload = deferred<Response>();
    const fetchMock = vi.fn().mockImplementation((input: RequestInfo | URL) => {
      const url = String(input);
      if (url.endsWith("/research-runs/run-stale-old/human-review")) {
        return oldPayload.promise;
      }
      if (url.endsWith("/research-runs/run-stale-new/human-review")) {
        return Promise.resolve(
          jsonResponse(
            makePayload(["approve", "reject"], {
              reason: "新しい停止理由",
            }),
          ),
        );
      }
      return Promise.reject(new Error(`Unexpected request: ${url}`));
    });
    globalThis.fetch = fetchMock;
    window.location.hash = "#/runs/run-stale-old/review";

    render(<App />);

    await waitFor(() =>
      expect(fetchMock).toHaveBeenCalledWith(
        "http://localhost:8000/research-runs/run-stale-old/human-review",
        expect.any(Object),
      ),
    );

    act(() => {
      window.location.hash = "#/runs/run-stale-new/review";
      window.dispatchEvent(new HashChangeEvent("hashchange"));
    });

    expect(await screen.findByText("新しい停止理由")).toBeInTheDocument();

    oldPayload.resolve(
      jsonResponse(
        makePayload(["approve", "reject"], {
          reason: "古い停止理由",
        }),
      ),
    );
    await act(async () => {
      await oldPayload.promise;
      await Promise.resolve();
      await Promise.resolve();
    });

    expect(screen.queryByText("古い停止理由")).not.toBeInTheDocument();
    expect(screen.getByText("新しい停止理由")).toBeInTheDocument();
  });

  it("submits a human review decision without a reviewer header", async () => {
    const fetchMock = vi.fn().mockImplementation((input: RequestInfo | URL) => {
      const url = String(input);
      if (url.endsWith(`/research-runs/${runId}/human-review`)) {
        return Promise.resolve({
          ok: true,
          status: 200,
          json: () => Promise.resolve(makePayload(["approve", "reject"])),
        } as Response);
      }
      if (url.endsWith(`/research-runs/${runId}/resume`)) {
        return Promise.resolve({
          ok: true,
          status: 200,
          json: () =>
            Promise.resolve({
              run_id: runId,
              status: "completed",
              done_reason: "human_approved",
              needs_human_review: false,
            }),
        } as Response);
      }
      return Promise.reject(new Error(`Unexpected request: ${url}`));
    });
    globalThis.fetch = fetchMock;

    render(<App />);

    await screen.findByText("承認");
    const approveBtn = document.querySelector<HTMLButtonElement>(
      'button[data-action="approve"]',
    );
    expect(approveBtn).not.toBeNull();
    await userEvent.click(approveBtn as HTMLButtonElement);

    expect(fetchMock).toHaveBeenCalledWith(
      `http://localhost:8000/research-runs/${runId}/resume`,
      expect.objectContaining({
        method: "POST",
        headers: { "Content-Type": "application/json" },
      }),
    );
  });

  it("submits a review retry decision", async () => {
    const fetchMock = vi.fn().mockImplementation(
      (input: RequestInfo | URL, init?: RequestInit) => {
        const url = String(input);
        if (url.endsWith(`/research-runs/${runId}/human-review`)) {
          return Promise.resolve({
            ok: true,
            status: 200,
            json: () =>
              Promise.resolve({
                ...makePayload(["approve", "request_review", "reject"]),
                reason: "review_timeout",
                latest_review: null,
              }),
          } as Response);
        }
        if (url.endsWith(`/research-runs/${runId}/resume`)) {
          expect(JSON.parse(String(init?.body))).toEqual({
            action: "request_review",
            comment: null,
          });
          return Promise.resolve({
            ok: true,
            status: 200,
            json: () =>
              Promise.resolve({
                run_id: runId,
                status: "completed",
                done_reason: "passed_review",
                needs_human_review: false,
              }),
          } as Response);
        }
        return Promise.reject(new Error(`Unexpected request: ${url}`));
      },
    );
    globalThis.fetch = fetchMock;

    render(<App />);

    await userEvent.click(
      await screen.findByRole("button", { name: /レビュー再実行/i }),
    );

    expect(fetchMock).toHaveBeenCalledWith(
      `http://localhost:8000/research-runs/${runId}/resume`,
      expect.objectContaining({
        method: "POST",
        headers: { "Content-Type": "application/json" },
      }),
    );
  });
});

describe("Settings (SCR-7)", () => {
  beforeEach(() => {
    window.location.hash = "#/settings";
  });

  it("renders the settings page with default option inputs", () => {
    render(<App />);

    expect(screen.getByText(/デフォルトオプション/i)).toBeInTheDocument();
    expect(screen.getByLabelText(/最大Targeted rerun回数/i)).toBeInTheDocument();
    expect(screen.getByLabelText(/最大LLM patch回数/i)).toBeInTheDocument();
  });

  it("renders API-aligned factory defaults", () => {
    render(<App />);

    expect(screen.getByLabelText(/最大Targeted rerun回数/i)).toHaveValue(2);
    expect(screen.getByLabelText(/最大Full rerun回数/i)).toHaveValue(1);
    expect(screen.getByLabelText(/最大LLM patch回数/i)).toHaveValue(3);
    expect(screen.getByLabelText(/最大Verification回数/i)).toHaveValue(3);
    expect(screen.getByLabelText(/最大反復回数/i)).toHaveValue(5);
    expect(screen.getByLabelText(/最大ツール呼び出し数/i)).toHaveValue(120);
    expect(screen.queryByLabelText(/最大コスト/i)).not.toBeInTheDocument();
  });

  it("normalizes stale saved factory defaults in settings", () => {
    localStorage.setItem("dro.defaults", JSON.stringify(staleSavedFactoryDefaults()));

    render(<App />);

    expect(screen.getByLabelText(/最大Targeted rerun回数/i)).toHaveValue(2);
    expect(screen.getByLabelText(/最大反復回数/i)).toHaveValue(5);
    expect(screen.getByLabelText(/最大ツール呼び出し数/i)).toHaveValue(120);
  });

  it("keeps user-modified saved defaults without normalization", () => {
    localStorage.setItem(
      "dro.defaults",
      JSON.stringify({ ...staleSavedFactoryDefaults(), max_total_tool_calls: 210 }),
    );

    render(<App />);

    expect(screen.getByLabelText(/最大Targeted rerun回数/i)).toHaveValue(3);
    expect(screen.getByLabelText(/最大反復回数/i)).toHaveValue(10);
    expect(screen.getByLabelText(/最大ツール呼び出し数/i)).toHaveValue(210);
  });

  it("renders only the default numeric editor", () => {
    render(<App />);

    const heading = screen.getByRole("heading", { name: /デフォルトオプション/i });
    const section = heading.closest("section");
    expect(section).not.toBeNull();
    expect(within(section as HTMLElement).getAllByRole("spinbutton")).toHaveLength(6);
  });

  it("persists saved defaults to localStorage", async () => {
    render(<App />);

    const saveBtn = screen.getByRole("button", { name: /^保存$/i });
    await userEvent.click(saveBtn);

    const stored = localStorage.getItem("dro.defaults");
    expect(stored).not.toBeNull();
  });

  it("saves and clears the browser Research API key", async () => {
    render(<App />);

    const input = screen.getByLabelText(/Research API key/i);
    await userEvent.type(input, "  browser-secret  ");
    await userEvent.click(screen.getByRole("button", { name: /API keyを保存/i }));

    expect(localStorage.getItem("dro.researchApiKey")).toBe("browser-secret");
    expect(input).toHaveValue("browser-secret");

    await userEvent.click(screen.getByRole("button", { name: /API keyを削除/i }));

    expect(localStorage.getItem("dro.researchApiKey")).toBeNull();
    expect(input).toHaveValue("");
  });

  it("sanitizes empty and out-of-range number inputs before saving", async () => {
    render(<App />);

    fireEvent.change(screen.getByLabelText(/最大Targeted rerun回数/i), {
      target: { value: "" },
    });
    fireEvent.change(screen.getByLabelText(/最大反復回数/i), {
      target: { value: "0" },
    });
    await userEvent.click(screen.getByRole("button", { name: /^保存$/i }));

    const stored = JSON.parse(localStorage.getItem("dro.defaults") ?? "{}") as Record<
      string,
      unknown
    >;
    expect(stored.max_targeted_rerun_runs).toBe(2);
    expect(stored.max_total_iterations).toBe(1);
  });
});
