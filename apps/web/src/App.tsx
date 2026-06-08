/**
 * App shell — sticky header, skip link, and router switch.
 */

import { Link, navigate, routes, useRoute } from "./router";
import {
  NewResearch,
  Dashboard,
  RunMonitor,
  HumanReview,
  ReportViewer,
  AuditLog,
  Settings,
  ForecastDashboard,
  NewForecast,
  ForecastDetail,
  ForecastAudit,
} from "./screens";
import "./App.css";

// ── Not found ─────────────────────────────────────────────────────────────────

function NotFound() {
  return (
    <div className="not-found">
      <h1>ページが見つかりません</h1>
      <p>指定されたURLは存在しません。</p>
      <button
        type="button"
        className="btn-secondary"
        onClick={() => navigate(routes().dashboard)}
      >
        ダッシュボードへ戻る
      </button>
    </div>
  );
}

// ── App ───────────────────────────────────────────────────────────────────────

export function App() {
  const route = useRoute();

  function renderScreen() {
    switch (route.name) {
      case "dashboard":
        return <Dashboard />;
      case "new":
        return <NewResearch />;
      case "monitor":
        return route.runId ? <RunMonitor runId={route.runId} /> : <NotFound />;
      case "review":
        return route.runId ? <HumanReview runId={route.runId} /> : <NotFound />;
      case "report":
        return route.runId ? (
          <ReportViewer
            runId={route.runId}
            initialTab={route.reportTab}
            initialAttemptNo={route.reportAttemptNo}
          />
        ) : (
          <NotFound />
        );
      case "audit":
        return route.runId ? (
          <AuditLog
            runId={route.runId}
            initialTab={route.auditTab}
            focusReviewNo={route.auditReviewNo}
          />
        ) : (
          <NotFound />
        );
      case "settings":
        return <Settings />;
      case "forecasts":
        return <ForecastDashboard />;
      case "forecast-new":
        return <NewForecast />;
      case "forecast-detail":
        return route.forecastId ? <ForecastDetail forecastId={route.forecastId} /> : <NotFound />;
      case "forecast-audit":
        return route.forecastId ? <ForecastAudit forecastId={route.forecastId} /> : <NotFound />;
      case "not-found":
      default:
        return <NotFound />;
    }
  }

  return (
    <>
      {/* Skip link for keyboard accessibility */}
      <a href="#main-content" className="skip-link">
        メインコンテンツへスキップ
      </a>

      {/* Sticky header */}
      <header className="app-header" role="banner">
        <div className="app-header-inner">
          <Link to={routes().dashboard} className="app-logo" aria-label="Deep Research Review Orchestrator ホーム">
            <span className="app-logo-mark" aria-hidden="true">◆</span>
            <span className="app-logo-text">Deep Research Review Orchestrator</span>
          </Link>

          <nav className="app-nav" aria-label="メインナビゲーション">
            <Link
              to={routes().dashboard}
              className={`nav-link${route.name === "dashboard" ? " nav-link--active" : ""}`}
            >
              ダッシュボード
            </Link>
            <Link
              to={routes().new}
              className={`nav-link${route.name === "new" ? " nav-link--active" : ""}`}
            >
              新規リサーチ
            </Link>
            <Link
              to={routes().settings}
              className={`nav-link${route.name === "settings" ? " nav-link--active" : ""}`}
            >
              設定
            </Link>
            <Link
              to={routes().forecasts}
              className={`nav-link${route.name.startsWith("forecast") || route.name === "forecasts" ? " nav-link--active" : ""}`}
            >
              Forecasts
            </Link>
          </nav>
        </div>
      </header>

      {/* Main content */}
      <main id="main-content" className="app-main">
        <div className="app-content">
          {renderScreen()}
        </div>
      </main>
    </>
  );
}
