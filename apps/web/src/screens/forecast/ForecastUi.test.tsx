import { cleanup, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { App } from "../../App";
import type { EstimateSetResponse, ForecastDetail } from "../../types";

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
  it("shows a framing preview from getForecast before approving", async () => {
    window.location.hash = "#/forecasts/new";
    const fetchMock = vi.fn(async (url: string | URL | Request, init?: RequestInit) => {
      const path = String(url).replace("http://localhost:8000", "");
      if (path === "/forecasts" && init?.method === "POST") {
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

    expect(screen.getByText("入力の目安")).toBeInTheDocument();
    expect(screen.getByText(/最大8件まで/)).toBeInTheDocument();

    await userEvent.type(screen.getByLabelText(/予測したい問い/), "Will the product launch?");
    await userEvent.type(screen.getByLabelText(/判定条件/), "Official source.");
    await userEvent.click(screen.getByRole("button", { name: "フレーミングを作成" }));

    expect(await screen.findByText("フレーミングプレビュー")).toBeInTheDocument();
    expect(screen.getByText("Will the product launch by Q4?")).toBeInTheDocument();
    expect(screen.getByText("Official launch announcement.")).toBeInTheDocument();
    expect(screen.getByText("Yes")).toBeInTheDocument();
    expect(screen.getByText("No")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "この内容で承認" })).toBeEnabled();

    await userEvent.click(screen.getByRole("button", { name: "この内容で承認" }));
    await waitFor(() => expect(window.location.hash).toBe("#/forecasts/forecast-1"));
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
