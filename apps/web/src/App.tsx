import { useState } from "react";

import { env } from "./env";
import "./App.css";

type HealthResponse = {
  status: string;
  env: string;
};

type HealthState =
  | { status: "idle" }
  | { status: "loading" }
  | { status: "success"; data: HealthResponse }
  | { status: "error"; message: string };

export function App() {
  const [health, setHealth] = useState<HealthState>({ status: "idle" });

  async function checkHealth() {
    setHealth({ status: "loading" });

    try {
      const response = await fetch(`${env.apiBaseUrl}/health`);

      if (!response.ok) {
        throw new Error(`API returned ${response.status}`);
      }

      const data = (await response.json()) as HealthResponse;
      setHealth({ status: "success", data });
    } catch (error) {
      const message = error instanceof Error ? error.message : "Unable to reach API";
      setHealth({ status: "error", message });
    }
  }

  return (
    <main className="app-shell">
      <section className="status-panel" aria-labelledby="app-title">
        <div>
          <p className="eyebrow">Future Predict</p>
          <h1 id="app-title">Prediction workspace</h1>
          <p className="lede">A lightweight API and React foundation for building forecasts.</p>
        </div>

        <dl className="metadata">
          <div>
            <dt>API base</dt>
            <dd>{env.apiBaseUrl}</dd>
          </div>
          <div>
            <dt>Health</dt>
            <dd>
              {health.status === "success"
                ? `${health.data.status} (${health.data.env})`
                : health.status}
            </dd>
          </div>
        </dl>

        <button type="button" onClick={checkHealth} disabled={health.status === "loading"}>
          {health.status === "loading" ? "Checking..." : "Check API health"}
        </button>

        {health.status === "error" ? (
          <p className="error" role="alert">
            {health.message}
          </p>
        ) : null}
      </section>
    </main>
  );
}

