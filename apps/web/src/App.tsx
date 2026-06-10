/**
 * App shell — sticky header, skip link, and router switch.
 */

import { useEffect, useRef, useState } from "react";

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

// ── Screen labels (used for route-change announcements) ─────────────────────────

const SCREEN_LABELS: Record<string, string> = {
  dashboard: "ダッシュボード",
  new: "新規リサーチ",
  monitor: "Runモニター",
  review: "人間レビュー",
  report: "レポート",
  audit: "監査ログ",
  settings: "設定",
  forecasts: "Forecasts",
  "forecast-new": "新規Forecast",
  "forecast-detail": "Forecast詳細",
  "forecast-audit": "Forecast監査",
  "not-found": "ページが見つかりません",
};

// ── App ───────────────────────────────────────────────────────────────────────

export function App() {
  const route = useRoute();
  // Skip the focus shift / announcement on the very first mount so we do not
  // steal focus from the user when the page initially loads.
  const isFirstRender = useRef(true);
  const [routeAnnouncement, setRouteAnnouncement] = useState("");

  useEffect(() => {
    if (isFirstRender.current) {
      isFirstRender.current = false;
      return;
    }
    // Move keyboard / screen-reader focus to the main region so SPA route
    // changes are perceivable. router.tsx already handles scrollTo(0, 0).
    // <main> has no accessible name (no aria-label), so focusing it does not
    // announce its content and therefore won't double-speak with the live
    // region announcement below.
    document.getElementById("main-content")?.focus();
    const label = SCREEN_LABELS[route.name] ?? "";
    setRouteAnnouncement(label ? `${label}に移動しました` : "");
  }, [route.name]);

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
              aria-current={route.name === "dashboard" ? "page" : undefined}
            >
              ダッシュボード
            </Link>
            <Link
              to={routes().new}
              className={`nav-link${route.name === "new" ? " nav-link--active" : ""}`}
              aria-current={route.name === "new" ? "page" : undefined}
            >
              新規リサーチ
            </Link>
            <Link
              to={routes().settings}
              className={`nav-link${route.name === "settings" ? " nav-link--active" : ""}`}
              aria-current={route.name === "settings" ? "page" : undefined}
            >
              設定
            </Link>
            <Link
              to={routes().forecasts}
              className={`nav-link${route.name.startsWith("forecast") || route.name === "forecasts" ? " nav-link--active" : ""}`}
              aria-current={
                route.name.startsWith("forecast") || route.name === "forecasts"
                  ? "page"
                  : undefined
              }
            >
              Forecasts
            </Link>
          </nav>
        </div>
      </header>

      {/* Main content */}
      <main id="main-content" className="app-main" tabIndex={-1}>
        <div className="app-content">
          {renderScreen()}
        </div>
      </main>

      {/* Polite route-change announcement for assistive technology.
         Uses a bare aria-live region (not role="status") so it never competes
         with the per-screen status regions that components expose. */}
      <div className="sr-only" aria-live="polite" aria-atomic="true">
        {routeAnnouncement}
      </div>
    </>
  );
}
