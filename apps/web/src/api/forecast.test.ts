import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { createForecast, dispatchCurrentStatePack, getForecastEstimateSet } from "./forecast";

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
});
