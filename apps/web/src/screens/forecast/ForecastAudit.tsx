import { useEffect, useState } from "react";

import { getForecastAudit } from "../../api/forecast";
import type { ForecastAuditResponse } from "../../types";
import { formatForecastError } from "./errors";

type AuditTab = "versions" | "probability" | "policy" | "reviews" | "simulation";

const TABS: Array<{ id: AuditTab; label: string }> = [
  { id: "versions", label: "Versions" },
  { id: "probability", label: "Probability" },
  { id: "policy", label: "Policy Decisions" },
  { id: "reviews", label: "Reviews" },
  { id: "simulation", label: "Simulation Runs" },
];

export function ForecastAudit({ forecastId }: { forecastId: string }) {
  const [audit, setAudit] = useState<ForecastAuditResponse | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [activeTab, setActiveTab] = useState<AuditTab>("versions");

  useEffect(() => {
    void getForecastAudit(forecastId)
      .then(setAudit)
      .catch((err) => setError(formatForecastError(err)));
  }, [forecastId]);

  return (
    <section className="screen">
      <div className="screen-header">
        <div>
          <h1>Forecast Audit</h1>
          <p className="screen-subtitle">{forecastId}</p>
        </div>
      </div>
      {error && (
        <div className="alert alert-error" role="alert" style={{ whiteSpace: "pre-wrap" }}>
          {error}
        </div>
      )}
      <div className="form-panel">
        <div className="audit-tabs" role="tablist" aria-label="Forecast audit tabs">
          {TABS.map((tab) => (
            <button
              key={tab.id}
              type="button"
              role="tab"
              aria-selected={activeTab === tab.id}
              className={`audit-tab${activeTab === tab.id ? " audit-tab--active" : ""}`}
              onClick={() => setActiveTab(tab.id)}
            >
              {tab.label}
            </button>
          ))}
        </div>
        {activeTab === "versions" && (
          <>
            <h2>Versions</h2>
            <pre>{JSON.stringify(audit?.versions ?? [], null, 2)}</pre>
          </>
        )}
        {activeTab === "probability" && (
          <>
            <h2>Probability</h2>
            <pre>
              {JSON.stringify(
                (audit?.events ?? []).filter(
                  (event) =>
                    event.event_type === "probabilities_computed" ||
                    event.event_type === "version_committed" ||
                    event.event_type === "forecast_resolved",
                ),
                null,
                2,
              )}
            </pre>
          </>
        )}
        {activeTab === "policy" && (
          <>
            <h2>Policy Decisions</h2>
            <pre>{JSON.stringify(audit?.policy_decisions ?? [], null, 2)}</pre>
          </>
        )}
        {activeTab === "reviews" && (
          <>
            <h2>Reviews</h2>
            <pre>{JSON.stringify(audit?.reviews ?? [], null, 2)}</pre>
          </>
        )}
        {activeTab === "simulation" && (
          <>
            <h2>Simulation Runs</h2>
            <p className="muted">Phase Cで利用します。</p>
          </>
        )}
      </div>
    </section>
  );
}
