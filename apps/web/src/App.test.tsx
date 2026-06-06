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

import { render, screen, cleanup, waitFor, within } from "@testing-library/react";
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
    max_deep_research_runs: 3,
    max_llm_fix_runs: 3,
    max_total_iterations: 10,
    max_no_progress_rounds: 3,
    max_cost_usd: 5,
    max_total_tool_calls: 200,
  };
}

// ── Setup / teardown ──────────────────────────────────────────────────────────

beforeEach(() => {
  vi.stubEnv("VITE_API_BASE_URL", "http://localhost:8000");
  clearStorage();
  window.location.hash = "#/";
});

afterEach(() => {
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

  it("shows reviewer ID prompt in the queue band when no reviewer id is set", () => {
    globalThis.fetch = vi.fn().mockResolvedValue({
      ok: true,
      status: 200,
      json: () => Promise.resolve([]),
    } as Response);

    render(<App />);

    expect(screen.getByText(/レビュアーIDが必要です/i)).toBeInTheDocument();
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
      max_deep_research_runs: 2,
      max_llm_fix_runs: 3,
      max_total_iterations: 5,
      max_no_progress_rounds: 2,
      max_cost_usd: 20,
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

    expect(body.options.max_deep_research_runs).toBe(2);
    expect(body.options.max_total_iterations).toBe(5);
    expect(body.options.max_cost_usd).toBe(20);
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
        next_instructions: null,
        can_be_fixed_by_llm: false,
        requires_new_external_research: true,
        reviewer_confidence: 80,
        high_risk_flags: [],
        public_web_search_used: false,
      },
      allowed_actions: allowedActions,
      audit_summary: {
        deep_research_runs: 2,
        llm_fix_runs: 1,
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
    localStorage.setItem("dro.reviewerId", "test-reviewer");
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
    const llmFixBtn = await screen.findByRole("button", { name: /軽微修正/i });
    const deepResearchBtn = screen.getByRole("button", { name: /再調査/i });

    expect(llmFixBtn).toBeDisabled();
    expect(deepResearchBtn).toBeDisabled();
  });

  it("enables actions that are in allowed_actions", async () => {
    globalThis.fetch = vi.fn().mockResolvedValue({
      ok: true,
      status: 200,
      json: () => Promise.resolve(makePayload(["approve", "reject"])),
    } as Response);

    render(<App />);

    const approveBtn = await screen.findByRole("button", { name: /承認/i });
    const rejectBtn = screen.getByRole("button", { name: /却下/i });

    expect(approveBtn).not.toBeDisabled();
    expect(rejectBtn).not.toBeDisabled();
  });

  it("shows reviewer ID setup screen when reviewer id is not set", async () => {
    // Ensure no reviewer id — beforeEach sets it, remove it here
    localStorage.removeItem("dro.reviewerId");

    // With no reviewerId, ReviewerRequiredError is thrown client-side before fetch
    globalThis.fetch = vi.fn();

    render(<App />);

    // The setup button should appear once the component detects missing reviewer id
    const setupBtn = await screen.findByRole("button", { name: /設定して続ける/i });
    expect(setupBtn).toBeInTheDocument();
  });
});

describe("Settings (SCR-7)", () => {
  beforeEach(() => {
    window.location.hash = "#/settings";
  });

  it("renders the settings page with default option inputs", () => {
    render(<App />);

    expect(screen.getByText(/デフォルトオプション/i)).toBeInTheDocument();
    expect(screen.getByLabelText(/最大Deep Research回数/i)).toBeInTheDocument();
    expect(screen.getByLabelText(/最大LLM修正回数/i)).toBeInTheDocument();
  });

  it("renders API-aligned factory defaults", () => {
    render(<App />);

    expect(screen.getByLabelText(/最大Deep Research回数/i)).toHaveValue(2);
    expect(screen.getByLabelText(/最大LLM修正回数/i)).toHaveValue(3);
    expect(screen.getByLabelText(/最大反復回数/i)).toHaveValue(5);
    expect(screen.getByLabelText(/最大停滞許容回数/i)).toHaveValue(2);
    expect(screen.getByLabelText(/最大コスト/i)).toHaveValue(20);
    expect(screen.getByLabelText(/最大ツール呼び出し数/i)).toHaveValue(120);
  });

  it("normalizes stale saved factory defaults in settings", () => {
    localStorage.setItem("dro.defaults", JSON.stringify(staleSavedFactoryDefaults()));

    render(<App />);

    expect(screen.getByLabelText(/最大Deep Research回数/i)).toHaveValue(2);
    expect(screen.getByLabelText(/最大反復回数/i)).toHaveValue(5);
    expect(screen.getByLabelText(/最大コスト/i)).toHaveValue(20);
    expect(screen.getByLabelText(/最大ツール呼び出し数/i)).toHaveValue(120);
  });

  it("keeps user-modified saved defaults without normalization", () => {
    localStorage.setItem(
      "dro.defaults",
      JSON.stringify({ ...staleSavedFactoryDefaults(), max_cost_usd: 7 }),
    );

    render(<App />);

    expect(screen.getByLabelText(/最大Deep Research回数/i)).toHaveValue(3);
    expect(screen.getByLabelText(/最大反復回数/i)).toHaveValue(10);
    expect(screen.getByLabelText(/最大コスト/i)).toHaveValue(7);
    expect(screen.getByLabelText(/最大ツール呼び出し数/i)).toHaveValue(200);
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
