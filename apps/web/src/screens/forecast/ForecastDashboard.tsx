import { useEffect, useState } from "react";

import { listForecasts } from "../../api/forecast";
import { Link, routes } from "../../router";
import type { ForecastSummary } from "../../types";
import { formatForecastError } from "./errors";

export function ForecastDashboard() {
  const [forecasts, setForecasts] = useState<ForecastSummary[]>([]);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    void listForecasts()
      .then(setForecasts)
      .catch((err) => setError(formatForecastError(err)));
  }, []);

  return (
    <section className="screen">
      <div className="screen-header">
        <div>
          <h1>Forecasts</h1>
          <p className="screen-subtitle">PhaseA versions and probability drafts</p>
        </div>
        <Link to={routes().forecastNew} className="btn-primary">
          New Forecast
        </Link>
      </div>
      {error && (
        <div className="alert alert-error" role="alert" style={{ whiteSpace: "pre-wrap" }}>
          {error}
        </div>
      )}
      <div className="run-grid">
        {forecasts.map((forecast) => (
          <Link
            key={forecast.forecast_id}
            to={routes().forecastDetail(forecast.forecast_id)}
            className="run-card run-card-link"
          >
            <div className="run-card-header">
              <span className="status-pill">{forecast.status}</span>
            </div>
            <p className="run-card-title">{forecast.question}</p>
            <p className="run-card-meta">{forecast.forecast_id}</p>
          </Link>
        ))}
      </div>
    </section>
  );
}
