import { cleanup, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { App } from "../../App";
import type {
  EstimateSetResponse,
  ForecastCreateRequest,
  ForecastDetail,
  ForecastFramingDraft,
  ForecastFramingDraftClarifyingQuestion,
  ForecastFramingDraftResponse,
} from "../../types";

Object.defineProperty(window, "scrollTo", { value: vi.fn(), writable: true });

function jsonResponse(data: unknown, status = 200): Response {
  return {
    ok: status >= 200 && status < 300,
    status,
    json: () => Promise.resolve(data),
  } as Response;
}

function forecastDetail(overrides: Partial<ForecastDetail> = {}): ForecastDetail {
  return {
    forecast_id: "forecast-1",
    question: "Will the product launch by Q4?",
    status: "framing_pending",
    resolution_date: null,
    current_framing_version: 1,
    approved_framing_version: null,
    committed_version_id: null,
    resolved_at: null,
    created_at: "2026-06-08T00:00:00Z",
    updated_at: "2026-06-08T00:00:00Z",
    target_population: null,
    unit_of_analysis: null,
    resolution_criteria: "Official launch announcement.",
    resolution_sources: [],
    decision_context: null,
    confidentiality_class: "public",
    outcomes: [
      {
        outcome_id: "outcome-yes",
        label: "Yes",
        definition: "Launches by Q4.",
        resolution_rule: "resolved by official source",
        normalization_group_id: "norm-1",
        sort_order: 0,
      },
      {
        outcome_id: "outcome-no",
        label: "No",
        definition: "Does not launch by Q4.",
        resolution_rule: "resolved by official source",
        normalization_group_id: "norm-1",
        sort_order: 1,
      },
    ],
    ...overrides,
  };
}

function estimateSet(overrides: Partial<EstimateSetResponse> = {}): EstimateSetResponse {
  return {
    estimate_set_id: "estimate-set-1",
    forecast_id: "forecast-1",
    status: "draft",
    engine_version: "phase_a_v1",
    input_snapshot_hash: "snapshot-hash-1",
    engine_code_hash: "engine-hash-1",
    random_seed: 0,
    normalization_group_id: "norm-1",
    estimates: [
      {
        estimate_id: "estimate-1",
        target_kind: "outcome",
        target_id: "outcome-yes",
        prior: 0.5,
        evidence_update: 0.2,
        cross_impact_adjustment: 0,
        simulation_adjustment: 0,
        calibration_adjustment: 0,
        human_adjustment: 0,
        final_probability: 0.73,
        uncertainty_range: { lo80: 0.63, hi80: 0.83 },
        components: { clamp_applied: false },
      },
    ],
    ...overrides,
  };
}

type FramingDraftOverrides = Partial<
  Omit<ForecastFramingDraftResponse, "create_payload" | "draft">
> & {
  create_payload?: Partial<ForecastCreateRequest> | null;
  draft?: Partial<ForecastFramingDraft>;
};

function framingDraft(overrides: FramingDraftOverrides = {}): ForecastFramingDraftResponse {
  const base: ForecastFramingDraftResponse = {
    draft: {
      forecast_prompt: "Forecast whether the product launches.",
      question: "Will the product launch by Q4?",
      resolution_criteria: "Official launch announcement.",
      resolution_sources: ["Official site"],
      target_population: "Product users",
      unit_of_analysis: "Product",
      decision_context: "Roadmap planning",
      outcomes: ["Yes", "No"],
      clarifying_questions: [],
      confidence: 0.82,
    },
    create_payload: {
      question: "Will the product launch by Q4?",
      resolution_criteria: "Official launch announcement.",
      resolution_sources: ["Official site"],
      target_population: "Product users",
      unit_of_analysis: "Product",
      decision_context: "Roadmap planning",
      confidentiality_class: "public",
      outcomes: ["Yes", "No"],
    },
    ready_to_create: true,
    model: "test-model",
    response_id: "resp-1",
    warnings: [],
  };
  return {
    ...base,
    ...overrides,
    draft: { ...base.draft, ...overrides.draft },
    create_payload:
      overrides.create_payload === null
        ? null
        : ({ ...base.create_payload, ...overrides.create_payload } as ForecastCreateRequest),
  };
}

function clarifyingQuestion(
  overrides: Partial<ForecastFramingDraftClarifyingQuestion> = {},
): ForecastFramingDraftClarifyingQuestion {
  return {
    question_id: "deadline",
    label: "期限",
    prompt: "期限はいつですか？",
    why_needed: "Forecastを公開情報で判定できる期限が必要です。",
    answer_type: "text",
    required: true,
    options: [],
    ...overrides,
  };
}

beforeEach(() => {
  vi.stubEnv("VITE_API_BASE_URL", "http://localhost:8000");
  localStorage.clear();
  window.location.hash = "#/";
});

afterEach(() => {
  vi.useRealTimers();
  vi.unstubAllEnvs();
  vi.restoreAllMocks();
  cleanup();
  localStorage.clear();
});

describe("Forecast UI", () => {
  it("starts new forecasts with a single rough question input", () => {
    window.location.hash = "#/forecasts/new";
    globalThis.fetch = vi.fn();

    render(<App />);

    expect(screen.getByRole("heading", { name: "まずはざっくり教えてください" })).toBeInTheDocument();
    expect(screen.getByRole("textbox", { name: /予測したいこと/ })).toBeInTheDocument();
    expect(screen.queryByLabelText("問い")).not.toBeInTheDocument();
    expect(screen.queryByLabelText("判定条件")).not.toBeInTheDocument();
    expect(screen.queryByLabelText("結果候補")).not.toBeInTheDocument();
    expect(screen.getByText("大枠だけ入力")).toBeInTheDocument();
    expect(screen.getByText("AIが下書き化")).toBeInTheDocument();
    expect(screen.getByText("不足点だけ確認")).toBeInTheDocument();
    expect(screen.getByText("保存後に承認")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "AIでForecast案を作成" })).toBeDisabled();
  });

  it("asks draft clarifying questions before final create and separate approval", async () => {
    window.location.hash = "#/forecasts/new";
    const fetchMock = vi.fn(async (url: string | URL | Request, init?: RequestInit) => {
      const path = String(url).replace("http://localhost:8000", "");
      if (path === "/forecasts/framing-drafts" && init?.method === "POST") {
        const body = JSON.parse(String(init.body));
        if (!body.previous_draft) {
          expect(body).toMatchObject({
            rough_question: "Will the product launch?",
            locale: "ja",
          });
          return jsonResponse(
            framingDraft({
              ready_to_create: false,
              warnings: ["期限を確認してください。"],
              draft: {
                clarifying_questions: [
                  clarifyingQuestion(),
                  clarifyingQuestion({
                    question_id: "product",
                    label: "対象プロダクト",
                    prompt: "対象プロダクトは何ですか？",
                    why_needed: "判定対象を固定するために必要です。",
                  }),
                ],
              },
              create_payload: null,
            }),
          );
        }
        expect(body.answers).toEqual([
          { question_id: "deadline", answer: "2026 Q4" },
          { question_id: "product", answer: "Forecast app" },
        ]);
        expect(body.previous_draft).toMatchObject({
          question: "Will the product launch by Q4?",
        });
        return jsonResponse(framingDraft());
      }
      if (path === "/forecasts" && init?.method === "POST") {
        expect(JSON.parse(String(init.body))).toMatchObject({
          question: "Will the Forecast app launch by 2026 Q4?",
          resolution_criteria: "Official launch announcement.",
          resolution_sources: ["Official site", "Status page"],
          target_population: "Product users",
          unit_of_analysis: "Product",
          decision_context: "Roadmap planning",
          confidentiality_class: "public",
          outcomes: ["Yes", "No"],
        });
        return jsonResponse({
          forecast_id: "forecast-1",
          status: "framing_pending",
          framing_version: 1,
          created_at: "2026-06-08T00:00:00Z",
        });
      }
      if (path === "/forecasts/forecast-1" && (!init || init.method === "GET")) {
        return jsonResponse(forecastDetail());
      }
      if (path === "/forecasts/forecast-1/review" && init?.method === "POST") {
        return jsonResponse({
          forecast_id: "forecast-1",
          action: "approve_framing",
          status: "framing_approved",
          approved_framing_version: 1,
        });
      }
      return jsonResponse({ detail: "unexpected request" }, 500);
    });
    globalThis.fetch = fetchMock;

    render(<App />);

    expect(screen.getByText("作成の流れ")).toBeInTheDocument();

    await userEvent.type(
      screen.getByRole("textbox", { name: /予測したいこと/ }),
      "Will the product launch?",
    );
    await userEvent.click(screen.getByRole("button", { name: "AIでForecast案を作成" }));

    expect(await screen.findByRole("heading", { name: "確認したいこと" })).toBeInTheDocument();
    expect(screen.getByText("期限を確認してください。")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Forecastを作成" })).not.toBeInTheDocument();

    await userEvent.type(screen.getByLabelText("期限"), "2026 Q4");
    await userEvent.type(screen.getByLabelText("対象プロダクト"), "Forecast app");
    await userEvent.click(screen.getByRole("button", { name: "回答をAIに反映" }));

    expect(await screen.findByRole("heading", { name: "最終確認" })).toBeInTheDocument();
    const questionInput = screen.getByRole("textbox", { name: /問い/ });
    await userEvent.clear(questionInput);
    await userEvent.type(questionInput, "Will the Forecast app launch by 2026 Q4?");
    const sourcesInput = screen.getByRole("textbox", { name: /判定ソース/ });
    await userEvent.clear(sourcesInput);
    await userEvent.type(sourcesInput, "Official site\n\nStatus page");
    await userEvent.click(screen.getByRole("button", { name: "Forecastを作成" }));

    expect(await screen.findByRole("heading", { name: "保存済みプレビュー" })).toBeInTheDocument();
    expect(screen.getByText("Will the product launch by Q4?")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "この内容で承認" })).toBeEnabled();

    await userEvent.click(screen.getByRole("button", { name: "この内容で承認" }));
    await waitFor(() => expect(window.location.hash).toBe("#/forecasts/forecast-1"));
  });

  it("preserves rough input when draft creation fails", async () => {
    window.location.hash = "#/forecasts/new";
    const fetchMock = vi.fn(async (url: string | URL | Request, init?: RequestInit) => {
      const path = String(url).replace("http://localhost:8000", "");
      if (path === "/forecasts/framing-drafts" && init?.method === "POST") {
        return jsonResponse({ detail: "Draft model unavailable." }, 503);
      }
      return jsonResponse({ detail: "unexpected request" }, 500);
    });
    globalThis.fetch = fetchMock;

    render(<App />);

    const roughInput = screen.getByRole("textbox", { name: /予測したいこと/ });
    await userEvent.type(roughInput, "Keep this rough idea");
    await userEvent.click(screen.getByRole("button", { name: "AIでForecast案を作成" }));

    expect(await screen.findByRole("alert")).toHaveTextContent("Draft model unavailable.");
    expect(screen.getByRole("textbox", { name: /予測したいこと/ })).toHaveValue(
      "Keep this rough idea",
    );
  });

  it("offers retry and manual final edit when a draft is not ready without questions", async () => {
    window.location.hash = "#/forecasts/new";
    const fetchMock = vi.fn(async (url: string | URL | Request, init?: RequestInit) => {
      const path = String(url).replace("http://localhost:8000", "");
      if (path === "/forecasts/framing-drafts" && init?.method === "POST") {
        return jsonResponse(
          framingDraft({
            ready_to_create: false,
            warnings: ["公開情報で判定できる期限が不足しています。"],
            draft: { clarifying_questions: [] },
            create_payload: null,
          }),
        );
      }
      return jsonResponse({ detail: "unexpected request" }, 500);
    });
    globalThis.fetch = fetchMock;

    render(<App />);

    await userEvent.type(
      screen.getByRole("textbox", { name: /予測したいこと/ }),
      "Maybe launch?",
    );
    await userEvent.click(screen.getByRole("button", { name: "AIでForecast案を作成" }));

    expect(await screen.findByRole("heading", { name: "大枠を調整" })).toBeInTheDocument();
    expect(screen.getByText("公開情報で判定できる期限が不足しています。")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Forecastを作成" })).not.toBeInTheDocument();

    await userEvent.click(screen.getByRole("button", { name: "最終編集を開く" }));

    expect(await screen.findByRole("heading", { name: "最終確認" })).toBeInTheDocument();
    expect(screen.getByText(/まだ作成準備が完了していません/)).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Forecastを作成" })).not.toBeInTheDocument();
  });

  it("renders typed 409 code, message and details on forecast commands", async () => {
    window.location.hash = "#/forecasts/forecast-1";
    const fetchMock = vi.fn(async (url: string | URL | Request, init?: RequestInit) => {
      const path = String(url).replace("http://localhost:8000", "");
      if (path === "/forecasts/forecast-1" && (!init || init.method === "GET")) {
        return jsonResponse(
          forecastDetail({
            status: "framing_approved",
            approved_framing_version: 1,
          }),
        );
      }
      if (path === "/forecasts/forecast-1/research-packs" && init?.method === "POST") {
        return jsonResponse(
          {
            detail: {
              code: "policy_requires_revision",
              message: "Policy requires framing revision.",
              details: { policy_decision_id: "policy-1" },
            },
          },
          409,
        );
      }
      return jsonResponse({ detail: "unexpected request" }, 500);
    });
    globalThis.fetch = fetchMock;

    render(<App />);

    expect(await screen.findByText("framing_approved")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Extract evidence" })).toBeDisabled();
    await userEvent.click(screen.getByRole("button", { name: "Dispatch pack" }));

    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent("policy_requires_revision");
    expect(alert).toHaveTextContent("Policy requires framing revision.");
    expect(alert).toHaveTextContent("policy_decision_id");
  });

  it("loads a draft estimate set on direct routes", async () => {
    window.location.hash = "#/forecasts/forecast-1";
    const fetchMock = vi.fn(async (url: string | URL | Request, init?: RequestInit) => {
      const path = String(url).replace("http://localhost:8000", "");
      if (path === "/forecasts/forecast-1" && (!init || init.method === "GET")) {
        return jsonResponse(forecastDetail({ status: "draft_ready" }));
      }
      if (path === "/forecasts/forecast-1/estimate-set" && (!init || init.method === "GET")) {
        return jsonResponse(estimateSet());
      }
      return jsonResponse({ detail: "unexpected request" }, 500);
    });
    globalThis.fetch = fetchMock;

    render(<App />);

    expect(await screen.findAllByText("phase_a_v1")).toHaveLength(2);
    expect(screen.getByText("snapshot-hash-1")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Compute" })).toBeDisabled();
    expect(fetchMock.mock.calls.some(([url]) => String(url).endsWith("/estimate-set"))).toBe(
      true,
    );
    expect(
      fetchMock.mock.calls.some(([url]) =>
        String(url).endsWith("/forecasts/forecast-1/probabilities/compute"),
      ),
    ).toBe(false);
  });

  it("requires claim-target link approval before compute", async () => {
    window.location.hash = "#/forecasts/forecast-1";
    let status: ForecastDetail["status"] = "scenarios_ready";
    const fetchMock = vi.fn(async (url: string | URL | Request, init?: RequestInit) => {
      const path = String(url).replace("http://localhost:8000", "");
      if (path === "/forecasts/forecast-1" && (!init || init.method === "GET")) {
        return jsonResponse(forecastDetail({ status }));
      }
      if (path === "/forecasts/forecast-1/review" && init?.method === "POST") {
        return jsonResponse({
          forecast_id: "forecast-1",
          action: "approve_claim_target_links",
          status: "scenarios_ready",
        });
      }
      if (path === "/forecasts/forecast-1/probabilities/compute" && init?.method === "POST") {
        status = "draft_ready";
        return jsonResponse(estimateSet());
      }
      if (path === "/forecasts/forecast-1/estimate-set" && (!init || init.method === "GET")) {
        return jsonResponse(estimateSet());
      }
      return jsonResponse({ detail: "unexpected request" }, 500);
    });
    globalThis.fetch = fetchMock;

    render(<App />);

    expect(await screen.findByText("scenarios_ready")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Approve claim links" })).toBeEnabled();
    expect(screen.getByRole("button", { name: "Compute" })).toBeDisabled();

    await userEvent.click(screen.getByRole("button", { name: "Approve claim links" }));

    await waitFor(() => expect(screen.getByRole("button", { name: "Compute" })).toBeEnabled());
    const reviewCall = fetchMock.mock.calls.find(
      ([url, init]) => String(url).endsWith("/forecasts/forecast-1/review") && init?.method === "POST",
    );
    expect(JSON.parse(String(reviewCall?.[1]?.body))).toMatchObject({
      action: "approve_claim_target_links",
    });
    expect(reviewCall?.[1]?.headers).toEqual(
      expect.objectContaining({
        "Idempotency-Key": expect.stringMatching(/^forecast-forecast-1-claimTargets-/),
      }),
    );

    await userEvent.click(screen.getByRole("button", { name: "Compute" }));

    expect(await screen.findByText("snapshot-hash-1")).toBeInTheDocument();
    const computeCall = fetchMock.mock.calls.find(
      ([url, init]) =>
        String(url).endsWith("/forecasts/forecast-1/probabilities/compute") &&
        init?.method === "POST",
    );
    expect(computeCall?.[1]?.headers).toEqual(
      expect.objectContaining({
        "Idempotency-Key": expect.stringMatching(/^forecast-forecast-1-compute-/),
      }),
    );
  });

  it("routes forecast URLs with extra path segments to not-found", () => {
    window.location.hash = "#/forecasts/forecast-1/audit/extra";
    globalThis.fetch = vi.fn();

    render(<App />);

    expect(screen.getByText("ページが見つかりません")).toBeInTheDocument();
  });

  it("shows resolve controls for committed forecasts", async () => {
    window.location.hash = "#/forecasts/forecast-1";
    const fetchMock = vi.fn(async (url: string | URL | Request, init?: RequestInit) => {
      const path = String(url).replace("http://localhost:8000", "");
      if (path === "/forecasts/forecast-1" && (!init || init.method === "GET")) {
        return jsonResponse(forecastDetail({ status: "committed" }));
      }
      if (path === "/forecasts/forecast-1/estimate-set" && (!init || init.method === "GET")) {
        return jsonResponse(estimateSet({ status: "frozen" }));
      }
      if (path === "/forecasts/forecast-1/resolve" && init?.method === "POST") {
        return jsonResponse({
          forecast_id: "forecast-1",
          outcome_id: "outcome-yes",
          multiclass_brier: 0.12,
          log_score: 0.32,
          scorer_version: "phase_a_scorer_v1",
          resolved_at: "2026-06-08T01:00:00Z",
        });
      }
      return jsonResponse({ detail: "unexpected request" }, 500);
    });
    globalThis.fetch = fetchMock;

    render(<App />);

    expect(await screen.findByText("snapshot-hash-1")).toBeInTheDocument();
    const resolvePanel = await screen.findByRole("heading", { name: "Resolve" });
    const panel = resolvePanel.closest(".form-panel");
    expect(panel).not.toBeNull();
    await userEvent.click(within(panel as HTMLElement).getByRole("button", { name: "Resolve" }));

    expect(await screen.findByText("Brier 0.1200")).toBeInTheDocument();
    expect(screen.getByText("phase_a_scorer_v1")).toBeInTheDocument();
  });
});
