import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { App } from "./App";

const originalFetch = globalThis.fetch;

describe("App", () => {
  beforeEach(() => {
    vi.stubEnv("VITE_API_BASE_URL", "http://localhost:8000");
  });

  afterEach(() => {
    globalThis.fetch = originalFetch;
    vi.unstubAllEnvs();
    vi.restoreAllMocks();
  });

  it("shows configured API base URL", () => {
    render(<App />);

    expect(screen.getByText("http://localhost:8000")).toBeInTheDocument();
  });

  it("checks API health", async () => {
    globalThis.fetch = vi.fn().mockResolvedValue({
      ok: true,
      json: async () => ({ status: "ok", env: "test" }),
    } as Response);

    render(<App />);

    await userEvent.click(screen.getByRole("button", { name: /check api health/i }));

    expect(await screen.findByText("ok (test)")).toBeInTheDocument();
    expect(globalThis.fetch).toHaveBeenCalledWith("http://localhost:8000/health");
  });
});

