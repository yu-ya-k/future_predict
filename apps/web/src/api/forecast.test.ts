import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import {
  createForecast,
  createForecastFramingDraft,
  dispatchCurrentStatePack,
  getManualResearchPackPrompt,
  getForecastEstimateSet,
  importManualResearchPack,
} from "./forecast";

function jsonResponse(data: unknown, status = 200): Response {
  return {
    ok: status >= 200 && status < 300,
    status,
    json: () => Promise.resolve(data),
  } as Response;
}

beforeEach(() => {
  vi.stubEnv("VITE_API_BASE_URL", "http://localhost:8000");
  localStorage.clear();
});

afterEach(() => {
  vi.unstubAllEnvs();
  vi.restoreAllMocks();
  localStorage.clear();
});

describe("forecast API client", () => {
  it("uses caller-provided idempotency keys for command endpoints", async () => {
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(
        jsonResponse({
          forecast_id: "forecast-1",
          status: "framing_pending",
          framing_version: 1,
          created_at: "2026-06-08T00:00:00Z",
        }),
      )
      .mockResolvedValueOnce(
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
    globalThis.fetch = fetchMock;

    await createForecast(
      { question: "Will this ship?", outcomes: ["Yes", "No"] },
      { idempotencyKey: "stable-create-key" },
    );
    await dispatchCurrentStatePack("forecast-1", {
      idempotencyKey: "stable-pack-key",
    });

    expect(fetchMock).toHaveBeenNthCalledWith(
      1,
      "http://localhost:8000/forecasts",
      expect.objectContaining({
        headers: expect.objectContaining({ "Idempotency-Key": "stable-create-key" }),
      }),
    );
    expect(fetchMock).toHaveBeenNthCalledWith(
      2,
      "http://localhost:8000/forecasts/forecast-1/research-packs",
      expect.objectContaining({
        headers: expect.objectContaining({ "Idempotency-Key": "stable-pack-key" }),
      }),
    );
  });

  it("loads the current forecast estimate set", async () => {
    const fetchMock = vi.fn().mockResolvedValueOnce(
      jsonResponse({
        estimate_set_id: "estimate-set-1",
        forecast_id: "forecast-1",
        status: "frozen",
        engine_version: "phase_a_v1",
        input_snapshot_hash: "snapshot-hash-1",
        engine_code_hash: "engine-hash-1",
        random_seed: 0,
        normalization_group_id: "norm-1",
        estimates: [],
      }),
    );
    globalThis.fetch = fetchMock;

    await expect(getForecastEstimateSet("forecast-1")).resolves.toMatchObject({
      estimate_set_id: "estimate-set-1",
      input_snapshot_hash: "snapshot-hash-1",
    });

    expect(fetchMock).toHaveBeenCalledWith(
      "http://localhost:8000/forecasts/forecast-1/estimate-set",
      expect.objectContaining({ method: "GET" }),
    );
  });

  it("loads and imports forecast manual research packs with FormData", async () => {
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(
        jsonResponse({
          forecast_id: "forecast-1",
          framing_version: 1,
          prompt: "Manual prompt",
          prompt_sha256: "prompt-hash",
          prompt_version: "current_state_pack_v1",
          pack_role: "current_state",
          tool_profile: "public",
          max_report_chars: 50000,
          max_file_bytes: 1048576,
        }),
      )
      .mockResolvedValueOnce(
        jsonResponse({
          pack_id: "pack-1",
          forecast_id: "forecast-1",
          research_run_id: "run-1",
          pack_role: "current_state",
          tool_profile: "public",
          status: "completed",
          policy_decision_id: "policy-1",
        }),
      )
      .mockResolvedValueOnce(
        jsonResponse({
          pack_id: "pack-2",
          forecast_id: "forecast-1",
          research_run_id: "run-2",
          pack_role: "current_state",
          tool_profile: "public",
          status: "completed",
          policy_decision_id: "policy-2",
        }),
      );
    globalThis.fetch = fetchMock;
    const file = new File(["Manual report file"], "manual-report.md", {
      type: "text/markdown",
    });

    await expect(getManualResearchPackPrompt("forecast-1")).resolves.toMatchObject({
      prompt_sha256: "prompt-hash",
    });
    await importManualResearchPack(
      "forecast-1",
      {
        promptSha256: "prompt-hash",
        report: { source: "text", text: "Manual report" },
      },
      { idempotencyKey: "manual-pack-key" },
    );
    await importManualResearchPack(
      "forecast-1",
      {
        promptSha256: "prompt-hash",
        report: { source: "file", file },
      },
      { idempotencyKey: "manual-pack-file-key" },
    );

    expect(fetchMock).toHaveBeenNthCalledWith(
      1,
      "http://localhost:8000/forecasts/forecast-1/research-packs/manual-prompt",
      expect.objectContaining({ method: "GET" }),
    );
    const secondCall = fetchMock.mock.calls[1];
    expect(secondCall[0]).toBe(
      "http://localhost:8000/forecasts/forecast-1/research-packs/manual-import",
    );
    expect(secondCall[1]).toEqual(
      expect.objectContaining({
        method: "POST",
        body: expect.any(FormData),
        headers: expect.objectContaining({ "Idempotency-Key": "manual-pack-key" }),
      }),
    );
    expect(secondCall[1].headers).not.toHaveProperty("Content-Type");
    const textBody = secondCall[1].body as FormData;
    expect(textBody.get("prompt_sha256")).toBe("prompt-hash");
    expect(textBody.get("report_text")).toBe("Manual report");
    expect(textBody.has("report_file")).toBe(false);

    const thirdCall = fetchMock.mock.calls[2];
    expect(thirdCall[0]).toBe(
      "http://localhost:8000/forecasts/forecast-1/research-packs/manual-import",
    );
    expect(thirdCall[1]).toEqual(
      expect.objectContaining({
        method: "POST",
        body: expect.any(FormData),
        headers: expect.objectContaining({
          "Idempotency-Key": "manual-pack-file-key",
        }),
      }),
    );
    expect(thirdCall[1].headers).not.toHaveProperty("Content-Type");
    const fileBody = thirdCall[1].body as FormData;
    expect(fileBody.get("prompt_sha256")).toBe("prompt-hash");
    expect(fileBody.has("report_text")).toBe(false);
    expect(fileBody.get("report_file")).toBe(file);
  });

  it("creates forecast framing drafts", async () => {
    const fetchMock = vi.fn().mockResolvedValueOnce(
      jsonResponse({
        draft: {
          forecast_prompt: "Forecast prompt",
          question: "Will this ship by Q4?",
          resolution_criteria: "Official announcement.",
          resolution_sources: ["Official site"],
          target_population: null,
          unit_of_analysis: null,
          decision_context: null,
          outcomes: ["Yes", "No"],
          clarifying_questions: [
            {
              question_id: "deadline",
              label: "Deadline",
              prompt: "What is the deadline?",
              why_needed: "A deadline is required.",
              answer_type: "text",
              required: true,
              options: [],
            },
          ],
          confidence: 0.7,
        },
        create_payload: {
          question: "Will this ship by Q4?",
          resolution_criteria: "Official announcement.",
          resolution_sources: ["Official site"],
          outcomes: ["Yes", "No"],
          confidentiality_class: "public",
        },
        ready_to_create: false,
        model: "test-model",
        response_id: "resp-1",
        warnings: ["Need deadline."],
      }),
    );
    globalThis.fetch = fetchMock;

    await expect(
      createForecastFramingDraft({
        rough_question: "Ship this?",
        answers: [{ question_id: "deadline", answer: "Q4" }],
        locale: "ja",
      }),
    ).resolves.toMatchObject({
      ready_to_create: false,
      draft: { clarifying_questions: [{ question_id: "deadline" }] },
    });

    expect(fetchMock).toHaveBeenCalledWith(
      "http://localhost:8000/forecasts/framing-drafts",
      expect.objectContaining({
        method: "POST",
        body: JSON.stringify({
          rough_question: "Ship this?",
          answers: [{ question_id: "deadline", answer: "Q4" }],
          locale: "ja",
        }),
      }),
    );
  });
});
