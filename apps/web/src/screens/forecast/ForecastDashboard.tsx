import { useCallback, useEffect, useState } from "react";

import { Skeleton } from "../../components";
import { listForecasts } from "../../api/forecast";
import { Link, routes } from "../../router";
import type { ForecastSummary } from "../../types";
import { formatForecastError } from "./errors";
import { forecastStatusLabel, forecastStatusTone } from "./forecastStatus";

const dateTimeFormatter = new Intl.DateTimeFormat("ja-JP", {
  year: "numeric",
  month: "2-digit",
  day: "2-digit",
  hour: "2-digit",
  minute: "2-digit",
});

const dateFormatter = new Intl.DateTimeFormat("ja-JP", {
  year: "numeric",
  month: "2-digit",
  day: "2-digit",
});

function formatDateTime(value: string): string | null {
  const parsed = new Date(value);
  return Number.isNaN(parsed.getTime()) ? null : dateTimeFormatter.format(parsed);
}

function formatDate(value: string): string | null {
  const parsed = new Date(value);
  return Number.isNaN(parsed.getTime()) ? null : dateFormatter.format(parsed);
}

export function ForecastDashboard() {
  const [forecasts, setForecasts] = useState<ForecastSummary[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);

  const load = useCallback(() => {
    setLoading(true);
    setError(null);
    return listForecasts()
      .then((data) => setForecasts(data))
      .catch((err) => setError(formatForecastError(err)))
      .finally(() => setLoading(false));
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  return (
    <section className="screen">
      <div className="screen-header">
        <div>
          <h1>Forecast一覧</h1>
          <p className="screen-subtitle">公開情報ベースの予測ドラフトと確率</p>
        </div>
        <Link to={routes().forecastNew} className="btn-primary">
          新規Forecast
        </Link>
      </div>

      {error && (
        <div className="alert alert-error" role="alert" style={{ whiteSpace: "pre-wrap" }}>
          {error}
          <div>
            <button type="button" className="btn-secondary" onClick={() => void load()}>
              再読み込み
            </button>
          </div>
        </div>
      )}

      {loading ? (
        <div className="run-grid" aria-busy="true">
          {Array.from({ length: 3 }, (_, index) => (
            <div key={index} className="run-card">
              <div className="run-card-header">
                <Skeleton width="6rem" height="1.5rem" />
              </div>
              <Skeleton width="80%" height="1.25rem" />
              <Skeleton width="60%" height="1rem" />
            </div>
          ))}
        </div>
      ) : forecasts.length === 0 && !error ? (
        <div className="empty-state" role="status">
          <span className="empty-state__icon" aria-hidden="true">
            <svg
              width="40"
              height="40"
              viewBox="0 0 24 24"
              fill="none"
              stroke="currentColor"
              strokeWidth="1.5"
              strokeLinecap="round"
              strokeLinejoin="round"
              aria-hidden="true"
            >
              <polyline points="22 12 16 12 14 15 10 15 8 12 2 12" />
              <path d="M5.45 5.11L2 12v6a2 2 0 0 0 2 2h16a2 2 0 0 0 2-2v-6l-3.45-6.89A2 2 0 0 0 16.76 4H7.24a2 2 0 0 0-1.79 1.11z" />
            </svg>
          </span>
          <p className="empty-state__title">Forecastがありません</p>
          <p className="empty-state__description">
            新規Forecastを作成すると、ここに一覧表示されます。
          </p>
          <Link to={routes().forecastNew} className="btn-primary">
            新規Forecast
          </Link>
        </div>
      ) : (
        <ul className="run-grid" role="list">
          {forecasts.map((forecast) => {
            const updatedAt = formatDateTime(forecast.updated_at);
            const resolutionDate = forecast.resolution_date
              ? formatDate(forecast.resolution_date)
              : null;
            return (
              <li key={forecast.forecast_id}>
                <Link
                  to={routes().forecastDetail(forecast.forecast_id)}
                  className="run-card run-card-link"
                  aria-label={`${forecast.question} — ${forecastStatusLabel(forecast.status)}`}
                >
                  <div className="run-card-header">
                    <div className="run-card-badges">
                      <span
                        className={`status-pill status-pill--${forecastStatusTone(forecast.status)}`}
                      >
                        {forecastStatusLabel(forecast.status)}
                      </span>
                      {forecast.committed_version_id && (
                        <span className="status-pill status-pill--pass">確定版あり</span>
                      )}
                    </div>
                  </div>
                  <p className="run-card-title">{forecast.question}</p>
                  <div className="run-card-meta">
                    {updatedAt && <span>更新 {updatedAt}</span>}
                    {resolutionDate && <span>決着予定 {resolutionDate}</span>}
                    <span className="run-card-id">{forecast.forecast_id}</span>
                  </div>
                </Link>
              </li>
            );
          })}
        </ul>
      )}
    </section>
  );
}
