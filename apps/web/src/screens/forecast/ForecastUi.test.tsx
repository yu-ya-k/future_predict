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

import { App } from "../../App";
import type {
  EstimateSetResponse,
  ForecastCreateRequest,
  ForecastCurrentResearchPack,
  ForecastDetail,
  ForecastFramingDraft,
  ForecastFramingDraftClarifyingQuestion,
  ForecastFramingDraftResponse,
} from "../../types";

Object.defineProperty(window, "scrollTo", { value: vi.fn(), writable: true });

const ORIGINAL_NAVIGATOR_CLIPBOARD = navigator.clipboard;
const ORIGINAL_DOCUMENT_EXEC_COMMAND = document.execCommand;

const NON_BINARY_OUTCOMES = [
  "Launch by 2026 Q4",
  "Delayed beyond 2026 Q4",
  "Launch canceled",
];

function jsonResponse(data: unknown, status = 200): Response {
  return {
    ok: status >= 200 && status < 300,
    status,
    json: () => Promise.resolve(data),
  } as Response;
}

function deferred<T>() {
  let resolve!: (value: T) => void;
  const promise = new Promise<T>((done) => {
    resolve = done;
  });
  return { promise, resolve };
}

function forecastDetail(
  overrides: Partial<ForecastDetail> = {},
): ForecastDetail {
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
    original_execution_prompt: null,
    target_population: null,
    unit_of_analysis: null,
    resolution_criteria: "Official launch announcement.",
    resolution_sources: [],
    decision_context: null,
    confidentiality_class: "public",
    current_research_pack: null,
    current_research_pack_status: null,
    approved_claim_target_link_count: 0,
    outcomes: [
      {
        outcome_id: "outcome-yes",
        label: NON_BINARY_OUTCOMES[0],
        definition: "Launches by Q4.",
        resolution_rule: "resolved by official source",
        normalization_group_id: "norm-1",
        sort_order: 0,
      },
      {
        outcome_id: "outcome-no",
        label: NON_BINARY_OUTCOMES[1],
        definition: "Launches after Q4.",
        resolution_rule: "resolved by official source",
        normalization_group_id: "norm-1",
        sort_order: 1,
      },
      {
        outcome_id: "outcome-canceled",
        label: NON_BINARY_OUTCOMES[2],
        definition: "Launch is canceled before release.",
        resolution_rule: "resolved by official source",
        normalization_group_id: "norm-1",
        sort_order: 2,
      },
    ],
    ...overrides,
  };
}

function estimateSet(
  overrides: Partial<EstimateSetResponse> = {},
): EstimateSetResponse {
  return {
    estimate_set_id: "estimate-set-1",
    forecast_id: "forecast-1",
    status: "draft",
    approved: false,
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

function currentResearchPack(
  overrides: Partial<ForecastCurrentResearchPack> = {},
): ForecastCurrentResearchPack {
  return {
    pack_id: "pack-1",
    research_run_id: "run-1",
    pack_status: "running",
    effective_status: "running",
    research_run_status: "waiting_deep_research",
    pack_created_at: "2026-06-08T00:00:00Z",
    pack_updated_at: "2026-06-08T00:00:00Z",
    research_run_created_at: "2026-06-08T00:00:00Z",
    research_run_updated_at: "2026-06-08T00:00:00Z",
    deep_research_started_at: "2026-06-08T00:05:00Z",
    total_tool_calls: 0,
    estimated_cost_usd: 0,
    done_reason: null,
    last_error: null,
    needs_human_review: false,
    ...overrides,
  };
}

type FramingDraftOverrides = Partial<
  Omit<ForecastFramingDraftResponse, "create_payload" | "draft">
> & {
  create_payload?: Partial<ForecastCreateRequest> | null;
  draft?: Partial<ForecastFramingDraft>;
};

function framingDraft(
  overrides: FramingDraftOverrides = {},
): ForecastFramingDraftResponse {
  const base: ForecastFramingDraftResponse = {
    draft: {
      forecast_prompt: "Forecast whether the product launches.",
      question: "Will the product launch by Q4?",
      resolution_criteria: "Official launch announcement.",
      resolution_sources: ["Official site"],
      target_population: "Product users",
      unit_of_analysis: "Product",
      decision_context: "Roadmap planning",
      outcomes: NON_BINARY_OUTCOMES,
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
      outcomes: NON_BINARY_OUTCOMES,
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
        : ({
            ...base.create_payload,
            ...overrides.create_payload,
          } as ForecastCreateRequest),
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
  Object.defineProperty(navigator, "clipboard", {
    configurable: true,
    value: ORIGINAL_NAVIGATOR_CLIPBOARD,
  });
  Object.defineProperty(document, "execCommand", {
    configurable: true,
    value: ORIGINAL_DOCUMENT_EXEC_COMMAND,
  });
  cleanup();
  localStorage.clear();
});

describe("Forecast UI", () => {
  it("starts new forecasts with a single rough question input", () => {
    window.location.hash = "#/forecasts/new";
    globalThis.fetch = vi.fn();

    render(<App />);

    expect(
      screen.getByRole("heading", { name: "まずはざっくり教えてください" }),
    ).toBeInTheDocument();
    expect(
      screen.getByRole("textbox", { name: /予測したいこと/ }),
    ).toBeInTheDocument();
    expect(
      screen.queryByLabelText("Forecast用の短い問い"),
    ).not.toBeInTheDocument();
    expect(screen.queryByLabelText("解決条件")).not.toBeInTheDocument();
    expect(screen.queryByLabelText("解決時の結果状態")).not.toBeInTheDocument();
    expect(screen.getByText("大枠だけ入力")).toBeInTheDocument();
    expect(screen.getByText("AIがメタデータ抽出")).toBeInTheDocument();
    expect(screen.getByText("不足点だけ確認")).toBeInTheDocument();
    expect(screen.getByText("保存後に承認")).toBeInTheDocument();
    expect(
      screen.getByRole("button", { name: "AIでForecast案を作成" }),
    ).toBeDisabled();
  });

  it("localizes framing warning codes without repeating duplicate messages", async () => {
    window.location.hash = "#/forecasts/new";
    const fetchMock = vi.fn(
      async (url: string | URL | Request, init?: RequestInit) => {
        const path = String(url).replace("http://localhost:8000", "");
        if (path === "/forecasts/framing-drafts" && init?.method === "POST") {
          return jsonResponse(
            framingDraft({
              ready_to_create: false,
              warnings: [
                "required_clarifying_answers_missing",
                " required_clarifying_answers_missing ",
              ],
              draft: { clarifying_questions: [clarifyingQuestion()] },
              create_payload: null,
            }),
          );
        }
        return jsonResponse({ detail: "unexpected request" }, 500);
      },
    );
    globalThis.fetch = fetchMock;

    render(<App />);

    await userEvent.type(
      screen.getByRole("textbox", { name: /予測したいこと/ }),
      "Will the product launch?",
    );
    await userEvent.click(
      screen.getByRole("button", { name: "AIでForecast案を作成" }),
    );

    const localizedWarning =
      "Forecast作成に必要なメタデータがまだ不足しています。追加質問に回答するか、最終編集で必須項目を補ってください。";
    expect(await screen.findByText(localizedWarning)).toBeInTheDocument();
    expect(screen.getAllByText(localizedWarning)).toHaveLength(1);
    expect(
      screen.queryByText("required_clarifying_answers_missing"),
    ).not.toBeInTheDocument();
  });

  it("opens Step 3 before clarifying questions when the API says the draft is ready", async () => {
    window.location.hash = "#/forecasts/new";
    const fetchMock = vi.fn(
      async (url: string | URL | Request, init?: RequestInit) => {
        const path = String(url).replace("http://localhost:8000", "");
        if (path === "/forecasts/framing-drafts" && init?.method === "POST") {
          return jsonResponse(
            framingDraft({
              ready_to_create: true,
              draft: { clarifying_questions: [clarifyingQuestion()] },
            }),
          );
        }
        return jsonResponse({ detail: "unexpected request" }, 500);
      },
    );
    globalThis.fetch = fetchMock;

    render(<App />);

    await userEvent.type(
      screen.getByRole("textbox", { name: /予測したいこと/ }),
      "Will the product launch?",
    );
    await userEvent.click(
      screen.getByRole("button", { name: "AIでForecast案を作成" }),
    );

    expect(
      await screen.findByRole("heading", { name: "保存前のフレーミング確認" }),
    ).toBeInTheDocument();
    expect(
      screen.queryByRole("heading", {
        name: "Forecastメタデータの不足確認",
      }),
    ).not.toBeInTheDocument();
  });

  it("describes same-label clarifying answer fields with their prompt and rationale", async () => {
    window.location.hash = "#/forecasts/new";
    const fetchMock = vi.fn(
      async (url: string | URL | Request, init?: RequestInit) => {
        const path = String(url).replace("http://localhost:8000", "");
        if (path === "/forecasts/framing-drafts" && init?.method === "POST") {
          return jsonResponse(
            framingDraft({
              ready_to_create: false,
              draft: {
                clarifying_questions: [
                  clarifyingQuestion({
                    question_id: "deadline",
                    label: "確認",
                    prompt: "期限はいつですか？",
                    why_needed:
                      "Forecastを公開情報で判定できる期限が必要です。",
                  }),
                  clarifyingQuestion({
                    question_id: "source",
                    label: "確認",
                    prompt: "確認に使う公開ソースは何ですか？",
                    why_needed:
                      "解決時に参照する公開情報を固定するために必要です。",
                  }),
                ],
              },
              create_payload: null,
            }),
          );
        }
        return jsonResponse({ detail: "unexpected request" }, 500);
      },
    );
    globalThis.fetch = fetchMock;

    render(<App />);

    await userEvent.type(
      screen.getByRole("textbox", { name: /予測したいこと/ }),
      "Will the product launch?",
    );
    await userEvent.click(
      screen.getByRole("button", { name: "AIでForecast案を作成" }),
    );

    const answerInputs = await screen.findAllByRole("textbox", {
      name: "確認",
    });
    expect(answerInputs).toHaveLength(2);
    expect(answerInputs[0]).toHaveAccessibleDescription(
      "期限はいつですか？ Forecastを公開情報で判定できる期限が必要です。",
    );
    expect(answerInputs[1]).toHaveAccessibleDescription(
      "確認に使う公開ソースは何ですか？ 解決時に参照する公開情報を固定するために必要です。",
    );
  });

  it("asks draft clarifying questions before final create and separate approval", async () => {
    window.location.hash = "#/forecasts/new";
    const fetchMock = vi.fn(
      async (url: string | URL | Request, init?: RequestInit) => {
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
          const body = JSON.parse(String(init.body));
          expect(body).toMatchObject({
            question: "Will the Forecast app launch by 2026 Q4?",
            original_execution_prompt: "Will the product launch?",
            resolution_criteria: "Official launch announcement.",
            resolution_sources: ["Official site", "Status page"],
            target_population: "Product users",
            unit_of_analysis: "Product",
            decision_context: "Roadmap planning",
            confidentiality_class: "public",
          });
          expect(body.outcomes).toEqual(NON_BINARY_OUTCOMES);
          return jsonResponse({
            forecast_id: "forecast-1",
            status: "framing_pending",
            framing_version: 1,
            created_at: "2026-06-08T00:00:00Z",
          });
        }
        if (
          path === "/forecasts/forecast-1" &&
          (!init || init.method === "GET")
        ) {
          return jsonResponse(forecastDetail());
        }
        if (
          path === "/forecasts/forecast-1/review" &&
          init?.method === "POST"
        ) {
          return jsonResponse({
            forecast_id: "forecast-1",
            action: "approve_framing",
            status: "framing_approved",
            approved_framing_version: 1,
          });
        }
        return jsonResponse({ detail: "unexpected request" }, 500);
      },
    );
    globalThis.fetch = fetchMock;

    render(<App />);

    expect(screen.getByText("作成の流れ")).toBeInTheDocument();

    await userEvent.type(
      screen.getByRole("textbox", { name: /予測したいこと/ }),
      "Will the product launch?",
    );
    await userEvent.click(
      screen.getByRole("button", { name: "AIでForecast案を作成" }),
    );

    expect(
      await screen.findByRole("heading", {
        name: "Forecastメタデータの不足確認",
      }),
    ).toBeInTheDocument();
    expect(screen.getByText("Step 1の元の依頼")).toBeInTheDocument();
    expect(screen.getByText("Will the product launch?")).toBeInTheDocument();
    expect(screen.getByText("AIが抽出・整理した点")).toBeInTheDocument();
    expect(
      screen.getByText("元の依頼からForecast用の短い問いを抽出しました。"),
    ).toBeInTheDocument();
    expect(
      screen.getByText(
        "元の実行プロンプトは変更しません。ここでは公開情報で解決状態を判定するために不足している期限・対象・ソースなどだけを確認します。",
      ),
    ).toBeInTheDocument();
    expect(screen.getByText("期限を確認してください。")).toBeInTheDocument();
    expect(
      screen.queryByRole("button", { name: "Forecastを作成" }),
    ).not.toBeInTheDocument();

    await userEvent.type(screen.getByLabelText("期限"), "2026 Q4");
    await userEvent.type(
      screen.getByLabelText("対象プロダクト"),
      "Forecast app",
    );
    await userEvent.click(
      screen.getByRole("button", { name: "回答をメタデータ案に反映" }),
    );

    expect(
      await screen.findByRole("heading", { name: "保存前のフレーミング確認" }),
    ).toBeInTheDocument();
    expect(screen.getByText("Step 1の元の依頼")).toBeInTheDocument();
    expect(screen.getByText("Will the product launch?")).toBeInTheDocument();
    expect(
      screen.getByRole("heading", { name: "Forecastメタデータ" }),
    ).toBeInTheDocument();
    const criteriaInput = screen.getByRole("textbox", { name: /解決条件/ });
    expect(criteriaInput).toHaveValue(
      "Official launch announcement.",
    );
    expect(
      screen.getByText(
        "これは元の調査依頼の答えではなく、後で公開情報に照らして選ぶ解決・結果状態です。Yes/Noに限る必要はありません。",
      ),
    ).toBeInTheDocument();
    const outcomesInput = screen.getByRole("textbox", {
      name: /解決時の結果状態/,
    });
    expect(outcomesInput).toHaveValue(NON_BINARY_OUTCOMES.join("\n"));
    await userEvent.clear(outcomesInput);
    expect(
      screen.getByText(/解決時の結果状態を1件以上入力してください/),
    ).toBeInTheDocument();
    expect(outcomesInput).toHaveAttribute("aria-invalid", "true");
    const createButton = screen.getByRole("button", {
      name: "Forecastを作成",
    });
    expect(createButton).toBeDisabled();
    await userEvent.type(outcomesInput, NON_BINARY_OUTCOMES.join("\n"));
    const questionInput = screen.getByRole("textbox", {
      name: /Forecast用の短い問い/,
    });
    await userEvent.clear(questionInput);
    expect(questionInput).toHaveAttribute(
      "aria-describedby",
      "forecast-final-question-help",
    );
    expect(questionInput).toHaveAttribute("aria-invalid", "true");
    expect(
      screen.getByText(/Forecast用の短い問いを入力してください/),
    ).toBeInTheDocument();
    expect(createButton).toBeDisabled();
    await userEvent.type(
      questionInput,
      "Will the Forecast app launch by 2026 Q4?",
    );
    await userEvent.clear(criteriaInput);
    expect(criteriaInput).toHaveAttribute(
      "aria-describedby",
      "forecast-final-criteria-help",
    );
    expect(criteriaInput).toHaveAttribute("aria-invalid", "true");
    expect(screen.getByText(/解決条件を入力してください/)).toBeInTheDocument();
    expect(createButton).toBeDisabled();
    await userEvent.type(criteriaInput, "Official launch announcement.");
    const sourcesInput = screen.getByRole("textbox", {
      name: /解決確認ソース/,
    });
    await userEvent.clear(sourcesInput);
    await userEvent.type(sourcesInput, "Official site\n\nStatus page");
    await userEvent.click(
      screen.getByRole("button", { name: "Forecastを作成" }),
    );

    expect(
      await screen.findByRole("heading", { name: "保存済みプレビュー" }),
    ).toBeInTheDocument();
    expect(
      screen.getByText("Will the product launch by Q4?"),
    ).toBeInTheDocument();
    for (const outcome of NON_BINARY_OUTCOMES) {
      expect(screen.getByText(outcome)).toBeInTheDocument();
    }
    expect(
      screen.getByRole("button", { name: "この内容で承認" }),
    ).toBeEnabled();

    await userEvent.click(
      screen.getByRole("button", { name: "この内容で承認" }),
    );
    await waitFor(() =>
      expect(window.location.hash).toBe("#/forecasts/forecast-1"),
    );
  });

  it("does not reuse an answer when the same question id returns with a changed prompt", async () => {
    window.location.hash = "#/forecasts/new";
    let draftCalls = 0;
    const fetchMock = vi.fn(
      async (url: string | URL | Request, init?: RequestInit) => {
        const path = String(url).replace("http://localhost:8000", "");
        if (path === "/forecasts/framing-drafts" && init?.method === "POST") {
          draftCalls += 1;
          const body = JSON.parse(String(init.body));
          if (draftCalls === 1) {
            return jsonResponse(
              framingDraft({
                ready_to_create: false,
                draft: {
                  clarifying_questions: [
                    clarifyingQuestion({ prompt: "期限はいつですか？" }),
                  ],
                },
                create_payload: null,
              }),
            );
          }
          if (draftCalls === 2) {
            expect(body.answers).toEqual([
              { question_id: "deadline", answer: "2026 Q4" },
            ]);
            return jsonResponse(
              framingDraft({
                ready_to_create: false,
                draft: {
                  clarifying_questions: [
                    clarifyingQuestion({
                      prompt: "公開情報で確認する具体的な日付はいつですか？",
                    }),
                  ],
                },
                create_payload: null,
              }),
            );
          }
          expect(body.answers).toEqual([
            { question_id: "deadline", answer: "2026-12-31" },
          ]);
          return jsonResponse(framingDraft());
        }
        return jsonResponse({ detail: "unexpected request" }, 500);
      },
    );
    globalThis.fetch = fetchMock;

    render(<App />);

    await userEvent.type(
      screen.getByRole("textbox", { name: /予測したいこと/ }),
      "Will the product launch?",
    );
    await userEvent.click(
      screen.getByRole("button", { name: "AIでForecast案を作成" }),
    );
    await userEvent.type(await screen.findByLabelText("期限"), "2026 Q4");
    await userEvent.click(
      screen.getByRole("button", { name: "回答をメタデータ案に反映" }),
    );

    const changedPromptAnswer = await screen.findByLabelText("期限");
    expect(changedPromptAnswer).toHaveValue("");
    expect(
      screen.getByText("公開情報で確認する具体的な日付はいつですか？"),
    ).toBeInTheDocument();

    await userEvent.type(changedPromptAnswer, "2026-12-31");
    await userEvent.click(
      screen.getByRole("button", { name: "回答をメタデータ案に反映" }),
    );

    expect(
      await screen.findByRole("heading", { name: "保存前のフレーミング確認" }),
    ).toBeInTheDocument();
  });

  it("dedupes answer history and caps refine requests to the API limit", async () => {
    window.location.hash = "#/forecasts/new";
    let draftCalls = 0;
    const finalRefineResponse = deferred<Response>();
    const firstQuestions = Array.from({ length: 5 }, (_, index) =>
      clarifyingQuestion({
        question_id: `q${index + 1}`,
        label: `確認${index + 1}`,
        prompt: `確認${index + 1}を入力してください。`,
      }),
    );
    const followUpQuestion = clarifyingQuestion({
      question_id: "q6",
      label: "確認6",
      prompt: "確認6を入力してください。",
    });
    const fetchMock = vi.fn(
      async (url: string | URL | Request, init?: RequestInit) => {
        const path = String(url).replace("http://localhost:8000", "");
        if (path === "/forecasts/framing-drafts" && init?.method === "POST") {
          draftCalls += 1;
          const body = JSON.parse(String(init.body));
          if (draftCalls === 1) {
            return jsonResponse(
              framingDraft({
                ready_to_create: false,
                draft: { clarifying_questions: firstQuestions },
                create_payload: null,
              }),
            );
          }
          if (draftCalls === 2) {
            expect(body.answers).toHaveLength(5);
            expect(
              new Set(
                body.answers.map(
                  (answer: { question_id: string }) => answer.question_id,
                ),
              ).size,
            ).toBe(5);
            return jsonResponse(
              framingDraft({
                ready_to_create: false,
                draft: { clarifying_questions: [followUpQuestion] },
                create_payload: null,
              }),
            );
          }
          expect(body.answers).toHaveLength(5);
          expect(
            new Set(
              body.answers.map(
                (answer: { question_id: string }) => answer.question_id,
              ),
            ).size,
          ).toBe(5);
          expect(body.answers).toEqual(
            expect.arrayContaining([
              { question_id: "q6", answer: "answer 6" },
            ]),
          );
          return finalRefineResponse.promise;
        }
        return jsonResponse({ detail: "unexpected request" }, 500);
      },
    );
    globalThis.fetch = fetchMock;

    render(<App />);

    await userEvent.type(
      screen.getByRole("textbox", { name: /予測したいこと/ }),
      "Will the product launch?",
    );
    await userEvent.click(
      screen.getByRole("button", { name: "AIでForecast案を作成" }),
    );
    for (let index = 1; index <= 5; index += 1) {
      await userEvent.type(
        await screen.findByLabelText(`確認${index}`),
        `answer ${index}`,
      );
    }
    await userEvent.click(
      screen.getByRole("button", { name: "回答をメタデータ案に反映" }),
    );

    await userEvent.type(await screen.findByLabelText("確認6"), "answer 6");
    await userEvent.click(
      screen.getByRole("button", { name: "回答をメタデータ案に反映" }),
    );

    const progress = await screen.findByRole("region", {
      name: "回答を反映中",
    });
    expect(within(progress).getByText("1/1件")).toBeInTheDocument();
    expect(within(progress).queryByText("5/1件")).not.toBeInTheDocument();

    await act(async () => {
      finalRefineResponse.resolve(jsonResponse(framingDraft()));
    });

    expect(
      await screen.findByRole("heading", { name: "保存前のフレーミング確認" }),
    ).toBeInTheDocument();
  });

  it("shows a concise progress status while creating the initial AI draft", async () => {
    window.location.hash = "#/forecasts/new";
    const draftResponse = deferred<Response>();
    const fetchMock = vi.fn(
      (url: string | URL | Request, init?: RequestInit) => {
        const path = String(url).replace("http://localhost:8000", "");
        if (path === "/forecasts/framing-drafts" && init?.method === "POST") {
          return draftResponse.promise;
        }
        return Promise.resolve(
          jsonResponse({ detail: "unexpected request" }, 500),
        );
      },
    );
    globalThis.fetch = fetchMock;

    render(<App />);

    await userEvent.type(
      screen.getByRole("textbox", { name: /予測したいこと/ }),
      "Will the product launch?",
    );
    await userEvent.click(
      screen.getByRole("button", { name: "AIでForecast案を作成" }),
    );

    expect(await screen.findByText("AI応答待ち")).toBeInTheDocument();
    expect(screen.getByRole("status")).toHaveTextContent(
      "AI応答待ち。1/4完了。現在実行中: AIメタデータ抽出。",
    );
    const progress = screen.getByRole("region", { name: "AI応答待ち" });
    const progressList = within(progress).getByRole("list", {
      name: "Forecast作成フロー",
    });
    expect(within(progressList).getAllByRole("listitem")).toHaveLength(4);
    expect(screen.getByText("元の依頼")).toBeInTheDocument();
    expect(screen.getByText("AIメタデータ抽出")).toBeInTheDocument();
    expect(
      screen.getByRole("button", { name: "Forecast案を作成中" }),
    ).toBeDisabled();

    await act(async () => {
      draftResponse.resolve(jsonResponse(framingDraft()));
    });

    expect(
      await screen.findByRole("heading", { name: "保存前のフレーミング確認" }),
    ).toBeInTheDocument();
  });

  it("shows a DAG-style progress panel while reflecting answers into the draft", async () => {
    window.location.hash = "#/forecasts/new";
    const refineResponse = deferred<Response>();
    const fetchMock = vi.fn(
      (url: string | URL | Request, init?: RequestInit) => {
        const path = String(url).replace("http://localhost:8000", "");
        if (path === "/forecasts/framing-drafts" && init?.method === "POST") {
          const body = JSON.parse(String(init.body));
          if (!body.previous_draft) {
            return Promise.resolve(
              jsonResponse(
                framingDraft({
                  ready_to_create: false,
                  draft: { clarifying_questions: [clarifyingQuestion()] },
                  create_payload: null,
                }),
              ),
            );
          }
          return refineResponse.promise;
        }
        return Promise.resolve(
          jsonResponse({ detail: "unexpected request" }, 500),
        );
      },
    );
    globalThis.fetch = fetchMock;

    render(<App />);

    await userEvent.type(
      screen.getByRole("textbox", { name: /予測したいこと/ }),
      "Will the product launch?",
    );
    await userEvent.click(
      screen.getByRole("button", { name: "AIでForecast案を作成" }),
    );
    await userEvent.type(await screen.findByLabelText("期限"), "2026 Q4");
    await userEvent.click(
      screen.getByRole("button", { name: "回答をメタデータ案に反映" }),
    );

    expect(await screen.findByText("回答を反映中")).toBeInTheDocument();
    expect(screen.getByRole("status")).toHaveTextContent(
      "回答を反映中。2/4完了。現在実行中: AIメタデータ更新。",
    );
    const progress = screen.getByRole("region", { name: "回答を反映中" });
    expect(
      within(progress).getByRole("heading", { name: "回答を反映中" }),
    ).toBeInTheDocument();
    expect(within(progress).getByText("元の依頼")).toBeInTheDocument();
    expect(within(progress).getByText("追加回答")).toBeInTheDocument();
    expect(within(progress).getByText("AIメタデータ更新")).toBeInTheDocument();
    expect(within(progress).getByText("保存前確認")).toBeInTheDocument();
    const progressList = within(progress).getByRole("list", {
      name: "Forecast作成フロー",
    });
    expect(within(progressList).getAllByRole("listitem")).toHaveLength(4);
    expect(
      screen.getByRole("button", { name: "Forecast案を更新中" }),
    ).toBeDisabled();

    await act(async () => {
      refineResponse.resolve(jsonResponse(framingDraft()));
    });

    expect(
      await screen.findByRole("heading", { name: "保存前のフレーミング確認" }),
    ).toBeInTheDocument();
  });

  it("shows a concise progress status while saving the forecast", async () => {
    window.location.hash = "#/forecasts/new";
    const createResponse = deferred<Response>();
    const fetchMock = vi.fn(
      (url: string | URL | Request, init?: RequestInit) => {
        const path = String(url).replace("http://localhost:8000", "");
        if (path === "/forecasts/framing-drafts" && init?.method === "POST") {
          return Promise.resolve(jsonResponse(framingDraft()));
        }
        if (path === "/forecasts" && init?.method === "POST") {
          return createResponse.promise;
        }
        if (
          path === "/forecasts/forecast-1" &&
          (!init || init.method === "GET")
        ) {
          return Promise.resolve(jsonResponse(forecastDetail()));
        }
        return Promise.resolve(
          jsonResponse({ detail: "unexpected request" }, 500),
        );
      },
    );
    globalThis.fetch = fetchMock;

    render(<App />);

    await userEvent.type(
      screen.getByRole("textbox", { name: /予測したいこと/ }),
      "Will the product launch?",
    );
    await userEvent.click(
      screen.getByRole("button", { name: "AIでForecast案を作成" }),
    );
    await userEvent.click(
      await screen.findByRole("button", { name: "Forecastを作成" }),
    );

    expect(await screen.findByText("Forecastを保存中")).toBeInTheDocument();
    expect(screen.getByRole("status")).toHaveTextContent(
      "Forecastを保存中。2/4完了。現在実行中: Forecast保存。",
    );
    expect(screen.getByText("メタデータ")).toBeInTheDocument();
    expect(screen.getByText("Forecast保存")).toBeInTheDocument();
    expect(
      screen.getByRole("button", { name: "Forecastを作成中" }),
    ).toBeDisabled();

    await act(async () => {
      createResponse.resolve(
        jsonResponse({
          forecast_id: "forecast-1",
          status: "framing_pending",
          framing_version: 1,
          created_at: "2026-06-08T00:00:00Z",
        }),
      );
    });

    expect(
      await screen.findByRole("heading", { name: "保存済みプレビュー" }),
    ).toBeInTheDocument();
  });

  it("preserves rough input when draft creation fails", async () => {
    window.location.hash = "#/forecasts/new";
    const fetchMock = vi.fn(
      async (url: string | URL | Request, init?: RequestInit) => {
        const path = String(url).replace("http://localhost:8000", "");
        if (path === "/forecasts/framing-drafts" && init?.method === "POST") {
          return jsonResponse({ detail: "Draft model unavailable." }, 503);
        }
        return jsonResponse({ detail: "unexpected request" }, 500);
      },
    );
    globalThis.fetch = fetchMock;

    render(<App />);

    const roughInput = screen.getByRole("textbox", { name: /予測したいこと/ });
    await userEvent.type(roughInput, "Keep this rough idea");
    await userEvent.click(
      screen.getByRole("button", { name: "AIでForecast案を作成" }),
    );

    expect(await screen.findByRole("alert")).toHaveTextContent(
      "Draft model unavailable.",
    );
    expect(screen.getByRole("textbox", { name: /予測したいこと/ })).toHaveValue(
      "Keep this rough idea",
    );
  });

  it("formats rough question length validation errors", async () => {
    window.location.hash = "#/forecasts/new";
    const fetchMock = vi.fn(
      async (url: string | URL | Request, init?: RequestInit) => {
        const path = String(url).replace("http://localhost:8000", "");
        if (path === "/forecasts/framing-drafts" && init?.method === "POST") {
          return jsonResponse(
            {
              detail: [
                {
                  type: "string_too_long",
                  loc: ["body", "rough_question"],
                  msg: "String should have at most 50000 characters",
                  input: "hidden-user-prompt",
                  ctx: { max_length: 50000 },
                },
              ],
            },
            422,
          );
        }
        return jsonResponse({ detail: "unexpected request" }, 500);
      },
    );
    globalThis.fetch = fetchMock;

    render(<App />);

    await userEvent.type(
      screen.getByRole("textbox", { name: /予測したいこと/ }),
      "Long idea",
    );
    await userEvent.click(
      screen.getByRole("button", { name: "AIでForecast案を作成" }),
    );

    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent("入力が長すぎます");
    expect(alert).toHaveTextContent("50,000文字以内");
    expect(alert).not.toHaveTextContent("hidden-user-prompt");
    expect(alert).not.toHaveTextContent("string_too_long");
  });

  it("creates from manual final edit after the user fills missing core metadata", async () => {
    window.location.hash = "#/forecasts/new";
    const fetchMock = vi.fn(
      async (url: string | URL | Request, init?: RequestInit) => {
        const path = String(url).replace("http://localhost:8000", "");
        if (path === "/forecasts/framing-drafts" && init?.method === "POST") {
          return jsonResponse(
            framingDraft({
              ready_to_create: false,
              warnings: ["公開情報で判定できる期限が不足しています。"],
              draft: { question: "", clarifying_questions: [] },
              create_payload: null,
            }),
          );
        }
        if (path === "/forecasts" && init?.method === "POST") {
          const body = JSON.parse(String(init.body));
          expect(body).toMatchObject({
            question: "Will the product launch by Q4?",
            original_execution_prompt: "Maybe launch?",
            resolution_criteria: "Official launch announcement.",
            resolution_sources: ["Official site"],
            target_population: "Product users",
            unit_of_analysis: "Product",
            decision_context: "Roadmap planning",
            confidentiality_class: "public",
          });
          expect(body.outcomes).toEqual(NON_BINARY_OUTCOMES);
          return jsonResponse({
            forecast_id: "forecast-1",
            status: "framing_pending",
            framing_version: 1,
            created_at: "2026-06-08T00:00:00Z",
          });
        }
        if (
          path === "/forecasts/forecast-1" &&
          (!init || init.method === "GET")
        ) {
          return jsonResponse(forecastDetail());
        }
        return jsonResponse({ detail: "unexpected request" }, 500);
      },
    );
    globalThis.fetch = fetchMock;

    render(<App />);

    await userEvent.type(
      screen.getByRole("textbox", { name: /予測したいこと/ }),
      "Maybe launch?",
    );
    await userEvent.click(
      screen.getByRole("button", { name: "AIでForecast案を作成" }),
    );

    expect(
      await screen.findByRole("heading", { name: "大枠を調整" }),
    ).toBeInTheDocument();
    expect(
      screen.getByText("公開情報で判定できる期限が不足しています。"),
    ).toBeInTheDocument();
    expect(
      screen.queryByRole("button", { name: "Forecastを作成" }),
    ).not.toBeInTheDocument();

    await userEvent.click(
      screen.getByRole("button", { name: "最終編集を開く" }),
    );

    expect(
      await screen.findByRole("heading", { name: "保存前のフレーミング確認" }),
    ).toBeInTheDocument();
    expect(
      screen.getByText(/必須メタデータが入力済みなら/),
    ).toBeInTheDocument();
    const questionInput = screen.getByRole("textbox", {
      name: /Forecast用の短い問い/,
    });
    expect(questionInput).toHaveValue("");
    expect(
      screen.getByText(/Forecast用の短い問いを入力してください/),
    ).toBeInTheDocument();
    expect(
      screen.getByRole("button", { name: "Forecastを作成" }),
    ).toBeDisabled();

    await userEvent.type(questionInput, "Will the product launch by Q4?");

    expect(
      screen.getByRole("button", { name: "Forecastを作成" }),
    ).toBeEnabled();

    await userEvent.click(
      screen.getByRole("button", { name: "Forecastを作成" }),
    );

    expect(
      await screen.findByRole("heading", { name: "保存済みプレビュー" }),
    ).toBeInTheDocument();
  });

  it("keeps the first rough input as original prompt across retry and create payload conflicts", async () => {
    window.location.hash = "#/forecasts/new";
    let draftCalls = 0;
    const fetchMock = vi.fn(
      async (url: string | URL | Request, init?: RequestInit) => {
        const path = String(url).replace("http://localhost:8000", "");
        if (path === "/forecasts/framing-drafts" && init?.method === "POST") {
          draftCalls += 1;
          const body = JSON.parse(String(init.body));
          if (draftCalls === 1) {
            expect(body).toMatchObject({
              rough_question: "Initial launch prompt",
              locale: "ja",
            });
            return jsonResponse(
              framingDraft({
                ready_to_create: false,
                warnings: ["判定期限を確認してください。"],
                draft: { clarifying_questions: [] },
                create_payload: null,
              }),
            );
          }
          expect(body).toMatchObject({
            rough_question: "Retry prompt with deadline",
            locale: "ja",
          });
          expect(body).not.toHaveProperty("previous_draft");
          return jsonResponse(
            framingDraft({
              create_payload: {
                original_execution_prompt: "API-provided conflicting prompt",
              },
            }),
          );
        }
        if (path === "/forecasts" && init?.method === "POST") {
          expect(JSON.parse(String(init.body))).toMatchObject({
            question: "Will the product launch by Q4?",
            original_execution_prompt: "Initial launch prompt",
          });
          return jsonResponse({
            forecast_id: "forecast-1",
            status: "framing_pending",
            framing_version: 1,
            created_at: "2026-06-08T00:00:00Z",
          });
        }
        if (
          path === "/forecasts/forecast-1" &&
          (!init || init.method === "GET")
        ) {
          return jsonResponse(
            forecastDetail({
              original_execution_prompt: "Initial launch prompt",
            }),
          );
        }
        return jsonResponse({ detail: "unexpected request" }, 500);
      },
    );
    globalThis.fetch = fetchMock;

    render(<App />);

    await userEvent.type(
      screen.getByRole("textbox", { name: /予測したいこと/ }),
      "Initial launch prompt",
    );
    await userEvent.click(
      screen.getByRole("button", { name: "AIでForecast案を作成" }),
    );

    expect(
      await screen.findByRole("heading", { name: "大枠を調整" }),
    ).toBeInTheDocument();
    expect(
      screen.getAllByText("Initial launch prompt").length,
    ).toBeGreaterThanOrEqual(1);

    const retryInput = screen.getByRole("textbox", { name: /予測したいこと/ });
    await userEvent.clear(retryInput);
    await userEvent.type(retryInput, "Retry prompt with deadline");
    await userEvent.click(
      screen.getByRole("button", { name: "AIでForecast案を再作成" }),
    );

    expect(
      await screen.findByRole("heading", { name: "保存前のフレーミング確認" }),
    ).toBeInTheDocument();
    expect(screen.getByText("Initial launch prompt")).toBeInTheDocument();
    expect(
      screen.queryByText("API-provided conflicting prompt"),
    ).not.toBeInTheDocument();

    await userEvent.click(
      screen.getByRole("button", { name: "Forecastを作成" }),
    );

    expect(
      await screen.findByRole("heading", { name: "保存済みプレビュー" }),
    ).toBeInTheDocument();
  });

  it("shows the PhaseA execution flow on forecast detail pages", async () => {
    window.location.hash = "#/forecasts/forecast-1";
    const fetchMock = vi.fn(
      async (url: string | URL | Request, init?: RequestInit) => {
        const path = String(url).replace("http://localhost:8000", "");
        if (
          path === "/forecasts/forecast-1" &&
          (!init || init.method === "GET")
        ) {
          return jsonResponse(
            forecastDetail({
              status: "framing_approved",
              approved_framing_version: 1,
            }),
          );
        }
        return jsonResponse({ detail: "unexpected request" }, 500);
      },
    );
    globalThis.fetch = fetchMock;

    render(<App />);

    expect(
      await screen.findByRole("heading", {
        name: "公開情報の収集方法を選択",
        level: 2,
      }),
    ).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /アプリで自動収集/ })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /ChatGPTで手動収集/ })).toBeInTheDocument();
    expect(
      screen.getByRole("button", { name: "公開情報の収集を開始" }),
    ).toBeEnabled();
    expect(screen.queryByRole("button", { name: "公開情報パック投入" })).toBeNull();

    const flow = await screen.findByRole("region", {
      name: "全体フロー",
    });
    expect(flow).toHaveClass("forecast-flow-progress--wrapped");
    const flowList = within(flow).getByRole("list", {
      name: "Forecast実行フロー",
    });
    const flowItems = within(flowList)
      .getAllByRole("listitem")
      .filter((item) => item.parentElement === flowList);
    expect(flowItems).toHaveLength(9);
    expect(
      flowItems.map(
        (item) => within(item).getByRole("heading", { level: 4 }).textContent,
      ),
    ).toEqual([
      "フレーミング承認",
      "公開情報の収集",
      "証拠を抽出",
      "シナリオを生成",
      "主張と結果の対応を承認",
      "確率を計算",
      "推定結果を承認",
      "予測版を確定",
      "実績結果で解決",
    ]);
    expect(within(flowItems[0]).getByText("完了")).toBeInTheDocument();
    expect(within(flowItems[1]).getByText("次に実行")).toBeInTheDocument();
    expect(within(flowItems[2]).getByText("待機")).toBeInTheDocument();
    expect(within(flow).getByText("1/9 完了")).toBeInTheDocument();
    expect(within(flow).getByText("未収集")).toBeInTheDocument();
    expect(flow.querySelectorAll(".forecast-flow-edge--wrap-break")).toHaveLength(2);
    expect(screen.queryByText("次にやること")).not.toBeInTheDocument();
    expect(screen.queryByText("実行できます")).not.toBeInTheDocument();
  });

  it("imports manual public information and advances to evidence extraction", async () => {
    window.location.hash = "#/forecasts/forecast-1";
    let imported = false;
    const writeText = vi.fn().mockRejectedValue(new Error("blocked"));
    const execCommand = vi.fn().mockReturnValue(true);
    Object.defineProperty(navigator, "clipboard", {
      configurable: true,
      value: { writeText },
    });
    Object.defineProperty(document, "execCommand", {
      configurable: true,
      value: execCommand,
    });
    const fetchMock = vi.fn(
      async (url: string | URL | Request, init?: RequestInit) => {
        const path = String(url).replace("http://localhost:8000", "");
        if (
          path === "/forecasts/forecast-1" &&
          (!init || init.method === "GET")
        ) {
          return jsonResponse(
            forecastDetail({
              status: imported ? "pack_running" : "framing_approved",
              approved_framing_version: 1,
              current_research_pack: imported
                ? currentResearchPack({
                    pack_status: "completed",
                    effective_status: "completed",
                    research_run_status: "completed",
                    total_tool_calls: 0,
                  })
                : null,
              current_research_pack_status: imported ? "completed" : null,
            }),
          );
        }
        if (
          path === "/forecasts/forecast-1/research-packs/manual-prompt" &&
          (!init || init.method === "GET")
        ) {
          return jsonResponse({
            forecast_id: "forecast-1",
            framing_version: 1,
            prompt: "Manual Deep Research prompt",
            prompt_sha256: "prompt-hash",
            prompt_version: "current_state_pack_v1",
            pack_role: "current_state",
            tool_profile: "public",
            max_report_chars: 50000,
            max_file_bytes: 1048576,
          });
        }
        if (
          path === "/forecasts/forecast-1/research-packs/manual-import" &&
          init?.method === "POST"
        ) {
          expect(init.body).toBeInstanceOf(FormData);
          const body = init.body as FormData;
          expect(body.get("prompt_sha256")).toBe("prompt-hash");
          expect(body.get("report_text")).toBe(
            "Manual report from ChatGPT Deep Research.",
          );
          expect(body.has("report_file")).toBe(false);
          imported = true;
          return jsonResponse({
            pack_id: "pack-1",
            forecast_id: "forecast-1",
            research_run_id: "run-1",
            pack_role: "current_state",
            tool_profile: "public",
            status: "completed",
            policy_decision_id: "policy-1",
          });
        }
        return jsonResponse({ detail: "unexpected request" }, 500);
      },
    );
    globalThis.fetch = fetchMock;

    render(<App />);

    await screen.findByRole("heading", {
      name: "公開情報の収集方法を選択",
      level: 2,
    });
    const modeGroup = screen.getByRole("group", { name: "公開情報の収集方法" });
    const autoMode = within(modeGroup).getByRole("button", {
      name: /アプリで自動収集/,
    });
    const manualMode = within(modeGroup).getByRole("button", {
      name: /ChatGPTで手動収集/,
    });
    expect(autoMode).toHaveAttribute("aria-pressed", "true");
    expect(manualMode).toHaveAttribute("aria-pressed", "false");
    expect(within(modeGroup).queryByRole("tab")).not.toBeInTheDocument();
    await userEvent.click(manualMode);

    expect(manualMode).toHaveAttribute("aria-pressed", "true");
    expect(
      await screen.findByRole("textbox", {
        name: "ChatGPT Deep Researchへ渡すPrompt",
      }),
    ).toHaveValue("Manual Deep Research prompt");
    await userEvent.click(screen.getByRole("button", { name: "Promptをコピー" }));
    expect(writeText).toHaveBeenCalledWith("Manual Deep Research prompt");
    expect(execCommand).toHaveBeenCalledWith("copy");
    expect(await screen.findByText("コピーしました")).toBeInTheDocument();
    await userEvent.type(
      screen.getByLabelText("結果を貼り付け"),
      "Manual report from ChatGPT Deep Research.",
    );
    await userEvent.click(screen.getByRole("button", { name: "結果を取り込む" }));

    expect(
      await screen.findByRole("heading", {
        name: "公開情報の収集が完了しました",
        level: 2,
      }),
    ).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "証拠を抽出" })).toBeEnabled();
    expect(screen.getByRole("link", { name: "取り込み記録" })).toHaveAttribute(
      "href",
      "#/runs/run-1",
    );
  });

  it("clears the actual manual report file input when text is typed and after successful file import", async () => {
    window.location.hash = "#/forecasts/forecast-1";
    const importBodyRef: { current: FormData | null } = { current: null };
    const fetchMock = vi.fn(
      async (url: string | URL | Request, init?: RequestInit) => {
        const path = String(url).replace("http://localhost:8000", "");
        if (
          path === "/forecasts/forecast-1" &&
          (!init || init.method === "GET")
        ) {
          return jsonResponse(
            forecastDetail({
              status: "framing_approved",
              approved_framing_version: 1,
            }),
          );
        }
        if (
          path === "/forecasts/forecast-1/research-packs/manual-prompt" &&
          (!init || init.method === "GET")
        ) {
          return jsonResponse({
            forecast_id: "forecast-1",
            framing_version: 1,
            prompt: "Manual Deep Research prompt",
            prompt_sha256: "prompt-hash",
            prompt_version: "current_state_pack_v1",
            pack_role: "current_state",
            tool_profile: "public",
            max_report_chars: 50000,
            max_file_bytes: 1048576,
          });
        }
        if (
          path === "/forecasts/forecast-1/research-packs/manual-import" &&
          init?.method === "POST"
        ) {
          expect(init.body).toBeInstanceOf(FormData);
          importBodyRef.current = init.body as FormData;
          return jsonResponse({
            pack_id: "pack-1",
            forecast_id: "forecast-1",
            research_run_id: "run-1",
            pack_role: "current_state",
            tool_profile: "public",
            status: "completed",
            policy_decision_id: "policy-1",
          });
        }
        return jsonResponse({ detail: "unexpected request" }, 500);
      },
    );
    globalThis.fetch = fetchMock;

    render(<App />);

    await screen.findByRole("heading", {
      name: "公開情報の収集方法を選択",
      level: 2,
    });
    await userEvent.click(screen.getByRole("button", { name: /ChatGPTで手動収集/ }));
    await screen.findByRole("textbox", {
      name: "ChatGPT Deep Researchへ渡すPrompt",
    });

    const fileInput = screen.getByLabelText(
      "md/txtをアップロード",
    ) as HTMLInputElement;
    const reportText = screen.getByLabelText(
      "結果を貼り付け",
    ) as HTMLTextAreaElement;
    const firstFile = new File(["Old file report"], "old-report.md", {
      type: "text/markdown",
    });
    await userEvent.upload(fileInput, firstFile);

    expect(fileInput.files).toHaveLength(1);
    expect(fileInput.files?.[0]).toBe(firstFile);
    expect(screen.getByText("選択中: old-report.md")).toBeInTheDocument();

    await userEvent.type(reportText, "Typed report replaces the file.");

    expect(fileInput.files).toHaveLength(0);
    expect(screen.queryByText(/選択中:/)).not.toBeInTheDocument();

    const importedFile = new File(["Imported file report"], "imported.md", {
      type: "text/markdown",
    });
    await userEvent.upload(fileInput, importedFile);

    expect(reportText).toHaveValue("");
    expect(fileInput.files).toHaveLength(1);
    expect(fileInput.files?.[0]).toBe(importedFile);

    await userEvent.click(screen.getByRole("button", { name: "結果を取り込む" }));

    await waitFor(() => expect(importBodyRef.current).not.toBeNull());
    const importBody = importBodyRef.current;
    if (!importBody) throw new Error("manual import FormData was not captured");
    expect(importBody.get("prompt_sha256")).toBe("prompt-hash");
    expect(importBody.has("report_text")).toBe(false);
    expect(importBody.get("report_file")).toBe(importedFile);
    await waitFor(() => expect(fileInput.files).toHaveLength(0));
    expect(reportText).toHaveValue("");
    expect(screen.getByRole("button", { name: "結果を取り込む" })).toBeDisabled();
  });

  it("resets manual prompt and report state when moving between forecast routes", async () => {
    window.location.hash = "#/forecasts/forecast-1";
    const promptForecastIds: string[] = [];
    const fetchMock = vi.fn(
      async (url: string | URL | Request, init?: RequestInit) => {
        const path = String(url).replace("http://localhost:8000", "");
        const forecastMatch = path.match(/^\/forecasts\/([^/]+)$/);
        if (forecastMatch && (!init || init.method === "GET")) {
          const forecastId = forecastMatch[1];
          return jsonResponse(
            forecastDetail({
              forecast_id: forecastId,
              question: `Question for ${forecastId}`,
              status: "framing_approved",
              approved_framing_version: 1,
            }),
          );
        }
        const promptMatch = path.match(
          /^\/forecasts\/([^/]+)\/research-packs\/manual-prompt$/,
        );
        if (promptMatch && (!init || init.method === "GET")) {
          const forecastId = promptMatch[1];
          promptForecastIds.push(forecastId);
          return jsonResponse({
            forecast_id: forecastId,
            framing_version: 1,
            prompt: `Manual prompt for ${forecastId}`,
            prompt_sha256: `${forecastId}-prompt-hash`,
            prompt_version: "current_state_pack_v1",
            pack_role: "current_state",
            tool_profile: "public",
            max_report_chars: 50000,
            max_file_bytes: 1048576,
          });
        }
        return jsonResponse({ detail: "unexpected request" }, 500);
      },
    );
    globalThis.fetch = fetchMock;

    render(<App />);

    expect(await screen.findByText("Question for forecast-1")).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: /ChatGPTで手動収集/ }));
    expect(
      await screen.findByRole("textbox", {
        name: "ChatGPT Deep Researchへ渡すPrompt",
      }),
    ).toHaveValue("Manual prompt for forecast-1");
    await userEvent.type(screen.getByLabelText("結果を貼り付け"), "Old report");

    act(() => {
      window.location.hash = "#/forecasts/forecast-2";
      window.dispatchEvent(new HashChangeEvent("hashchange"));
    });

    expect(await screen.findByText("Question for forecast-2")).toBeInTheDocument();
    const modeGroup = screen.getByRole("group", { name: "公開情報の収集方法" });
    expect(
      within(modeGroup).getByRole("button", { name: /アプリで自動収集/ }),
    ).toHaveAttribute("aria-pressed", "true");
    expect(
      screen.queryByRole("textbox", {
        name: "ChatGPT Deep Researchへ渡すPrompt",
      }),
    ).not.toBeInTheDocument();

    await userEvent.click(
      within(modeGroup).getByRole("button", { name: /ChatGPTで手動収集/ }),
    );

    expect(
      await screen.findByRole("textbox", {
        name: "ChatGPT Deep Researchへ渡すPrompt",
      }),
    ).toHaveValue("Manual prompt for forecast-2");
    expect(screen.getByLabelText("結果を貼り付け")).toHaveValue("");
    expect(promptForecastIds).toEqual(["forecast-1", "forecast-2"]);
  });

  it("separates pack submission from a backend-running research pack", async () => {
    window.location.hash = "#/forecasts/forecast-1";
    const packRequest = deferred<Response>();
    let submitted = false;
    const fetchMock = vi.fn(
      async (url: string | URL | Request, init?: RequestInit) => {
        const path = String(url).replace("http://localhost:8000", "");
        if (
          path === "/forecasts/forecast-1" &&
          (!init || init.method === "GET")
        ) {
          return jsonResponse(
            forecastDetail({
              status: submitted ? "pack_running" : "framing_approved",
              approved_framing_version: 1,
              current_research_pack: submitted
                ? {
                    pack_id: "pack-1",
                    research_run_id: "run-1",
                    pack_status: "running",
                    effective_status: "running",
                    research_run_status: "waiting_deep_research",
                    pack_created_at: "2026-06-08T00:00:00Z",
                    pack_updated_at: "2026-06-08T00:00:00Z",
                    research_run_created_at: "2026-06-08T00:02:00Z",
                    research_run_updated_at: "2026-06-08T00:02:00Z",
                    deep_research_started_at: "2026-06-08T00:05:00Z",
                    total_tool_calls: 1,
                    estimated_cost_usd: 0,
                    done_reason: null,
                    needs_human_review: false,
                  }
                : null,
              current_research_pack_status: submitted ? "running" : null,
            }),
          );
        }
        if (
          path === "/forecasts/forecast-1/research-packs" &&
          init?.method === "POST"
        ) {
          return packRequest.promise;
        }
        return jsonResponse({ detail: "unexpected request" }, 500);
      },
    );
    globalThis.fetch = fetchMock;

    render(<App />);

    const packButton = await screen.findByRole("button", {
      name: "公開情報の収集を開始",
    });
    await userEvent.click(packButton);

    const flow = await screen.findByRole("region", {
      name: "全体フロー",
    });
    const flowList = within(flow).getByRole("list", {
      name: "Forecast実行フロー",
    });
    const flowItems = within(flowList)
      .getAllByRole("listitem")
      .filter((item) => item.parentElement === flowList);
    const packItem = flowItems[1];
    expect(within(packItem).getByText("登録中")).toBeInTheDocument();
    expect(within(packItem).getByText("サーバーに登録中")).toBeInTheDocument();
    expect(within(packItem).queryByText("実行中")).not.toBeInTheDocument();
    expect(screen.getAllByText("サーバーに登録中").length).toBeGreaterThanOrEqual(2);
    expect(
      screen.getByRole("heading", { name: "公開情報をサーバーに登録中" }),
    ).toBeInTheDocument();
    expect(
      screen.getByText(
        "Research Packを作成するリクエストを送っています。登録されるとResearch run IDと開始時刻が表示されます。",
      ),
    ).toBeInTheDocument();
    expect(screen.getByText("経過時間")).toBeInTheDocument();
    expect(
      screen.getByRole("button", { name: "状態を再確認" }),
    ).toBeInTheDocument();
    expect(
      screen.queryByText("公開情報を収集中です"),
    ).not.toBeInTheDocument();

    submitted = true;
    packRequest.resolve(
      jsonResponse({
        pack_id: "pack-1",
        forecast_id: "forecast-1",
        research_run_id: "run-1",
        pack_role: "current_state",
        tool_profile: "public",
        status: "running",
        policy_decision_id: "policy-1",
      }),
    );

    expect(
      await screen.findByRole("heading", { name: "公開情報を収集中です" }),
    ).toBeInTheDocument();
    expect(
      screen.getByRole("link", { name: "Research runを開く" }),
    ).toHaveAttribute("href", "#/runs/run-1");
  });

  it("polls pending pack submission until the current research pack appears", async () => {
    window.location.hash = "#/forecasts/forecast-1";
    vi.useFakeTimers();
    const packRequest = deferred<Response>();
    let forecastGetCount = 0;
    let packRequestStarted = false;
    const fetchMock = vi.fn(
      async (url: string | URL | Request, init?: RequestInit) => {
        const path = String(url).replace("http://localhost:8000", "");
        if (
          path === "/forecasts/forecast-1" &&
          (!init || init.method === "GET")
        ) {
          forecastGetCount += 1;
          return jsonResponse(
            forecastDetail({
              status: packRequestStarted ? "pack_running" : "framing_approved",
              approved_framing_version: 1,
              current_research_pack: packRequestStarted
                ? {
                    pack_id: "pack-1",
                    research_run_id: "run-1",
                    pack_status: "completed",
                    effective_status: "completed",
                    research_run_status: "completed",
                    pack_created_at: "2026-06-08T00:00:00Z",
                    pack_updated_at: "2026-06-08T00:00:00Z",
                    research_run_created_at: "2026-06-08T00:00:00Z",
                    research_run_updated_at: "2026-06-08T01:00:00Z",
                    deep_research_started_at: "2026-06-08T00:05:00Z",
                    total_tool_calls: 24,
                    estimated_cost_usd: 2.5,
                    done_reason: "forecast_raw_report_collected",
                    needs_human_review: false,
                  }
                : null,
              current_research_pack_status: packRequestStarted
                ? "completed"
                : null,
            }),
          );
        }
        if (
          path === "/forecasts/forecast-1/research-packs" &&
          init?.method === "POST"
        ) {
          packRequestStarted = true;
          return packRequest.promise;
        }
        return jsonResponse({ detail: "unexpected request" }, 500);
      },
    );
    globalThis.fetch = fetchMock;

    render(<App />);

    await act(async () => {
      await Promise.resolve();
      await Promise.resolve();
    });
    fireEvent.click(
      screen.getByRole("button", {
        name: "公開情報の収集を開始",
      }),
    );
    expect(
      screen.getByRole("heading", { name: "公開情報をサーバーに登録中" }),
    ).toBeInTheDocument();

    await act(async () => {
      await vi.advanceTimersByTimeAsync(1_000);
      await Promise.resolve();
    });

    expect(
      screen.getByRole("heading", { name: "公開情報の収集が完了しました" }),
    ).toBeInTheDocument();
    expect(
      screen.getByRole("button", { name: "証拠を抽出" }),
    ).toBeEnabled();
    expect(forecastGetCount).toBeGreaterThan(1);
  });

  it("keeps slow pending pack submission copy neutral", async () => {
    window.location.hash = "#/forecasts/forecast-1";
    vi.useFakeTimers();
    const packRequest = deferred<Response>();
    const fetchMock = vi.fn(
      async (url: string | URL | Request, init?: RequestInit) => {
        const path = String(url).replace("http://localhost:8000", "");
        if (
          path === "/forecasts/forecast-1" &&
          (!init || init.method === "GET")
        ) {
          return jsonResponse(
            forecastDetail({
              status: "framing_approved",
              approved_framing_version: 1,
            }),
          );
        }
        if (
          path === "/forecasts/forecast-1/research-packs" &&
          init?.method === "POST"
        ) {
          return packRequest.promise;
        }
        return jsonResponse({ detail: "unexpected request" }, 500);
      },
    );
    globalThis.fetch = fetchMock;

    render(<App />);

    await act(async () => {
      await Promise.resolve();
      await Promise.resolve();
    });
    fireEvent.click(
      screen.getByRole("button", {
        name: "公開情報の収集を開始",
      }),
    );
    expect(
      screen.getByRole("heading", { name: "公開情報をサーバーに登録中" }),
    ).toBeInTheDocument();

    await act(async () => {
      await vi.advanceTimersByTimeAsync(31_000);
      await Promise.resolve();
    });

    expect(
      screen.getByRole("heading", {
        name: "サーバー応答待ち。まだForecast Packは確認できません",
      }),
    ).toBeInTheDocument();
    expect(
      screen.getByText(
        "Research Pack作成リクエストへの応答を待っています。最新状態は自動で確認しています。",
      ),
    ).toBeInTheDocument();
  });

  it("shows a submitting current research pack as Deep Research submit waiting", async () => {
    window.location.hash = "#/forecasts/forecast-1";
    const fetchMock = vi.fn(
      async (url: string | URL | Request, init?: RequestInit) => {
        const path = String(url).replace("http://localhost:8000", "");
        if (
          path === "/forecasts/forecast-1" &&
          (!init || init.method === "GET")
        ) {
          return jsonResponse(
            forecastDetail({
              status: "pack_running",
              approved_framing_version: 1,
              current_research_pack: {
                pack_id: "pack-1",
                research_run_id: "run-1",
                pack_status: "running",
                effective_status: "submitting",
                research_run_status: "queued",
                pack_created_at: "2026-06-08T00:00:00Z",
                pack_updated_at: "2026-06-08T00:00:00Z",
                research_run_created_at: "2026-06-08T00:00:00Z",
                research_run_updated_at: "2026-06-08T00:00:00Z",
                deep_research_started_at: null,
                total_tool_calls: 0,
                estimated_cost_usd: 0,
                done_reason: null,
                needs_human_review: false,
              },
              current_research_pack_status: "running",
            }),
          );
        }
        return jsonResponse({ detail: "unexpected request" }, 500);
      },
    );
    globalThis.fetch = fetchMock;

    render(<App />);

    const flow = await screen.findByRole("region", {
      name: "全体フロー",
    });
    const flowList = within(flow).getByRole("list", {
      name: "Forecast実行フロー",
    });
    const flowItems = within(flowList)
      .getAllByRole("listitem")
      .filter((item) => item.parentElement === flowList);
    const packItem = flowItems[1];
    expect(within(packItem).getAllByText("Deep Research送信待ち").length).toBeGreaterThan(1);
    expect(within(packItem).queryByText("登録中")).not.toBeInTheDocument();
    expect(within(packItem).queryByText("実行中")).not.toBeInTheDocument();
    expect(
      screen.getByRole("heading", { name: "Deep Researchへの送信を待っています" }),
    ).toBeInTheDocument();
    expect(screen.getAllByText("Deep Research送信待ち").length).toBeGreaterThan(1);
    expect(screen.getByText("run-1")).toBeInTheDocument();
    expect(
      screen.queryByRole("heading", { name: "公開情報を収集中です" }),
    ).not.toBeInTheDocument();
  });

  it("polls a submitting current research pack until it starts running", async () => {
    window.location.hash = "#/forecasts/forecast-1";
    vi.useFakeTimers();
    let forecastGetCount = 0;
    const fetchMock = vi.fn(
      async (url: string | URL | Request, init?: RequestInit) => {
        const path = String(url).replace("http://localhost:8000", "");
        if (
          path === "/forecasts/forecast-1" &&
          (!init || init.method === "GET")
        ) {
          forecastGetCount += 1;
          const effectiveStatus =
            forecastGetCount === 1 ? "submitting" : "running";
          return jsonResponse(
            forecastDetail({
              status: "pack_running",
              approved_framing_version: 1,
              current_research_pack: currentResearchPack({
                effective_status: effectiveStatus,
                research_run_status:
                  effectiveStatus === "submitting"
                    ? "queued"
                    : "waiting_deep_research",
                deep_research_started_at:
                  effectiveStatus === "submitting"
                    ? null
                    : "2026-06-08T00:05:00Z",
              }),
              current_research_pack_status: effectiveStatus,
            }),
          );
        }
        return jsonResponse({ detail: "unexpected request" }, 500);
      },
    );
    globalThis.fetch = fetchMock;

    render(<App />);

    await act(async () => {
      await Promise.resolve();
      await Promise.resolve();
    });
    expect(
      screen.getByRole("heading", { name: "Deep Researchへの送信を待っています" }),
    ).toBeInTheDocument();
    expect(forecastGetCount).toBe(1);

    await act(async () => {
      await vi.advanceTimersByTimeAsync(1_000);
      await Promise.resolve();
    });

    expect(
      screen.getByRole("heading", { name: "公開情報を収集中です" }),
    ).toBeInTheDocument();
    expect(forecastGetCount).toBe(2);
  });

  it("clears a stale poll error after a successful current pack transition", async () => {
    window.location.hash = "#/forecasts/forecast-1";
    vi.useFakeTimers();
    let forecastGetCount = 0;
    const fetchMock = vi.fn(
      async (url: string | URL | Request, init?: RequestInit) => {
        const path = String(url).replace("http://localhost:8000", "");
        if (
          path === "/forecasts/forecast-1" &&
          (!init || init.method === "GET")
        ) {
          forecastGetCount += 1;
          if (forecastGetCount === 2) {
            return jsonResponse({ detail: "temporary poll failure" }, 503);
          }
          const effectiveStatus =
            forecastGetCount === 1 ? "submitting" : "running";
          return jsonResponse(
            forecastDetail({
              status: "pack_running",
              approved_framing_version: 1,
              current_research_pack: currentResearchPack({
                effective_status: effectiveStatus,
                research_run_status:
                  effectiveStatus === "submitting"
                    ? "queued"
                    : "waiting_deep_research",
                deep_research_started_at:
                  effectiveStatus === "submitting"
                    ? null
                    : "2026-06-08T00:05:00Z",
              }),
              current_research_pack_status: effectiveStatus,
            }),
          );
        }
        return jsonResponse({ detail: "unexpected request" }, 500);
      },
    );
    globalThis.fetch = fetchMock;

    render(<App />);

    await act(async () => {
      await Promise.resolve();
      await Promise.resolve();
    });
    expect(
      screen.getByRole("heading", { name: "Deep Researchへの送信を待っています" }),
    ).toBeInTheDocument();

    await act(async () => {
      await vi.advanceTimersByTimeAsync(1_000);
      await Promise.resolve();
    });

    expect(screen.getByRole("alert")).toHaveTextContent(
      "temporary poll failure",
    );

    await act(async () => {
      await vi.advanceTimersByTimeAsync(1_000);
      await Promise.resolve();
    });

    expect(
      screen.getByRole("heading", { name: "公開情報を収集中です" }),
    ).toBeInTheDocument();
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
    expect(forecastGetCount).toBe(3);
  });

  it("keeps pack_running on the research pack node without enabling evidence extraction", async () => {
    window.location.hash = "#/forecasts/forecast-1";
    vi.spyOn(Date, "now").mockReturnValue(
      Date.parse("2026-06-08T01:15:00Z"),
    );
    const fetchMock = vi.fn(
      async (url: string | URL | Request, init?: RequestInit) => {
        const path = String(url).replace("http://localhost:8000", "");
        if (
          path === "/forecasts/forecast-1" &&
          (!init || init.method === "GET")
        ) {
          return jsonResponse(
            forecastDetail({
              status: "pack_running",
              approved_framing_version: 1,
              current_research_pack: {
                pack_id: "pack-1",
                research_run_id: "run-1",
                pack_status: "running",
                effective_status: "running",
                research_run_status: "waiting_deep_research",
                pack_created_at: "2026-06-08T00:00:00Z",
                pack_updated_at: "2026-06-08T00:00:00Z",
                research_run_created_at: "2026-06-08T00:02:00Z",
                research_run_updated_at: "2026-06-08T00:02:00Z",
                deep_research_started_at: "2026-06-08T00:05:00Z",
                total_tool_calls: 12,
                estimated_cost_usd: 1.25,
                done_reason: null,
                needs_human_review: false,
              },
              current_research_pack_status: "running",
            }),
          );
        }
        return jsonResponse({ detail: "unexpected request" }, 500);
      },
    );
    globalThis.fetch = fetchMock;

    render(<App />);

    const flow = await screen.findByRole("region", {
      name: "全体フロー",
    });
    expect(within(flow).getByText("1/9 完了")).toBeInTheDocument();
    expect(within(flow).getByText("公開情報を収集中")).toBeInTheDocument();
    expect(
      await screen.findByRole("heading", { name: "公開情報を収集中です" }),
    ).toBeInTheDocument();
    expect(
      screen.getByText(/開始時刻/),
    ).toBeInTheDocument();
    expect(
      screen.getByText("70分"),
    ).toBeInTheDocument();
    expect(screen.getByText("12件")).toBeInTheDocument();
    expect(
      screen.getByRole("link", { name: "Research runを開く" }),
    ).toHaveAttribute("href", "#/runs/run-1");
    expect(screen.queryByRole("button", { name: "証拠抽出" })).toBeNull();
    expect(screen.queryByText("次にやること")).not.toBeInTheDocument();
    expect(screen.queryByText("実行できます")).not.toBeInTheDocument();
    expect(screen.queryByText("Next action")).not.toBeInTheDocument();
    expect(
      screen.queryByText(/waiting for current_state pack completion/),
    ).not.toBeInTheDocument();
    expect(
      screen.queryByText(/Evidence extraction will unlock/i),
    ).not.toBeInTheDocument();
  });

  it("does not show a needs_human_review research pack as running in the flow", async () => {
    window.location.hash = "#/forecasts/forecast-1";
    const fetchMock = vi.fn(
      async (url: string | URL | Request, init?: RequestInit) => {
        const path = String(url).replace("http://localhost:8000", "");
        if (
          path === "/forecasts/forecast-1" &&
          (!init || init.method === "GET")
        ) {
          return jsonResponse(
            forecastDetail({
              status: "pack_running",
              approved_framing_version: 1,
              current_research_pack: {
                pack_id: "pack-1",
                research_run_id: "run-1",
                pack_status: "running",
                effective_status: "needs_human_review",
                research_run_status: "needs_human_review",
                pack_created_at: "2026-06-08T00:00:00Z",
                pack_updated_at: "2026-06-08T00:00:00Z",
                research_run_created_at: "2026-06-08T00:00:00Z",
                research_run_updated_at: "2026-06-08T00:30:00Z",
                deep_research_started_at: "2026-06-08T00:05:00Z",
                total_tool_calls: 18,
                estimated_cost_usd: 1.7,
                done_reason: "human_review_required",
                last_error: "APITimeoutError('Request timed out.')",
                needs_human_review: true,
              },
              current_research_pack_status: "needs_human_review",
            }),
          );
        }
        return jsonResponse({ detail: "unexpected request" }, 500);
      },
    );
    globalThis.fetch = fetchMock;

    render(<App />);

    const flow = await screen.findByRole("region", {
      name: "全体フロー",
    });
    const flowList = within(flow).getByRole("list", {
      name: "Forecast実行フロー",
    });
    const flowItems = within(flowList)
      .getAllByRole("listitem")
      .filter((item) => item.parentElement === flowList);
    const packItem = flowItems[1];
    expect(within(packItem).getByText("要対応")).toBeInTheDocument();
    expect(within(packItem).getByText("確認が必要")).toBeInTheDocument();
    expect(within(packItem).queryByText("実行中")).not.toBeInTheDocument();
    expect(
      within(flowItems[2]).queryByText("次に実行"),
    ).not.toBeInTheDocument();
    expect(
      screen.getByRole("heading", { name: "公開情報の収集に確認が必要です" }),
    ).toBeInTheDocument();
    expect(screen.getAllByText("公開情報フェーズ").length).toBeGreaterThan(0);
    expect(screen.getAllByText("要確認").length).toBeGreaterThan(0);
    expect(screen.getByText(/human_review_required/)).toBeInTheDocument();
    expect(screen.getByText(/APITimeoutError/)).toBeInTheDocument();
    const currentStep = screen.getByRole("region", {
      name: "公開情報の収集に確認が必要です",
    });
    expect(
      within(currentStep).getByRole("link", { name: "Research run詳細" }),
    ).toHaveAttribute("href", "#/runs/run-1");
    expect(
      within(currentStep).queryByRole("button", { name: "状態を再確認" }),
    ).toBeNull();
    expect(
      screen.queryByRole("button", { name: "証拠抽出" }),
    ).toBeNull();
  });

  it("recovers a blocked research pack with manual ChatGPT import", async () => {
    window.location.hash = "#/forecasts/forecast-1";
    let imported = false;
    const recoveryPromptHash = "a".repeat(64);
    Object.defineProperty(navigator, "clipboard", {
      configurable: true,
      value: { writeText: vi.fn().mockRejectedValue(new Error("blocked")) },
    });
    Object.defineProperty(document, "execCommand", {
      configurable: true,
      value: vi.fn().mockReturnValue(false),
    });
    const fetchMock = vi.fn(
      async (url: string | URL | Request, init?: RequestInit) => {
        const path = String(url).replace("http://localhost:8000", "");
        if (
          path === "/forecasts/forecast-1" &&
          (!init || init.method === "GET")
        ) {
          return jsonResponse(
            forecastDetail({
              status: "pack_running",
              approved_framing_version: 1,
              current_research_pack: imported
                ? currentResearchPack({
                    pack_status: "completed",
                    effective_status: "completed",
                    research_run_status: "completed",
                    total_tool_calls: 0,
                    needs_human_review: false,
                  })
                : currentResearchPack({
                    pack_status: "needs_human_review",
                    effective_status: "needs_human_review",
                    research_run_status: "needs_human_review",
                    done_reason: "deep_research_submit_failed",
                    last_error: "APITimeoutError('Request timed out.')",
                    needs_human_review: true,
                  }),
              current_research_pack_status: imported ? "completed" : "needs_human_review",
            }),
          );
        }
        if (
          path === "/forecasts/forecast-1/research-packs/manual-prompt" &&
          (!init || init.method === "GET")
        ) {
          return jsonResponse({
            forecast_id: "forecast-1",
            framing_version: 1,
            prompt: "Recovery Deep Research prompt",
            prompt_sha256: recoveryPromptHash,
            prompt_version: "current_state_pack_v1",
            pack_role: "current_state",
            tool_profile: "public",
            max_report_chars: 50000,
            max_file_bytes: 1048576,
            pack_id: "pack-1",
            research_run_id: "run-1",
            recovering_existing_pack: true,
            recoverable_status: "needs_human_review",
          });
        }
        if (
          path === "/forecasts/forecast-1/research-packs/manual-import" &&
          init?.method === "POST"
        ) {
          expect(init.body).toBeInstanceOf(FormData);
          const body = init.body as FormData;
          expect(body.get("prompt_sha256")).toBe(recoveryPromptHash);
          expect(body.get("report_text")).toBe("Recovered manual report.");
          imported = true;
          return jsonResponse({
            pack_id: "pack-1",
            forecast_id: "forecast-1",
            research_run_id: "run-1",
            pack_role: "current_state",
            tool_profile: "public",
            status: "completed",
            policy_decision_id: "policy-1",
          });
        }
        return jsonResponse({ detail: "unexpected request" }, 500);
      },
    );
    globalThis.fetch = fetchMock;

    render(<App />);

    const currentStep = await screen.findByRole("region", {
      name: "公開情報の収集に確認が必要です",
    });
    expect(
      within(currentStep).getByRole("link", { name: "Research run詳細" }),
    ).toHaveAttribute("href", "#/runs/run-1");
    expect(screen.queryByRole("group", { name: "公開情報の収集方法" })).toBeNull();

    await userEvent.click(
      within(currentStep).getByRole("button", {
        name: "ChatGPT Deep Researchで手動収集に切り替え",
      }),
    );
    const promptTextbox = await screen.findByRole("textbox", {
      name: "ChatGPT Deep Researchへ渡すPrompt",
    });
    expect(promptTextbox).toHaveValue("Recovery Deep Research prompt");
    await userEvent.click(screen.getByRole("button", { name: "Promptをコピー" }));
    expect(
      await screen.findByText(
        "コピーできませんでした。Prompt欄を選択してコピーするか、Markdownでダウンロードしてください。",
      ),
    ).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "全文を選択" }));
    expect(promptTextbox).toHaveFocus();
    expect((promptTextbox as HTMLTextAreaElement).selectionStart).toBe(0);
    expect((promptTextbox as HTMLTextAreaElement).selectionEnd).toBe(
      "Recovery Deep Research prompt".length,
    );
    expect(screen.getByText("既存の公開情報パックを手動レポートで復旧します。"))
      .toBeInTheDocument();
    await userEvent.type(screen.getByLabelText("結果を貼り付け"), "Recovered manual report.");
    await userEvent.click(screen.getByRole("button", { name: "結果を取り込む" }));

    expect(
      await screen.findByRole("heading", {
        name: "公開情報の収集が完了しました",
        level: 2,
      }),
    ).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "証拠を抽出" })).toBeEnabled();
    expect(
      screen.queryByRole("button", {
        name: "ChatGPT Deep Researchで手動収集に切り替え",
      }),
    ).toBeNull();
  });

  it.each([
    {
      effectiveStatus: "failed",
      title: "公開情報の収集に失敗しました",
      flowMeta: "収集に失敗",
      packStatus: "失敗",
      doneReason: "deep_research_failed",
    },
    {
      effectiveStatus: "cancelled",
      title: "公開情報の収集が中断されました",
      flowMeta: "収集を中断",
      packStatus: "中断",
      doneReason: "cancelled_by_operator",
    },
  ])(
    "does not show a $effectiveStatus research pack as running",
    async ({ effectiveStatus, title, flowMeta, packStatus, doneReason }) => {
      window.location.hash = "#/forecasts/forecast-1";
      const fetchMock = vi.fn(
        async (url: string | URL | Request, init?: RequestInit) => {
          const path = String(url).replace("http://localhost:8000", "");
          if (
            path === "/forecasts/forecast-1" &&
            (!init || init.method === "GET")
          ) {
            return jsonResponse(
              forecastDetail({
                status: "pack_running",
                approved_framing_version: 1,
                current_research_pack: {
                  pack_id: "pack-1",
                  research_run_id: "run-1",
                  pack_status: "running",
                  effective_status: effectiveStatus,
                  research_run_status: effectiveStatus,
                  pack_created_at: "2026-06-08T00:00:00Z",
                  pack_updated_at: "2026-06-08T00:00:00Z",
                  research_run_created_at: "2026-06-08T00:00:00Z",
                  research_run_updated_at: "2026-06-08T00:30:00Z",
                  deep_research_started_at: "2026-06-08T00:05:00Z",
                  total_tool_calls: 9,
                  estimated_cost_usd: 1.1,
                  done_reason: doneReason,
                  needs_human_review: false,
                },
                current_research_pack_status: effectiveStatus,
              }),
            );
          }
          return jsonResponse({ detail: "unexpected request" }, 500);
        },
      );
      globalThis.fetch = fetchMock;

      render(<App />);

      const flow = await screen.findByRole("region", {
        name: "全体フロー",
      });
      const flowList = within(flow).getByRole("list", {
        name: "Forecast実行フロー",
      });
      const flowItems = within(flowList)
        .getAllByRole("listitem")
        .filter((item) => item.parentElement === flowList);
      const packItem = flowItems[1];
      expect(within(packItem).getByText("要対応")).toBeInTheDocument();
      expect(within(packItem).getByText(flowMeta)).toBeInTheDocument();
      expect(within(packItem).queryByText("実行中")).not.toBeInTheDocument();
      expect(screen.getByRole("heading", { name: title })).toBeInTheDocument();
      expect(screen.getAllByText("公開情報フェーズ").length).toBeGreaterThan(0);
      expect(screen.getAllByText(packStatus).length).toBeGreaterThan(0);
      expect(screen.getByText(doneReason)).toBeInTheDocument();
      const currentStep = screen.getByRole("region", { name: title });
      expect(
        within(currentStep).getByRole("link", { name: "Research run詳細" }),
      ).toHaveAttribute("href", "#/runs/run-1");
      expect(
        within(currentStep).queryByRole("button", { name: "状態を再確認" }),
      ).toBeNull();
    },
  );

  it("links to the research run from the forecast detail header", async () => {
    window.location.hash = "#/forecasts/forecast-1";
    const fetchMock = vi.fn(
      async (url: string | URL | Request, init?: RequestInit) => {
        const path = String(url).replace("http://localhost:8000", "");
        if (
          path === "/forecasts/forecast-1" &&
          (!init || init.method === "GET")
        ) {
          return jsonResponse(
            forecastDetail({
              status: "pack_running",
              approved_framing_version: 1,
              current_research_pack: {
                pack_id: "pack-1",
                research_run_id: "run-1",
                pack_status: "running",
                effective_status: "running",
                research_run_status: "waiting_deep_research",
                pack_created_at: "2026-06-08T00:00:00Z",
                pack_updated_at: "2026-06-08T00:00:00Z",
                research_run_created_at: "2026-06-08T00:00:00Z",
                research_run_updated_at: "2026-06-08T00:00:00Z",
                deep_research_started_at: null,
                total_tool_calls: 0,
                estimated_cost_usd: 0,
                done_reason: null,
                needs_human_review: false,
              },
              current_research_pack_status: "running",
            }),
          );
        }
        return jsonResponse({ detail: "unexpected request" }, 500);
      },
    );
    globalThis.fetch = fetchMock;

    render(<App />);

    expect(
      await screen.findByRole("link", { name: "Research run詳細" }),
    ).toHaveAttribute("href", "#/runs/run-1");
    expect(
      screen.getByRole("heading", { name: "Deep Researchへの送信を待っています" }),
    ).toBeInTheDocument();
    expect(
      screen.queryByRole("heading", { name: "公開情報を収集中です" }),
    ).not.toBeInTheDocument();
  });

  it("does not show the running wait banner after the current research pack is completed", async () => {
    window.location.hash = "#/forecasts/forecast-1";
    const fetchMock = vi.fn(
      async (url: string | URL | Request, init?: RequestInit) => {
        const path = String(url).replace("http://localhost:8000", "");
        if (
          path === "/forecasts/forecast-1" &&
          (!init || init.method === "GET")
        ) {
          return jsonResponse(
            forecastDetail({
              status: "pack_running",
              approved_framing_version: 1,
              current_research_pack: {
                pack_id: "pack-1",
                research_run_id: "run-1",
                pack_status: "running",
                effective_status: "completed",
                research_run_status: "completed",
                pack_created_at: "2026-06-08T00:00:00Z",
                pack_updated_at: "2026-06-08T00:00:00Z",
                research_run_created_at: "2026-06-08T00:00:00Z",
                research_run_updated_at: "2026-06-08T01:00:00Z",
                deep_research_started_at: "2026-06-08T00:05:00Z",
                total_tool_calls: 24,
                estimated_cost_usd: 2.5,
                done_reason: "forecast_raw_report_collected",
                needs_human_review: false,
              },
              current_research_pack_status: "completed",
            }),
          );
        }
        return jsonResponse({ detail: "unexpected request" }, 500);
      },
    );
    globalThis.fetch = fetchMock;

    render(<App />);

    expect(
      await screen.findByRole("heading", { name: "公開情報の収集が完了しました" }),
    ).toBeInTheDocument();
    expect(screen.getAllByText("公開情報フェーズ").length).toBeGreaterThan(0);
    expect(screen.getAllByText("完了").length).toBeGreaterThan(0);
    expect(
      screen.getByRole("button", { name: "証拠を抽出" }),
    ).toBeEnabled();
    expect(screen.queryByText("公開情報を収集中です")).not.toBeInTheDocument();
  });

  it("stops polling after a running current research pack completes", async () => {
    window.location.hash = "#/forecasts/forecast-1";
    vi.useFakeTimers();
    let forecastGetCount = 0;
    const fetchMock = vi.fn(
      async (url: string | URL | Request, init?: RequestInit) => {
        const path = String(url).replace("http://localhost:8000", "");
        if (
          path === "/forecasts/forecast-1" &&
          (!init || init.method === "GET")
        ) {
          forecastGetCount += 1;
          return jsonResponse(
            forecastDetail({
              status: "pack_running",
              approved_framing_version: 1,
              current_research_pack: {
                pack_id: "pack-1",
                research_run_id: "run-1",
                pack_status: "running",
                effective_status:
                  forecastGetCount === 1 ? "running" : "completed",
                research_run_status:
                  forecastGetCount === 1 ? "waiting_deep_research" : "completed",
                pack_created_at: "2026-06-08T00:00:00Z",
                pack_updated_at: "2026-06-08T00:00:00Z",
                research_run_created_at: "2026-06-08T00:00:00Z",
                research_run_updated_at: "2026-06-08T01:00:00Z",
                deep_research_started_at: "2026-06-08T00:05:00Z",
                total_tool_calls: forecastGetCount === 1 ? 12 : 24,
                estimated_cost_usd: forecastGetCount === 1 ? 1.25 : 2.5,
                done_reason:
                  forecastGetCount === 1 ? null : "forecast_raw_report_collected",
                needs_human_review: false,
              },
              current_research_pack_status:
                forecastGetCount === 1 ? "running" : "completed",
            }),
          );
        }
        return jsonResponse({ detail: "unexpected request" }, 500);
      },
    );
    globalThis.fetch = fetchMock;

    render(<App />);

    await act(async () => {
      await Promise.resolve();
      await Promise.resolve();
    });
    expect(
      screen.getByText("公開情報を収集中です"),
    ).toBeInTheDocument();
    expect(forecastGetCount).toBe(1);

    await act(async () => {
      await vi.advanceTimersByTimeAsync(3_000);
      await Promise.resolve();
    });

    expect(
      screen.getByText("公開情報の収集が完了しました"),
    ).toBeInTheDocument();
    const countAfterCompletion = forecastGetCount;

    await act(async () => {
      await vi.advanceTimersByTimeAsync(12_000);
      await Promise.resolve();
    });

    expect(forecastGetCount).toBe(countAfterCompletion);
  });

  it("unlocks evidence extraction when the current research pack is completed", async () => {
    window.location.hash = "#/forecasts/forecast-1";
    const fetchMock = vi.fn(
      async (url: string | URL | Request, init?: RequestInit) => {
        const path = String(url).replace("http://localhost:8000", "");
        if (
          path === "/forecasts/forecast-1" &&
          (!init || init.method === "GET")
        ) {
          return jsonResponse(
            forecastDetail({
              status: "pack_running",
              approved_framing_version: 1,
              current_research_pack: {
                pack_id: "pack-1",
                research_run_id: "run-1",
                pack_status: "running",
                effective_status: "completed",
                research_run_status: "completed",
                pack_created_at: "2026-06-08T00:00:00Z",
                pack_updated_at: "2026-06-08T00:00:00Z",
                research_run_created_at: "2026-06-08T00:00:00Z",
                research_run_updated_at: "2026-06-08T01:00:00Z",
                deep_research_started_at: "2026-06-08T00:05:00Z",
                total_tool_calls: 24,
                estimated_cost_usd: 2.5,
                done_reason: "forecast_raw_report_collected",
                needs_human_review: false,
              },
              current_research_pack_status: "completed",
            }),
          );
        }
        if (
          path === "/forecasts/forecast-1/evidence/extract" &&
          init?.method === "POST"
        ) {
          return jsonResponse(forecastDetail({ status: "evidence_ready" }));
        }
        return jsonResponse({ detail: "unexpected request" }, 500);
      },
    );
    globalThis.fetch = fetchMock;

    render(<App />);

    const flow = await screen.findByRole("region", {
      name: "全体フロー",
    });
    const flowList = within(flow).getByRole("list", {
      name: "Forecast実行フロー",
    });
    const flowItems = within(flowList)
      .getAllByRole("listitem")
      .filter((item) => item.parentElement === flowList);
    expect(within(flowItems[1]).getByText("完了")).toBeInTheDocument();
    expect(within(flowItems[2]).getByText("次に実行")).toBeInTheDocument();
    expect(
      screen.getByRole("button", { name: "証拠を抽出" }),
    ).toBeEnabled();
    expect(
      screen.getByRole("heading", { name: "公開情報の収集が完了しました" }),
    ).toBeInTheDocument();

    await userEvent.click(screen.getByRole("button", { name: "証拠を抽出" }));

    await waitFor(() =>
      expect(
        fetchMock.mock.calls.some(
          ([url, init]) =>
            String(url).endsWith("/forecasts/forecast-1/evidence/extract") &&
            init?.method === "POST",
        ),
      ).toBe(true),
    );
  });

  it("generates scenarios from the current-step CTA after evidence extraction", async () => {
    window.location.hash = "#/forecasts/forecast-1";
    const fetchMock = vi.fn(
      async (url: string | URL | Request, init?: RequestInit) => {
        const path = String(url).replace("http://localhost:8000", "");
        if (
          path === "/forecasts/forecast-1" &&
          (!init || init.method === "GET")
        ) {
          return jsonResponse(forecastDetail({ status: "evidence_ready" }));
        }
        if (
          path === "/forecasts/forecast-1/scenarios/generate" &&
          init?.method === "POST"
        ) {
          return jsonResponse(forecastDetail({ status: "scenarios_ready" }));
        }
        return jsonResponse({ detail: "unexpected request" }, 500);
      },
    );
    globalThis.fetch = fetchMock;

    render(<App />);

    expect(
      await screen.findByRole("heading", { name: "証拠抽出が完了しました" }),
    ).toBeInTheDocument();

    await userEvent.click(screen.getByRole("button", { name: "シナリオを生成" }));

    await waitFor(() =>
      expect(
        fetchMock.mock.calls.some(
          ([url, init]) =>
            String(url).endsWith("/forecasts/forecast-1/scenarios/generate") &&
            init?.method === "POST",
        ),
      ).toBe(true),
    );
  });

  it("shows committed forecast detail flow as ready to resolve", async () => {
    window.location.hash = "#/forecasts/forecast-1";
    const fetchMock = vi.fn(
      async (url: string | URL | Request, init?: RequestInit) => {
        const path = String(url).replace("http://localhost:8000", "");
        if (
          path === "/forecasts/forecast-1" &&
          (!init || init.method === "GET")
        ) {
          return jsonResponse(
            forecastDetail({
              status: "committed",
              approved_framing_version: 1,
            }),
          );
        }
        if (
          path === "/forecasts/forecast-1/estimate-set" &&
          (!init || init.method === "GET")
        ) {
          return jsonResponse(estimateSet({ status: "frozen" }));
        }
        return jsonResponse({ detail: "unexpected request" }, 500);
      },
    );
    globalThis.fetch = fetchMock;

    render(<App />);

    const flow = await screen.findByRole("region", {
      name: "全体フロー",
    });
    const flowList = within(flow).getByRole("list", {
      name: "Forecast実行フロー",
    });
    const flowItems = within(flowList)
      .getAllByRole("listitem")
      .filter((item) => item.parentElement === flowList);
    expect(flowItems).toHaveLength(9);
    expect(
      flowItems.map(
        (item) => within(item).getByRole("heading", { level: 4 }).textContent,
      ),
    ).toEqual([
      "フレーミング承認",
      "公開情報の収集",
      "証拠を抽出",
      "シナリオを生成",
      "主張と結果の対応を承認",
      "確率を計算",
      "推定結果を承認",
      "予測版を確定",
      "実績結果で解決",
    ]);
    expect(within(flowItems[7]).getByText("完了")).toBeInTheDocument();
    expect(within(flowItems[8]).getByText("次に実行")).toBeInTheDocument();
    expect(within(flow).getByText("8/9 完了")).toBeInTheDocument();
  });

  it("renders typed 409 code, message and details on forecast commands", async () => {
    window.location.hash = "#/forecasts/forecast-1";
    const fetchMock = vi.fn(
      async (url: string | URL | Request, init?: RequestInit) => {
        const path = String(url).replace("http://localhost:8000", "");
        if (
          path === "/forecasts/forecast-1" &&
          (!init || init.method === "GET")
        ) {
          return jsonResponse(
            forecastDetail({
              status: "framing_approved",
              approved_framing_version: 1,
            }),
          );
        }
        if (
          path === "/forecasts/forecast-1/research-packs" &&
          init?.method === "POST"
        ) {
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
      },
    );
    globalThis.fetch = fetchMock;

    render(<App />);

    expect(await screen.findAllByText("フレーミング承認済み")).not.toHaveLength(0);
    expect(
      screen.queryByRole("button", { name: "証拠抽出" }),
    ).toBeNull();
    await userEvent.click(
      screen.getByRole("button", { name: "公開情報の収集を開始" }),
    );

    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent("policy_requires_revision");
    expect(alert).toHaveTextContent("Policy requires framing revision.");
    expect(alert).toHaveTextContent("policy_decision_id");
  });

  it("surfaces missing resolution outcome states when dispatching legacy forecasts", async () => {
    window.location.hash = "#/forecasts/forecast-1";
    const fetchMock = vi.fn(
      async (url: string | URL | Request, init?: RequestInit) => {
        const path = String(url).replace("http://localhost:8000", "");
        if (
          path === "/forecasts/forecast-1" &&
          (!init || init.method === "GET")
        ) {
          return jsonResponse(
            forecastDetail({
              status: "framing_approved",
              approved_framing_version: 1,
              outcomes: [],
            }),
          );
        }
        if (
          path === "/forecasts/forecast-1/research-packs" &&
          init?.method === "POST"
        ) {
          return jsonResponse(
            {
              detail: {
                code: "forecast_outcomes_required",
                message:
                  "Forecast PhaseA requires at least one resolution outcome state.",
                details: {},
              },
            },
            409,
          );
        }
        return jsonResponse({ detail: "unexpected request" }, 500);
      },
    );
    globalThis.fetch = fetchMock;

    render(<App />);

    expect(await screen.findAllByText("フレーミング承認済み")).not.toHaveLength(0);
    await userEvent.click(
      screen.getByRole("button", { name: "公開情報の収集を開始" }),
    );

    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent("forecast_outcomes_required");
    expect(alert).toHaveTextContent(
      "Forecast PhaseA requires at least one resolution outcome state.",
    );
  });

  it("loads a draft estimate set on direct routes", async () => {
    window.location.hash = "#/forecasts/forecast-1";
    const fetchMock = vi.fn(
      async (url: string | URL | Request, init?: RequestInit) => {
        const path = String(url).replace("http://localhost:8000", "");
        if (
          path === "/forecasts/forecast-1" &&
          (!init || init.method === "GET")
        ) {
          return jsonResponse(forecastDetail({ status: "draft_ready" }));
        }
        if (
          path === "/forecasts/forecast-1/estimate-set" &&
          (!init || init.method === "GET")
        ) {
          return jsonResponse(estimateSet());
        }
        return jsonResponse({ detail: "unexpected request" }, 500);
      },
    );
    globalThis.fetch = fetchMock;

    render(<App />);

    expect(await screen.findAllByText("phase_a_v1")).toHaveLength(2);
    expect(screen.getByText("snapshot-hash-1")).toBeInTheDocument();
    expect(
      screen.getByRole("button", { name: "推定結果を承認" }),
    ).toBeEnabled();
    expect(screen.queryByRole("button", { name: "予測版を確定" })).toBeNull();
    expect(screen.queryByRole("button", { name: "確率計算" })).toBeNull();
    expect(
      fetchMock.mock.calls.some(([url]) =>
        String(url).endsWith("/estimate-set"),
      ),
    ).toBe(true);
    expect(
      fetchMock.mock.calls.some(([url]) =>
        String(url).endsWith("/forecasts/forecast-1/probabilities/compute"),
      ),
    ).toBe(false);
  });

  it("enables commit only after PhaseA approval", async () => {
    window.location.hash = "#/forecasts/forecast-1";
    let phaseAApproved = false;
    let status: ForecastDetail["status"] = "draft_ready";
    const fetchMock = vi.fn(
      async (url: string | URL | Request, init?: RequestInit) => {
        const path = String(url).replace("http://localhost:8000", "");
        if (
          path === "/forecasts/forecast-1" &&
          (!init || init.method === "GET")
        ) {
          return jsonResponse(forecastDetail({ status }));
        }
        if (
          path === "/forecasts/forecast-1/estimate-set" &&
          (!init || init.method === "GET")
        ) {
          return jsonResponse(estimateSet({ approved: phaseAApproved }));
        }
        if (
          path === "/forecasts/forecast-1/review" &&
          init?.method === "POST"
        ) {
          expect(JSON.parse(String(init.body))).toMatchObject({
            action: "approve_phase_a_version",
            estimate_set_id: "estimate-set-1",
          });
          phaseAApproved = true;
          return jsonResponse({
            forecast_id: "forecast-1",
            action: "approve_phase_a_version",
            status: "draft_ready",
            estimate_set_id: "estimate-set-1",
          });
        }
        if (
          path === "/forecasts/forecast-1/versions/commit" &&
          init?.method === "POST"
        ) {
          expect(phaseAApproved).toBe(true);
          status = "committed";
          return jsonResponse({
            version_id: "version-1",
            forecast_id: "forecast-1",
            estimate_set_id: "estimate-set-1",
            input_snapshot_hash: "snapshot-hash-1",
            snapshot_artifact_path: ".data/forecast-runs/version-1.json",
            committed_at: "2026-06-08T01:00:00Z",
          });
        }
        return jsonResponse({ detail: "unexpected request" }, 500);
      },
    );
    globalThis.fetch = fetchMock;

    render(<App />);

    expect(await screen.findByText("snapshot-hash-1")).toBeInTheDocument();
    expect(
      screen.getByRole("button", { name: "推定結果を承認" }),
    ).toBeEnabled();
    expect(screen.queryByRole("button", { name: "予測版を確定" })).toBeNull();

    await userEvent.click(
      screen.getByRole("button", { name: "推定結果を承認" }),
    );

    await waitFor(() =>
      expect(screen.getByRole("button", { name: "予測版を確定" })).toBeEnabled(),
    );

    await userEvent.click(screen.getByRole("button", { name: "予測版を確定" }));

    await waitFor(() =>
      expect(
        fetchMock.mock.calls.some(
          ([url, init]) =>
            String(url).endsWith("/forecasts/forecast-1/versions/commit") &&
            init?.method === "POST",
        ),
      ).toBe(true),
    );
  });

  it("restores claim-target approval from forecast detail after reload", async () => {
    window.location.hash = "#/forecasts/forecast-1";
    const fetchMock = vi.fn(
      async (url: string | URL | Request, init?: RequestInit) => {
        const path = String(url).replace("http://localhost:8000", "");
        if (
          path === "/forecasts/forecast-1" &&
          (!init || init.method === "GET")
        ) {
          return jsonResponse(
            forecastDetail({
              status: "scenarios_ready",
              approved_framing_version: 1,
              approved_claim_target_link_count: 2,
            }),
          );
        }
        return jsonResponse({ detail: "unexpected request" }, 500);
      },
    );
    globalThis.fetch = fetchMock;

    render(<App />);

    expect(await screen.findAllByText("シナリオ生成済み")).not.toHaveLength(0);
    expect(
      screen.queryByRole("button", { name: "主張と結果の対応を承認" }),
    ).toBeNull();
    expect(screen.getByRole("button", { name: "確率を計算" })).toBeEnabled();
  });

  it("requires claim-target link approval before compute", async () => {
    window.location.hash = "#/forecasts/forecast-1";
    let status: ForecastDetail["status"] = "scenarios_ready";
    const fetchMock = vi.fn(
      async (url: string | URL | Request, init?: RequestInit) => {
        const path = String(url).replace("http://localhost:8000", "");
        if (
          path === "/forecasts/forecast-1" &&
          (!init || init.method === "GET")
        ) {
          return jsonResponse(forecastDetail({ status }));
        }
        if (
          path === "/forecasts/forecast-1/review" &&
          init?.method === "POST"
        ) {
          return jsonResponse({
            forecast_id: "forecast-1",
            action: "approve_claim_target_links",
            status: "scenarios_ready",
          });
        }
        if (
          path === "/forecasts/forecast-1/probabilities/compute" &&
          init?.method === "POST"
        ) {
          status = "draft_ready";
          return jsonResponse(estimateSet());
        }
        if (
          path === "/forecasts/forecast-1/estimate-set" &&
          (!init || init.method === "GET")
        ) {
          return jsonResponse(estimateSet());
        }
        return jsonResponse({ detail: "unexpected request" }, 500);
      },
    );
    globalThis.fetch = fetchMock;

    render(<App />);

    expect(await screen.findAllByText("シナリオ生成済み")).not.toHaveLength(0);
    expect(
      screen.getByRole("button", { name: "主張と結果の対応を承認" }),
    ).toBeEnabled();
    expect(screen.queryByRole("button", { name: "確率を計算" })).toBeNull();

    await userEvent.click(
      screen.getByRole("button", { name: "主張と結果の対応を承認" }),
    );

    await waitFor(() =>
      expect(screen.getByRole("button", { name: "確率を計算" })).toBeEnabled(),
    );
    const reviewCall = fetchMock.mock.calls.find(
      ([url, init]) =>
        String(url).endsWith("/forecasts/forecast-1/review") &&
        init?.method === "POST",
    );
    expect(JSON.parse(String(reviewCall?.[1]?.body))).toMatchObject({
      action: "approve_claim_target_links",
    });
    expect(reviewCall?.[1]?.headers).toEqual(
      expect.objectContaining({
        "Idempotency-Key": expect.stringMatching(
          /^forecast-forecast-1-claimTargets-/,
        ),
      }),
    );

    await userEvent.click(screen.getByRole("button", { name: "確率を計算" }));

    expect(await screen.findByText("snapshot-hash-1")).toBeInTheDocument();
    const computeCall = fetchMock.mock.calls.find(
      ([url, init]) =>
        String(url).endsWith("/forecasts/forecast-1/probabilities/compute") &&
        init?.method === "POST",
    );
    expect(computeCall?.[1]?.headers).toEqual(
      expect.objectContaining({
        "Idempotency-Key": expect.stringMatching(
          /^forecast-forecast-1-compute-/,
        ),
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
    const fetchMock = vi.fn(
      async (url: string | URL | Request, init?: RequestInit) => {
        const path = String(url).replace("http://localhost:8000", "");
        if (
          path === "/forecasts/forecast-1" &&
          (!init || init.method === "GET")
        ) {
          return jsonResponse(forecastDetail({ status: "committed" }));
        }
        if (
          path === "/forecasts/forecast-1/estimate-set" &&
          (!init || init.method === "GET")
        ) {
          return jsonResponse(estimateSet({ status: "frozen" }));
        }
        if (
          path === "/forecasts/forecast-1/resolve" &&
          init?.method === "POST"
        ) {
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
      },
    );
    globalThis.fetch = fetchMock;

    render(<App />);

    expect(await screen.findByText("snapshot-hash-1")).toBeInTheDocument();
    const resolvePanel = await screen.findByRole("heading", {
      name: "実績結果で解決",
      level: 2,
    });
    const panel = resolvePanel.closest(".form-panel");
    expect(panel).not.toBeNull();
    await userEvent.click(
      within(panel as HTMLElement).getByRole("button", {
        name: "実績結果で解決",
      }),
    );

    expect(await screen.findByText("Brier 0.1200")).toBeInTheDocument();
    expect(screen.getByText("phase_a_scorer_v1")).toBeInTheDocument();
  });
});
