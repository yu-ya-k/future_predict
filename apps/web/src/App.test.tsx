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

import { act, render, screen, cleanup, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { App } from "./App";

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

function makeItemAssessment(overrides: Record<string, unknown> = {}) {
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

  it("unwraps ResearchItem API wrapper responses in the monitor", async () => {
    globalThis.fetch = vi.fn((input: RequestInfo | URL) => {
      const url = String(input);
      if (url.endsWith(`/research-runs/${runId}/reviews`)) {
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
              latest_score: null,
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
  });

  it("separates total elapsed time from the current Deep Research attempt", async () => {
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
      if (url.endsWith(`/research-runs/${runId}/reviews`)) {
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
            deep_research_submitted_at: "2026-06-06T04:30:00.000Z",
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
    expect(screen.getByText(/開始時刻: 13:30/)).toBeInTheDocument();
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
      if (url.endsWith(`/research-runs/${runId}/reviews`)) {
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
    globalThis.fetch = fetchMock;

    render(<App />);

    await userEvent.click(await screen.findByRole("button", { name: "指示内容" }));

    expect(await screen.findByText("Deep Researchへの指示内容")).toBeInTheDocument();
    expect(screen.getByText(/実際のDeep Research指示/i)).toBeInTheDocument();
    expect(screen.queryByText(/重複したcollect側の指示/i)).not.toBeInTheDocument();
    expect(screen.getAllByText("Deep Research 1回目")).toHaveLength(1);
    expect(screen.getByText("completed")).toBeInTheDocument();
    expect(fetchMock).toHaveBeenCalledWith(
      expect.stringContaining(`/research-runs/${runId}/attempts`),
      expect.any(Object),
    );
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

  it("links to report and review history tabs", async () => {
    globalThis.fetch = vi.fn((input: RequestInfo | URL) => {
      const url = String(input);
      if (url.endsWith(`/research-runs/${runId}/citations`)) {
        return Promise.resolve({
          ok: true,
          status: 200,
          json: () => Promise.resolve([]),
        } as Response);
      }
      if (url.endsWith(`/research-runs/${runId}/reviews`)) {
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

    await userEvent.click(screen.getByRole("link", { name: "レビュー内容" }));

    expect(window.location.hash).toBe(`#/runs/${runId}/report?tab=reviews`);
  });
});

describe("ReportViewer (SCR-5)", () => {
  const runId = "run-report-back-test";

  function makeAttempt(runNo: number, report: string, status = "completed") {
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
    };
  }

  function makeReview(reviewNo: number, rationale: string, score = 70) {
    return {
      review_no: reviewNo,
      verdict: "needs_targeted_rerun",
      recommended_route: "needs_targeted_rerun",
      goal_achieved: false,
      score,
      rationale,
      gaps: [`ギャップ ${reviewNo}`],
      factuality_concerns: [],
      source_quality_concerns: [],
      item_assessments: [makeItemAssessment({ item_id: `RI-0${reviewNo}` })],
      next_instructions: `改善指示 ${reviewNo}`,
      reviewer_confidence: 80,
      high_risk_flags: [],
      public_web_search_used: false,
      reviewer_response_id: `resp_review_${reviewNo}`,
      report_hash: `hash${reviewNo}`,
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
      if (url.endsWith(`/research-runs/${runId}/reviews`)) {
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

  it("defaults to the latest Deep Research output without mixed tabs", async () => {
    globalThis.fetch = vi.fn((input: RequestInfo | URL) => {
      const url = String(input);
      if (url.endsWith(`/research-runs/${runId}/citations`)) {
        return Promise.resolve({
          ok: true,
          status: 200,
          json: () => Promise.resolve([]),
        } as Response);
      }
      if (url.endsWith(`/research-runs/${runId}/reviews`)) {
        return Promise.resolve({
          ok: true,
          status: 200,
          json: () => Promise.resolve([makeReview(1, "レビュー側の内容です", 58)]),
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
    expect(await screen.findByText("2回目のDeep Research出力")).toBeInTheDocument();
    expect(screen.queryByRole("tab", { name: "最新版" })).not.toBeInTheDocument();
    expect(screen.queryByRole("tab", { name: "レビュー履歴" })).not.toBeInTheDocument();
    expect(screen.queryByText("レビュー側の内容です")).not.toBeInTheDocument();
    expect(screen.queryByLabelText("品質スコア")).not.toBeInTheDocument();
  });

  it("opens Deep Research history from the report tab URL", async () => {
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
      if (url.endsWith(`/research-runs/${runId}/reviews`)) {
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
    expect(await screen.findByText("2回目のDeep Research出力")).toBeInTheDocument();
    expect(screen.queryByRole("tab", { name: "レビュー履歴" })).not.toBeInTheDocument();

    await userEvent.click(screen.getByRole("button", { name: /1回目/ }));

    expect(await screen.findByText("1回目のDeep Research出力")).toBeInTheDocument();
  });

  it("opens full review history from the review tab URL", async () => {
    window.location.hash = `#/runs/${runId}/report?tab=reviews`;
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
          json: () => Promise.resolve([makeAttempt(1, "# Deep Research側の出力")]),
        } as Response);
      }
      if (url.endsWith(`/research-runs/${runId}/reviews`)) {
        return Promise.resolve({
          ok: true,
          status: 200,
          json: () =>
            Promise.resolve([
              {
                review_no: 1,
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
                public_web_search_used: false,
                reviewer_response_id: "resp_review_1",
                report_hash: "hash1",
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
            status: "needs_human_review",
            final_report: null,
            report: "# 候補レポート",
            warnings: [],
          }),
      } as Response);
    });

    render(<App />);

    expect(await screen.findByRole("heading", { name: "レビュー内容" })).toBeInTheDocument();
    expect(await screen.findByText("目的達成度が不足しています")).toBeInTheDocument();
    expect(screen.queryByRole("tab", { name: "Deep Research履歴" })).not.toBeInTheDocument();
    expect(screen.queryByText("Deep Research側の出力")).not.toBeInTheDocument();
    expect(screen.getByText("想定出力の比較表が不足")).toBeInTheDocument();
    expect(screen.getByText("次回は公式資料を優先して比較表を作る")).toBeInTheDocument();
  });

  it("auto-follows newly added Deep Research attempts while latest is selected", async () => {
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
      if (url.endsWith(`/research-runs/${runId}/reviews`)) {
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
                ? [makeAttempt(1, "# 1回目のDeep Research出力")]
                : [
                    makeAttempt(1, "# 1回目のDeep Research出力"),
                    makeAttempt(2, "# 2回目のDeep Research出力"),
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

    await flushPollingTimers();

    expect(await screen.findByText("2回目のDeep Research出力")).toBeInTheDocument();
  });

  it("keeps an explicitly selected older Deep Research attempt while polling adds newer output", async () => {
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
      if (url.endsWith(`/research-runs/${runId}/reviews`)) {
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

    expect(await screen.findByText("2回目のDeep Research出力")).toBeInTheDocument();

    await userEvent.click(screen.getByRole("button", { name: /1回目/ }));
    expect(await screen.findByText("1回目のDeep Research出力")).toBeInTheDocument();

    await flushPollingTimers();

    expect(screen.getByText("1回目のDeep Research出力")).toBeInTheDocument();
    expect(screen.queryByText("3回目のDeep Research出力")).not.toBeInTheDocument();
  });

  it("auto-follows newly added reviews while latest review is selected", async () => {
    const flushPollingTimers = capturePollingTimers();
    window.location.hash = `#/runs/${runId}/report?tab=reviews`;
    let reviewCalls = 0;
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
      if (url.endsWith(`/research-runs/${runId}/reviews`)) {
        reviewCalls += 1;
        return Promise.resolve({
          ok: true,
          status: 200,
          json: () =>
            Promise.resolve(
              reviewCalls === 1
                ? [makeReview(1, "1回目レビューの理由", 58)]
                : [
                    makeReview(1, "1回目レビューの理由", 58),
                    makeReview(2, "2回目レビューの理由", 76),
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
            status: "reviewing",
            final_report: null,
            report: null,
            warnings: [],
          }),
      } as Response);
    });

    render(<App />);

    expect(await screen.findByText("1回目レビューの理由")).toBeInTheDocument();

    await flushPollingTimers();

    expect(await screen.findByText("2回目レビューの理由")).toBeInTheDocument();
  });
});

describe("HumanReview (SCR-4)", () => {
  const runId = "run-hr-test";

  function makePayload(allowedActions: string[]) {
    return {
      run_id: runId,
      reason: "スコアが閾値以下です",
      latest_report: "# テストレポート\n\nサンプル内容",
      latest_review: {
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
      allowed_actions: allowedActions,
      audit_summary: {
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

  it("disables actions not in allowed_actions", async () => {
    globalThis.fetch = vi.fn().mockResolvedValue({
      ok: true,
      status: 200,
      json: () => Promise.resolve(makePayload(["approve", "reject"])),
    } as Response);

    render(<App />);

    // Wait for payload to load (buttons are rendered after fetch)
    const llmPatchBtn = await screen.findByRole("button", { name: /LLM patch/i });
    const targetedRerunBtn = screen.getByRole("button", { name: /Targeted rerun/i });

    expect(llmPatchBtn).toBeDisabled();
    expect(targetedRerunBtn).toBeDisabled();
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

  it("shows review retry only when the backend allows it", async () => {
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
});
