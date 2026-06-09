import { useEffect, useState } from "react";

import { getForecastAudit } from "../../api/forecast";
import type { ForecastAuditResponse } from "../../types";
import { formatForecastError } from "./errors";

export function ForecastAudit({ forecastId }: { forecastId: string }) {
  const [audit, setAudit] = useState<ForecastAuditResponse | null>(null);
  const [error, setError] = useState<string | null>(null);

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
        <h2>Versions</h2>
        <pre>{JSON.stringify(audit?.versions ?? [], null, 2)}</pre>
        <h2>Probability</h2>
        <pre>{JSON.stringify(audit?.events ?? [], null, 2)}</pre>
        <h2>Policy Decisions</h2>
        <pre>{JSON.stringify(audit?.policy_decisions ?? [], null, 2)}</pre>
      </div>
    </section>
  );
}
